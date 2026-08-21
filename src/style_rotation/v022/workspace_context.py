from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.asset_selection import ExplicitAssetSelectionService
from style_rotation.v022.frozen_sp500_environment import (
    _BENCHMARK_DATASET_KEY as ACTIVE_BENCHMARK_DATASET_KEY,
)
from style_rotation.v022.frozen_sp500_environment import (
    _BENCHMARK_DATASET_VERSION as ACTIVE_BENCHMARK_DATASET_VERSION,
)
from style_rotation.v022.frozen_sp500_environment import (
    _DATASET_GATE_KEY as ACTIVE_DATASET_GATE_KEY,
)
from style_rotation.v022.frozen_sp500_environment import (
    _DATASET_GATE_VERSION as ACTIVE_DATASET_GATE_VERSION,
)
from style_rotation.v022.frozen_sp500_environment import (
    _RISK_DATASET_KEY as ACTIVE_RISK_DATASET_KEY,
)
from style_rotation.v022.frozen_sp500_environment import (
    _RISK_DATASET_VERSION as ACTIVE_RISK_DATASET_VERSION,
)
from style_rotation.v022.frozen_sp500_environment import (
    FROZEN_SP500_COHORT_VERSION,
    frozen_sp500_cohort_key,
)
from style_rotation.v022.green_baseline_registry import (
    GREEN_BASELINE_REGISTRY_CATALOG_VERSION as FROZEN_SP500_REGISTRY_CATALOG_VERSION,
)
from style_rotation.v022.green_baseline_registry import (
    GREEN_BASELINE_REGISTRY_RELEASE_KEY as ACTIVE_ASSET_REGISTRY_RELEASE_KEY,
)

UNCONFIGURED_ASSET_CONTEXT_KEY = "unconfigured"
CANONICAL_MARKET_INPUT = "canonical_market_bars"


@dataclass(frozen=True, slots=True)
class ResolvedWorkspaceContext:
    asset_context_document: dict[str, Any]
    resolved_data_binding_document: dict[str, Any]
    asset_context_fingerprint: str
    resolved_data_binding_fingerprint: str


@dataclass(frozen=True, slots=True)
class ActiveV022WorkspaceIdentity:
    """One complete, published v0.22 Registry/Gate/runtime environment."""

    asset_registry_release_id: uuid.UUID
    asset_registry_artifact_id: uuid.UUID
    asset_registry_version_number: int
    asset_registry_catalog_version: str
    asset_registry_as_of_date: date
    universe_history_id: uuid.UUID
    risk_dataset_publication_id: uuid.UUID
    risk_dataset_artifact_id: uuid.UUID
    risk_dataset_key: str
    risk_dataset_version_number: int
    benchmark_dataset_publication_id: uuid.UUID
    benchmark_dataset_artifact_id: uuid.UUID
    benchmark_dataset_key: str
    benchmark_dataset_version_number: int
    dataset_gate_assessment_id: uuid.UUID
    dataset_gate_artifact_id: uuid.UUID


def active_v022_workspace_identity(
    connection: Connection,
) -> ActiveV022WorkspaceIdentity | None:
    """Return the sole complete v0.22 environment, never an arbitrary latest row.

    Registry publication alone is insufficient.  New Drafts and the candidate
    catalog become active only after the exact risk Dataset, Dataset Gate and both
    frequency runtime contracts have all been published.  This prevents a larger
    historical Dataset (for example v4) from winning a coverage-count tie-break.
    """

    rows = (
        connection.execute(
            text(
                """
                SELECT registry.asset_registry_release_id,
                       registry.artifact_id AS asset_registry_artifact_id,
                       registry.version_number AS asset_registry_version_number,
                       registry.catalog_version AS asset_registry_catalog_version,
                       registry.as_of_date AS asset_registry_as_of_date,
                       gate.universe_history_id,
                       risk.dataset_publication_id AS risk_dataset_publication_id,
                       risk.artifact_id AS risk_dataset_artifact_id,
                       risk.dataset_key AS risk_dataset_key,
                       risk.version_number AS risk_dataset_version_number,
                       benchmark.dataset_publication_id AS benchmark_dataset_publication_id,
                       benchmark.artifact_id AS benchmark_dataset_artifact_id,
                       benchmark.dataset_key AS benchmark_dataset_key,
                       benchmark.version_number AS benchmark_dataset_version_number,
                       gate.dataset_gate_assessment_id,
                       gate.artifact_id AS dataset_gate_artifact_id
                  FROM catalog.asset_registry_release registry
                  JOIN lineage.artifact registry_artifact
                    ON registry_artifact.artifact_id=registry.artifact_id
                   AND registry_artifact.status='published'
                  JOIN lineage.artifact_dependency risk_dependency
                    ON risk_dependency.artifact_id=registry.artifact_id
                   AND risk_dependency.role='canonical_market_dataset'
                  JOIN data.dataset_publication risk
                    ON risk.artifact_id=risk_dependency.depends_on_artifact_id
                   AND risk.dataset_key=:risk_dataset_key
                   AND risk.version_number=:risk_dataset_version
                  JOIN lineage.artifact risk_artifact
                    ON risk_artifact.artifact_id=risk.artifact_id
                   AND risk_artifact.status='published'
                  JOIN lineage.artifact_dependency gate_dependency
                    ON gate_dependency.artifact_id=registry.artifact_id
                   AND gate_dependency.role='dataset_gate_assessment'
                  JOIN data.v022_dataset_gate_assessment gate
                    ON gate.artifact_id=gate_dependency.depends_on_artifact_id
                   AND gate.dataset_publication_id=risk.dataset_publication_id
                   AND gate.gate_key=:dataset_gate_key
                   AND gate.version_number=:dataset_gate_version
                   AND gate.ranking_eligibility='rankable_research'
                   AND gate.product_eligibility IN ('eligible','eligible_with_warnings')
                  JOIN lineage.artifact gate_artifact
                    ON gate_artifact.artifact_id=gate.artifact_id
                   AND gate_artifact.status='published'
                  JOIN experiment.v022_evaluation_cohort_version weekly_cohort
                    ON weekly_cohort.universe_history_id=gate.universe_history_id
                   AND weekly_cohort.dataset_publication_id=risk.dataset_publication_id
                   AND weekly_cohort.cohort_key=:weekly_cohort_key
                   AND weekly_cohort.version_number=:cohort_version
                   AND weekly_cohort.frequency='weekly'
                   AND weekly_cohort.research_tier='rankable_research'
                  JOIN lineage.artifact weekly_cohort_artifact
                    ON weekly_cohort_artifact.artifact_id=weekly_cohort.artifact_id
                   AND weekly_cohort_artifact.status='published'
                  JOIN experiment.v022_evaluation_cohort_runtime_contract weekly_runtime
                    ON weekly_runtime.evaluation_cohort_version_id=
                       weekly_cohort.evaluation_cohort_version_id
                   AND weekly_runtime.dataset_gate_assessment_id=
                       gate.dataset_gate_assessment_id
                   AND weekly_runtime.ranking_eligibility='rankable_research'
                  JOIN lineage.artifact weekly_runtime_artifact
                    ON weekly_runtime_artifact.artifact_id=weekly_runtime.artifact_id
                   AND weekly_runtime_artifact.status='published'
                  JOIN experiment.v022_evaluation_cohort_version monthly_cohort
                    ON monthly_cohort.universe_history_id=gate.universe_history_id
                   AND monthly_cohort.dataset_publication_id=risk.dataset_publication_id
                   AND monthly_cohort.cohort_key=:monthly_cohort_key
                   AND monthly_cohort.benchmark_dataset_publication_id=
                       weekly_cohort.benchmark_dataset_publication_id
                   AND monthly_cohort.version_number=:cohort_version
                   AND monthly_cohort.frequency='monthly'
                   AND monthly_cohort.research_tier='rankable_research'
                  JOIN lineage.artifact monthly_cohort_artifact
                    ON monthly_cohort_artifact.artifact_id=monthly_cohort.artifact_id
                   AND monthly_cohort_artifact.status='published'
                  JOIN experiment.v022_evaluation_cohort_runtime_contract monthly_runtime
                    ON monthly_runtime.evaluation_cohort_version_id=
                       monthly_cohort.evaluation_cohort_version_id
                   AND monthly_runtime.dataset_gate_assessment_id=
                       gate.dataset_gate_assessment_id
                   AND monthly_runtime.ranking_eligibility='rankable_research'
                  JOIN lineage.artifact monthly_runtime_artifact
                    ON monthly_runtime_artifact.artifact_id=monthly_runtime.artifact_id
                   AND monthly_runtime_artifact.status='published'
                  JOIN data.dataset_publication benchmark
                    ON benchmark.dataset_publication_id=
                       weekly_cohort.benchmark_dataset_publication_id
                   AND benchmark.dataset_key=:benchmark_dataset_key
                   AND benchmark.version_number=:benchmark_dataset_version
                  JOIN lineage.artifact benchmark_artifact
                    ON benchmark_artifact.artifact_id=benchmark.artifact_id
                   AND benchmark_artifact.status='published'
                 WHERE registry.release_key=:registry_release_key
                   AND registry.catalog_version=:registry_catalog_version
                """
            ),
            {
                "registry_release_key": ACTIVE_ASSET_REGISTRY_RELEASE_KEY,
                "registry_catalog_version": FROZEN_SP500_REGISTRY_CATALOG_VERSION,
                "risk_dataset_key": ACTIVE_RISK_DATASET_KEY,
                "risk_dataset_version": ACTIVE_RISK_DATASET_VERSION,
                "benchmark_dataset_key": ACTIVE_BENCHMARK_DATASET_KEY,
                "benchmark_dataset_version": ACTIVE_BENCHMARK_DATASET_VERSION,
                "dataset_gate_key": ACTIVE_DATASET_GATE_KEY,
                "dataset_gate_version": ACTIVE_DATASET_GATE_VERSION,
                "cohort_version": FROZEN_SP500_COHORT_VERSION,
                "weekly_cohort_key": frozen_sp500_cohort_key("weekly"),
                "monthly_cohort_key": frozen_sp500_cohort_key("monthly"),
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        return None
    if len(rows) != 1:
        raise LookupError("Active v0.22 workspace environment identity is not unique")
    row = rows[0]
    return ActiveV022WorkspaceIdentity(
        asset_registry_release_id=row["asset_registry_release_id"],
        asset_registry_artifact_id=row["asset_registry_artifact_id"],
        asset_registry_version_number=int(row["asset_registry_version_number"]),
        asset_registry_catalog_version=str(row["asset_registry_catalog_version"]),
        asset_registry_as_of_date=row["asset_registry_as_of_date"],
        universe_history_id=row["universe_history_id"],
        risk_dataset_publication_id=row["risk_dataset_publication_id"],
        risk_dataset_artifact_id=row["risk_dataset_artifact_id"],
        risk_dataset_key=str(row["risk_dataset_key"]),
        risk_dataset_version_number=int(row["risk_dataset_version_number"]),
        benchmark_dataset_publication_id=row["benchmark_dataset_publication_id"],
        benchmark_dataset_artifact_id=row["benchmark_dataset_artifact_id"],
        benchmark_dataset_key=str(row["benchmark_dataset_key"]),
        benchmark_dataset_version_number=int(row["benchmark_dataset_version_number"]),
        dataset_gate_assessment_id=row["dataset_gate_assessment_id"],
        dataset_gate_artifact_id=row["dataset_gate_artifact_id"],
    )


def require_active_v022_workspace_identity(
    connection: Connection,
) -> ActiveV022WorkspaceIdentity:
    identity = active_v022_workspace_identity(connection)
    if identity is None:
        raise LookupError(
            "Complete published v0.22 Registry/Gate/weekly/monthly runtime environment "
            "not found"
        )
    return identity


def _resolve_active_market_dataset(
    connection: Connection,
    identity: ActiveV022WorkspaceIdentity,
    legacy_asset_ids: tuple[uuid.UUID, ...],
) -> RowMapping:
    if not legacy_asset_ids or len(legacy_asset_ids) != len(set(legacy_asset_ids)):
        raise ValueError("Workspace assets must contain unique canonical Asset identities")
    rows = (
        connection.execute(
            text(
                """
                SELECT publication.dataset_publication_id,publication.artifact_id,
                       publication.dataset_key,publication.version_number,
                       publication.coverage_start,publication.coverage_end,
                       publication.calendar_version_id,
                       calendar.artifact_id AS calendar_artifact_id
                  FROM data.dataset_publication publication
                  JOIN lineage.artifact artifact
                    ON artifact.artifact_id=publication.artifact_id
                   AND artifact.status='published'
                  LEFT JOIN catalog.calendar_version calendar
                    ON calendar.calendar_version_id=publication.calendar_version_id
                  JOIN data.dataset_coverage coverage
                    ON coverage.dataset_publication_id=publication.dataset_publication_id
                 WHERE publication.dataset_publication_id IN :dataset_ids
                   AND publication.value_kind='daily_bar'
                   AND coverage.asset_id IN :asset_ids
                 GROUP BY publication.dataset_publication_id,publication.artifact_id,
                          publication.dataset_key,publication.version_number,
                          publication.coverage_start,publication.coverage_end,
                          publication.calendar_version_id,calendar.artifact_id
                HAVING count(DISTINCT coverage.asset_id)=:asset_count
                """
            ).bindparams(
                bindparam("dataset_ids", expanding=True),
                bindparam("asset_ids", expanding=True),
            ),
            {
                "dataset_ids": (
                    identity.risk_dataset_publication_id,
                    identity.benchmark_dataset_publication_id,
                ),
                "asset_ids": legacy_asset_ids,
                "asset_count": len(legacy_asset_ids),
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        raise LookupError(
            "No active v0.22 canonical market dataset covers every selected Security"
        )
    if len(rows) != 1:
        raise LookupError("Selected Securities match multiple active v0.22 market datasets")
    return rows[0]


def unconfigured_workspace_context() -> ResolvedWorkspaceContext:
    """Return the honest empty state used by a new Research Round."""

    asset_context = {
        "contract_version": "v0.22.0",
        "selection_kind": "unconfigured",
        "asset_context_key": UNCONFIGURED_ASSET_CONTEXT_KEY,
        "selection_group": "unconfigured",
        "members": [],
    }
    data_binding = {
        "contract_version": "v0.22.0",
        "asset_context_key": UNCONFIGURED_ASSET_CONTEXT_KEY,
        "bindings": [],
    }
    return ResolvedWorkspaceContext(
        asset_context_document=asset_context,
        resolved_data_binding_document=data_binding,
        asset_context_fingerprint=sha256_hexdigest(asset_context),
        resolved_data_binding_fingerprint=sha256_hexdigest(data_binding),
    )


class GraphWorkspaceContextResolver:
    """Resolve readable context choices into exact immutable published identities."""

    def resolve(
        self,
        connection: Connection,
        *,
        asset_context_key: str,
        data_input_keys: tuple[str, ...],
    ) -> ResolvedWorkspaceContext:
        if not asset_context_key.strip():
            raise ValueError("Asset Context key is required")
        if len(data_input_keys) != len(set(data_input_keys)):
            raise ValueError("Data input keys contain duplicates")
        if data_input_keys != (CANONICAL_MARKET_INPUT,):
            raise ValueError(
                "M3 Graph Workspace currently requires canonical_market_bars exactly"
            )
        active = require_active_v022_workspace_identity(connection)
        definition = connection.execute(
            text(
                """
                SELECT release.asset_registry_release_id,release.artifact_id AS release_artifact_id,
                       release.catalog_version,definition.asset_set_definition_id,
                       definition.set_key,definition.set_type
                FROM catalog.asset_registry_release release
                JOIN lineage.artifact release_artifact
                  ON release_artifact.artifact_id=release.artifact_id
                 AND release_artifact.status='published'
                JOIN catalog.asset_set_definition definition
                  ON definition.asset_registry_release_id=release.asset_registry_release_id
                WHERE definition.set_key=:context
                  AND release.asset_registry_release_id=:release
                """
            ),
            {
                "context": asset_context_key,
                "release": active.asset_registry_release_id,
            },
        ).mappings().one_or_none()
        if definition is None:
            raise LookupError(f"Published Asset Context not found: {asset_context_key}")
        dynamic_identity: dict[str, Any] = {}
        if definition["set_type"] == "fixed":
            members = connection.execute(
                text(
                    """
                    SELECT member.ordinal,security.security_id,security.security_key,
                           security.legacy_asset_id,profile.instrument_type,
                           profile.tradability
                      FROM catalog.asset_set_member member
                      JOIN catalog.security security
                        ON security.security_id=member.security_id
                      JOIN catalog.security_profile profile
                        ON profile.asset_registry_release_id=:release
                       AND profile.security_id=security.security_id
                     WHERE member.asset_set_definition_id=:definition
                     ORDER BY member.ordinal
                    """
                ),
                {
                    "release": definition["asset_registry_release_id"],
                    "definition": definition["asset_set_definition_id"],
                },
            ).mappings().all()
            selection_kind = "fixed_asset_set"
        elif definition["set_type"] == "dynamic_methodology":
            snapshot = connection.execute(
                text(
                    """
                    SELECT methodology.universe_methodology_id,
                           methodology.artifact_id AS methodology_artifact_id,
                           history.universe_history_id,
                           history.artifact_id AS history_artifact_id,
                           snapshot.universe_snapshot_id,snapshot.effective_session
                      FROM catalog.universe_methodology methodology
                      JOIN lineage.artifact methodology_artifact
                        ON methodology_artifact.artifact_id=methodology.artifact_id
                       AND methodology_artifact.status='published'
                      JOIN catalog.universe_history history
                        ON history.universe_methodology_id=
                           methodology.universe_methodology_id
                      JOIN lineage.artifact history_artifact
                        ON history_artifact.artifact_id=history.artifact_id
                       AND history_artifact.status='published'
                      JOIN catalog.universe_snapshot snapshot
                        ON snapshot.universe_history_id=history.universe_history_id
                     WHERE methodology.methodology_key=:context
                       AND history.universe_history_id=:history
                     ORDER BY history_artifact.created_at DESC,
                              snapshot.effective_session DESC,
                              snapshot.universe_snapshot_id
                     LIMIT 1
                    """
                ),
                {
                    "context": asset_context_key,
                    "history": active.universe_history_id,
                },
            ).mappings().one_or_none()
            if snapshot is None:
                raise LookupError(
                    f"Published Dynamic Universe Snapshot not found: {asset_context_key}"
                )
            members = connection.execute(
                text(
                    """
                    SELECT member.ordinal,security.security_id,security.security_key,
                           security.legacy_asset_id,profile.instrument_type,
                           profile.tradability
                      FROM catalog.universe_snapshot_member member
                      JOIN catalog.security security
                        ON security.security_id=member.security_id
                      JOIN catalog.security_profile profile
                        ON profile.asset_registry_release_id=:release
                       AND profile.security_id=security.security_id
                     WHERE member.universe_snapshot_id=:snapshot
                     ORDER BY member.ordinal
                    """
                ),
                {
                    "release": definition["asset_registry_release_id"],
                    "snapshot": snapshot["universe_snapshot_id"],
                },
            ).mappings().all()
            selection_kind = "dynamic_universe_snapshot"
            dynamic_identity = {
                "universe_methodology_id": str(snapshot["universe_methodology_id"]),
                "universe_methodology_artifact_id": str(
                    snapshot["methodology_artifact_id"]
                ),
                "universe_history_id": str(snapshot["universe_history_id"]),
                "universe_history_artifact_id": str(snapshot["history_artifact_id"]),
                "universe_snapshot_id": str(snapshot["universe_snapshot_id"]),
                "universe_effective_session": snapshot["effective_session"].isoformat(),
            }
        else:
            raise ValueError("Asset Context type is not executable by v0.22")
        if not members:
            raise LookupError(f"Asset Context has no frozen members: {asset_context_key}")
        if any(row["legacy_asset_id"] is None for row in members):
            raise LookupError("Asset Context contains a Security without canonical Asset identity")
        if any(row["tradability"] == "reference_only" for row in members):
            raise ValueError("Asset Context contains a reference-only Security")

        legacy_asset_ids = tuple(row["legacy_asset_id"] for row in members)
        publication = _resolve_active_market_dataset(connection, active, legacy_asset_ids)

        asset_document: dict[str, Any] = {
            "contract_version": "v0.22.0",
            "selection_kind": selection_kind,
            "asset_context_key": asset_context_key,
            "asset_registry_release_id": str(definition["asset_registry_release_id"]),
            "asset_registry_artifact_id": str(definition["release_artifact_id"]),
            "asset_registry_catalog_version": definition["catalog_version"],
            "asset_set_definition_id": str(definition["asset_set_definition_id"]),
            **dynamic_identity,
            "members": [
                {
                    "ordinal": row["ordinal"],
                    "security_id": str(row["security_id"]),
                    "security_key": row["security_key"],
                    "instrument_type": row["instrument_type"],
                }
                for row in members
            ],
        }
        binding_document: dict[str, Any] = {
            "contract_version": "v0.22.0",
            "bindings": [
                {
                    "input_key": CANONICAL_MARKET_INPUT,
                    "dataset_publication_id": str(publication["dataset_publication_id"]),
                    "dataset_artifact_id": str(publication["artifact_id"]),
                    "dataset_key": publication["dataset_key"],
                    "dataset_version_number": publication["version_number"],
                    "coverage_start": publication["coverage_start"].isoformat(),
                    "coverage_end": publication["coverage_end"].isoformat(),
                    "calendar_version_id": (
                        str(publication["calendar_version_id"])
                        if publication["calendar_version_id"] is not None
                        else None
                    ),
                    "calendar_artifact_id": (
                        str(publication["calendar_artifact_id"])
                        if publication["calendar_artifact_id"] is not None
                        else None
                    ),
                    "security_ids": [str(row["security_id"]) for row in members],
                }
            ],
        }
        return ResolvedWorkspaceContext(
            asset_document,
            binding_document,
            sha256_hexdigest(asset_document),
            sha256_hexdigest(binding_document),
        )

    def resolve_explicit_selection(
        self,
        connection: Connection,
        *,
        asset_registry_release_id: uuid.UUID,
        security_ids: tuple[Any, ...],
        data_input_keys: tuple[str, ...],
        created_by: str,
    ) -> ResolvedWorkspaceContext:
        """Publish and resolve one exact user-selected candidate universe."""
        if data_input_keys != (CANONICAL_MARKET_INPUT,):
            raise ValueError(
                "Explicit Asset Selection currently requires canonical_market_bars exactly"
            )
        active = require_active_v022_workspace_identity(connection)
        if asset_registry_release_id != active.asset_registry_release_id:
            raise LookupError(
                "Graph Draft Asset Registry is not the active v0.22 Registry; "
                "reset or explicitly rebase before changing assets"
            )
        parsed_ids = tuple(
            value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
            for value in security_ids
        )
        publication = ExplicitAssetSelectionService().publish(
            connection,
            asset_registry_release_id=asset_registry_release_id,
            security_ids=parsed_ids,
            created_by=created_by,
        )
        rows = connection.execute(
            text(
                """
                SELECT member.ordinal,member.security_id,security.legacy_asset_id
                  FROM workspace.v022_explicit_asset_selection_member member
                  JOIN catalog.security security ON security.security_id=member.security_id
                 WHERE member.explicit_asset_selection_id=:selection
                 ORDER BY member.ordinal
                """
            ),
            {"selection": publication.selection_id},
        ).mappings().all()
        if len(rows) != len(parsed_ids) or any(
            row["legacy_asset_id"] is None for row in rows
        ):
            raise LookupError("Explicit Asset Selection has incomplete canonical identities")
        legacy_asset_ids = tuple(row["legacy_asset_id"] for row in rows)
        dataset = _resolve_active_market_dataset(connection, active, legacy_asset_ids)
        binding_document: dict[str, Any] = {
            "contract_version": "v0.22.0",
            "bindings": [
                {
                    "input_key": CANONICAL_MARKET_INPUT,
                    "dataset_publication_id": str(dataset["dataset_publication_id"]),
                    "dataset_artifact_id": str(dataset["artifact_id"]),
                    "dataset_key": dataset["dataset_key"],
                    "dataset_version_number": dataset["version_number"],
                    "coverage_start": dataset["coverage_start"].isoformat(),
                    "coverage_end": dataset["coverage_end"].isoformat(),
                    "calendar_version_id": (
                        str(dataset["calendar_version_id"])
                        if dataset["calendar_version_id"] is not None
                        else None
                    ),
                    "calendar_artifact_id": (
                        str(dataset["calendar_artifact_id"])
                        if dataset["calendar_artifact_id"] is not None
                        else None
                    ),
                    "security_ids": [str(row["security_id"]) for row in rows],
                }
            ],
        }
        asset_document = publication.asset_context_document
        return ResolvedWorkspaceContext(
            asset_document,
            binding_document,
            sha256_hexdigest(asset_document),
            sha256_hexdigest(binding_document),
        )
