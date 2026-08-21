from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date
from functools import partial
from typing import Any, Literal, cast

from pydantic import Field, model_validator
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.contracts import Key, StrictModel
from style_rotation.v022.graph import AssetContextSnapshot

CONTRACT_VERSION: Literal["v0.22.0"] = "v0.22.0"
ARTIFACT_TYPE = "v022_compiled_defense_execution_context"
ARTIFACT_VERSION = 1


class DefenseResolvedInputBinding(StrictModel):
    input_key: Key
    input_role: Literal["timing_reference", "defensive_asset", "reserve_accrual"]
    allocation_member_ordinal: int | None = Field(default=None, ge=0)
    dataset_publication_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    dataset_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_key: str = Field(min_length=1)
    dataset_version_number: int = Field(ge=1)
    calendar_version_id: uuid.UUID | None = None
    calendar_artifact_id: uuid.UUID | None = None
    calendar_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    reserve_return_model_version_id: uuid.UUID | None = None
    reserve_return_model_artifact_id: uuid.UUID | None = None
    coverage_start: date
    coverage_end: date
    security_ids: tuple[uuid.UUID, ...]

    @model_validator(mode="after")
    def validate_identity(self) -> DefenseResolvedInputBinding:
        if self.coverage_start > self.coverage_end:
            raise ValueError("Defense input coverage is inverted")
        calendar_values = (
            self.calendar_version_id,
            self.calendar_artifact_id,
            self.calendar_fingerprint,
        )
        if any(value is None for value in calendar_values) != all(
            value is None for value in calendar_values
        ):
            raise ValueError("Defense input Calendar identity must be complete")
        model_pair = (
            self.reserve_return_model_version_id,
            self.reserve_return_model_artifact_id,
        )
        if (model_pair[0] is None) != (model_pair[1] is None):
            raise ValueError("Defense input Reserve Model identity must be complete")
        if self.input_role == "timing_reference":
            if self.allocation_member_ordinal is not None or len(self.security_ids) != 1:
                raise ValueError("Timing input requires one non-allocation Security")
        elif self.input_role == "defensive_asset":
            if self.allocation_member_ordinal is None or len(self.security_ids) != 1:
                raise ValueError("Defensive asset input requires one Allocation Security")
        elif (
            self.allocation_member_ordinal is None
            or self.security_ids
            or self.reserve_return_model_version_id is None
        ):
            raise ValueError("Reserve input requires one Allocation member and its Model")
        return self


class DefenseResolvedBindingSnapshot(StrictModel):
    contract_version: Literal["v0.22.0"]
    bindings: tuple[DefenseResolvedInputBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_bindings(self) -> DefenseResolvedBindingSnapshot:
        keys = [item.input_key for item in self.bindings]
        if len(keys) != len(set(keys)):
            raise ValueError("Defense input keys must be unique")
        return self


@dataclass(frozen=True, slots=True)
class DefenseExecutionContextPublication:
    context_id: uuid.UUID
    artifact_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    defense_version_id: uuid.UUID
    context_fingerprint: str
    resolved_input_binding_fingerprint: str
    input_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _Identity:
    artifact_id: uuid.UUID
    semantic_fingerprint: str


@dataclass(frozen=True, slots=True)
class _BaseContext:
    context_id: uuid.UUID
    artifact: _Identity
    context_fingerprint: str
    coverage_start: date
    coverage_end: date
    asset_context: AssetContextSnapshot


@dataclass(frozen=True, slots=True)
class _Package:
    defense_version_id: uuid.UUID
    artifact: _Identity
    version_fingerprint: str
    timing_policy_version_id: uuid.UUID
    timing: _Identity
    timing_rule: dict[str, Any]
    allocation_policy_version_id: uuid.UUID
    allocation: _Identity
    asset_registry_release_id: uuid.UUID
    registry: _Identity
    allocation_asset_set_definition_id: uuid.UUID
    reserve_return_model_version_id: uuid.UUID | None
    reserve_model: _Identity | None


@dataclass(frozen=True, slots=True)
class _AllocationMember:
    ordinal: int
    security_id: uuid.UUID
    security_key: str
    legacy_asset_id: uuid.UUID | None
    component_role: str


@dataclass(frozen=True, slots=True)
class _ResolvedInput:
    ordinal: int
    binding: DefenseResolvedInputBinding
    document: dict[str, Any]
    fingerprint: str
    dataset: _Identity
    calendar: _Identity | None


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


class DefenseExecutionContextService:
    """Freeze auxiliary Defense inputs without changing the risk Graph context."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        compiled_execution_data_context_id: uuid.UUID,
        defense_version_id: uuid.UUID | None,
    ) -> DefenseExecutionContextPublication | None:
        if defense_version_id is None:
            return None
        with self._engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
                {
                    "lock_key": (
                        "v022-defense-context:"
                        f"{compiled_execution_data_context_id}:{defense_version_id}"
                    )
                },
            )
            existing = self._existing(
                connection,
                compiled_execution_data_context_id,
                defense_version_id,
            )
            if existing is not None:
                return existing
            base = self._base_context(connection, compiled_execution_data_context_id)
            package = self._package(connection, defense_version_id, base)
            members = self._allocation_members(connection, package)
            inputs = self._resolve_inputs(connection, base, package, members)
            binding_snapshot = DefenseResolvedBindingSnapshot(
                contract_version=CONTRACT_VERSION,
                bindings=tuple(item.binding for item in inputs),
            )
            binding_document = binding_snapshot.model_dump(mode="json")
            binding_fingerprint = sha256_hexdigest(binding_document)
            artifact_key = (
                "compiled_defense_execution_context__"
                f"{base.context_fingerprint}__{package.version_fingerprint}"
            )
            dependencies = _dependencies(base, package, inputs)
            semantic_payload = {
                "contract_version": CONTRACT_VERSION,
                "compiled_execution_data_context_id": str(base.context_id),
                "risk_context_fingerprint": base.context_fingerprint,
                "defense_version_id": str(package.defense_version_id),
                "defense_package_fingerprint": package.version_fingerprint,
                "defense_package_artifact_semantic_fingerprint": (
                    package.artifact.semantic_fingerprint
                ),
                "resolved_input_binding_document": binding_document,
                "resolved_input_binding_fingerprint": binding_fingerprint,
            }
            fingerprint = _semantic_fingerprint(
                artifact_key,
                semantic_payload,
                dependencies,
                base,
                package,
                inputs,
            )
            context_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"bird:v0.22:compiled-defense-execution-context:{fingerprint}",
            )
            result = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type=ARTIFACT_TYPE,
                artifact_key=artifact_key,
                version_number=ARTIFACT_VERSION,
                semantic_payload=semantic_payload,
                content_payload=semantic_payload,
                dependencies=dependencies,
                reason="publish immutable v0.22 compiled Defense execution context",
                draft_writer=partial(
                    self._write_projection,
                    context_id=context_id,
                    base=base,
                    package=package,
                    binding_document=binding_document,
                    binding_fingerprint=binding_fingerprint,
                    inputs=inputs,
                    context_fingerprint=fingerprint,
                ),
            )
            if result.semantic_fingerprint != fingerprint:
                raise ValueError("Defense Execution Context fingerprint calculation drifted")
        self._validate_projection(
            result.artifact_id,
            context_id,
            base.context_id,
            package.defense_version_id,
            fingerprint,
            binding_document,
            inputs,
        )
        return DefenseExecutionContextPublication(
            context_id,
            result.artifact_id,
            base.context_id,
            package.defense_version_id,
            fingerprint,
            binding_fingerprint,
            len(inputs),
            result.reused,
        )

    @staticmethod
    def _existing(
        connection: Connection,
        base_context_id: uuid.UUID,
        defense_version_id: uuid.UUID,
    ) -> DefenseExecutionContextPublication | None:
        row = (
            connection.execute(
                text(
                    """
                    SELECT context.compiled_defense_execution_context_id,
                           context.artifact_id,context.context_fingerprint,
                           context.resolved_input_binding_fingerprint,
                           context.resolved_input_binding_document,context.input_count,
                           artifact.artifact_type,artifact.version_number,
                           artifact.status,artifact.semantic_fingerprint,
                           count(input.ordinal) AS child_count
                      FROM defense.v022_compiled_defense_execution_context context
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=context.artifact_id
                      LEFT JOIN defense.v022_compiled_defense_execution_data_input input
                        ON input.compiled_defense_execution_context_id=
                           context.compiled_defense_execution_context_id
                     WHERE context.compiled_execution_data_context_id=:base_context
                       AND context.defense_version_id=:defense
                     GROUP BY context.compiled_defense_execution_context_id,
                              context.artifact_id,context.context_fingerprint,
                              context.resolved_input_binding_fingerprint,
                              context.resolved_input_binding_document,context.input_count,
                              artifact.artifact_type,artifact.version_number,
                              artifact.status,artifact.semantic_fingerprint
                    """
                ),
                {"base_context": base_context_id, "defense": defense_version_id},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if (
            row["artifact_type"] != ARTIFACT_TYPE
            or row["version_number"] != ARTIFACT_VERSION
            or row["status"] != "published"
            or row["semantic_fingerprint"] != row["context_fingerprint"]
            or row["child_count"] != row["input_count"]
            or row["resolved_input_binding_document"].get("contract_version") != CONTRACT_VERSION
            or len(row["resolved_input_binding_document"].get("bindings", [])) != row["input_count"]
        ):
            raise ValueError(
                "defense_context_existing_incomplete: immutable Context cannot be replayed"
            )
        return DefenseExecutionContextPublication(
            row["compiled_defense_execution_context_id"],
            row["artifact_id"],
            base_context_id,
            defense_version_id,
            row["context_fingerprint"],
            row["resolved_input_binding_fingerprint"],
            row["input_count"],
            True,
        )

    @staticmethod
    def _base_context(connection: Connection, context_id: uuid.UUID) -> _BaseContext:
        row = (
            connection.execute(
                text(
                    """
                SELECT context.artifact_id,context.context_fingerprint,
                       context.asset_context_document,
                       max(input.coverage_start) AS coverage_start,
                       min(input.coverage_end) AS coverage_end,
                       artifact.artifact_type,artifact.status,
                       artifact.semantic_fingerprint
                  FROM workspace.v022_compiled_execution_data_context context
                  JOIN lineage.artifact artifact ON artifact.artifact_id=context.artifact_id
                  JOIN workspace.v022_compiled_execution_data_input input
                    ON input.compiled_execution_data_context_id=
                       context.compiled_execution_data_context_id
                 WHERE context.compiled_execution_data_context_id=:context
                 GROUP BY context.artifact_id,context.context_fingerprint,
                          context.asset_context_document,artifact.artifact_type,
                          artifact.status,artifact.semantic_fingerprint
                """
                ),
                {"context": context_id},
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or row["artifact_type"] != "v022_compiled_execution_data_context"
            or row["status"] != "published"
            or row["semantic_fingerprint"] is None
            or row["coverage_start"] > row["coverage_end"]
        ):
            raise ValueError(
                "defense_context_risk_context_unpublished: exact base Context required"
            )
        return _BaseContext(
            context_id,
            _Identity(row["artifact_id"], row["semantic_fingerprint"]),
            row["context_fingerprint"],
            row["coverage_start"],
            row["coverage_end"],
            AssetContextSnapshot.model_validate(row["asset_context_document"]),
        )

    @staticmethod
    def _package(
        connection: Connection,
        defense_version_id: uuid.UUID,
        base: _BaseContext,
    ) -> _Package:
        row = (
            connection.execute(
                text(
                    """
                SELECT package.*,version.artifact_id AS package_artifact_id,
                       version.version_fingerprint,
                       package_artifact.artifact_type AS package_artifact_type,
                       package_artifact.status AS package_status,
                       package_artifact.semantic_fingerprint AS package_fingerprint,
                       timing.rule AS timing_rule,
                       timing_artifact.status AS timing_status,
                       timing_artifact.semantic_fingerprint AS timing_fingerprint,
                       allocation_artifact.status AS allocation_status,
                       allocation_artifact.semantic_fingerprint AS allocation_fingerprint,
                       registry_artifact.status AS registry_status,
                       registry_artifact.semantic_fingerprint AS registry_fingerprint,
                       reserve_artifact.status AS reserve_status,
                       reserve_artifact.semantic_fingerprint AS reserve_fingerprint,
                       supported.asset_set_definition_id AS supported_asset_set_definition_id
                  FROM defense.v022_defense_package_policy_binding package
                  JOIN defense.defense_version version
                    ON version.defense_version_id=package.defense_version_id
                  JOIN lineage.artifact package_artifact
                    ON package_artifact.artifact_id=version.artifact_id
                  JOIN defense.v022_timing_policy_version timing
                    ON timing.timing_policy_version_id=package.timing_policy_version_id
                   AND timing.artifact_id=package.timing_policy_artifact_id
                  JOIN lineage.artifact timing_artifact
                    ON timing_artifact.artifact_id=timing.artifact_id
                  JOIN defense.v022_allocation_policy_version allocation
                    ON allocation.allocation_policy_version_id=
                       package.allocation_policy_version_id
                   AND allocation.artifact_id=package.allocation_policy_artifact_id
                  JOIN lineage.artifact allocation_artifact
                    ON allocation_artifact.artifact_id=allocation.artifact_id
                  JOIN lineage.artifact registry_artifact
                    ON registry_artifact.artifact_id=package.asset_registry_artifact_id
                  LEFT JOIN lineage.artifact reserve_artifact
                    ON reserve_artifact.artifact_id=
                       package.reserve_return_model_artifact_id
                  LEFT JOIN defense.v022_defense_package_supported_asset_set supported
                    ON supported.defense_version_id=package.defense_version_id
                   AND supported.asset_context_key=:asset_context_key
                   AND supported.asset_registry_release_id=:registry_release
                   AND supported.asset_registry_artifact_id=:registry_artifact
                 WHERE package.defense_version_id=:defense
                """
                ),
                {
                    "defense": defense_version_id,
                    "asset_context_key": base.asset_context.asset_context_key,
                    "registry_release": base.asset_context.asset_registry_release_id,
                    "registry_artifact": base.asset_context.asset_registry_artifact_id,
                },
            )
            .mappings()
            .one_or_none()
        )
        if (
            row is None
            or (
                base.asset_context.selection_kind != "explicit_security_selection"
                and row["supported_asset_set_definition_id"]
                != base.asset_context.asset_set_definition_id
            )
            or (
                base.asset_context.selection_kind == "explicit_security_selection"
                and (
                    base.asset_context.selection_group not in {"stock", "fund"}
                    or row["asset_registry_release_id"]
                    != base.asset_context.asset_registry_release_id
                    or row["asset_registry_artifact_id"]
                    != base.asset_context.asset_registry_artifact_id
                )
            )
            or row["package_artifact_type"] != "v022_defense_version"
            or row["package_status"] != "published"
            or row["timing_status"] != "published"
            or row["allocation_status"] != "published"
            or row["registry_status"] != "published"
            or row["package_fingerprint"] is None
            or row["timing_fingerprint"] is None
            or row["allocation_fingerprint"] is None
            or row["registry_fingerprint"] is None
            or (
                row["reserve_return_model_artifact_id"] is not None
                and (row["reserve_status"] != "published" or row["reserve_fingerprint"] is None)
            )
        ):
            raise ValueError(
                "defense_context_package_incompatible: exact published Package for "
                "risk universe required"
            )
        reserve = (
            None
            if row["reserve_return_model_artifact_id"] is None
            else _Identity(row["reserve_return_model_artifact_id"], row["reserve_fingerprint"])
        )
        return _Package(
            defense_version_id,
            _Identity(row["package_artifact_id"], row["package_fingerprint"]),
            row["version_fingerprint"],
            row["timing_policy_version_id"],
            _Identity(row["timing_policy_artifact_id"], row["timing_fingerprint"]),
            row["timing_rule"],
            row["allocation_policy_version_id"],
            _Identity(row["allocation_policy_artifact_id"], row["allocation_fingerprint"]),
            row["asset_registry_release_id"],
            _Identity(row["asset_registry_artifact_id"], row["registry_fingerprint"]),
            row["allocation_asset_set_definition_id"],
            row["reserve_return_model_version_id"],
            reserve,
        )

    @staticmethod
    def _allocation_members(
        connection: Connection, package: _Package
    ) -> tuple[_AllocationMember, ...]:
        rows = (
            connection.execute(
                text(
                    """
                SELECT member.ordinal,member.security_id,security.security_key,
                       security.legacy_asset_id,member.component_role
                  FROM defense.v022_allocation_policy_member member
                  JOIN catalog.security security ON security.security_id=member.security_id
                 WHERE member.allocation_policy_version_id=:version
                 ORDER BY member.ordinal
                """
                ),
                {"version": package.allocation_policy_version_id},
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ValueError("defense_context_allocation_empty: Package has no members")
        return tuple(_AllocationMember(**dict(row)) for row in rows)

    @classmethod
    def _resolve_inputs(
        cls,
        connection: Connection,
        base: _BaseContext,
        package: _Package,
        members: tuple[_AllocationMember, ...],
    ) -> tuple[_ResolvedInput, ...]:
        timing_key = (
            package.timing_rule.get("reference_asset_key")
            if package.timing_rule.get("rule_type") == "moving_average_tiered_budget"
            else None
        )
        timing_security = None
        if timing_key is not None:
            timing_security = cls._security(connection, package, str(timing_key))
        defensive = tuple(item for item in members if item.component_role == "defensive_asset")
        if any(item.legacy_asset_id is None for item in defensive):
            raise ValueError(
                "defense_context_defensive_identity_missing: canonical Asset identity required"
            )
        market_security_ids = tuple(
            dict.fromkeys(
                ([timing_security["security_id"]] if timing_security is not None else [])
                + [item.security_id for item in defensive]
            )
        )
        market_asset_ids = tuple(
            dict.fromkeys(
                ([timing_security["legacy_asset_id"]] if timing_security is not None else [])
                + [item.legacy_asset_id for item in defensive]
            )
        )
        market = cls._market_dataset(
            connection,
            base,
            market_asset_ids,
            timing_security["legacy_asset_id"] if timing_security is not None else None,
            int(package.timing_rule.get("moving_average_window_sessions", 0)),
        )
        bindings: list[DefenseResolvedInputBinding] = []
        if timing_security is not None:
            bindings.append(
                cls._binding(
                    "timing_reference__" + timing_security["security_key"],
                    "timing_reference",
                    None,
                    (timing_security["security_id"],),
                    market,
                )
            )
        for member in defensive:
            bindings.append(
                cls._binding(
                    "defensive_asset__" + member.security_key,
                    "defensive_asset",
                    member.ordinal,
                    (member.security_id,),
                    market,
                )
            )
        reserve_members = tuple(item for item in members if item.component_role == "reserve")
        if reserve_members:
            if len(reserve_members) != 1 or package.reserve_model is None:
                raise ValueError(
                    "defense_context_reserve_identity_missing: exact Reserve Model required"
                )
            reserve = cls._reserve_dataset(connection, base, package)
            member = reserve_members[0]
            bindings.append(
                cls._binding(
                    "reserve_accrual__" + member.security_key,
                    "reserve_accrual",
                    member.ordinal,
                    (),
                    reserve,
                    package=package,
                )
            )
        if {item.security_ids[0] for item in bindings if item.security_ids} != set(
            market_security_ids
        ):
            raise ValueError("Defense input Security resolution drifted")
        common_start = max(
            (base.coverage_start, *(binding.coverage_start for binding in bindings))
        )
        common_end = min(
            (base.coverage_end, *(binding.coverage_end for binding in bindings))
        )
        if common_start > common_end:
            raise ValueError(
                "defense_context_common_coverage_missing: risk and Defense inputs do not overlap"
            )
        return tuple(
            _ResolvedInput(
                ordinal,
                binding,
                binding.model_dump(mode="json"),
                sha256_hexdigest(binding.model_dump(mode="json")),
                _Identity(binding.dataset_artifact_id, binding.dataset_fingerprint),
                (
                    None
                    if binding.calendar_artifact_id is None
                    else _Identity(
                        binding.calendar_artifact_id,
                        str(binding.calendar_fingerprint),
                    )
                ),
            )
            for ordinal, binding in enumerate(bindings)
        )

    @staticmethod
    def _security(connection: Connection, package: _Package, security_key: str) -> dict[str, Any]:
        row = (
            connection.execute(
                text(
                    """
                SELECT security.security_id,security.security_key,security.legacy_asset_id
                  FROM catalog.security security
                  JOIN catalog.security_profile profile
                    ON profile.security_id=security.security_id
                   AND profile.asset_registry_release_id=:release
                 WHERE security.security_key=:key
                """
                ),
                {"release": package.asset_registry_release_id, "key": security_key},
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["legacy_asset_id"] is None:
            raise ValueError(
                f"defense_context_security_missing: {security_key} lacks canonical identity"
            )
        return dict(row)

    @staticmethod
    def _market_dataset(
        connection: Connection,
        base: _BaseContext,
        asset_ids: tuple[uuid.UUID | None, ...],
        timing_asset_id: uuid.UUID | None,
        timing_window: int,
    ) -> dict[str, Any]:
        if not asset_ids or any(item is None for item in asset_ids):
            raise ValueError("defense_context_market_identity_missing")
        exact_asset_ids = tuple(item for item in asset_ids if item is not None)
        candidates = (
            connection.execute(
                text(
                    """
                SELECT publication.dataset_publication_id,publication.artifact_id,
                       publication.dataset_key,publication.version_number,
                       publication.coverage_start,publication.coverage_end,
                       publication.calendar_version_id,
                       dataset.semantic_fingerprint AS dataset_fingerprint,
                       calendar.artifact_id AS calendar_artifact_id,
                       calendar_artifact.semantic_fingerprint AS calendar_fingerprint,
                       dataset.created_at
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact dataset ON dataset.artifact_id=publication.artifact_id
                  JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=publication.calendar_version_id
                  JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                  JOIN data.dataset_coverage coverage
                    ON coverage.dataset_publication_id=publication.dataset_publication_id
                 WHERE publication.value_kind='daily_bar'
                   AND dataset.status='published'
                   AND dataset.semantic_fingerprint IS NOT NULL
                   AND calendar_artifact.status='published'
                   AND calendar_artifact.semantic_fingerprint IS NOT NULL
                   AND publication.coverage_start<=:start
                   AND publication.coverage_end>=:end
                   AND coverage.asset_id IN :asset_ids
                   AND coverage.coverage_start<=:start
                   AND coverage.coverage_end>=:end
                   AND coverage.missing_count=0
                 GROUP BY publication.dataset_publication_id,publication.artifact_id,
                          publication.dataset_key,publication.version_number,
                          publication.coverage_start,publication.coverage_end,
                          publication.calendar_version_id,dataset.semantic_fingerprint,
                          calendar.artifact_id,calendar_artifact.semantic_fingerprint,
                          dataset.created_at
                HAVING count(DISTINCT coverage.asset_id)=:asset_count
                 ORDER BY dataset.created_at DESC,publication.version_number DESC,
                          publication.dataset_publication_id DESC
                """
                ).bindparams(bindparam("asset_ids", expanding=True)),
                {
                    "start": base.coverage_start,
                    "end": base.coverage_end,
                    "asset_ids": exact_asset_ids,
                    "asset_count": len(exact_asset_ids),
                },
            )
            .mappings()
            .all()
        )
        for candidate in candidates:
            actual = (
                connection.execute(
                    text(
                        """
                    SELECT asset_id,count(*) AS observation_count,
                           min(session_date) AS coverage_start,
                           max(session_date) AS coverage_end
                      FROM data.daily_bar
                     WHERE dataset_publication_id=:publication
                       AND asset_id IN :asset_ids
                     GROUP BY asset_id
                    """
                    ).bindparams(bindparam("asset_ids", expanding=True)),
                    {
                        "publication": candidate["dataset_publication_id"],
                        "asset_ids": exact_asset_ids,
                    },
                )
                .mappings()
                .all()
            )
            by_asset = {row["asset_id"]: row for row in actual}
            if set(by_asset) != set(exact_asset_ids) or any(
                row["coverage_start"] > base.coverage_start
                or row["coverage_end"] < base.coverage_end
                for row in actual
            ):
                continue
            if timing_asset_id is not None and (
                timing_asset_id not in by_asset
                or by_asset[timing_asset_id]["observation_count"] < timing_window
            ):
                continue
            return dict(candidate)
        raise ValueError(
            "defense_context_market_coverage_missing: one exact Dataset must cover "
            "SPY and every defensive asset"
        )

    @staticmethod
    def _reserve_dataset(
        connection: Connection, base: _BaseContext, package: _Package
    ) -> dict[str, Any]:
        assert package.reserve_model is not None
        candidates = (
            connection.execute(
                text(
                    """
                SELECT publication.dataset_publication_id,publication.artifact_id,
                       publication.dataset_key,publication.version_number,
                       publication.coverage_start,publication.coverage_end,
                       publication.calendar_version_id,
                       dataset.semantic_fingerprint AS dataset_fingerprint,
                       calendar.artifact_id AS calendar_artifact_id,
                       calendar_artifact.semantic_fingerprint AS calendar_fingerprint,
                       dataset.created_at
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact dataset ON dataset.artifact_id=publication.artifact_id
                  JOIN lineage.artifact_dependency dependency
                    ON dependency.artifact_id=publication.artifact_id
                   AND dependency.depends_on_artifact_id=:model_artifact
                   AND dependency.role='reserve_model'
                  JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=publication.calendar_version_id
                  JOIN lineage.artifact calendar_artifact
                    ON calendar_artifact.artifact_id=calendar.artifact_id
                 WHERE publication.value_kind='reserve_return'
                   AND dataset.status='published'
                   AND dataset.semantic_fingerprint IS NOT NULL
                   AND calendar_artifact.status='published'
                   AND calendar_artifact.semantic_fingerprint IS NOT NULL
                   AND publication.coverage_start<=:end
                   AND publication.coverage_end>=:start
                 ORDER BY dataset.created_at DESC,publication.version_number DESC,
                          publication.dataset_publication_id DESC
                """
                ),
                {
                    "model_artifact": package.reserve_model.artifact_id,
                    "start": base.coverage_start,
                    "end": base.coverage_end,
                },
            )
            .mappings()
            .all()
        )
        for candidate in candidates:
            actual = connection.execute(
                text(
                    """
                    SELECT min(interval_start),max(interval_end),count(*)
                      FROM data.reserve_return
                     WHERE dataset_publication_id=:publication
                    """
                ),
                {"publication": candidate["dataset_publication_id"]},
            ).one()
            if actual[0] <= base.coverage_end and actual[1] >= base.coverage_start:
                return dict(candidate)
        raise ValueError("defense_context_reserve_coverage_missing: exact Reserve Dataset required")

    @staticmethod
    def _binding(
        input_key: str,
        input_role: Literal["timing_reference", "defensive_asset", "reserve_accrual"],
        member_ordinal: int | None,
        security_ids: tuple[uuid.UUID, ...],
        dataset: dict[str, Any],
        *,
        package: _Package | None = None,
    ) -> DefenseResolvedInputBinding:
        return DefenseResolvedInputBinding(
            input_key=input_key,
            input_role=input_role,
            allocation_member_ordinal=member_ordinal,
            dataset_publication_id=dataset["dataset_publication_id"],
            dataset_artifact_id=dataset["artifact_id"],
            dataset_fingerprint=dataset["dataset_fingerprint"],
            dataset_key=dataset["dataset_key"],
            dataset_version_number=dataset["version_number"],
            calendar_version_id=dataset["calendar_version_id"],
            calendar_artifact_id=dataset["calendar_artifact_id"],
            calendar_fingerprint=dataset["calendar_fingerprint"],
            reserve_return_model_version_id=(
                None if package is None else package.reserve_return_model_version_id
            ),
            reserve_return_model_artifact_id=(
                None
                if package is None or package.reserve_model is None
                else package.reserve_model.artifact_id
            ),
            coverage_start=dataset["coverage_start"],
            coverage_end=dataset["coverage_end"],
            security_ids=security_ids,
        )

    @staticmethod
    def _write_projection(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        context_id: uuid.UUID,
        base: _BaseContext,
        package: _Package,
        binding_document: dict[str, Any],
        binding_fingerprint: str,
        inputs: tuple[_ResolvedInput, ...],
        context_fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO defense.v022_compiled_defense_execution_context (
                  compiled_defense_execution_context_id,artifact_id,
                  compiled_execution_data_context_id,defense_version_id,
                  defense_package_artifact_id,timing_policy_version_id,
                  timing_policy_artifact_id,allocation_policy_version_id,
                  allocation_policy_artifact_id,asset_registry_release_id,
                  asset_registry_artifact_id,allocation_asset_set_definition_id,
                  reserve_return_model_version_id,reserve_return_model_artifact_id,
                  contract_version,resolved_input_binding_document,
                  resolved_input_binding_fingerprint,input_count,context_fingerprint
                ) VALUES (
                  :id,:artifact,:risk_context,:defense,:package_artifact,
                  :timing_version,:timing_artifact,:allocation_version,
                  :allocation_artifact,:registry_release,:registry_artifact,
                  :allocation_set,:reserve_model_version,:reserve_model_artifact,
                  :contract,CAST(:binding AS jsonb),:binding_fingerprint,
                  :input_count,:context_fingerprint
                )
                """
            ),
            {
                "id": context_id,
                "artifact": artifact_id,
                "risk_context": base.context_id,
                "defense": package.defense_version_id,
                "package_artifact": package.artifact.artifact_id,
                "timing_version": package.timing_policy_version_id,
                "timing_artifact": package.timing.artifact_id,
                "allocation_version": package.allocation_policy_version_id,
                "allocation_artifact": package.allocation.artifact_id,
                "registry_release": package.asset_registry_release_id,
                "registry_artifact": package.registry.artifact_id,
                "allocation_set": package.allocation_asset_set_definition_id,
                "reserve_model_version": package.reserve_return_model_version_id,
                "reserve_model_artifact": (
                    None if package.reserve_model is None else package.reserve_model.artifact_id
                ),
                "contract": CONTRACT_VERSION,
                "binding": _json(binding_document),
                "binding_fingerprint": binding_fingerprint,
                "input_count": len(inputs),
                "context_fingerprint": context_fingerprint,
            },
        )
        for item in inputs:
            binding = item.binding
            connection.execute(
                text(
                    """
                    INSERT INTO defense.v022_compiled_defense_execution_data_input (
                      compiled_defense_execution_context_id,ordinal,input_key,input_role,
                      allocation_member_ordinal,dataset_publication_id,dataset_artifact_id,
                      dataset_fingerprint,calendar_version_id,calendar_artifact_id,
                      calendar_fingerprint,reserve_return_model_version_id,
                      reserve_return_model_artifact_id,coverage_start,coverage_end,
                      security_ids,binding_document,binding_fingerprint
                    ) VALUES (
                      :context,:ordinal,:input_key,:input_role,:member_ordinal,
                      :dataset_publication,:dataset_artifact,:dataset_fingerprint,
                      :calendar_version,:calendar_artifact,:calendar_fingerprint,
                      :reserve_model_version,:reserve_model_artifact,:coverage_start,
                      :coverage_end,CAST(:security_ids AS jsonb),
                      CAST(:binding AS jsonb),:binding_fingerprint
                    )
                    """
                ),
                {
                    "context": context_id,
                    "ordinal": item.ordinal,
                    "input_key": binding.input_key,
                    "input_role": binding.input_role,
                    "member_ordinal": binding.allocation_member_ordinal,
                    "dataset_publication": binding.dataset_publication_id,
                    "dataset_artifact": binding.dataset_artifact_id,
                    "dataset_fingerprint": binding.dataset_fingerprint,
                    "calendar_version": binding.calendar_version_id,
                    "calendar_artifact": binding.calendar_artifact_id,
                    "calendar_fingerprint": binding.calendar_fingerprint,
                    "reserve_model_version": binding.reserve_return_model_version_id,
                    "reserve_model_artifact": binding.reserve_return_model_artifact_id,
                    "coverage_start": binding.coverage_start,
                    "coverage_end": binding.coverage_end,
                    "security_ids": _json([str(value) for value in binding.security_ids]),
                    "binding": _json(item.document),
                    "binding_fingerprint": item.fingerprint,
                },
            )

    def _validate_projection(
        self,
        artifact_id: uuid.UUID,
        context_id: uuid.UUID,
        base_context_id: uuid.UUID,
        defense_version_id: uuid.UUID,
        context_fingerprint: str,
        binding_document: dict[str, Any],
        inputs: tuple[_ResolvedInput, ...],
    ) -> None:
        with self._engine.connect() as connection:
            context = (
                connection.execute(
                    text(
                        """
                    SELECT compiled_defense_execution_context_id,
                           compiled_execution_data_context_id,defense_version_id,
                           context_fingerprint,resolved_input_binding_document,input_count
                      FROM defense.v022_compiled_defense_execution_context
                     WHERE artifact_id=:artifact
                    """
                    ),
                    {"artifact": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            children = (
                connection.execute(
                    text(
                        """
                    SELECT ordinal,binding_document,binding_fingerprint
                      FROM defense.v022_compiled_defense_execution_data_input
                     WHERE compiled_defense_execution_context_id=:context
                     ORDER BY ordinal
                    """
                    ),
                    {"context": context_id},
                )
                .mappings()
                .all()
            )
        if (
            context is None
            or context["compiled_defense_execution_context_id"] != context_id
            or context["compiled_execution_data_context_id"] != base_context_id
            or context["defense_version_id"] != defense_version_id
            or context["context_fingerprint"] != context_fingerprint
            or context["resolved_input_binding_document"] != binding_document
            or context["input_count"] != len(inputs)
            or len(children) != len(inputs)
            or any(
                child["ordinal"] != item.ordinal
                or child["binding_document"] != item.document
                or child["binding_fingerprint"] != item.fingerprint
                for child, item in zip(children, inputs, strict=True)
            )
        ):
            raise ValueError("Defense Execution Context projection identity collision")


def _dependencies(
    base: _BaseContext,
    package: _Package,
    inputs: tuple[_ResolvedInput, ...],
) -> tuple[DependencyInput, ...]:
    result = [
        DependencyInput(base.artifact.artifact_id, "compiled_execution_data_context", 0),
        DependencyInput(package.artifact.artifact_id, "defense_package", 1),
        DependencyInput(package.timing.artifact_id, "defense_timing_policy_version", 2),
        DependencyInput(package.allocation.artifact_id, "defense_allocation_policy_version", 3),
        DependencyInput(package.registry.artifact_id, "asset_registry_release", 4),
    ]
    if package.reserve_model is not None:
        result.append(
            DependencyInput(package.reserve_model.artifact_id, "reserve_return_model_version", 5)
        )
    datasets: dict[uuid.UUID, int] = {}
    calendars: dict[uuid.UUID, int] = {}
    for item in inputs:
        datasets.setdefault(item.dataset.artifact_id, item.ordinal)
        if item.calendar is not None:
            calendars.setdefault(item.calendar.artifact_id, item.ordinal)
    result.extend(
        DependencyInput(artifact_id, "defense_data_input", ordinal)
        for artifact_id, ordinal in datasets.items()
    )
    result.extend(
        DependencyInput(artifact_id, "defense_calendar", ordinal)
        for artifact_id, ordinal in calendars.items()
    )
    return tuple(result)


def _semantic_fingerprint(
    artifact_key: str,
    semantic_payload: dict[str, Any],
    dependencies: tuple[DependencyInput, ...],
    base: _BaseContext,
    package: _Package,
    inputs: tuple[_ResolvedInput, ...],
) -> str:
    fingerprints = {
        base.artifact.artifact_id: base.artifact.semantic_fingerprint,
        package.artifact.artifact_id: package.artifact.semantic_fingerprint,
        package.timing.artifact_id: package.timing.semantic_fingerprint,
        package.allocation.artifact_id: package.allocation.semantic_fingerprint,
        package.registry.artifact_id: package.registry.semantic_fingerprint,
    }
    if package.reserve_model is not None:
        fingerprints[package.reserve_model.artifact_id] = package.reserve_model.semantic_fingerprint
    for item in inputs:
        fingerprints[item.dataset.artifact_id] = item.dataset.semantic_fingerprint
        if item.calendar is not None:
            fingerprints[item.calendar.artifact_id] = item.calendar.semantic_fingerprint
    return sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": ARTIFACT_TYPE,
                "artifact_key": artifact_key,
                "version_number": ARTIFACT_VERSION,
            },
            "semantic_payload": semantic_payload,
            "dependencies": [
                {
                    "role": item.role,
                    "ordinal": item.ordinal,
                    "semantic_fingerprint": fingerprints[item.artifact_id],
                }
                for item in dependencies
            ],
        }
    )


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
