from __future__ import annotations

import csv
import io
import json
import uuid
import zlib
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.canonical import (
    FACTOR_QUANTUM,
    PRICE_QUANTUM,
    CanonicalAction,
    CanonicalBar,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput

ObservationPriceSemantics = Literal["raw_ohlcv_and_actions"]
ReconstructionPolicy = Literal[
    "raw_ohlcv_actions_backward_total_return_v1",
    "split_normalized_ohlcv_dividends_backward_total_return_v2",
]
GapType = Literal[
    "missing_bar",
    "provider_conflict",
    "corporate_action_conflict",
    "ticker_boundary",
    "abnormal_last_day",
    "uniform_exclusion",
]
ResolutionKind = Literal[
    "replace_with_alternate", "retain_primary", "exclude_security", "unresolved"
]
EvidenceRole = Literal[
    "review_note",
    "provider_comparison",
    "exchange_notice",
    "corporate_action_terms",
    "other_public_record",
]

_OBSERVATION_CONTRACT = "v0.22.alternate_market_observation.v1"
_RESOLUTION_CONTRACT = "v0.22.market_gap_resolution.v1"
_PLAN_CONTRACT = "v0.22.market_reconciliation_plan.v1"
_BINDING_CONTRACT = "v0.22.reconciled_market_dataset_binding.v1"
V1_RECONSTRUCTION_POLICY: ReconstructionPolicy = (
    "raw_ohlcv_actions_backward_total_return_v1"
)
V2_RECONSTRUCTION_POLICY: ReconstructionPolicy = (
    "split_normalized_ohlcv_dividends_backward_total_return_v2"
)
DEFAULT_RECONSTRUCTION_POLICY = V2_RECONSTRUCTION_POLICY
_SUPPORTED_RECONSTRUCTION_POLICIES = frozenset(
    {V1_RECONSTRUCTION_POLICY, V2_RECONSTRUCTION_POLICY}
)
_V1_PRICE_SEMANTICS = "historical_constituent_pit__frozen_reconciled_retrospective_prices"
_V2_PRICE_SEMANTICS = (
    "historical_constituent_pit__frozen_reconciled_retrospective_"
    "split_normalized_total_return_prices"
)


def _price_semantics(reconstruction_policy: ReconstructionPolicy) -> str:
    if reconstruction_policy == V1_RECONSTRUCTION_POLICY:
        return _V1_PRICE_SEMANTICS
    if reconstruction_policy == V2_RECONSTRUCTION_POLICY:
        return _V2_PRICE_SEMANTICS
    raise ValueError("Unsupported market reconstruction policy")


@dataclass(frozen=True, slots=True)
class AlternateObservationSetSpec:
    source_snapshot_security_subject_id: uuid.UUID
    observation_key: str
    version_number: int
    created_by: str

    def __post_init__(self) -> None:
        _require_text("observation_key", self.observation_key)
        _require_text("created_by", self.created_by)
        if self.version_number < 1:
            raise ValueError("Alternate Observation version_number must be positive")


@dataclass(frozen=True, slots=True)
class AlternateObservationSetPublication:
    alternate_observation_set_id: uuid.UUID
    artifact_id: uuid.UUID
    observation_fingerprint: str
    bar_count: int
    action_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class GapResolutionEvidenceRef:
    artifact_id: uuid.UUID
    role: EvidenceRole


@dataclass(frozen=True, slots=True)
class MarketGapResolutionSpec:
    primary_dataset_publication_id: uuid.UUID
    security_id: uuid.UUID
    gap_key: str
    version_number: int
    gap_type: GapType
    gap_start: date
    gap_end: date
    resolution_kind: ResolutionKind
    evidence: tuple[GapResolutionEvidenceRef, ...]
    created_by: str
    alternate_observation_set_id: uuid.UUID | None = None
    supersedes_market_gap_resolution_id: uuid.UUID | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("gap_key", self.gap_key)
        _require_text("created_by", self.created_by)
        if self.version_number < 1:
            raise ValueError("Gap Resolution version_number must be positive")
        if self.gap_start > self.gap_end:
            raise ValueError("Gap Resolution interval is reversed")
        if not self.evidence:
            raise ValueError("Gap Resolution requires review evidence")
        if len({item.artifact_id for item in self.evidence}) != len(self.evidence):
            raise ValueError("Gap Resolution evidence must be unique")
        if (self.resolution_kind == "replace_with_alternate") != (
            self.alternate_observation_set_id is not None
        ):
            raise ValueError("Only alternate replacement requires an Observation Set")
        if self.version_number == 1 and self.supersedes_market_gap_resolution_id is not None:
            raise ValueError("First Gap Resolution version cannot supersede another")
        if self.version_number > 1 and self.supersedes_market_gap_resolution_id is None:
            raise ValueError("Later Gap Resolution versions require exact supersession")
        _json_object(self.details, "details")


@dataclass(frozen=True, slots=True)
class MarketGapResolutionPublication:
    market_gap_resolution_id: uuid.UUID
    artifact_id: uuid.UUID
    resolution_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class MarketReconciliationSpec:
    primary_dataset_publication_id: uuid.UUID
    resolution_ids: tuple[uuid.UUID, ...]
    cleaning_version_id: uuid.UUID
    calendar_version_id: uuid.UUID
    output_dataset_key: str
    output_version_number: int
    created_by: str
    reconstruction_policy: ReconstructionPolicy = DEFAULT_RECONSTRUCTION_POLICY

    def __post_init__(self) -> None:
        _require_text("output_dataset_key", self.output_dataset_key)
        _require_text("created_by", self.created_by)
        if self.output_version_number < 1:
            raise ValueError("Reconciled Dataset version_number must be positive")
        if not self.resolution_ids:
            raise ValueError("Reconciliation requires at least one reviewed Resolution")
        if len(set(self.resolution_ids)) != len(self.resolution_ids):
            raise ValueError("Reconciliation Resolution identities must be unique")
        if self.reconstruction_policy not in _SUPPORTED_RECONSTRUCTION_POLICIES:
            raise ValueError("Unsupported market reconstruction policy")


@dataclass(frozen=True, slots=True)
class MarketReconciliationPublication:
    market_reconciliation_plan_id: uuid.UUID
    plan_artifact_id: uuid.UUID
    dataset_publication_id: uuid.UUID
    dataset_artifact_id: uuid.UUID
    plan_fingerprint: str
    binding_fingerprint: str
    replaced_bar_count: int
    excluded_security_count: int
    reused: bool


@dataclass(frozen=True, slots=True)
class _RawBar:
    security_id: uuid.UUID
    asset_id: uuid.UUID
    session_date: date
    open_raw: Decimal
    high_raw: Decimal
    low_raw: Decimal
    close_raw: Decimal
    volume_raw: int
    provider_adjusted_close: Decimal | None = None


@dataclass(frozen=True, slots=True)
class _ObservationRows:
    bars: tuple[_RawBar, ...]
    actions: tuple[CanonicalAction, ...]


@dataclass(frozen=True, slots=True)
class _ResolutionRow:
    resolution_id: uuid.UUID
    artifact_id: uuid.UUID
    fingerprint: str
    security_id: uuid.UUID
    gap_start: date
    gap_end: date
    resolution_kind: ResolutionKind
    alternate_observation_set_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _ReconciliationInputs:
    primary: RowMapping
    cleaning: RowMapping
    calendar: RowMapping
    resolutions: tuple[_ResolutionRow, ...]


@dataclass(frozen=True, slots=True)
class _MaterializedDataset:
    bars: tuple[CanonicalBar, ...]
    actions: tuple[CanonicalAction, ...]
    asset_by_security: dict[uuid.UUID, uuid.UUID]
    source_snapshot_ids: tuple[uuid.UUID, ...]
    replaced_bar_count: int
    excluded_security_ids: frozenset[uuid.UUID]


class AlternateObservationService:
    """Freeze one provider response as raw evidence; adjusted close is never canonical input."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: AlternateObservationSetSpec) -> AlternateObservationSetPublication:
        with self._engine.connect() as connection:
            source = _source_snapshot_subject(connection, spec.source_snapshot_security_subject_id)
        rows = _parse_alternate_csv(
            zlib.decompress(cast(bytes, source["compressed_payload"])),
            cast(uuid.UUID, source["security_id"]),
            cast(uuid.UUID, source["legacy_asset_id"]),
        )
        document = {
            "contract_version": _OBSERVATION_CONTRACT,
            "source_snapshot_id": str(source["source_snapshot_id"]),
            "source_snapshot_artifact_id": str(source["artifact_id"]),
            "source_snapshot_security_subject_id": str(spec.source_snapshot_security_subject_id),
            "security_id": str(source["security_id"]),
            "observation_key": spec.observation_key,
            "version_number": spec.version_number,
            "provider_key": str(source["provider_scope"]),
            "coverage_start": rows.bars[0].session_date.isoformat(),
            "coverage_end": rows.bars[-1].session_date.isoformat(),
            "bar_count": len(rows.bars),
            "action_count": len(rows.actions),
            "price_input_semantics": "raw_ohlcv_and_actions",
            "bars_hash": sha256_hexdigest([asdict(item) for item in rows.bars]),
            "actions_hash": sha256_hexdigest([asdict(item) for item in rows.actions]),
        }
        fingerprint = sha256_hexdigest(document)
        observation_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:alternate-observation:{fingerprint}"
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_alternate_observation_set (
                      alternate_observation_set_id,artifact_id,source_snapshot_id,
                      source_snapshot_artifact_id,source_snapshot_security_subject_id,
                      security_id,observation_key,version_number,provider_key,
                      coverage_start,coverage_end,bar_count,action_count,
                      observation_document,observation_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:snapshot,:snapshot_artifact,:subject,:security,
                      :key,:version,:provider,:start,:end,:bars,:actions,
                      CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": observation_id,
                    "artifact": artifact_id,
                    "snapshot": source["source_snapshot_id"],
                    "snapshot_artifact": source["artifact_id"],
                    "subject": spec.source_snapshot_security_subject_id,
                    "security": source["security_id"],
                    "key": spec.observation_key,
                    "version": spec.version_number,
                    "provider": source["provider_scope"],
                    "start": rows.bars[0].session_date,
                    "end": rows.bars[-1].session_date,
                    "bars": len(rows.bars),
                    "actions": len(rows.actions),
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by,
                },
            )
            _write_alternate_rows(connection, observation_id, rows)

        result = self._artifacts.publish(
            artifact_type="v022_alternate_observation_set",
            artifact_key=f"v022_alternate_observation__{spec.observation_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=(DependencyInput(source["artifact_id"], "source_snapshot", 0),),
            reason=f"publish alternate market observation {spec.observation_key}",
            draft_writer=writer,
        )
        return AlternateObservationSetPublication(
            observation_id,
            result.artifact_id,
            fingerprint,
            len(rows.bars),
            len(rows.actions),
            result.reused,
        )


class MarketGapResolutionService:
    """Publish a human-reviewed conclusion without mutating either provider's facts."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, spec: MarketGapResolutionSpec) -> MarketGapResolutionPublication:
        with self._engine.connect() as connection:
            primary = _published_dataset(connection, spec.primary_dataset_publication_id)
            alternate = (
                _published_alternate(connection, spec.alternate_observation_set_id)
                if spec.alternate_observation_set_id is not None
                else None
            )
            _published_evidence(connection, tuple(item.artifact_id for item in spec.evidence))
            _validate_resolution_supersession(connection, spec)
        if alternate is not None:
            if alternate["security_id"] != spec.security_id:
                raise ValueError("Alternate Observation belongs to another Security")
            if (
                alternate["coverage_start"] > spec.gap_end
                or alternate["coverage_end"] < spec.gap_start
            ):
                raise ValueError("Alternate Observation does not overlap the reviewed interval")
        document = {
            "contract_version": _RESOLUTION_CONTRACT,
            "primary_dataset_publication_id": str(spec.primary_dataset_publication_id),
            "primary_dataset_artifact_id": str(primary["artifact_id"]),
            "security_id": str(spec.security_id),
            "gap_key": spec.gap_key,
            "version_number": spec.version_number,
            "gap_type": spec.gap_type,
            "gap_start": spec.gap_start.isoformat(),
            "gap_end": spec.gap_end.isoformat(),
            "resolution_kind": spec.resolution_kind,
            "alternate_observation_set_id": (
                str(spec.alternate_observation_set_id)
                if spec.alternate_observation_set_id is not None
                else None
            ),
            "evidence_count": len(spec.evidence),
            "evidence": [
                {"ordinal": ordinal, "artifact_id": str(item.artifact_id), "role": item.role}
                for ordinal, item in enumerate(spec.evidence)
            ],
            "supersedes_market_gap_resolution_id": (
                str(spec.supersedes_market_gap_resolution_id)
                if spec.supersedes_market_gap_resolution_id is not None
                else None
            ),
            "details": spec.details,
        }
        fingerprint = sha256_hexdigest(document)
        resolution_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:market-gap-resolution:{fingerprint}"
        )
        dependencies = [DependencyInput(primary["artifact_id"], "primary_dataset", 0)]
        offset = 1
        if alternate is not None:
            dependencies.append(
                DependencyInput(alternate["artifact_id"], "alternate_observation", 1)
            )
            offset = 2
        dependencies.extend(
            DependencyInput(item.artifact_id, "review_evidence", offset + ordinal)
            for ordinal, item in enumerate(spec.evidence)
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_market_gap_resolution (
                      market_gap_resolution_id,artifact_id,primary_dataset_publication_id,
                      primary_dataset_artifact_id,security_id,gap_key,version_number,
                      gap_type,gap_start,gap_end,resolution_kind,
                      alternate_observation_set_id,alternate_observation_artifact_id,
                      evidence_count,supersedes_market_gap_resolution_id,
                      resolution_document,resolution_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:primary,:primary_artifact,:security,:key,:version,
                      :gap_type,:start,:end,:kind,:alternate,:alternate_artifact,
                      :evidence_count,:supersedes,CAST(:document AS jsonb),:fingerprint,
                      :created_by
                    )
                    """
                ),
                {
                    "id": resolution_id,
                    "artifact": artifact_id,
                    "primary": spec.primary_dataset_publication_id,
                    "primary_artifact": primary["artifact_id"],
                    "security": spec.security_id,
                    "key": spec.gap_key,
                    "version": spec.version_number,
                    "gap_type": spec.gap_type,
                    "start": spec.gap_start,
                    "end": spec.gap_end,
                    "kind": spec.resolution_kind,
                    "alternate": spec.alternate_observation_set_id,
                    "alternate_artifact": alternate["artifact_id"] if alternate else None,
                    "evidence_count": len(spec.evidence),
                    "supersedes": spec.supersedes_market_gap_resolution_id,
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": spec.created_by,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_market_gap_resolution_evidence (
                      market_gap_resolution_id,ordinal,evidence_artifact_id,evidence_role
                    ) VALUES (:resolution,:ordinal,:artifact,:role)
                    """
                ),
                [
                    {
                        "resolution": resolution_id,
                        "ordinal": ordinal,
                        "artifact": item.artifact_id,
                        "role": item.role,
                    }
                    for ordinal, item in enumerate(spec.evidence)
                ],
            )

        result = self._artifacts.publish(
            artifact_type="v022_market_gap_resolution",
            artifact_key=f"v022_market_gap_resolution__{spec.gap_key}",
            version_number=spec.version_number,
            semantic_payload=document,
            content_payload=document,
            dependencies=tuple(dependencies),
            reason=f"publish reviewed market gap resolution {spec.gap_key}",
            draft_writer=writer,
        )
        return MarketGapResolutionPublication(
            resolution_id, result.artifact_id, fingerprint, result.reused
        )


class MarketReconciliationService:
    """Apply exact reviewed intervals and publish a new, replayable canonical Dataset."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def reconcile(self, spec: MarketReconciliationSpec) -> MarketReconciliationPublication:
        inputs = self._load_inputs(spec)
        price_semantics = _price_semantics(spec.reconstruction_policy)
        plan_document = _plan_document(spec, inputs)
        plan_fingerprint = sha256_hexdigest(plan_document)
        plan_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:market-reconciliation-plan:{plan_fingerprint}"
        )
        plan_dependencies = (
            DependencyInput(inputs.primary["artifact_id"], "primary_dataset", 0),
            DependencyInput(inputs.cleaning["artifact_id"], "cleaning_version", 1),
            DependencyInput(inputs.calendar["artifact_id"], "calendar_version", 2),
        ) + tuple(
            DependencyInput(item.artifact_id, "gap_resolution", 3 + ordinal)
            for ordinal, item in enumerate(inputs.resolutions)
        )

        def plan_writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_market_reconciliation_plan (
                      market_reconciliation_plan_id,artifact_id,
                      primary_dataset_publication_id,primary_dataset_artifact_id,
                      cleaning_version_id,cleaning_artifact_id,calendar_version_id,
                      calendar_artifact_id,output_dataset_key,output_version_number,
                      reconstruction_policy,resolution_count,excluded_security_count,
                      plan_document,plan_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:primary,:primary_artifact,:cleaning,
                      :cleaning_artifact,:calendar,:calendar_artifact,:output_key,
                      :output_version,:policy,:resolution_count,:excluded_count,
                      CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": plan_id,
                    "artifact": artifact_id,
                    "primary": spec.primary_dataset_publication_id,
                    "primary_artifact": inputs.primary["artifact_id"],
                    "cleaning": spec.cleaning_version_id,
                    "cleaning_artifact": inputs.cleaning["artifact_id"],
                    "calendar": spec.calendar_version_id,
                    "calendar_artifact": inputs.calendar["artifact_id"],
                    "output_key": spec.output_dataset_key,
                    "output_version": spec.output_version_number,
                    "policy": spec.reconstruction_policy,
                    "resolution_count": len(inputs.resolutions),
                    "excluded_count": len(
                        {
                            item.security_id
                            for item in inputs.resolutions
                            if item.resolution_kind == "exclude_security"
                        }
                    ),
                    "document": json.dumps(plan_document, sort_keys=True),
                    "fingerprint": plan_fingerprint,
                    "created_by": spec.created_by,
                },
            )
            connection.execute(
                text(
                    """
                    INSERT INTO data.v022_market_reconciliation_plan_resolution (
                      market_reconciliation_plan_id,ordinal,market_gap_resolution_id,
                      resolution_artifact_id
                    ) VALUES (:plan,:ordinal,:resolution,:artifact)
                    """
                ),
                [
                    {
                        "plan": plan_id,
                        "ordinal": ordinal,
                        "resolution": item.resolution_id,
                        "artifact": item.artifact_id,
                    }
                    for ordinal, item in enumerate(inputs.resolutions)
                ],
            )

        plan = self._artifacts.publish(
            artifact_type="v022_market_reconciliation_plan",
            artifact_key=f"v022_market_reconciliation_plan__{spec.output_dataset_key}",
            version_number=spec.output_version_number,
            semantic_payload=plan_document,
            content_payload=plan_document,
            dependencies=plan_dependencies,
            reason=f"publish market reconciliation plan {spec.output_dataset_key}",
            draft_writer=plan_writer,
        )
        materialized = self._materialize(inputs, spec.reconstruction_policy)
        binding_document = {
            "contract_version": _BINDING_CONTRACT,
            "market_reconciliation_plan_id": str(plan_id),
            "plan_artifact_id": str(plan.artifact_id),
            "primary_dataset_publication_id": str(spec.primary_dataset_publication_id),
            "output_dataset_key": spec.output_dataset_key,
            "output_version_number": spec.output_version_number,
            "price_semantics": price_semantics,
            "historical_price_pit_claimed": False,
            "reconstruction_policy": spec.reconstruction_policy,
            "replaced_bar_count": materialized.replaced_bar_count,
            "excluded_security_count": len(materialized.excluded_security_ids),
        }
        binding_fingerprint = sha256_hexdigest(binding_document)
        content = {
            **binding_document,
            "bars_hash": sha256_hexdigest([asdict(item) for item in materialized.bars]),
            "actions_hash": sha256_hexdigest([asdict(item) for item in materialized.actions]),
            "bar_count": len(materialized.bars),
            "action_count": len(materialized.actions),
        }
        dataset_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird:v0.22:reconciled-market-dataset:{spec.output_dataset_key}:{spec.output_version_number}",
        )

        def dataset_writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            _write_reconciled_dataset(
                connection,
                dataset_id,
                artifact_id,
                plan_id,
                plan.artifact_id,
                spec,
                inputs,
                materialized,
                binding_document,
                binding_fingerprint,
            )

        dataset = self._artifacts.publish(
            artifact_type="dataset_publication",
            artifact_key=spec.output_dataset_key,
            version_number=spec.output_version_number,
            semantic_payload=binding_document,
            content_payload=content,
            dependencies=(DependencyInput(plan.artifact_id, "reconciliation_plan", 0),),
            reason=f"publish reconciled market Dataset {spec.output_dataset_key}",
            draft_writer=dataset_writer,
        )
        return MarketReconciliationPublication(
            plan_id,
            plan.artifact_id,
            dataset_id,
            dataset.artifact_id,
            plan_fingerprint,
            binding_fingerprint,
            materialized.replaced_bar_count,
            len(materialized.excluded_security_ids),
            plan.reused and dataset.reused,
        )

    def _load_inputs(self, spec: MarketReconciliationSpec) -> _ReconciliationInputs:
        with self._engine.connect() as connection:
            primary = _published_dataset(connection, spec.primary_dataset_publication_id)
            cleaning = _published_cleaning(connection, spec.cleaning_version_id)
            calendar = _published_calendar(connection, spec.calendar_version_id)
            rows = (
                connection.execute(
                    text(
                        """
                    SELECT resolution.market_gap_resolution_id,resolution.artifact_id,
                           resolution.resolution_fingerprint,resolution.security_id,
                           resolution.gap_start,resolution.gap_end,
                           resolution.resolution_kind,
                           resolution.alternate_observation_set_id,artifact.status
                      FROM data.v022_market_gap_resolution resolution
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=resolution.artifact_id
                     WHERE resolution.market_gap_resolution_id=ANY(:ids)
                    """
                    ),
                    {"ids": list(spec.resolution_ids)},
                )
                .mappings()
                .all()
            )
        by_id = {cast(uuid.UUID, row["market_gap_resolution_id"]): row for row in rows}
        if set(by_id) != set(spec.resolution_ids):
            raise LookupError("One or more Market Gap Resolutions were not found")
        resolutions: list[_ResolutionRow] = []
        for resolution_id in spec.resolution_ids:
            row = by_id[resolution_id]
            if row["status"] != "published":
                raise ValueError("Reconciliation requires published Gap Resolutions")
            resolutions.append(
                _ResolutionRow(
                    resolution_id,
                    cast(uuid.UUID, row["artifact_id"]),
                    str(row["resolution_fingerprint"]),
                    cast(uuid.UUID, row["security_id"]),
                    cast(date, row["gap_start"]),
                    cast(date, row["gap_end"]),
                    cast(ResolutionKind, row["resolution_kind"]),
                    cast(uuid.UUID | None, row["alternate_observation_set_id"]),
                )
            )
        _validate_reconciliation_resolutions(
            spec.primary_dataset_publication_id, primary, resolutions
        )
        return _ReconciliationInputs(primary, cleaning, calendar, tuple(resolutions))

    def _materialize(
        self,
        inputs: _ReconciliationInputs,
        reconstruction_policy: ReconstructionPolicy,
    ) -> _MaterializedDataset:
        with self._engine.connect() as connection:
            raw_bars, actions, asset_by_security = _primary_rows(
                connection, cast(uuid.UUID, inputs.primary["dataset_publication_id"])
            )
            source_snapshot_ids = _source_snapshots(
                connection, cast(uuid.UUID, inputs.primary["dataset_publication_id"])
            )
            alternate_rows = {
                item.resolution_id: _alternate_rows(
                    connection, item.alternate_observation_set_id
                )
                for item in inputs.resolutions
                if item.alternate_observation_set_id is not None
            }
            alternate_snapshots = tuple(
                cast(uuid.UUID, row)
                for row in connection.execute(
                    text(
                        """
                        SELECT DISTINCT item.source_snapshot_id
                          FROM data.v022_alternate_observation_set item
                         WHERE item.alternate_observation_set_id=ANY(:ids)
                         ORDER BY item.source_snapshot_id
                        """
                    ),
                    {
                        "ids": [
                            item.alternate_observation_set_id
                            for item in inputs.resolutions
                            if item.alternate_observation_set_id is not None
                        ]
                    },
                ).scalars()
            )
        bars_by_key = {(item.security_id, item.session_date): item for item in raw_bars}
        actions_by_key = {(uuid.UUID(item.symbol), item.effective_date): item for item in actions}
        excluded: set[uuid.UUID] = set()
        replaced = 0
        for resolution in inputs.resolutions:
            if resolution.resolution_kind == "unresolved":
                raise ValueError("Unresolved gaps cannot produce a reconciled Dataset")
            if resolution.resolution_kind == "retain_primary":
                continue
            if resolution.resolution_kind == "exclude_security":
                excluded.add(resolution.security_id)
                continue
            alternate = alternate_rows[resolution.resolution_id]
            replacement_bars = tuple(
                item
                for item in alternate.bars
                if resolution.gap_start <= item.session_date <= resolution.gap_end
            )
            if not replacement_bars:
                raise ValueError("Alternate replacement contains no bar in the reviewed interval")
            for key in tuple(bars_by_key):
                if (
                    key[0] == resolution.security_id
                    and resolution.gap_start <= key[1] <= resolution.gap_end
                ):
                    del bars_by_key[key]
            for item in replacement_bars:
                bars_by_key[(item.security_id, item.session_date)] = item
            replaced += len(replacement_bars)
            for key in tuple(actions_by_key):
                if (
                    key[0] == resolution.security_id
                    and resolution.gap_start <= key[1] <= resolution.gap_end
                ):
                    del actions_by_key[key]
            for action in alternate.actions:
                if resolution.gap_start <= action.effective_date <= resolution.gap_end:
                    actions_by_key[(resolution.security_id, action.effective_date)] = action
        merged_raw = tuple(
            item
            for key, item in sorted(
                bars_by_key.items(), key=lambda value: (str(value[0][0]), value[0][1])
            )
            if key[0] not in excluded
        )
        merged_actions = tuple(
            item
            for key, item in sorted(
                actions_by_key.items(), key=lambda value: (str(value[0][0]), value[0][1])
            )
            if key[0] not in excluded
        )
        if not merged_raw:
            raise ValueError("Reconciliation cannot exclude every market observation")
        rebuilt = rebuild_back_adjusted_bars(
            merged_raw,
            merged_actions,
            reconstruction_policy=reconstruction_policy,
        )
        return _MaterializedDataset(
            rebuilt,
            merged_actions,
            asset_by_security,
            _deduplicate(source_snapshot_ids + alternate_snapshots),
            replaced,
            frozenset(excluded),
        )


def rebuild_back_adjusted_bars(
    bars: tuple[_RawBar, ...],
    actions: tuple[CanonicalAction, ...],
    *,
    reconstruction_policy: ReconstructionPolicy = DEFAULT_RECONSTRUCTION_POLICY,
) -> tuple[CanonicalBar, ...]:
    """Rebuild total return without trusting provider adjusted-close observations.

    The v2 input contract treats provider OHLC as already split-normalized and treats
    cash dividends as being on that same per-share basis. Split ratios remain frozen
    corporate-action evidence, but applying them to the price transition again would
    double-adjust the series. The legacy v1 branch remains explicit for exact replay of
    already-published v1 plans.
    """

    if reconstruction_policy not in _SUPPORTED_RECONSTRUCTION_POLICIES:
        raise ValueError("Unsupported market reconstruction policy")

    actions_by_key = {(uuid.UUID(item.symbol), item.effective_date): item for item in actions}
    grouped: dict[uuid.UUID, list[_RawBar]] = defaultdict(list)
    for item in bars:
        grouped[item.security_id].append(item)
    result: list[CanonicalBar] = []
    for security_id, security_bars in sorted(grouped.items(), key=lambda item: str(item[0])):
        security_bars.sort(key=lambda item: item.session_date)
        adjusted_close: dict[date, Decimal] = {
            security_bars[-1].session_date: security_bars[-1].close_raw.quantize(PRICE_QUANTUM)
        }
        for ordinal in range(len(security_bars) - 1, 0, -1):
            current = security_bars[ordinal]
            prior = security_bars[ordinal - 1]
            action = actions_by_key.get((security_id, current.session_date))
            dividend = action.cash_dividend if action is not None else Decimal(0)
            if reconstruction_policy == V1_RECONSTRUCTION_POLICY:
                split = (
                    action.split_ratio
                    if action is not None and action.split_ratio > 0
                    else Decimal(1)
                )
                transition_value = current.close_raw * split + dividend
            else:
                transition_value = current.close_raw + dividend
            if transition_value <= 0:
                raise ValueError("Corporate action reconstruction produced nonpositive value")
            gross_return = transition_value / prior.close_raw
            adjusted_close[prior.session_date] = (
                adjusted_close[current.session_date] / gross_return
            ).quantize(PRICE_QUANTUM)
        for item in security_bars:
            close_adj = adjusted_close[item.session_date]
            factor = (close_adj / item.close_raw).quantize(FACTOR_QUANTUM)
            if factor <= 0:
                raise ValueError("Reconstructed adjustment factor must be positive")
            result.append(
                CanonicalBar(
                    str(security_id),
                    item.session_date,
                    item.open_raw,
                    item.high_raw,
                    item.low_raw,
                    item.close_raw,
                    close_adj,
                    (item.open_raw * factor).quantize(PRICE_QUANTUM),
                    (item.high_raw * factor).quantize(PRICE_QUANTUM),
                    (item.low_raw * factor).quantize(PRICE_QUANTUM),
                    close_adj,
                    factor,
                    item.volume_raw,
                )
            )
    return tuple(sorted(result, key=lambda item: (item.symbol, item.session_date)))


def _source_snapshot_subject(connection: Connection, subject_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
            SELECT subject.source_snapshot_security_subject_id,subject.source_snapshot_id,
                   subject.security_id,subject.provider_scope,subject.fetch_status,
                   snapshot.artifact_id,snapshot.compressed_payload,artifact.status,
                   security.legacy_asset_id
              FROM data.source_snapshot_security_subject subject
              JOIN data.source_snapshot snapshot
                ON snapshot.source_snapshot_id=subject.source_snapshot_id
              JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id
              JOIN catalog.security security ON security.security_id=subject.security_id
             WHERE subject.source_snapshot_security_subject_id=:subject
            """
            ),
            {"subject": subject_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published" or row["fetch_status"] != "fetched":
        raise LookupError("Published fetched Source Snapshot subject not found")
    if row["legacy_asset_id"] is None:
        raise ValueError("Alternate Observation Security lacks the canonical Asset bridge")
    return row


def _parse_alternate_csv(
    payload: bytes, security_id: uuid.UUID, asset_id: uuid.UUID
) -> _ObservationRows:
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    if reader.fieldnames is None:
        raise ValueError("Alternate observation CSV header is absent")
    bars: list[_RawBar] = []
    actions: list[CanonicalAction] = []
    seen: set[date] = set()
    for row_number, row in enumerate(reader, start=2):
        try:
            session = date.fromisoformat(_csv_value(row, "session_date", "Date", "date"))
            if session in seen:
                raise ValueError("duplicate session date")
            seen.add(session)
            open_raw = _csv_decimal(row, "Open", "open").quantize(PRICE_QUANTUM)
            high_raw = _csv_decimal(row, "High", "high").quantize(PRICE_QUANTUM)
            low_raw = _csv_decimal(row, "Low", "low").quantize(PRICE_QUANTUM)
            close_raw = _csv_decimal(row, "Close", "close").quantize(PRICE_QUANTUM)
            volume_value = _csv_decimal(row, "Volume", "volume")
            if volume_value != volume_value.to_integral_value():
                raise ValueError("volume must be an integer")
            volume = int(volume_value)
            if min(open_raw, high_raw, low_raw, close_raw) <= 0:
                raise ValueError("raw prices must be positive")
            if high_raw < max(open_raw, close_raw) or low_raw > min(open_raw, close_raw):
                raise ValueError("OHLC geometry is invalid")
            if volume < 0:
                raise ValueError("volume cannot be negative")
            adjusted_text = _csv_optional_value(row, "Adj Close", "adj_close", "adjusted_close")
            adjusted = Decimal(adjusted_text).quantize(PRICE_QUANTUM) if adjusted_text else None
            if adjusted is not None and adjusted <= 0:
                raise ValueError("provider adjusted close must be positive")
            dividend = _csv_optional_decimal(row, "Dividends", "dividends").quantize(PRICE_QUANTUM)
            split = _csv_optional_decimal(row, "Stock Splits", "stock_splits").quantize(
                PRICE_QUANTUM
            )
            if dividend < 0 or split < 0:
                raise ValueError("corporate actions cannot be negative")
            bars.append(
                _RawBar(
                    security_id,
                    asset_id,
                    session,
                    open_raw,
                    high_raw,
                    low_raw,
                    close_raw,
                    volume,
                    adjusted,
                )
            )
            if dividend > 0 or split > 0:
                actions.append(CanonicalAction(str(security_id), session, dividend, split))
        except (InvalidOperation, KeyError, ValueError) as error:
            raise ValueError(f"Invalid alternate observation row {row_number}: {error}") from error
    if not bars:
        raise ValueError("Alternate Observation requires at least one market bar")
    return _ObservationRows(
        tuple(sorted(bars, key=lambda item: item.session_date)),
        tuple(sorted(actions, key=lambda item: item.effective_date)),
    )


def _write_alternate_rows(
    connection: Connection, observation_id: uuid.UUID, rows: _ObservationRows
) -> None:
    connection.execute(
        text(
            """
            INSERT INTO data.v022_alternate_market_bar (
              alternate_observation_set_id,session_date,open_raw,high_raw,low_raw,
              close_raw,volume_raw,provider_adjusted_close
            ) VALUES (:set,:session,:open,:high,:low,:close,:volume,:adjusted)
            """
        ),
        [
            {
                "set": observation_id,
                "session": item.session_date,
                "open": item.open_raw,
                "high": item.high_raw,
                "low": item.low_raw,
                "close": item.close_raw,
                "volume": item.volume_raw,
                "adjusted": item.provider_adjusted_close,
            }
            for item in rows.bars
        ],
    )
    if rows.actions:
        connection.execute(
            text(
                """
                INSERT INTO data.v022_alternate_corporate_action (
                  alternate_observation_set_id,effective_date,cash_dividend,split_ratio
                ) VALUES (:set,:date,:dividend,:split)
                """
            ),
            [
                {
                    "set": observation_id,
                    "date": item.effective_date,
                    "dividend": item.cash_dividend,
                    "split": item.split_ratio,
                }
                for item in rows.actions
            ],
        )


def _published_dataset(connection: Connection, dataset_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
            SELECT publication.*,artifact.status
              FROM data.dataset_publication publication
              JOIN lineage.artifact artifact ON artifact.artifact_id=publication.artifact_id
             WHERE publication.dataset_publication_id=:dataset
            """
            ),
            {"dataset": dataset_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published" or row["value_kind"] != "daily_bar":
        raise LookupError("Published canonical daily-bar Dataset not found")
    return row


def _published_alternate(connection: Connection, observation_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
            SELECT item.*,artifact.status
              FROM data.v022_alternate_observation_set item
              JOIN lineage.artifact artifact ON artifact.artifact_id=item.artifact_id
             WHERE item.alternate_observation_set_id=:id
            """
            ),
            {"id": observation_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        raise LookupError("Published Alternate Observation Set not found")
    return row


def _published_evidence(connection: Connection, artifact_ids: tuple[uuid.UUID, ...]) -> None:
    count = connection.execute(
        text(
            """
            SELECT count(*) FROM lineage.artifact
             WHERE artifact_id=ANY(:ids) AND status='published'
            """
        ),
        {"ids": list(artifact_ids)},
    ).scalar_one()
    if count != len(artifact_ids):
        raise LookupError("Gap Resolution evidence must be published")


def _validate_resolution_supersession(
    connection: Connection, spec: MarketGapResolutionSpec
) -> None:
    if spec.supersedes_market_gap_resolution_id is None:
        return
    row = (
        connection.execute(
            text(
                """
            SELECT resolution.primary_dataset_publication_id,resolution.gap_key,
                   resolution.version_number,artifact.status
              FROM data.v022_market_gap_resolution resolution
              JOIN lineage.artifact artifact ON artifact.artifact_id=resolution.artifact_id
             WHERE resolution.market_gap_resolution_id=:id
            """
            ),
            {"id": spec.supersedes_market_gap_resolution_id},
        )
        .mappings()
        .one_or_none()
    )
    if (
        row is None
        or row["status"] != "published"
        or row["primary_dataset_publication_id"] != spec.primary_dataset_publication_id
        or row["gap_key"] != spec.gap_key
        or row["version_number"] != spec.version_number - 1
    ):
        raise ValueError("Gap Resolution supersession is not exact")


def _published_cleaning(connection: Connection, version_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
            SELECT version.cleaning_version_id,version.artifact_id,artifact.status
              FROM data.cleaning_version version
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.cleaning_version_id=:id
            """
            ),
            {"id": version_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        raise LookupError("Published Cleaning Version not found")
    return row


def _published_calendar(connection: Connection, version_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                """
            SELECT version.calendar_version_id,version.artifact_id,artifact.status
              FROM catalog.calendar_version version
              JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id
             WHERE version.calendar_version_id=:id
            """
            ),
            {"id": version_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        raise LookupError("Published Calendar Version not found")
    return row


def _validate_reconciliation_resolutions(
    primary_dataset_id: uuid.UUID,
    primary: RowMapping,
    resolutions: list[_ResolutionRow],
) -> None:
    intervals: dict[uuid.UUID, list[tuple[date, date]]] = defaultdict(list)
    excluded: set[uuid.UUID] = set()
    for item in resolutions:
        if item.resolution_kind == "unresolved":
            raise ValueError("Unresolved gaps cannot enter a Reconciliation Plan")
        intervals[item.security_id].append((item.gap_start, item.gap_end))
        if item.resolution_kind == "exclude_security":
            excluded.add(item.security_id)
    for security_id, values in intervals.items():
        ordered = sorted(values)
        if any(
            current[0] <= previous[1]
            for previous, current in zip(ordered, ordered[1:], strict=False)
        ):
            raise ValueError(f"Gap Resolution intervals overlap for Security {security_id}")
        if security_id in excluded and len(values) != 1:
            raise ValueError("Uniform Security exclusion cannot be combined with interval repair")
    if primary["dataset_publication_id"] != primary_dataset_id:
        raise ValueError("Primary Dataset identity drifted")


def _plan_document(
    spec: MarketReconciliationSpec, inputs: _ReconciliationInputs
) -> dict[str, object]:
    excluded = {
        item.security_id
        for item in inputs.resolutions
        if item.resolution_kind == "exclude_security"
    }
    return {
        "contract_version": _PLAN_CONTRACT,
        "primary_dataset_publication_id": str(spec.primary_dataset_publication_id),
        "primary_dataset_artifact_id": str(inputs.primary["artifact_id"]),
        "cleaning_version_id": str(spec.cleaning_version_id),
        "calendar_version_id": str(spec.calendar_version_id),
        "output_dataset_key": spec.output_dataset_key,
        "output_version_number": spec.output_version_number,
        "reconstruction_policy": spec.reconstruction_policy,
        "resolution_count": len(inputs.resolutions),
        "excluded_security_count": len(excluded),
        "resolutions": [
            {
                "ordinal": ordinal,
                "market_gap_resolution_id": str(item.resolution_id),
                "artifact_id": str(item.artifact_id),
                "resolution_fingerprint": item.fingerprint,
                "security_id": str(item.security_id),
                "gap_start": item.gap_start.isoformat(),
                "gap_end": item.gap_end.isoformat(),
                "resolution_kind": item.resolution_kind,
            }
            for ordinal, item in enumerate(inputs.resolutions)
        ],
    }


def _primary_rows(
    connection: Connection, dataset_id: uuid.UUID
) -> tuple[tuple[_RawBar, ...], tuple[CanonicalAction, ...], dict[uuid.UUID, uuid.UUID]]:
    rows = (
        connection.execute(
            text(
                """
            SELECT security.security_id,bar.asset_id,bar.session_date,bar.open_raw,
                   bar.high_raw,bar.low_raw,bar.close_raw,bar.volume_raw
              FROM data.daily_bar bar
              JOIN catalog.security security ON security.legacy_asset_id=bar.asset_id
             WHERE bar.dataset_publication_id=:dataset
             ORDER BY security.security_id,bar.session_date
            """
            ),
            {"dataset": dataset_id},
        )
        .mappings()
        .all()
    )
    raw = tuple(
        _RawBar(
            cast(uuid.UUID, row["security_id"]),
            cast(uuid.UUID, row["asset_id"]),
            cast(date, row["session_date"]),
            cast(Decimal, row["open_raw"]),
            cast(Decimal, row["high_raw"]),
            cast(Decimal, row["low_raw"]),
            cast(Decimal, row["close_raw"]),
            int(row["volume_raw"]),
        )
        for row in rows
    )
    if not raw:
        raise ValueError("Primary Dataset has no bridged market rows")
    asset_by_security = {item.security_id: item.asset_id for item in raw}
    action_rows = (
        connection.execute(
            text(
                """
            SELECT security.security_id,action.effective_date,action.cash_dividend,
                   action.split_ratio
              FROM data.corporate_action action
              JOIN catalog.security security ON security.legacy_asset_id=action.asset_id
             WHERE action.dataset_publication_id=:dataset
             ORDER BY security.security_id,action.effective_date
            """
            ),
            {"dataset": dataset_id},
        )
        .mappings()
        .all()
    )
    actions = tuple(
        CanonicalAction(
            str(row["security_id"]),
            cast(date, row["effective_date"]),
            cast(Decimal, row["cash_dividend"]),
            cast(Decimal, row["split_ratio"]),
        )
        for row in action_rows
    )
    return raw, actions, asset_by_security


def _source_snapshots(connection: Connection, dataset_id: uuid.UUID) -> tuple[uuid.UUID, ...]:
    return tuple(
        cast(uuid.UUID, item)
        for item in connection.execute(
            text(
                """
                SELECT source_snapshot_id FROM data.dataset_input
                 WHERE dataset_publication_id=:dataset
                 ORDER BY role,ordinal
                """
            ),
            {"dataset": dataset_id},
        ).scalars()
    )


def _alternate_rows(connection: Connection, observation_id: uuid.UUID) -> _ObservationRows:
    header = _published_alternate(connection, observation_id)
    asset_id = connection.execute(
        text("SELECT legacy_asset_id FROM catalog.security WHERE security_id=:security"),
        {"security": header["security_id"]},
    ).scalar_one_or_none()
    if asset_id is None:
        raise ValueError("Alternate Observation Security lacks an Asset bridge")
    bars = tuple(
        _RawBar(
            cast(uuid.UUID, header["security_id"]),
            cast(uuid.UUID, asset_id),
            cast(date, row["session_date"]),
            cast(Decimal, row["open_raw"]),
            cast(Decimal, row["high_raw"]),
            cast(Decimal, row["low_raw"]),
            cast(Decimal, row["close_raw"]),
            int(row["volume_raw"]),
            cast(Decimal | None, row["provider_adjusted_close"]),
        )
        for row in connection.execute(
            text(
                """
                SELECT * FROM data.v022_alternate_market_bar
                 WHERE alternate_observation_set_id=:id ORDER BY session_date
                """
            ),
            {"id": observation_id},
        ).mappings()
    )
    actions = tuple(
        CanonicalAction(
            str(header["security_id"]),
            cast(date, row["effective_date"]),
            cast(Decimal, row["cash_dividend"]),
            cast(Decimal, row["split_ratio"]),
        )
        for row in connection.execute(
            text(
                """
                SELECT * FROM data.v022_alternate_corporate_action
                 WHERE alternate_observation_set_id=:id ORDER BY effective_date
                """
            ),
            {"id": observation_id},
        ).mappings()
    )
    return _ObservationRows(bars, actions)


def _write_reconciled_dataset(
    connection: Connection,
    dataset_id: uuid.UUID,
    artifact_id: uuid.UUID,
    plan_id: uuid.UUID,
    plan_artifact_id: uuid.UUID,
    spec: MarketReconciliationSpec,
    inputs: _ReconciliationInputs,
    materialized: _MaterializedDataset,
    binding_document: dict[str, object],
    binding_fingerprint: str,
) -> None:
    start = min(item.session_date for item in materialized.bars)
    end = max(item.session_date for item in materialized.bars)
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_publication (
              dataset_publication_id,artifact_id,cleaning_version_id,calendar_version_id,
              dataset_key,version_number,dataset_kind,value_kind,coverage_start,
              coverage_end,row_count
            ) VALUES (:id,:artifact,:cleaning,:calendar,:key,:version,'canonical',
                      'daily_bar',:start,:end,:rows)
            """
        ),
        {
            "id": dataset_id,
            "artifact": artifact_id,
            "cleaning": spec.cleaning_version_id,
            "calendar": spec.calendar_version_id,
            "key": spec.output_dataset_key,
            "version": spec.output_version_number,
            "start": start,
            "end": end,
            "rows": len(materialized.bars),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_input (
              dataset_input_id,dataset_publication_id,source_snapshot_id,role,ordinal
            ) VALUES (:id,:dataset,:snapshot,'source_snapshot',:ordinal)
            """
        ),
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"bird:v0.22:reconciled-dataset-input:{dataset_id}:{ordinal}",
                ),
                "dataset": dataset_id,
                "snapshot": snapshot_id,
                "ordinal": ordinal,
            }
            for ordinal, snapshot_id in enumerate(materialized.source_snapshot_ids)
        ],
    )
    bar_sql = text(
        """
        INSERT INTO data.daily_bar (
          dataset_publication_id,asset_id,session_date,open_raw,high_raw,low_raw,
          close_raw,adj_close,open_adj,high_adj,low_adj,close_adj,
          adjustment_factor,volume_raw
        ) VALUES (:dataset,:asset,:session,:open_raw,:high_raw,:low_raw,:close_raw,
                  :adj_close,:open_adj,:high_adj,:low_adj,:close_adj,:factor,:volume)
        """
    )
    for offset in range(0, len(materialized.bars), 10_000):
        connection.execute(
            bar_sql,
            [
                {
                    "dataset": dataset_id,
                    "asset": materialized.asset_by_security[uuid.UUID(item.symbol)],
                    "session": item.session_date,
                    "open_raw": item.open_raw,
                    "high_raw": item.high_raw,
                    "low_raw": item.low_raw,
                    "close_raw": item.close_raw,
                    "adj_close": item.adj_close,
                    "open_adj": item.open_adj,
                    "high_adj": item.high_adj,
                    "low_adj": item.low_adj,
                    "close_adj": item.close_adj,
                    "factor": item.adjustment_factor,
                    "volume": item.volume_raw,
                }
                for item in materialized.bars[offset : offset + 10_000]
            ],
        )
    if materialized.actions:
        connection.execute(
            text(
                """
                INSERT INTO data.corporate_action (
                  dataset_publication_id,asset_id,effective_date,cash_dividend,split_ratio
                ) VALUES (:dataset,:asset,:date,:dividend,:split)
                """
            ),
            [
                {
                    "dataset": dataset_id,
                    "asset": materialized.asset_by_security[uuid.UUID(item.symbol)],
                    "date": item.effective_date,
                    "dividend": item.cash_dividend,
                    "split": item.split_ratio,
                }
                for item in materialized.actions
            ],
        )
    sessions = tuple(
        cast(date, item)
        for item in connection.execute(
            text(
                """
                SELECT session_date FROM catalog.calendar_session
                 WHERE calendar_version_id=:calendar
                 ORDER BY session_date
                """
            ),
            {"calendar": spec.calendar_version_id},
        ).scalars()
    )
    grouped: dict[uuid.UUID, list[CanonicalBar]] = defaultdict(list)
    for item in materialized.bars:
        grouped[uuid.UUID(item.symbol)].append(item)
    connection.execute(
        text(
            """
            INSERT INTO data.dataset_coverage (
              dataset_coverage_id,dataset_publication_id,asset_id,subject_key,
              coverage_start,coverage_end,observation_count,missing_count
            ) VALUES (:id,:dataset,:asset,:subject,:start,:end,:count,:missing)
            """
        ),
        [
            {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL, f"bird:v0.22:reconciled-coverage:{dataset_id}:{security}"
                ),
                "dataset": dataset_id,
                "asset": materialized.asset_by_security[security],
                "subject": str(security),
                "start": min(item.session_date for item in items),
                "end": max(item.session_date for item in items),
                "count": len(items),
                "missing": len(
                    {
                        session
                        for session in sessions
                        if min(item.session_date for item in items)
                        <= session
                        <= max(item.session_date for item in items)
                    }.difference({item.session_date for item in items})
                ),
            }
            for security, items in sorted(grouped.items(), key=lambda item: str(item[0]))
        ],
    )
    connection.execute(
        text(
            """
            INSERT INTO data.v022_reconciled_market_dataset_binding (
              dataset_publication_id,dataset_artifact_id,market_reconciliation_plan_id,
              plan_artifact_id,primary_dataset_publication_id,
              primary_dataset_artifact_id,price_semantics,reconstruction_policy,
              replaced_bar_count,excluded_security_count,binding_document,
              binding_fingerprint
            ) VALUES (:dataset,:artifact,:plan,:plan_artifact,:primary,
                      :primary_artifact,:semantics,:policy,:replaced,:excluded,
                      CAST(:document AS jsonb),:fingerprint)
            """
        ),
        {
            "dataset": dataset_id,
            "artifact": artifact_id,
            "plan": plan_id,
            "plan_artifact": plan_artifact_id,
            "primary": spec.primary_dataset_publication_id,
            "primary_artifact": inputs.primary["artifact_id"],
            "semantics": _price_semantics(spec.reconstruction_policy),
            "policy": spec.reconstruction_policy,
            "replaced": materialized.replaced_bar_count,
            "excluded": len(materialized.excluded_security_ids),
            "document": json.dumps(binding_document, sort_keys=True),
            "fingerprint": binding_fingerprint,
        },
    )


def _csv_value(row: dict[str, str], *keys: str) -> str:
    value = _csv_optional_value(row, *keys)
    if value is None:
        raise ValueError(f"missing {keys[0]}")
    return value


def _csv_optional_value(row: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None and value.strip():
            return value.strip()
    return None


def _csv_decimal(row: dict[str, str], *keys: str) -> Decimal:
    return Decimal(_csv_value(row, *keys))


def _csv_optional_decimal(row: dict[str, str], *keys: str) -> Decimal:
    value = _csv_optional_value(row, *keys)
    return Decimal(value) if value is not None else Decimal(0)


def _deduplicate(values: tuple[uuid.UUID, ...]) -> tuple[uuid.UUID, ...]:
    return tuple(dict.fromkeys(values))


def _require_text(label: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{label} is required")


def _json_object(value: dict[str, Any], label: str) -> None:
    try:
        encoded = json.loads(json.dumps(value, sort_keys=True))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be JSON-compatible") from error
    if not isinstance(encoded, dict):
        raise ValueError(f"{label} must be an object")
