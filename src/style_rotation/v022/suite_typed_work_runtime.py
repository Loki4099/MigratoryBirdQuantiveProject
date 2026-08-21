from __future__ import annotations

import hashlib
import io
import json
import re
import uuid
from collections import OrderedDict, defaultdict
from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.v022.aggregation_work_runtime import (
    SignalManifestPoint,
    VerifiedSignalManifestReader,
)
from style_rotation.v022.dag import ClaimedGraphWork
from style_rotation.v022.defense_runtime import (
    DefenseAllocationMember,
    DefenseDecision,
    DefensePriceObservation,
    MergedPortfolioTarget,
    NetTargetWeight,
    SleeveContribution,
    evaluate_defense_timing,
    merge_sleeves,
)
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.portfolio_runtime import (
    PortfolioCellSpec,
    evaluate_portfolio_cell,
)
from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.runtime_output_payloads import (
    CanonicalRuntimePayload,
    PortfolioEvaluationPitEvidence,
    PortfolioExecutionIdentity,
    PortfolioRuntimeResultEnvelope,
    PortfolioSessionPitEvidence,
    adapt_defense_budget_decisions,
    adapt_merged_portfolio_targets,
    adapt_portfolio_cell_result,
    adapt_strategy_unit_risk_targets,
    validate_canonical_runtime_payload,
)
from style_rotation.v022.settlement_accounting import (
    RuntimeSettlementInstruction,
    RuntimeSettlementLeg,
)
from style_rotation.v022.strategy_compat_runtime import (
    StrategyAssetInput,
    StrategyUnitRiskTarget,
    UnitRiskPosition,
    build_unit_risk_topk_target,
)

TypedWorkKind = Literal["strategy_target", "defense_decision", "sleeve_merge", "portfolio_cell"]

_OUTPUT_PORTS: Mapping[TypedWorkKind, str] = {
    "strategy_target": "strategy_unit_risk_target",
    "defense_decision": "defense_budget_decision",
    "sleeve_merge": "merged_portfolio_target",
    "portfolio_cell": "portfolio_cell_result",
}
_ARTIFACT_TYPES: Mapping[TypedWorkKind, str] = {
    "strategy_target": "v022_strategy_unit_risk_target_path",
    "defense_decision": "v022_defense_decision_path",
    "sleeve_merge": "v022_merged_portfolio_target_path",
    "portfolio_cell": "v022_portfolio_cell_runtime_result",
}
_OBJECT_URI = re.compile(r"payload-object://sha256/([0-9a-f]{64})\.([a-z0-9][a-z0-9._-]{0,19})")


@dataclass(frozen=True, slots=True)
class PortfolioMarketInputRequest:
    portfolio_evaluation_data_context_id: uuid.UUID
    compiled_execution_data_context_id: uuid.UUID
    canonical_market_dataset_publication_id: uuid.UUID
    benchmark_dataset_publication_id: uuid.UUID
    benchmark_calendar_version_id: uuid.UUID
    reserve_dataset_publication_id: uuid.UUID
    effective_start: date
    effective_end: date
    benchmark_asset_id: uuid.UUID
    benchmark_asset_key: str
    investable_asset_ids: tuple[uuid.UUID, ...]
    decision_dates: tuple[date, ...]
    cohort_common_sessions: tuple[date, ...] = ()
    required_asset_sessions: tuple[tuple[uuid.UUID, tuple[date, ...]], ...] = ()
    carry_forward_asset_sessions: tuple[
        tuple[uuid.UUID, tuple[date, ...]], ...
    ] = ()
    settlements: tuple[RuntimeSettlementInstruction, ...] = ()


@dataclass(frozen=True, slots=True)
class PortfolioMarketInputs:
    bars: tuple[AccountingMarketBar, ...]
    reserve_intervals: tuple[AccountingReserveInterval, ...]
    common_sessions: tuple[date, ...]
    evaluation_input_cutoff_at: datetime
    session_input_known_at: tuple[tuple[date, datetime], ...]
    settlements: tuple[RuntimeSettlementInstruction, ...] = ()


class PortfolioMarketInputLoader(Protocol):
    """Raw/PIT slice supplies this port; this module never invents market evidence."""

    def load(self, request: PortfolioMarketInputRequest) -> PortfolioMarketInputs: ...


class RepresentativePortfolioMarketInputLoader:
    """Load the exploratory 4-ETF/SPY/reserve portfolio slice from frozen identities."""

    def __init__(self, engine: Engine, *, max_cached_panels: int = 2) -> None:
        if max_cached_panels < 1:
            raise ValueError("Portfolio market panel cache must retain at least one panel")
        self._engine = engine
        self._max_cached_panels = max_cached_panels
        self._cache: OrderedDict[tuple[object, ...], PortfolioMarketInputs] = OrderedDict()

    def load(self, request: PortfolioMarketInputRequest) -> PortfolioMarketInputs:
        if (
            request.effective_start > request.effective_end
            or not request.investable_asset_ids
            or not request.decision_dates
        ):
            raise V022RuntimeContractError(
                "representative_portfolio_request_invalid",
                "Representative Portfolio request requires assets, decisions, and a range",
            )
        cache_key = _portfolio_market_panel_cache_key(request)
        cached = self._cache.get(cache_key)
        with self._engine.connect() as connection:
            execution_anchor_session = (
                connection.scalar(
                    text(
                        """
                        SELECT max(session_date)
                          FROM catalog.calendar_session
                         WHERE calendar_version_id=:calendar AND session_date<:start
                        """
                    ),
                    {
                        "calendar": request.benchmark_calendar_version_id,
                        "start": request.effective_start,
                    },
                )
                if request.cohort_common_sessions
                else None
            )
            if request.cohort_common_sessions and not isinstance(execution_anchor_session, date):
                raise V022RuntimeDataError(
                    "portfolio_execution_anchor_missing",
                    "Frozen evaluation start requires its preceding common session",
                )
            load_start = (
                execution_anchor_session
                if request.cohort_common_sessions
                else request.effective_start
            )
            proof = (
                connection.execute(
                    text(
                        """
                    SELECT proof.snapshot_semantics,proof.materialization_state,
                           proof.manifest_artifact_status,
                           proof.dataset_artifact_status
                      FROM (
                        SELECT binding.snapshot_semantics,
                               manifest.materialization_state,
                               manifest_artifact.status AS manifest_artifact_status,
                               dataset_artifact.status AS dataset_artifact_status,
                               1 AS proof_priority
                          FROM data.v022_execution_context_payload_binding binding
                          JOIN processing.feature_version feature
                            ON feature.feature_version_id=binding.feature_version_id
                          JOIN processing.feature_variant variant
                            ON variant.feature_variant_id=feature.feature_variant_id
                          JOIN data.payload_manifest manifest
                            ON manifest.payload_manifest_id=binding.payload_manifest_id
                          JOIN lineage.artifact manifest_artifact
                            ON manifest_artifact.artifact_id=manifest.artifact_id
                          JOIN data.dataset_publication dataset
                            ON dataset.dataset_publication_id=
                               binding.dataset_publication_id
                          JOIN lineage.artifact dataset_artifact
                            ON dataset_artifact.artifact_id=dataset.artifact_id
                         WHERE binding.compiled_execution_data_context_id=:context AND
                               binding.dataset_publication_id=:dataset AND
                               variant.variant_key='adjusted_close'
                        UNION ALL
                        SELECT binding.snapshot_semantics,
                               manifest.materialization_state,
                               manifest_artifact.status AS manifest_artifact_status,
                               dataset_artifact.status AS dataset_artifact_status,
                               0 AS proof_priority
                          FROM processing.v022_compiled_context_calculation_binding
                               context_binding
                          JOIN data.v022_calculation_context_payload_binding binding
                            ON binding.calculation_context_id=
                               context_binding.calculation_context_id
                          JOIN processing.feature_version feature
                            ON feature.feature_version_id=binding.feature_version_id
                          JOIN processing.feature_variant variant
                            ON variant.feature_variant_id=feature.feature_variant_id
                          JOIN data.payload_manifest manifest
                            ON manifest.payload_manifest_id=binding.payload_manifest_id
                          JOIN lineage.artifact manifest_artifact
                            ON manifest_artifact.artifact_id=manifest.artifact_id
                          JOIN data.dataset_publication dataset
                            ON dataset.dataset_publication_id=
                               binding.dataset_publication_id
                          JOIN lineage.artifact dataset_artifact
                            ON dataset_artifact.artifact_id=dataset.artifact_id
                         WHERE context_binding.compiled_execution_data_context_id=:context AND
                               binding.dataset_publication_id=:dataset AND
                               variant.variant_key='adjusted_close'
                      ) proof
                     ORDER BY proof.proof_priority
                     LIMIT 1
                    """
                    ),
                    {
                        "context": request.compiled_execution_data_context_id,
                        "dataset": request.canonical_market_dataset_publication_id,
                    },
                )
                .mappings()
                .one_or_none()
            )
            if proof is None or not (
                proof["snapshot_semantics"].get("semantic_mode")
                == "back_adjusted_historical_research"
                and proof["snapshot_semantics"].get("price_basis") == "back_adjusted"
                and proof["snapshot_semantics"].get("product_warning_required") is True
                and proof["materialization_state"] == "materialized"
                and proof["manifest_artifact_status"] == "published"
                and proof["dataset_artifact_status"] == "published"
            ):
                raise V022RuntimeDataError(
                    "representative_market_snapshot_unpublished",
                    "Portfolio input requires a published frozen canonical-market payload proof",
                )
            if cached is not None:
                self._cache.move_to_end(cache_key)
                return cached
            security_rows = (
                connection.execute(
                    text(
                        """
                    SELECT security_id,legacy_asset_id,security_key
                      FROM catalog.security WHERE security_id IN :security_ids
                     ORDER BY security_id
                    """
                    ).bindparams(bindparam("security_ids", expanding=True)),
                    {"security_ids": request.investable_asset_ids},
                )
                .mappings()
                .all()
            )
            if len(security_rows) != len(request.investable_asset_ids) or any(
                row["legacy_asset_id"] is None for row in security_rows
            ):
                raise V022RuntimeDataError(
                    "representative_portfolio_asset_mapping_missing",
                    "Every portfolio Security requires its canonical market Asset mapping",
                )
            market_rows = (
                connection.execute(
                    text(
                        """
                    SELECT asset_id,session_date,open_adj,adj_close
                      FROM data.daily_bar
                     WHERE dataset_publication_id=:dataset AND
                           asset_id IN :asset_ids AND
                           session_date BETWEEN :start AND :end
                     ORDER BY session_date,asset_id
                    """
                    ).bindparams(bindparam("asset_ids", expanding=True)),
                    {
                        "dataset": request.canonical_market_dataset_publication_id,
                        "asset_ids": [row["legacy_asset_id"] for row in security_rows],
                        "start": load_start,
                        "end": request.effective_end,
                    },
                )
                .mappings()
                .all()
            )
            benchmark_rows = (
                connection.execute(
                    text(
                        """
                    SELECT asset_id,session_date,open_adj,adj_close
                      FROM data.daily_bar
                     WHERE dataset_publication_id=:dataset AND asset_id=:asset AND
                           session_date BETWEEN :start AND :end
                     ORDER BY session_date
                    """
                    ),
                    {
                        "dataset": request.benchmark_dataset_publication_id,
                        "asset": request.benchmark_asset_id,
                        "start": load_start,
                        "end": request.effective_end,
                    },
                )
                .mappings()
                .all()
            )
            session_rows = (
                connection.execute(
                    text(
                        """
                    SELECT session_date,close_at_utc
                      FROM catalog.calendar_session
                     WHERE calendar_version_id=:calendar AND
                           session_date BETWEEN :start AND :end
                     ORDER BY session_date
                    """
                    ),
                    {
                        "calendar": request.benchmark_calendar_version_id,
                        "start": load_start,
                        "end": request.effective_end,
                    },
                )
                .mappings()
                .all()
            )
            session_closes = {
                row["session_date"]: cast(datetime, row["close_at_utc"]).astimezone(UTC)
                for row in session_rows
                if cast(datetime, row["close_at_utc"]).utcoffset() is not None
            }
            known_rows = (
                connection.execute(
                    text(
                        """
                    SELECT snapshot.fetched_at
                      FROM data.dataset_input input
                      JOIN data.source_snapshot snapshot
                        ON snapshot.source_snapshot_id=input.source_snapshot_id
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=snapshot.artifact_id
                     WHERE input.dataset_publication_id IN (:market,:benchmark) AND
                           artifact.status='published'
                    UNION ALL
                    SELECT artifact.published_at AS fetched_at
                      FROM data.dataset_publication publication
                      JOIN lineage.artifact artifact
                        ON artifact.artifact_id=publication.artifact_id
                     WHERE publication.dataset_publication_id=:reserve AND
                           artifact.status='published'
                    """
                    ),
                    {
                        "market": request.canonical_market_dataset_publication_id,
                        "benchmark": request.benchmark_dataset_publication_id,
                        "reserve": request.reserve_dataset_publication_id,
                    },
                )
                .mappings()
                .all()
            )
            if not known_rows or any(row["fetched_at"] is None for row in known_rows):
                raise V022RuntimeDataError(
                    "representative_portfolio_known_at_missing",
                    "Frozen market and reserve inputs require publication-time evidence",
                )
            evaluation_cutoff = max(
                cast(datetime, row["fetched_at"]).astimezone(UTC) for row in known_rows
            )

            security_by_legacy = {
                row["legacy_asset_id"]: (row["security_id"], row["security_key"])
                for row in security_rows
            }
            target_dates: dict[uuid.UUID, set[date]] = {
                row["security_id"]: set() for row in security_rows
            }
            bars_by_security: dict[uuid.UUID, dict[date, AccountingMarketBar]] = {
                row["security_id"]: {} for row in security_rows
            }
            for row in market_rows:
                identity = security_by_legacy.get(row["asset_id"])
                if identity is None:
                    continue
                target_dates[identity[0]].add(row["session_date"])
                bars_by_security[identity[0]][row["session_date"]] = AccountingMarketBar(
                    identity[0],
                    identity[1],
                    row["session_date"],
                    Decimal(row["open_adj"]),
                    Decimal(row["adj_close"]),
                )
            benchmark_dates = {row["session_date"] for row in benchmark_rows}
            if not benchmark_dates or any(not dates for dates in target_dates.values()):
                raise V022RuntimeDataError(
                    "representative_portfolio_market_rows_missing",
                    "Frozen market inputs do not cover every Portfolio Asset",
                )
            required_by_asset = dict(request.required_asset_sessions)
            carry_forward_by_asset = dict(request.carry_forward_asset_sessions)
            if request.cohort_common_sessions:
                common_sessions = (
                    cast(date, execution_anchor_session),
                ) + request.cohort_common_sessions
                common = set(common_sessions)
                bars = list(
                    _masked_accounting_bars(
                        bars_by_security,
                        common_sessions=common_sessions,
                        required_by_asset=required_by_asset,
                        carry_forward_by_asset=carry_forward_by_asset,
                    )
                )
            else:
                common = set(benchmark_dates)
                for dates in target_dates.values():
                    common.intersection_update(dates)
                common_sessions = tuple(sorted(common))
                bars = [
                    bar
                    for security_bars in bars_by_security.values()
                    for session, bar in security_bars.items()
                    if session in common
                ]
            if (
                not common_sessions
                or request.effective_start not in common_sessions
                or common_sessions[-1] != request.effective_end
                or not set(request.decision_dates).issubset(common)
                or not set(common_sessions).issubset(benchmark_dates)
                or not set(common_sessions).issubset(session_closes)
            ):
                raise V022RuntimeDataError(
                    "representative_portfolio_common_sessions_incomplete",
                    "Portfolio inputs do not reproduce the exact effective common range",
                )
            bars.extend(
                AccountingMarketBar(
                    request.benchmark_asset_id,
                    request.benchmark_asset_key,
                    row["session_date"],
                    Decimal(row["open_adj"]),
                    Decimal(row["adj_close"]),
                )
                for row in benchmark_rows
                if row["session_date"] in common
            )
            common_pairs = set(zip(common_sessions, common_sessions[1:], strict=False))
            reserve_rows = (
                connection.execute(
                    text(
                        """
                    SELECT interval_start,interval_end,accrual_factor,
                           source_observation_date,source_available_date,quality_status
                      FROM data.reserve_return
                     WHERE dataset_publication_id=:dataset AND
                           interval_start>=:start AND interval_end<=:end
                     ORDER BY interval_start,interval_end
                    """
                    ),
                    {
                        "dataset": request.reserve_dataset_publication_id,
                        "start": common_sessions[0],
                        "end": common_sessions[-1],
                    },
                )
                .mappings()
                .all()
            )
        reserve = tuple(
            AccountingReserveInterval(
                row["interval_start"],
                row["interval_end"],
                Decimal(row["accrual_factor"]),
                row["source_observation_date"],
                row["source_available_date"],
                cast(Any, row["quality_status"]),
            )
            for row in reserve_rows
            if (row["interval_start"], row["interval_end"]) in common_pairs
        )
        if {(item.interval_start, item.interval_end) for item in reserve} != common_pairs:
            raise V022RuntimeDataError(
                "representative_portfolio_reserve_intervals_missing",
                "Reserve Dataset does not cover every exact common-session interval",
            )
        evaluation_start_index = common_sessions.index(request.effective_start)
        path_sessions = common_sessions[evaluation_start_index:]
        result = PortfolioMarketInputs(
            bars=tuple(sorted(bars, key=lambda item: (item.session_date, item.asset_key))),
            reserve_intervals=reserve,
            common_sessions=common_sessions,
            evaluation_input_cutoff_at=evaluation_cutoff,
            session_input_known_at=tuple(
                (session, session_closes[session]) for session in path_sessions
            ),
            settlements=request.settlements,
        )
        self._cache[cache_key] = result
        self._cache.move_to_end(cache_key)
        while len(self._cache) > self._max_cached_panels:
            self._cache.popitem(last=False)
        return result


def _portfolio_market_panel_cache_key(
    request: PortfolioMarketInputRequest,
) -> tuple[object, ...]:
    """Identify exact frozen market evidence independently of one compiled Graph."""

    return (
        request.portfolio_evaluation_data_context_id,
        request.canonical_market_dataset_publication_id,
        request.benchmark_dataset_publication_id,
        request.benchmark_calendar_version_id,
        request.reserve_dataset_publication_id,
        request.effective_start,
        request.effective_end,
        request.benchmark_asset_id,
        request.benchmark_asset_key,
        request.investable_asset_ids,
        request.decision_dates,
        request.cohort_common_sessions,
        request.required_asset_sessions,
        request.carry_forward_asset_sessions,
        request.settlements,
    )


def _masked_accounting_bars(
    bars_by_security: Mapping[uuid.UUID, Mapping[date, AccountingMarketBar]],
    *,
    common_sessions: tuple[date, ...],
    required_by_asset: Mapping[uuid.UUID, tuple[date, ...]],
    carry_forward_by_asset: Mapping[uuid.UUID, tuple[date, ...]] | None = None,
) -> tuple[AccountingMarketBar, ...]:
    """Fill zero-weight or frozen non-tradable valuation dates only."""

    result: list[AccountingMarketBar] = []
    common = set(common_sessions)
    for security_id, real_by_date in bars_by_security.items():
        real_dates = tuple(sorted(set(real_by_date).intersection(common)))
        if not real_dates:
            raise V022RuntimeDataError(
                "cohort_portfolio_market_history_missing",
                "A Cohort Portfolio asset has no market history in the frozen interval",
                details={"security_id": str(security_id)},
            )
        missing_required = set(required_by_asset.get(security_id, ())).difference(real_by_date)
        allowed_carry = set((carry_forward_by_asset or {}).get(security_id, ()))
        unsupported = missing_required.difference(allowed_carry)
        first_real = real_dates[0]
        unsupported.update(session for session in missing_required if session <= first_real)
        if unsupported:
            raise V022RuntimeDataError(
                "cohort_position_market_bar_missing",
                "A held or traded Cohort asset lacks a required market bar",
                details={
                    "security_id": str(security_id),
                    "first_missing_session": min(unsupported).isoformat(),
                    "missing_session_count": len(unsupported),
                },
            )
        first = real_by_date[real_dates[0]]
        prior_close = first.adjusted_open
        for session in common_sessions:
            real = real_by_date.get(session)
            if real is not None:
                result.append(real)
                prior_close = real.adjusted_close
                continue
            # Accounting requires a rectangular matrix. This value is consumed only
            # while the exact frozen target path holds zero units of this Security.
            result.append(
                AccountingMarketBar(
                    security_id,
                    first.asset_key,
                    session,
                    prior_close,
                    prior_close,
                )
            )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PublishedTypedWorkOutput:
    graph_work_item_id: uuid.UUID
    work_kind: TypedWorkKind
    artifact_id: uuid.UUID
    payload_manifest_id: uuid.UUID
    payload_manifest_artifact_id: uuid.UUID
    manifest_hash: str
    reused_publication: bool


@dataclass(frozen=True, slots=True)
class DefenseTimingWindow:
    observations: tuple[DefensePriceObservation, ...]
    expected_sessions: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class _CohortPortfolioRequirements:
    common_sessions: tuple[date, ...]
    required_asset_sessions: tuple[tuple[uuid.UUID, tuple[date, ...]], ...]
    carry_forward_asset_sessions: tuple[
        tuple[uuid.UUID, tuple[date, ...]], ...
    ]
    settlements: tuple[RuntimeSettlementInstruction, ...]


@dataclass(frozen=True, slots=True)
class _TypedWorkContext:
    graph_run_id: uuid.UUID
    graph_work_item_id: uuid.UUID
    work_kind: TypedWorkKind
    worker_key: str
    fencing_token: int
    execution_fingerprint: str
    output_payload_contract_version_id: uuid.UUID
    physical_encoding_version_id: uuid.UUID
    specification_document: Mapping[str, object]
    dependencies: tuple[DependencyInput, ...]
    values: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _PreparedPayload:
    content_hash: str
    storage_uri: str
    byte_size: int
    payload_object_id: uuid.UUID
    payload_partition_id: uuid.UUID
    partition_descriptor_hash: str
    payload_manifest_id: uuid.UUID
    logical_payload_fingerprint: str
    manifest_hash: str
    coverage_document: Mapping[str, object]


class TypedSuiteWorkExecutor:
    """Execute the four typed post-Aggregation Work kinds behind one claim entry."""

    def __init__(
        self,
        engine: Engine,
        *,
        object_store: LocalPayloadObjectStore,
        object_root: Path,
        portfolio_input_loader: PortfolioMarketInputLoader | None = None,
    ) -> None:
        self._engine = engine
        self._object_store = object_store
        self._object_root = object_root.resolve()
        self._signal_reader = VerifiedSignalManifestReader(engine, object_root)
        self._portfolio_input_loader = portfolio_input_loader

    def execute_claim(
        self,
        *,
        graph_run_id: uuid.UUID,
        claim: ClaimedGraphWork,
        worker_key: str,
    ) -> PublishedTypedWorkOutput:
        if claim.work_kind not in _OUTPUT_PORTS:
            raise V022RuntimeContractError(
                "typed_work_kind_unsupported",
                "Typed Suite executor accepts only post-Aggregation Work",
            )
        context = _load_typed_work_context(
            self._engine,
            graph_run_id=graph_run_id,
            claim=claim,
            worker_key=worker_key,
        )
        payload = self._calculate(context)
        prepared = _prepare_payload(self._object_store, context=context, payload=payload)
        return _publish_typed_output(
            self._engine, context=context, payload=payload, prepared=prepared
        )

    def _calculate(self, context: _TypedWorkContext) -> CanonicalRuntimePayload:
        if context.work_kind == "strategy_target":
            manifest_id = cast(uuid.UUID, context.values["source_manifest_id"])
            manifest_hash = cast(str, context.values["source_manifest_hash"])
            manifest_artifact_id = cast(uuid.UUID, context.values["source_manifest_artifact_id"])
            points = self._signal_reader.read(
                payload_manifest_id=manifest_id,
                expected_manifest_hash=manifest_hash,
                expected_artifact_id=manifest_artifact_id,
                catalog_release_id=cast(uuid.UUID, context.values["catalog_release_id"]),
                allowed_asset_keys=cast(Mapping[uuid.UUID, str], context.values["asset_keys"]),
                decision_dates=cast(
                    frozenset[date], context.values["decision_dates"]
                ),
                # The frozen Cohort mask, not the global Asset Context union,
                # defines the exact candidate panel for each decision date.
                # A monthly grid can legitimately omit a Context member that
                # is never selectable on a month-end.  The per-date equality
                # guard in build_strategy_target_payload remains strict.
                require_exact_asset_context=False,
            )
            return build_strategy_target_payload(
                points,
                variant_key=cast(str, context.values["strategy_variant_key"]),
                resolved_parameters=cast(
                    Mapping[str, object], context.values["resolved_parameters"]
                ),
                research_mode=cast(Literal["formal", "exploratory"], context.values["suite_mode"]),
                work_execution_fingerprint=context.execution_fingerprint,
                selectable_asset_ids_by_date=cast(
                    Mapping[date, frozenset[uuid.UUID]],
                    context.values["selectable_asset_ids_by_date"],
                ),
            )
        if context.work_kind == "defense_decision":
            strategy_payload = _read_runtime_payload(
                self._engine,
                self._object_root,
                table="strategy.v022_strategy_target_path",
                document_column="target_document",
                source_work_item_id=cast(uuid.UUID, context.values["source_strategy_work_item_id"]),
            )
            targets = decode_strategy_targets(strategy_payload)
            timing_variant_key = cast(str, context.values["timing_variant_key"])
            timing_windows = (
                load_ma200_defense_timing_windows(
                    self._engine,
                    compiled_defense_execution_context_id=cast(
                        uuid.UUID,
                        context.values["compiled_defense_execution_context_id"],
                    ),
                    decision_dates=tuple(item.decision_date for item in targets),
                )
                if timing_variant_key == "spy_ma200_tiered_budget"
                else None
            )
            return build_defense_payload(
                targets,
                defense_version_id=cast(uuid.UUID, context.values["defense_version_id"]),
                timing_policy_version_id=cast(
                    uuid.UUID, context.values["timing_policy_version_id"]
                ),
                allocation_policy_version_id=cast(
                    uuid.UUID, context.values["allocation_policy_version_id"]
                ),
                timing_variant_key=timing_variant_key,
                timing_windows=timing_windows,
                work_execution_fingerprint=context.execution_fingerprint,
            )
        if context.work_kind == "sleeve_merge":
            strategy_payload = _read_runtime_payload(
                self._engine,
                self._object_root,
                table="strategy.v022_strategy_target_path",
                document_column="target_document",
                source_work_item_id=cast(uuid.UUID, context.values["source_strategy_work_item_id"]),
            )
            defense_work = cast(uuid.UUID | None, context.values.get("source_defense_work_item_id"))
            defense_payload = (
                _read_runtime_payload(
                    self._engine,
                    self._object_root,
                    table="defense.v022_defense_decision_path",
                    document_column="decision_document",
                    source_work_item_id=defense_work,
                )
                if defense_work is not None
                else None
            )
            return build_merged_target_payload(
                decode_strategy_targets(strategy_payload),
                defense_decisions=(
                    decode_defense_decisions(defense_payload)
                    if defense_payload is not None
                    else None
                ),
                allocation_members=cast(
                    tuple[DefenseAllocationMember, ...],
                    context.values["allocation_members"],
                ),
                compiled_strategy_branch_id=cast(
                    uuid.UUID, context.values["compiled_strategy_branch_id"]
                ),
                work_execution_fingerprint=context.execution_fingerprint,
            )
        if self._portfolio_input_loader is None:
            raise V022RuntimeContractError(
                "portfolio_market_input_loader_missing",
                "Portfolio Work requires the Raw/PIT materialization loader",
            )
        merge_payload = _read_runtime_payload(
            self._engine,
            self._object_root,
            table="strategy.v022_merged_portfolio_target_path",
            document_column="target_document",
            source_work_item_id=cast(uuid.UUID, context.values["source_merge_work_item_id"]),
        )
        merged_targets = decode_merged_targets(merge_payload)
        cohort_requirements = _validate_portfolio_cohort_mask(
            self._engine,
            cast(
                uuid.UUID,
                context.values["evaluation_cohort_runtime_contract_id"],
            ),
            merged_targets,
        )
        request = PortfolioMarketInputRequest(
            portfolio_evaluation_data_context_id=cast(
                uuid.UUID, context.values["portfolio_evaluation_data_context_id"]
            ),
            compiled_execution_data_context_id=cast(
                uuid.UUID, context.values["compiled_execution_data_context_id"]
            ),
            canonical_market_dataset_publication_id=cast(
                uuid.UUID,
                context.values["canonical_market_dataset_publication_id"],
            ),
            benchmark_dataset_publication_id=cast(
                uuid.UUID, context.values["benchmark_dataset_publication_id"]
            ),
            benchmark_calendar_version_id=cast(
                uuid.UUID, context.values["benchmark_calendar_version_id"]
            ),
            reserve_dataset_publication_id=cast(
                uuid.UUID, context.values["reserve_dataset_publication_id"]
            ),
            effective_start=cast(date, context.values["effective_start"]),
            effective_end=cast(date, context.values["effective_end"]),
            benchmark_asset_id=cast(uuid.UUID, context.values["benchmark_asset_id"]),
            benchmark_asset_key=cast(str, context.values["benchmark_asset_key"]),
            investable_asset_ids=tuple(
                sorted(
                    {
                        item.asset_id
                        for target in merged_targets
                        for item in target.net_asset_weights
                    }
                    | {
                        leg.target_asset_id
                        for instruction in cohort_requirements.settlements
                        for leg in instruction.legs
                        if leg.target_asset_id is not None
                    },
                    key=str,
                )
            ),
            decision_dates=tuple(item.decision_date for item in merged_targets),
            cohort_common_sessions=cohort_requirements.common_sessions,
            required_asset_sessions=cohort_requirements.required_asset_sessions,
            carry_forward_asset_sessions=(
                cohort_requirements.carry_forward_asset_sessions
            ),
            settlements=cohort_requirements.settlements,
        )
        inputs = self._portfolio_input_loader.load(request)
        return build_portfolio_cell_payload(
            merged_targets,
            inputs=inputs,
            identity=PortfolioExecutionIdentity(
                compiled_strategy_branch_id=cast(
                    uuid.UUID, context.values["compiled_strategy_branch_id"]
                ),
                configuration_snapshot_id=cast(
                    uuid.UUID, context.values["configuration_snapshot_id"]
                ),
                evaluation_data_context_fingerprint=cast(
                    str, context.values["evaluation_data_context_fingerprint"]
                ),
                effective_start=request.effective_start,
                effective_end=request.effective_end,
                benchmark_asset_id=request.benchmark_asset_id,
                benchmark_asset_key=request.benchmark_asset_key,
                cost_policy_key=cast(str, context.values["cost_policy_key"]),
                cost_bps_per_side=cast(Decimal, context.values["cost_bps_per_side"]),
                execution_delay_sessions=cast(int, context.values["execution_delay_sessions"]),
                initial_capital_identity_only=cast(Decimal, context.values["initial_capital"]),
            ),
            work_execution_fingerprint=context.execution_fingerprint,
        )


def execute_claimed_typed_suite_work(
    engine: Engine,
    *,
    object_store: LocalPayloadObjectStore,
    object_root: Path,
    graph_run_id: uuid.UUID,
    claim: ClaimedGraphWork,
    worker_key: str,
    portfolio_input_loader: PortfolioMarketInputLoader | None = None,
) -> PublishedTypedWorkOutput:
    """Production entry point for one claimed post-Aggregation Work item."""

    loader = portfolio_input_loader or RepresentativePortfolioMarketInputLoader(engine)
    return TypedSuiteWorkExecutor(
        engine,
        object_store=object_store,
        object_root=object_root,
        portfolio_input_loader=loader,
    ).execute_claim(
        graph_run_id=graph_run_id,
        claim=claim,
        worker_key=worker_key,
    )


def build_strategy_target_payload(
    points: Sequence[SignalManifestPoint],
    *,
    variant_key: str,
    resolved_parameters: Mapping[str, object],
    research_mode: Literal["formal", "exploratory"],
    work_execution_fingerprint: str,
    selectable_asset_ids_by_date: Mapping[date, frozenset[uuid.UUID]] | None = None,
) -> CanonicalRuntimePayload:
    if variant_key not in {
        "cross_section_rank_top_k_parity",
        "cross_section_rank_top_k_large_cap_parity",
        "cross_section_rank_top_k_large_cap_multi_frequency",
    }:
        raise V022RuntimeContractError(
            "strategy_runtime_variant_unsupported", "Strategy Variant is not executable"
        )
    grouped: dict[date, list[SignalManifestPoint]] = defaultdict(list)
    for point in points:
        grouped[point.decision_date].append(point)
    targets: list[StrategyUnitRiskTarget] = []
    target_k = _positive_int(resolved_parameters.get("target_k"), "target_k")
    decisions_started = False
    for decision_date in sorted(grouped):
        if (
            selectable_asset_ids_by_date is not None
            and decision_date not in selectable_asset_ids_by_date
        ):
            continue
        selectable = (
            selectable_asset_ids_by_date[decision_date]
            if selectable_asset_ids_by_date is not None
            else None
        )
        current = sorted(
            (
                item
                for item in grouped[decision_date]
                if selectable is None or item.asset_id in selectable
            ),
            key=lambda item: item.asset_key,
        )
        if selectable is not None:
            observed_asset_ids = {item.asset_id for item in current}
            if observed_asset_ids != selectable:
                raise V022RuntimeDataError(
                    "cohort_decision_signal_panel_mismatch",
                    "A frozen Cohort decision requires one signal row for every selectable asset",
                    details={
                        "decision_date": decision_date.isoformat(),
                        "selectable_count": len(selectable),
                        "observed_count": len(observed_asset_ids),
                        "missing_count": len(selectable - observed_asset_ids),
                    },
                )
        rankable_count = sum(
            item.signal_value is not None and item.missing_reason is None for item in current
        )
        minimum_start_count = (
            target_k * 2 if variant_key == "cross_section_rank_top_k_parity" else target_k
        )
        if not decisions_started and rankable_count < minimum_start_count:
            if selectable_asset_ids_by_date is not None:
                raise V022RuntimeDataError(
                    "cohort_first_decision_incomplete",
                    "The first frozen Cohort decision does not contain enough selectable signals",
                    details={
                        "decision_date": decision_date.isoformat(),
                        "selectable_count": len(selectable or ()),
                        "rankable_count": rankable_count,
                        "required_count": minimum_start_count,
                    },
                )
            continue
        known_at = max(item.known_at for item in current).astimezone(UTC)
        if known_at.date() != decision_date:
            raise V022RuntimeDataError(
                "strategy_signal_session_close_mismatch",
                "Final-signal known_at must belong to its exact Decision session",
                details={
                    "decision_date": decision_date.isoformat(),
                    "signal_known_at": known_at.isoformat(),
                },
            )
        targets.append(
            build_unit_risk_topk_target(
                tuple(
                    StrategyAssetInput(
                        item.asset_id,
                        item.asset_key,
                        item.signal_value if item.missing_reason is None else None,
                    )
                    for item in current
                ),
                decision_date=decision_date,
                decision_cutoff_at=known_at,
                input_known_at=known_at,
                variant_key=cast(Any, variant_key),
                target_k=target_k,
                research_mode=research_mode,
                selection_buffer=cast(Any, resolved_parameters.get("selection_buffer", "none")),
                sector_cap=cast(Any, resolved_parameters.get("sector_cap", "none")),
            )
        )
        decisions_started = True
    if not targets:
        raise V022RuntimeDataError(
            "strategy_no_post_warmup_decision",
            "Final signal path contains no rankable post-warm-up Decision",
        )
    return adapt_strategy_unit_risk_targets(
        tuple(targets), work_execution_fingerprint=work_execution_fingerprint
    )


def build_fixed20_defense_payload(
    targets: Sequence[StrategyUnitRiskTarget],
    *,
    defense_version_id: uuid.UUID,
    timing_policy_version_id: uuid.UUID,
    allocation_policy_version_id: uuid.UUID,
    timing_variant_key: str,
    work_execution_fingerprint: str,
) -> CanonicalRuntimePayload:
    return build_defense_payload(
        targets,
        defense_version_id=defense_version_id,
        timing_policy_version_id=timing_policy_version_id,
        allocation_policy_version_id=allocation_policy_version_id,
        timing_variant_key=timing_variant_key,
        timing_windows=None,
        work_execution_fingerprint=work_execution_fingerprint,
    )


def build_defense_payload(
    targets: Sequence[StrategyUnitRiskTarget],
    *,
    defense_version_id: uuid.UUID,
    timing_policy_version_id: uuid.UUID,
    allocation_policy_version_id: uuid.UUID,
    timing_variant_key: str,
    timing_windows: Mapping[date, DefenseTimingWindow] | None,
    work_execution_fingerprint: str,
) -> CanonicalRuntimePayload:
    if timing_variant_key not in {"fixed20_budget", "spy_ma200_tiered_budget"}:
        raise V022RuntimeContractError(
            "defense_runtime_variant_unsupported",
            "Defense Timing Variant is not executable",
        )
    if timing_variant_key == "fixed20_budget":
        if timing_windows is not None:
            raise V022RuntimeContractError(
                "fixed20_timing_window_forbidden",
                "Fixed20 Defense cannot consume a market Timing window",
            )
        executable_targets = tuple(targets)
    else:
        if timing_windows is None:
            raise V022RuntimeContractError(
                "ma200_timing_window_missing",
                "MA200 Defense requires its frozen SPY Timing windows",
            )
        executable_targets = tuple(
            item for item in targets if item.decision_date in timing_windows
        )
        if not executable_targets:
            raise V022RuntimeDataError(
                "ma200_no_post_warmup_decision",
                "MA200 Defense has no Strategy decision after its 200-session warm-up",
            )
    decisions = tuple(
        evaluate_defense_timing(
            cast(Any, timing_variant_key),
            decision_date=item.decision_date,
            decision_cutoff_at=item.decision_cutoff_at,
            observations=(
                timing_windows[item.decision_date].observations
                if timing_windows is not None
                else ()
            ),
            expected_window_sessions=(
                timing_windows[item.decision_date].expected_sessions
                if timing_windows is not None
                else ()
            ),
        )
        for item in executable_targets
    )
    return adapt_defense_budget_decisions(
        decisions,
        work_execution_fingerprint=work_execution_fingerprint,
        defense_version_id=defense_version_id,
        timing_policy_version_id=timing_policy_version_id,
        allocation_policy_version_id=allocation_policy_version_id,
    )


def load_ma200_defense_timing_windows(
    engine: Engine,
    *,
    compiled_defense_execution_context_id: uuid.UUID,
    decision_dates: tuple[date, ...],
) -> Mapping[date, DefenseTimingWindow]:
    if not decision_dates:
        raise V022RuntimeContractError(
            "ma200_strategy_decisions_empty",
            "MA200 Defense requires at least one Strategy decision",
        )
    with engine.connect() as connection:
        identity = (
            connection.execute(
                text(
                    """
                    SELECT input.dataset_publication_id,input.calendar_version_id,
                           security.legacy_asset_id
                      FROM defense.v022_compiled_defense_execution_data_input input
                      JOIN catalog.security security
                        ON security.security_id=(input.security_ids->>0)::uuid
                     WHERE input.compiled_defense_execution_context_id=:context
                       AND input.input_role='timing_reference'
                    """
                ),
                {"context": compiled_defense_execution_context_id},
            )
            .mappings()
            .one_or_none()
        )
        if (
            identity is None
            or identity["calendar_version_id"] is None
            or identity["legacy_asset_id"] is None
        ):
            raise V022RuntimeContractError(
                "ma200_timing_reference_missing",
                "Defense Context lacks its exact SPY Dataset and Calendar binding",
            )
        rows = (
            connection.execute(
                text(
                    """
                    SELECT session.session_date,session.close_at_utc,bar.adj_close
                      FROM catalog.calendar_session session
                      LEFT JOIN data.daily_bar bar
                        ON bar.dataset_publication_id=:dataset
                       AND bar.asset_id=:asset
                       AND bar.session_date=session.session_date
                     WHERE session.calendar_version_id=:calendar
                       AND session.session_date<=:end
                     ORDER BY session.session_date
                    """
                ),
                {
                    "dataset": identity["dataset_publication_id"],
                    "asset": identity["legacy_asset_id"],
                    "calendar": identity["calendar_version_id"],
                    "end": max(decision_dates),
                },
            )
            .mappings()
            .all()
        )
    sessions = tuple(row["session_date"] for row in rows)
    observations = {
        row["session_date"]: DefensePriceObservation(
            row["session_date"],
            cast(datetime, row["close_at_utc"]).astimezone(UTC),
            Decimal(row["adj_close"]),
        )
        for row in rows
        if row["adj_close"] is not None
        and cast(datetime, row["close_at_utc"]).utcoffset() is not None
    }
    windows: dict[date, DefenseTimingWindow] = {}
    for decision_date in sorted(set(decision_dates)):
        eligible_sessions = tuple(item for item in sessions if item <= decision_date)
        if len(eligible_sessions) < 200:
            continue
        expected = eligible_sessions[-200:]
        available = tuple(
            observations[item] for item in expected if item in observations
        )
        windows[decision_date] = DefenseTimingWindow(available, expected)
    return windows


def build_merged_target_payload(
    targets: Sequence[StrategyUnitRiskTarget],
    *,
    defense_decisions: Sequence[DefenseDecision] | None,
    allocation_members: tuple[DefenseAllocationMember, ...],
    compiled_strategy_branch_id: uuid.UUID,
    work_execution_fingerprint: str,
) -> CanonicalRuntimePayload:
    decisions_by_date = (
        {item.decision_date: item for item in defense_decisions}
        if defense_decisions is not None
        else {}
    )
    selected_targets = (
        tuple(item for item in targets if item.decision_date in decisions_by_date)
        if defense_decisions is not None
        else tuple(targets)
    )
    if defense_decisions is not None and len(selected_targets) != len(decisions_by_date):
        raise V022RuntimeContractError(
            "defense_strategy_decision_set_mismatch",
            "Defense decisions must map to exact Strategy decision dates",
        )
    merged = tuple(
        merge_sleeves(
            item,
            defense_decision=decisions_by_date.get(item.decision_date),
            allocation_members=allocation_members if defense_decisions is not None else (),
        )
        for item in selected_targets
    )
    return adapt_merged_portfolio_targets(
        merged,
        work_execution_fingerprint=work_execution_fingerprint,
        compiled_strategy_branch_id=compiled_strategy_branch_id,
    )


def build_portfolio_cell_payload(
    targets: tuple[MergedPortfolioTarget, ...],
    *,
    inputs: PortfolioMarketInputs,
    identity: PortfolioExecutionIdentity,
    work_execution_fingerprint: str,
) -> CanonicalRuntimePayload:
    evaluation = evaluate_portfolio_cell(
        PortfolioCellSpec(
            context_fingerprint=identity.evaluation_data_context_fingerprint,
            simulation_end=identity.effective_end,
            cost_bps_per_side=identity.cost_bps_per_side,
            initial_capital=identity.initial_capital_identity_only,
            benchmark_asset_id=identity.benchmark_asset_id,
            benchmark_asset_key=identity.benchmark_asset_key,
            simulation_start=identity.effective_start,
            execution_delay_sessions=identity.execution_delay_sessions,
        ),
        targets=targets,
        bars=inputs.bars,
        reserve_intervals=inputs.reserve_intervals,
        common_sessions=inputs.common_sessions,
        settlements=inputs.settlements,
    )
    pit = PortfolioEvaluationPitEvidence(
        evaluation_input_cutoff_at=inputs.evaluation_input_cutoff_at,
        sessions=tuple(
            PortfolioSessionPitEvidence(session, known_at)
            for session, known_at in inputs.session_input_known_at
        ),
    )
    return adapt_portfolio_cell_result(
        PortfolioRuntimeResultEnvelope.accepted(evaluation),
        identity=identity,
        pit_evidence=pit,
        work_execution_fingerprint=work_execution_fingerprint,
    )


def _validate_portfolio_cohort_mask(
    engine: Engine,
    runtime_contract_id: uuid.UUID,
    targets: tuple[MergedPortfolioTarget, ...],
) -> _CohortPortfolioRequirements:
    """Enforce the exact M106 candidate and execution mask before loading market data."""

    with engine.connect() as connection:
        cohort_range = connection.execute(
            text(
                """
                SELECT cohort.evaluation_start,cohort.evaluation_end
                  FROM experiment.v022_evaluation_cohort_runtime_contract contract
                  JOIN experiment.v022_evaluation_cohort_version cohort
                    ON cohort.evaluation_cohort_version_id=
                       contract.evaluation_cohort_version_id
                 WHERE contract.evaluation_cohort_runtime_contract_id=:contract
                """
            ),
            {"contract": runtime_contract_id},
        ).mappings().one_or_none()
        sessions = tuple(
            cast(date, value)
            for value in connection.execute(
                text(
                    """
                    SELECT session.session_date
                      FROM experiment.v022_evaluation_cohort_runtime_contract contract
                      JOIN experiment.v022_evaluation_cohort_session session
                        ON session.evaluation_cohort_version_id=
                           contract.evaluation_cohort_version_id
                     WHERE contract.evaluation_cohort_runtime_contract_id=:contract
                     ORDER BY session.ordinal
                    """
                ),
                {"contract": runtime_contract_id},
            ).scalars()
        )
        rows = connection.execute(
            text(
                """
                SELECT security_id,effective_start,effective_end,is_selectable,is_tradable,
                       valuation_state
                  FROM experiment.v022_cohort_runtime_mask_interval
                 WHERE evaluation_cohort_runtime_contract_id=:contract
                 ORDER BY security_id,ordinal
                """
            ),
            {"contract": runtime_contract_id},
        ).mappings().all()
        settlement_rows = connection.execute(
            text(
                """
                SELECT instruction.security_id,security.security_key,
                       instruction.event_type,instruction.settlement_session,
                       instruction.legs_document
                  FROM experiment.v022_cohort_settlement_instruction instruction
                  JOIN catalog.security security
                    ON security.security_id=instruction.security_id
                 WHERE instruction.evaluation_cohort_runtime_contract_id=:contract
                 ORDER BY instruction.ordinal
                """
            ),
            {"contract": runtime_contract_id},
        ).mappings().all()
        target_security_ids = tuple(
            sorted(
                {
                    uuid.UUID(cast(str, leg["target_security_id"]))
                    for item in settlement_rows
                    for leg in cast(list[dict[str, object]], item["legs_document"])
                    if leg.get("target_security_id") is not None
                },
                key=str,
            )
        )
        target_keys = (
            {
                cast(uuid.UUID, item["security_id"]): cast(str, item["security_key"])
                for item in connection.execute(
                    text(
                        """
                        SELECT security_id,security_key FROM catalog.security
                         WHERE security_id IN :security_ids
                        """
                    ).bindparams(bindparam("security_ids", expanding=True)),
                    {"security_ids": target_security_ids},
                ).mappings()
            }
            if target_security_ids
            else {}
        )
    if cohort_range is None or not sessions or not rows:
        raise V022RuntimeContractError(
            "cohort_runtime_mask_missing",
            "Portfolio Work requires the exact published M106 runtime mask",
        )
    intervals: dict[uuid.UUID, list[RowMapping]] = defaultdict(list)
    for row in rows:
        intervals[cast(uuid.UUID, row["security_id"])].append(row)
    session_index = {value: ordinal for ordinal, value in enumerate(sessions)}
    settlements = tuple(
        RuntimeSettlementInstruction(
            source_asset_id=cast(uuid.UUID, item["security_id"]),
            source_asset_key=cast(str, item["security_key"]),
            event_type=cast(str, item["event_type"]),
            settlement_session=cast(date, item["settlement_session"]),
            legs=tuple(
                _runtime_settlement_leg(leg, target_keys)
                for leg in cast(list[dict[str, object]], item["legs_document"])
            ),
        )
        for item in settlement_rows
        if cast(date, item["settlement_session"]) >= cohort_range["evaluation_start"]
    )

    def state(asset_id: uuid.UUID, session: date) -> RowMapping | None:
        return next(
            (
                row
                for row in intervals.get(asset_id, ())
                if row["effective_start"] <= session <= row["effective_end"]
            ),
            None,
        )

    positive_by_execution: dict[date, frozenset[uuid.UUID]] = {}
    for target in targets:
        index = session_index.get(target.decision_date)
        if index is None or index + 1 >= len(sessions):
            raise V022RuntimeDataError(
                "cohort_execution_session_missing",
                "A Portfolio decision is outside the frozen Cohort session path",
                details={"decision_date": target.decision_date.isoformat()},
            )
        execution_date = sessions[index + 1]
        positive = frozenset(
            item.asset_id for item in target.net_asset_weights if item.target_weight > 0
        )
        positive_by_execution[execution_date] = positive
        for weight in target.net_asset_weights:
            if weight.target_weight <= 0:
                continue
            decision_state = state(weight.asset_id, target.decision_date)
            execution_state = state(weight.asset_id, execution_date)
            if decision_state is None or decision_state["is_selectable"] is not True:
                raise V022RuntimeDataError(
                    "cohort_target_not_selectable",
                    "A positive Portfolio target is outside the frozen selectable mask",
                    details={
                        "asset_id": str(weight.asset_id),
                        "decision_date": target.decision_date.isoformat(),
                    },
                )
            if execution_state is None or execution_state["is_tradable"] is not True:
                raise V022RuntimeDataError(
                    "cohort_target_not_tradable",
                    "A positive Portfolio target cannot execute under the frozen tradability mask",
                    details={
                        "asset_id": str(weight.asset_id),
                        "execution_date": execution_date.isoformat(),
                    },
                )
    required: dict[uuid.UUID, set[date]] = defaultdict(set)
    carry_forward: dict[uuid.UUID, set[date]] = defaultdict(set)
    current: frozenset[uuid.UUID] = frozenset()
    applied_settlements: list[RuntimeSettlementInstruction] = []
    settlements_by_date: dict[date, list[RuntimeSettlementInstruction]] = defaultdict(list)
    for instruction in settlements:
        settlements_by_date[instruction.settlement_session].append(instruction)
    for session in sessions:
        for instruction in settlements_by_date.get(session, ()):
            if instruction.source_asset_id not in current:
                continue
            applied_settlements.append(instruction)
            transformed = set(current)
            if instruction.event_type != "spinoff":
                transformed.discard(instruction.source_asset_id)
            for leg in instruction.legs:
                if leg.target_asset_id is not None:
                    transformed.add(leg.target_asset_id)
                    required[leg.target_asset_id].add(session)
            current = frozenset(transformed)
        next_target = positive_by_execution.get(session)
        if next_target is not None:
            closing = current.difference(next_target)
            for asset_id in closing:
                execution_state = state(asset_id, session)
                if execution_state is None or execution_state["is_tradable"] is not True:
                    raise V022RuntimeDataError(
                        "cohort_close_not_tradable",
                        "An existing Cohort holding cannot close under the frozen mask",
                        details={
                            "asset_id": str(asset_id),
                            "execution_date": session.isoformat(),
                        },
                    )
            for asset_id in current.union(next_target):
                required[asset_id].add(session)
            current = next_target
        else:
            for asset_id in current:
                required[asset_id].add(session)
                holding_state = state(asset_id, session)
                if (
                    holding_state is not None
                    and holding_state["is_tradable"] is not True
                    and holding_state["valuation_state"]
                    in {"stale_confirmed", "unavailable"}
                ):
                    carry_forward[asset_id].add(session)
    evaluation_sessions = tuple(
        item
        for item in sessions
        if cohort_range["evaluation_start"] <= item <= cohort_range["evaluation_end"]
    )
    if not evaluation_sessions:
        raise V022RuntimeContractError(
            "cohort_evaluation_sessions_missing",
            "Portfolio Work requires the exact frozen Cohort evaluation sessions",
        )
    return _CohortPortfolioRequirements(
        evaluation_sessions,
        tuple(
            (asset_id, tuple(sorted(values)))
            for asset_id, values in sorted(required.items(), key=lambda item: str(item[0]))
        ),
        tuple(
            (asset_id, tuple(sorted(values)))
            for asset_id, values in sorted(
                carry_forward.items(), key=lambda item: str(item[0])
            )
        ),
        tuple(applied_settlements),
    )


def _runtime_settlement_leg(
    document: Mapping[str, object], target_keys: Mapping[uuid.UUID, str]
) -> RuntimeSettlementLeg:
    target_value = document.get("target_security_id")
    target_id = uuid.UUID(cast(str, target_value)) if target_value is not None else None
    if target_id is not None and target_id not in target_keys:
        raise V022RuntimeContractError(
            "cohort_settlement_target_identity_missing",
            "Settlement target Security has no stable runtime identity",
            details={"target_security_id": str(target_id)},
        )
    quantity = document.get("quantity_per_source_share")
    cash = document.get("cash_amount_per_source_share")
    return RuntimeSettlementLeg(
        leg_kind=cast(Any, document["leg_kind"]),
        target_asset_id=target_id,
        target_asset_key=target_keys.get(target_id) if target_id is not None else None,
        quantity_per_source_share=(
            Decimal(cast(str, quantity)) if quantity is not None else None
        ),
        cash_amount_per_source_share=(
            Decimal(cast(str, cash)) if cash is not None else None
        ),
        currency=cast(str | None, document.get("currency")),
    )


def encode_runtime_payload_parquet(payload: CanonicalRuntimePayload) -> bytes:
    validate_canonical_runtime_payload(payload)
    schema = pa.schema(
        [
            pa.field("contract_key", pa.string(), nullable=False),
            pa.field("work_execution_fingerprint", pa.string(), nullable=False),
            pa.field("canonical_document_fingerprint", pa.string(), nullable=False),
            pa.field("row_or_item_count", pa.int64(), nullable=False),
            pa.field("document_json", pa.string(), nullable=False),
        ]
    )
    table = pa.Table.from_pylist(
        [
            {
                "contract_key": payload.contract_key,
                "work_execution_fingerprint": payload.work_execution_fingerprint,
                "canonical_document_fingerprint": payload.canonical_document_fingerprint,
                "row_or_item_count": payload.row_or_item_count,
                "document_json": _json(payload.document),
            }
        ],
        schema=schema,
    )
    buffer = io.BytesIO()
    pq.write_table(
        table,
        buffer,
        compression="zstd",
        use_dictionary=False,
        write_statistics=True,
        data_page_version="1.0",
    )
    return buffer.getvalue()


def decode_runtime_payload_parquet(content: bytes) -> CanonicalRuntimePayload:
    try:
        table = pq.read_table(io.BytesIO(content))
    except Exception as error:
        raise V022RuntimeDataError(
            "typed_runtime_payload_not_parquet", "Typed runtime Payload is unreadable"
        ) from error
    if table.num_rows != 1 or tuple(table.column_names) != (
        "contract_key",
        "work_execution_fingerprint",
        "canonical_document_fingerprint",
        "row_or_item_count",
        "document_json",
    ):
        raise V022RuntimeContractError(
            "typed_runtime_payload_schema_invalid",
            "Typed runtime Payload must contain one canonical document row",
        )
    row = table.to_pylist()[0]
    payload = CanonicalRuntimePayload(
        contract_key=cast(Any, row["contract_key"]),
        output_port_key=cast(Any, row["contract_key"]),
        work_execution_fingerprint=row["work_execution_fingerprint"],
        canonical_document_fingerprint=row["canonical_document_fingerprint"],
        row_or_item_count=row["row_or_item_count"],
        document=json.loads(row["document_json"]),
    )
    validate_canonical_runtime_payload(payload)
    return payload


def decode_strategy_targets(payload: CanonicalRuntimePayload) -> tuple[StrategyUnitRiskTarget, ...]:
    validate_canonical_runtime_payload(payload)
    if payload.contract_key != "strategy_unit_risk_target":
        raise V022RuntimeContractError(
            "strategy_payload_contract_mismatch", "Expected Strategy Target Payload"
        )
    grouped: dict[date, list[Mapping[str, object]]] = defaultdict(list)
    for row in cast(list[Mapping[str, object]], payload.document["rows"]):
        grouped[date.fromisoformat(cast(str, row["decision_date"]))].append(row)
    result = []
    for decision in sorted(grouped):
        rows = grouped[decision]
        positions = tuple(
            UnitRiskPosition(
                uuid.UUID(cast(str, row["asset_id"])),
                cast(str, row["asset_key"]),
                Decimal(cast(str, row["model_score"])),
                cast(int, row["rank"]),
                Decimal(cast(str, row["slot_share"])),
                Decimal(cast(str, row["unit_risk_weight"])),
                cast(bool, row["retained_by_buffer"]),
            )
            for row in rows
        )
        result.append(
            StrategyUnitRiskTarget(
                decision,
                _parse_datetime(cast(str, rows[0]["decision_cutoff_at"])),
                _parse_datetime(cast(str, rows[0]["input_known_at"])),
                len(rows),
                len(rows),
                Decimal(1),
                positions,
            )
        )
    return tuple(result)


def decode_defense_decisions(payload: CanonicalRuntimePayload) -> tuple[DefenseDecision, ...]:
    validate_canonical_runtime_payload(payload)
    if payload.contract_key != "defense_budget_decision":
        raise V022RuntimeContractError(
            "defense_payload_contract_mismatch", "Expected Defense Decision Payload"
        )
    return tuple(
        DefenseDecision(
            date.fromisoformat(row["decision_date"]),
            _parse_datetime(row["decision_cutoff_at"]),
            cast(
                Any,
                "fixed20_budget" if row["indicator_value"] is None else "spy_ma200_tiered_budget",
            ),
            cast(Any, row["regime_key"]),
            row["reason_code"],
            Decimal(row["risk_budget"]),
            Decimal(row["defense_budget"]),
            Decimal(row["indicator_value"]) if row["indicator_value"] is not None else None,
            _parse_datetime(row["input_known_at"]) if row["input_known_at"] is not None else None,
        )
        for row in cast(list[dict[str, Any]], payload.document["rows"])
    )


def decode_merged_targets(payload: CanonicalRuntimePayload) -> tuple[MergedPortfolioTarget, ...]:
    validate_canonical_runtime_payload(payload)
    if payload.contract_key != "merged_portfolio_target":
        raise V022RuntimeContractError(
            "merged_payload_contract_mismatch", "Expected Merged Target Payload"
        )
    document = payload.document
    contributions_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    nets_by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    reserve_by_date = {
        row["decision_date"]: Decimal(row["reserve_target_weight"])
        for row in cast(list[dict[str, Any]], document["reserve_target"])
    }
    for row in cast(list[dict[str, Any]], document["ordered_sleeve_contributions"]):
        contributions_by_date[row["decision_date"]].append(row)
    for row in cast(list[dict[str, Any]], document["ordered_net_asset_targets"]):
        nets_by_date[row["decision_date"]].append(row)
    result = []
    for identity in cast(list[dict[str, Any]], document["decision_identity"]):
        key = identity["decision_date"]
        result.append(
            MergedPortfolioTarget(
                date.fromisoformat(key),
                _parse_datetime(identity["decision_cutoff_at"]),
                _parse_datetime(identity["input_known_at"]),
                Decimal(identity["risk_budget"]),
                Decimal(identity["defense_budget"]),
                tuple(
                    SleeveContribution(
                        cast(Any, row["sleeve_role"]),
                        row["source_ordinal"],
                        uuid.UUID(row["asset_id"]) if row["asset_id"] is not None else None,
                        row["asset_key"],
                        Decimal(row["sleeve_weight"]),
                        Decimal(row["portfolio_weight"]),
                    )
                    for row in contributions_by_date[key]
                ),
                tuple(
                    NetTargetWeight(
                        uuid.UUID(row["asset_id"]),
                        row["asset_key"],
                        Decimal(row["target_weight"]),
                    )
                    for row in nets_by_date[key]
                ),
                reserve_by_date[key],
            )
        )
    return tuple(result)


def _load_typed_work_context(
    engine: Engine,
    *,
    graph_run_id: uuid.UUID,
    claim: ClaimedGraphWork,
    worker_key: str,
) -> _TypedWorkContext:
    kind = cast(TypedWorkKind, claim.work_kind)
    with engine.connect() as connection:
        active = (
            connection.execute(
                text(
                    """
                SELECT run.status AS run_status,consumer.occurrence_kind,
                       consumer.binding_disposition,consumer.released_at,
                       work.status AS work_status,work.work_kind,
                       work.execution_fingerprint,work.lease_owner,
                       work.lease_expires_at,work.lease_expires_at>now() AS lease_active,
                       work.fencing_token,work.cancel_requested_at
                  FROM workspace.v022_graph_work_consumer consumer
                  JOIN workspace.v022_graph_run run
                    ON run.graph_run_id=consumer.graph_run_id
                  JOIN workspace.v022_graph_work_item work
                    ON work.graph_work_item_id=consumer.graph_work_item_id
                 WHERE consumer.graph_run_id=:run AND
                       consumer.graph_work_item_id=:item
                """
                ),
                {"run": graph_run_id, "item": claim.graph_work_item_id},
            )
            .mappings()
            .one_or_none()
        )
        if active is None or not (
            active["run_status"] == "running"
            and active["occurrence_kind"] == kind
            and active["binding_disposition"] == "execute"
            and active["released_at"] is None
            and active["work_status"] == "running"
            and active["work_kind"] == kind
            and active["lease_owner"] == worker_key
            and active["lease_active"] is True
            and active["fencing_token"] == claim.fencing_token
            and active["cancel_requested_at"] is None
        ):
            raise V022RuntimeContractError(
                "typed_work_fence_invalid",
                "Typed Suite Work requires its exact active fenced claim",
            )
        if kind == "strategy_target":
            values, dependencies, spec = _load_strategy_context(
                connection, graph_run_id, claim.graph_work_item_id
            )
        elif kind == "defense_decision":
            values, dependencies, spec = _load_defense_context(connection, claim.graph_work_item_id)
        elif kind == "sleeve_merge":
            values, dependencies, spec = _load_merge_context(connection, claim.graph_work_item_id)
        else:
            values, dependencies, spec = _load_portfolio_context(
                connection, claim.graph_work_item_id
            )
    return _TypedWorkContext(
        graph_run_id,
        claim.graph_work_item_id,
        kind,
        worker_key,
        claim.fencing_token,
        cast(str, active["execution_fingerprint"]),
        cast(uuid.UUID, spec["output_payload_contract_version_id"]),
        cast(uuid.UUID, spec["physical_encoding_version_id"]),
        cast(Mapping[str, object], spec["specification_document"]),
        dependencies,
        values,
    )


def _cohort_decision_dates(
    connection: Connection, *, runtime_contract_id: uuid.UUID
) -> frozenset[date]:
    rows = connection.execute(
        text(
            """
            SELECT session.session_date
              FROM experiment.v022_evaluation_cohort_runtime_contract contract
              JOIN experiment.v022_evaluation_cohort_session session
                ON session.evaluation_cohort_version_id=
                   contract.evaluation_cohort_version_id
             WHERE contract.evaluation_cohort_runtime_contract_id=:contract
               AND session.session_role='evaluation'
               AND session.is_decision_session=true
             ORDER BY session.ordinal
            """
        ),
        {"contract": runtime_contract_id},
    ).scalars().all()
    decision_dates = frozenset(cast(date, item) for item in rows)
    if not decision_dates:
        raise V022RuntimeDataError(
            "cohort_decision_dates_empty",
            "Typed Suite Work requires nonempty frozen Cohort decision dates",
        )
    return decision_dates


def _load_strategy_context(
    connection: Connection, graph_run_id: uuid.UUID, work_item_id: uuid.UUID
) -> tuple[dict[str, object], tuple[DependencyInput, ...], RowMapping]:
    row = (
        connection.execute(
            text(
                """
            SELECT spec.*,plan.catalog_release_id,suite.suite_mode,
                   runtime_contract.evaluation_cohort_runtime_contract_id,
                   runtime_contract.artifact_id AS cohort_runtime_artifact_id,
                   variant.variant_key AS strategy_variant_key,
                   preset.resolved_parameters,
                   strategy_version.artifact_id AS strategy_version_artifact_id,
                   preset.artifact_id AS preset_artifact_id,
                   snapshot.artifact_id AS snapshot_artifact_id,
                   execution_context.artifact_id AS execution_context_artifact_id,
                   execution_context.asset_context_document,
                   aggregation_output.payload_manifest_id AS source_manifest_id,
                   source_manifest.manifest_hash AS source_manifest_hash,
                   source_manifest.artifact_id AS source_manifest_artifact_id,
                   aggregation_version.execution_mode AS source_aggregation_execution_mode
              FROM strategy.v022_strategy_target_work_spec spec
              JOIN experiment.v022_suite_runtime_plan plan
                ON plan.suite_runtime_plan_id=spec.suite_runtime_plan_id
              JOIN experiment.v022_research_suite suite
                ON suite.research_suite_id=plan.research_suite_id
              JOIN experiment.v022_research_suite_evaluation_cohort_binding
                suite_cohort ON suite_cohort.research_suite_id=suite.research_suite_id
              JOIN experiment.v022_evaluation_cohort_runtime_contract runtime_contract
                ON runtime_contract.evaluation_cohort_version_id=
                   suite_cohort.evaluation_cohort_version_id
              JOIN strategy.v022_compiled_strategy_branch branch
                ON branch.compiled_strategy_branch_id=spec.compiled_strategy_branch_id
              JOIN strategy.v022_strategy_version strategy_version
                ON strategy_version.strategy_version_id=branch.strategy_version_id
              JOIN strategy.v022_strategy_variant variant
                ON variant.strategy_variant_id=strategy_version.strategy_variant_id
              JOIN strategy.v022_compiled_strategy_branch_preset_binding preset_binding
                ON preset_binding.compiled_strategy_branch_id=
                   spec.compiled_strategy_branch_id
              JOIN strategy.v022_strategy_parameter_preset_version preset
                ON preset.strategy_parameter_preset_version_id=
                   preset_binding.strategy_parameter_preset_version_id
              JOIN experiment.v022_research_configuration_snapshot snapshot
                ON snapshot.configuration_snapshot_id=spec.configuration_snapshot_id
              JOIN workspace.v022_compiled_execution_data_context execution_context
                ON execution_context.compiled_execution_data_context_id=
                   spec.compiled_execution_data_context_id
              JOIN aggregation.graph_run_aggregation_binding aggregation_binding
                ON aggregation_binding.graph_run_id=plan.graph_run_id AND
                   aggregation_binding.graph_work_item_id=
                   spec.source_aggregation_work_item_id
              JOIN aggregation.aggregation_run_output aggregation_output
                ON aggregation_output.aggregation_run_id=
                   aggregation_binding.aggregation_run_id
              JOIN aggregation.aggregation_run aggregation_run
                ON aggregation_run.aggregation_run_id=
                   aggregation_binding.aggregation_run_id
              JOIN aggregation.aggregation_version aggregation_version
                ON aggregation_version.aggregation_version_id=
                   aggregation_run.aggregation_version_id
              JOIN data.payload_manifest source_manifest
                ON source_manifest.payload_manifest_id=
                   aggregation_output.payload_manifest_id
             WHERE spec.graph_work_item_id=:work AND plan.graph_run_id=:run
            """
            ),
            {"work": work_item_id, "run": graph_run_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V022RuntimeContractError(
            "strategy_work_spec_missing", "Strategy Work lacks its exact frozen Spec"
        )
    decision_dates = _cohort_decision_dates(
        connection,
        runtime_contract_id=cast(
            uuid.UUID, row["evaluation_cohort_runtime_contract_id"]
        ),
    )
    asset_rows = tuple(
        {
            "security_id": uuid.UUID(item["security_id"]),
            "security_key": item["security_key"],
        }
        for item in row["asset_context_document"]["members"]
    )
    selectable_rows = connection.execute(
        text(
            """
            WITH ordered_sessions AS (
              SELECT session.session_date,session.is_decision_session,
                     lead(session.session_date) OVER (ORDER BY session.ordinal)
                       AS execution_session
                FROM experiment.v022_evaluation_cohort_runtime_contract contract
                JOIN experiment.v022_evaluation_cohort_session session
                  ON session.evaluation_cohort_version_id=
                     contract.evaluation_cohort_version_id
               WHERE contract.evaluation_cohort_runtime_contract_id=:contract
                 AND session.session_role='evaluation'
            )
            SELECT session.session_date,decision_mask.security_id
              FROM experiment.v022_evaluation_cohort_runtime_contract contract
              JOIN ordered_sessions session
                ON session.is_decision_session=true
               AND session.execution_session IS NOT NULL
              JOIN experiment.v022_cohort_runtime_mask_interval decision_mask
                ON decision_mask.evaluation_cohort_runtime_contract_id=
                   contract.evaluation_cohort_runtime_contract_id
               AND decision_mask.is_selectable=true
               AND session.session_date BETWEEN decision_mask.effective_start
                                            AND decision_mask.effective_end
              JOIN experiment.v022_cohort_runtime_mask_interval execution_mask
                ON execution_mask.evaluation_cohort_runtime_contract_id=
                   contract.evaluation_cohort_runtime_contract_id
               AND execution_mask.security_id=decision_mask.security_id
               AND execution_mask.is_tradable=true
               AND session.execution_session BETWEEN execution_mask.effective_start
                                                 AND execution_mask.effective_end
             WHERE contract.evaluation_cohort_runtime_contract_id=:contract
             ORDER BY session.session_date,decision_mask.security_id
            """
        ),
        {"contract": row["evaluation_cohort_runtime_contract_id"]},
    ).mappings().all()
    selectable_by_date: dict[date, set[uuid.UUID]] = defaultdict(set)
    for item in selectable_rows:
        selectable_by_date[cast(date, item["session_date"])].add(
            cast(uuid.UUID, item["security_id"])
        )
    if not selectable_by_date:
        raise V022RuntimeDataError(
            "cohort_decision_mask_empty",
            "Strategy Work requires a nonempty M106 decision-session selection mask",
        )
    values: dict[str, object] = {
        "source_manifest_id": row["source_manifest_id"],
        "source_manifest_hash": row["source_manifest_hash"],
        "source_manifest_artifact_id": row["source_manifest_artifact_id"],
        "source_aggregation_execution_mode": row[
            "source_aggregation_execution_mode"
        ],
        "catalog_release_id": row["catalog_release_id"],
        "asset_keys": {item["security_id"]: item["security_key"] for item in asset_rows},
        "strategy_variant_key": row["strategy_variant_key"],
        "resolved_parameters": row["resolved_parameters"],
        "suite_mode": row["suite_mode"],
        "evaluation_cohort_runtime_contract_id": row[
            "evaluation_cohort_runtime_contract_id"
        ],
        "decision_dates": decision_dates,
        "selectable_asset_ids_by_date": {
            key: frozenset(value) for key, value in selectable_by_date.items()
        },
    }
    dependencies = tuple(
        DependencyInput(cast(uuid.UUID, artifact_id), role, ordinal)
        for ordinal, (artifact_id, role) in enumerate(
            (
                (row["source_manifest_artifact_id"], "aggregation_output"),
                (row["strategy_version_artifact_id"], "strategy_version"),
                (row["preset_artifact_id"], "strategy_parameter_preset"),
                (row["snapshot_artifact_id"], "configuration_snapshot"),
                (row["execution_context_artifact_id"], "execution_data_context"),
            )
        )
    )
    return values, dependencies, row


def _load_defense_context(
    connection: Connection, work_item_id: uuid.UUID
) -> tuple[dict[str, object], tuple[DependencyInput, ...], RowMapping]:
    row = (
        connection.execute(
            text(
                """
            SELECT spec.*,strategy_output.artifact_id AS strategy_artifact_id,
                   package.artifact_id AS package_artifact_id,
                   timing.artifact_id AS timing_artifact_id,
                   allocation.artifact_id AS allocation_artifact_id,
                   defense_context.artifact_id AS defense_context_artifact_id,
                   timing_variant.variant_key AS timing_variant_key
              FROM defense.v022_defense_decision_work_spec spec
              JOIN strategy.v022_strategy_target_path strategy_output
                ON strategy_output.graph_work_item_id=spec.source_strategy_work_item_id
              JOIN defense.defense_version package
                ON package.defense_version_id=spec.defense_version_id
              JOIN defense.v022_timing_policy_version timing
                ON timing.timing_policy_version_id=spec.timing_policy_version_id
              JOIN defense.v022_timing_policy_variant timing_variant
                ON timing_variant.timing_policy_variant_id=
                   timing.timing_policy_variant_id
              JOIN defense.v022_allocation_policy_version allocation
                ON allocation.allocation_policy_version_id=
                   spec.allocation_policy_version_id
              JOIN defense.v022_compiled_defense_execution_context defense_context
                ON defense_context.compiled_defense_execution_context_id=
                   spec.compiled_defense_execution_context_id
             WHERE spec.graph_work_item_id=:work
            """
            ),
            {"work": work_item_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V022RuntimeContractError(
            "defense_work_spec_missing", "Defense Work lacks its exact frozen Spec"
        )
    values = {
        "source_strategy_work_item_id": row["source_strategy_work_item_id"],
        "defense_version_id": row["defense_version_id"],
        "timing_policy_version_id": row["timing_policy_version_id"],
        "allocation_policy_version_id": row["allocation_policy_version_id"],
        "timing_variant_key": row["timing_variant_key"],
        "compiled_defense_execution_context_id": row[
            "compiled_defense_execution_context_id"
        ],
    }
    dependencies = tuple(
        DependencyInput(cast(uuid.UUID, artifact_id), role, ordinal)
        for ordinal, (artifact_id, role) in enumerate(
            (
                (row["strategy_artifact_id"], "strategy_target"),
                (row["package_artifact_id"], "defense_package"),
                (row["timing_artifact_id"], "timing_policy"),
                (row["allocation_artifact_id"], "allocation_policy"),
                (row["defense_context_artifact_id"], "defense_execution_context"),
            )
        )
    )
    return values, dependencies, row


def _load_merge_context(
    connection: Connection, work_item_id: uuid.UUID
) -> tuple[dict[str, object], tuple[DependencyInput, ...], RowMapping]:
    row = (
        connection.execute(
            text(
                """
            SELECT spec.*,strategy_output.artifact_id AS strategy_artifact_id,
                   defense_output.artifact_id AS defense_artifact_id
              FROM strategy.v022_sleeve_merge_work_spec spec
              JOIN strategy.v022_strategy_target_path strategy_output
                ON strategy_output.graph_work_item_id=spec.source_strategy_work_item_id
              LEFT JOIN defense.v022_defense_decision_path defense_output
                ON defense_output.graph_work_item_id=spec.source_defense_work_item_id
             WHERE spec.graph_work_item_id=:work
            """
            ),
            {"work": work_item_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V022RuntimeContractError(
            "merge_work_spec_missing", "Sleeve Merge Work lacks its exact frozen Spec"
        )
    members: tuple[DefenseAllocationMember, ...] = ()
    if row["source_defense_work_item_id"] is not None:
        member_rows = (
            connection.execute(
                text(
                    """
                SELECT member.ordinal,member.security_id,member.asset_key,
                       member.component_role,member.sleeve_weight
                  FROM defense.v022_defense_decision_work_spec defense_spec
                  JOIN defense.v022_allocation_policy_member member
                    ON member.allocation_policy_version_id=
                       defense_spec.allocation_policy_version_id
                 WHERE defense_spec.graph_work_item_id=:defense_work
                 ORDER BY member.ordinal
                """
                ),
                {"defense_work": row["source_defense_work_item_id"]},
            )
            .mappings()
            .all()
        )
        members = tuple(
            DefenseAllocationMember(
                None if item["component_role"] == "reserve" else item["security_id"],
                item["asset_key"],
                cast(Any, item["component_role"]),
                item["sleeve_weight"],
                item["ordinal"],
            )
            for item in member_rows
        )
    dependencies = [DependencyInput(row["strategy_artifact_id"], "strategy_target", 0)]
    if row["defense_artifact_id"] is not None:
        dependencies.append(DependencyInput(row["defense_artifact_id"], "defense_decision", 1))
    values: dict[str, object] = {
        "source_strategy_work_item_id": row["source_strategy_work_item_id"],
        "source_defense_work_item_id": row["source_defense_work_item_id"],
        "allocation_members": members,
        "compiled_strategy_branch_id": row["compiled_strategy_branch_id"],
    }
    return values, tuple(dependencies), row


def _load_portfolio_context(
    connection: Connection, work_item_id: uuid.UUID
) -> tuple[dict[str, object], tuple[DependencyInput, ...], RowMapping]:
    row = (
        connection.execute(
            text(
                """
            SELECT spec.*,merge_output.artifact_id AS merge_artifact_id,
                   runtime_contract.evaluation_cohort_runtime_contract_id,
                   evaluation_context.artifact_id AS evaluation_context_artifact_id,
                   evaluation_context.context_fingerprint,
                   evaluation_context.benchmark_asset_id,
                   evaluation_context.benchmark_asset_key,
                   evaluation_context.benchmark_dataset_publication_id,
                   evaluation_context.benchmark_calendar_version_id,
                   evaluation_context.reserve_dataset_publication_id,
                   market_input.dataset_publication_id AS
                     canonical_market_dataset_publication_id,
                   strategy_spec.compiled_execution_data_context_id,
                   snapshot.artifact_id AS snapshot_artifact_id,
                   policy_context.semantic_context_document
              FROM experiment.v022_portfolio_cell_work_spec spec
              JOIN strategy.v022_merged_portfolio_target_path merge_output
                ON merge_output.graph_work_item_id=spec.source_merge_work_item_id
              JOIN experiment.v022_portfolio_evaluation_data_context evaluation_context
                ON evaluation_context.portfolio_evaluation_data_context_id=
                   spec.portfolio_evaluation_data_context_id
              JOIN experiment.v022_research_cell cell
                ON cell.research_cell_id=spec.research_cell_id
              JOIN experiment.v022_suite_runtime_plan plan
                ON plan.suite_runtime_plan_id=spec.suite_runtime_plan_id
              JOIN experiment.v022_research_suite_evaluation_cohort_binding
                suite_cohort ON suite_cohort.research_suite_id=plan.research_suite_id
              JOIN experiment.v022_evaluation_cohort_runtime_contract runtime_contract
                ON runtime_contract.evaluation_cohort_version_id=
                   suite_cohort.evaluation_cohort_version_id
              JOIN strategy.v022_sleeve_merge_work_spec merge_spec
                ON merge_spec.graph_work_item_id=spec.source_merge_work_item_id
              JOIN strategy.v022_strategy_target_work_spec strategy_spec
                ON strategy_spec.graph_work_item_id=
                   merge_spec.source_strategy_work_item_id
              JOIN workspace.v022_compiled_execution_data_input market_input
                ON market_input.compiled_execution_data_context_id=
                   strategy_spec.compiled_execution_data_context_id AND
                   market_input.input_key='canonical_market_bars'
              JOIN experiment.v022_evaluation_matrix_policy_context policy_context
                ON policy_context.evaluation_matrix_policy_id=
                   cell.evaluation_matrix_policy_id AND
                   policy_context.ordinal=cell.evaluation_context_ordinal
              JOIN experiment.v022_research_configuration_snapshot snapshot
                ON snapshot.configuration_snapshot_id=spec.configuration_snapshot_id
             WHERE spec.graph_work_item_id=:work
            """
            ),
            {"work": work_item_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise V022RuntimeContractError(
            "portfolio_work_spec_missing", "Portfolio Cell Work lacks its exact frozen Spec"
        )
    execution = cast(
        Mapping[str, object],
        cast(Mapping[str, object], row["semantic_context_document"])["execution_policy"],
    )
    cost = cast(Mapping[str, object], execution["cost_policy"])
    effective = cast(
        Mapping[str, object],
        cast(Mapping[str, object], row["specification_document"])["effective_range"],
    )
    values: dict[str, object] = {
        "source_merge_work_item_id": row["source_merge_work_item_id"],
        "evaluation_cohort_runtime_contract_id": row[
            "evaluation_cohort_runtime_contract_id"
        ],
        "portfolio_evaluation_data_context_id": row["portfolio_evaluation_data_context_id"],
        "compiled_execution_data_context_id": row[
            "compiled_execution_data_context_id"
        ],
        "compiled_strategy_branch_id": row["compiled_strategy_branch_id"],
        "configuration_snapshot_id": row["configuration_snapshot_id"],
        "evaluation_data_context_fingerprint": row["context_fingerprint"],
        "benchmark_asset_id": row["benchmark_asset_id"],
        "benchmark_asset_key": row["benchmark_asset_key"],
        "benchmark_dataset_publication_id": row["benchmark_dataset_publication_id"],
        "benchmark_calendar_version_id": row["benchmark_calendar_version_id"],
        "reserve_dataset_publication_id": row["reserve_dataset_publication_id"],
        "canonical_market_dataset_publication_id": row["canonical_market_dataset_publication_id"],
        "effective_start": date.fromisoformat(cast(str, effective["start"])),
        "effective_end": date.fromisoformat(cast(str, effective["end"])),
        "cost_policy_key": cost["policy_key"],
        "cost_bps_per_side": Decimal(cast(str, cost["basis_points_per_side"])),
        "execution_delay_sessions": 1,
        "initial_capital": Decimal(cast(str, execution["initial_capital_usd"])),
    }
    dependencies = (
        DependencyInput(row["merge_artifact_id"], "merged_portfolio_target", 0),
        DependencyInput(
            row["evaluation_context_artifact_id"],
            "portfolio_evaluation_data_context",
            1,
        ),
        DependencyInput(row["snapshot_artifact_id"], "configuration_snapshot", 2),
    )
    return values, dependencies, row


def _read_runtime_payload(
    engine: Engine,
    object_root: Path,
    *,
    table: str,
    document_column: str,
    source_work_item_id: uuid.UUID,
) -> CanonicalRuntimePayload:
    allowed = {
        ("strategy.v022_strategy_target_path", "target_document"),
        ("defense.v022_defense_decision_path", "decision_document"),
        ("strategy.v022_merged_portfolio_target_path", "target_document"),
    }
    if (table, document_column) not in allowed:
        raise ValueError("runtime source table is not allowlisted")
    with engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    f"""
                SELECT output.{document_column} AS stored_document,
                       output.work_execution_fingerprint,
                       output.manifest_hash,manifest.partition_count,
                       manifest.materialization_state,
                       output_artifact.status AS output_artifact_status,
                       manifest_artifact.status AS manifest_artifact_status,
                       link.ordinal,partition.byte_size AS partition_byte_size,
                       object.storage_uri,object.object_content_hash,
                       object.byte_size AS object_byte_size,object.object_state,
                       object.verification_status,object.verified_at
                  FROM {table} output
                  JOIN lineage.artifact output_artifact
                    ON output_artifact.artifact_id=output.artifact_id
                  JOIN data.payload_manifest manifest
                    ON manifest.payload_manifest_id=output.payload_manifest_id
                  JOIN lineage.artifact manifest_artifact
                    ON manifest_artifact.artifact_id=manifest.artifact_id
                  JOIN data.payload_manifest_partition link
                    ON link.payload_manifest_id=manifest.payload_manifest_id
                  JOIN data.payload_partition partition
                    ON partition.payload_partition_id=link.payload_partition_id
                  JOIN data.payload_object object
                    ON object.payload_object_id=partition.payload_object_id
                 WHERE output.graph_work_item_id=:work
                 ORDER BY link.ordinal
                """
                ),
                {"work": source_work_item_id},
            )
            .mappings()
            .all()
        )
    if len(rows) != 1:
        raise V022RuntimeContractError(
            "typed_upstream_manifest_incomplete",
            "Typed upstream output requires exactly one materialized partition",
        )
    row = rows[0]
    if not (
        row["partition_count"] == 1
        and row["ordinal"] == 0
        and row["materialization_state"] == "materialized"
        and row["output_artifact_status"] == "published"
        and row["manifest_artifact_status"] == "published"
        and row["object_state"] == "published"
        and row["verification_status"] == "verified"
        and row["verified_at"] is not None
        and row["partition_byte_size"] == row["object_byte_size"]
    ):
        raise V022RuntimeContractError(
            "typed_upstream_manifest_unpublished",
            "Typed upstream Manifest is not a verified published materialization",
        )
    content = _read_object_bytes(object_root, row["storage_uri"])
    if (
        len(content) != row["object_byte_size"]
        or hashlib.sha256(content).hexdigest() != row["object_content_hash"]
    ):
        raise V022RuntimeDataError(
            "typed_upstream_object_hash_mismatch",
            "Typed upstream Object bytes differ from their frozen identity",
        )
    payload = decode_runtime_payload_parquet(content)
    if (
        payload.work_execution_fingerprint != row["work_execution_fingerprint"]
        or payload.document != row["stored_document"]
    ):
        raise V022RuntimeDataError(
            "typed_upstream_projection_mismatch",
            "Typed upstream bytes differ from their database projection",
        )
    return payload


def _prepare_payload(
    object_store: LocalPayloadObjectStore,
    *,
    context: _TypedWorkContext,
    payload: CanonicalRuntimePayload,
) -> _PreparedPayload:
    content = encode_runtime_payload_parquet(payload)
    stored = object_store.publish(content, file_extension="parquet")
    if stored.content_hash != hashlib.sha256(content).hexdigest():
        raise V022RuntimeDataError(
            "typed_output_store_hash_mismatch",
            "Content-addressed store returned a different typed output hash",
        )
    effective = cast(Mapping[str, object], context.specification_document["effective_range"])
    coverage = {"start": effective["start"], "end": effective["end"]}
    partition_fields = {
        "work_execution_fingerprint": context.execution_fingerprint,
        "output_port_key": payload.output_port_key,
    }
    partition_key_hash = sha256_hexdigest(partition_fields)
    partition_key = {
        "fields": partition_fields,
        "partition_key_hash": partition_key_hash,
    }
    descriptor_hash = sha256_hexdigest(
        {
            "object_content_hash": stored.content_hash,
            "payload_contract_version_id": context.output_payload_contract_version_id,
            "physical_encoding_version_id": context.physical_encoding_version_id,
            "partition_key": partition_key,
            "coverage": coverage,
            "row_or_item_count": payload.row_or_item_count,
        }
    )
    object_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird-v022:payload-object:{stored.content_hash}")
    partition_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird-v022:payload-partition:{descriptor_hash}")
    logical = sha256_hexdigest(
        {
            "payload_contract_version_id": context.output_payload_contract_version_id,
            "canonical_document_fingerprint": payload.canonical_document_fingerprint,
            "partition_descriptor_hash": descriptor_hash,
        }
    )
    manifest_hash = sha256_hexdigest(
        {
            "work_execution_fingerprint": context.execution_fingerprint,
            "output_port_key": payload.output_port_key,
            "physical_encoding_version_id": context.physical_encoding_version_id,
            "logical_payload_fingerprint": logical,
            "payload_partition_id": partition_id,
            "partition_descriptor_hash": descriptor_hash,
        }
    )
    return _PreparedPayload(
        stored.content_hash,
        stored.storage_uri,
        stored.byte_size,
        object_id,
        partition_id,
        descriptor_hash,
        uuid.uuid5(uuid.NAMESPACE_URL, f"bird-v022:payload-manifest:{manifest_hash}"),
        logical,
        manifest_hash,
        coverage,
    )


def _publish_typed_output(
    engine: Engine,
    *,
    context: _TypedWorkContext,
    payload: CanonicalRuntimePayload,
    prepared: _PreparedPayload,
) -> PublishedTypedWorkOutput:
    artifact_type = _ARTIFACT_TYPES[context.work_kind]
    semantic = {
        "work_execution_fingerprint": context.execution_fingerprint,
        "work_kind": context.work_kind,
        "output_port_key": payload.output_port_key,
        "payload_contract_version_id": context.output_payload_contract_version_id,
        "logical_payload_fingerprint": prepared.logical_payload_fingerprint,
    }
    content = {
        **semantic,
        "canonical_document_fingerprint": payload.canonical_document_fingerprint,
        "physical_encoding_version_id": context.physical_encoding_version_id,
        "manifest_hash": prepared.manifest_hash,
        "object_content_hash": prepared.content_hash,
    }
    with engine.begin() as connection:
        connection.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": f"v022-typed-output:{context.execution_fingerprint}"},
        )
        _lock_active_claim(connection, context)
        bound = cast(Engine, _BoundConnection(connection))
        output_publication = ArtifactService(bound).publish(
            artifact_type=artifact_type,
            artifact_key=f"{artifact_type}__{context.execution_fingerprint}",
            version_number=1,
            semantic_payload=semantic,
            content_payload=content,
            dependencies=context.dependencies,
            reason=f"publish {context.work_kind} typed runtime output",
        )
        manifest_publication = ArtifactService(bound).publish(
            artifact_type="v022_payload_manifest",
            artifact_key=f"v022_payload_manifest__{prepared.manifest_hash}",
            version_number=1,
            semantic_payload={
                "manifest_hash": prepared.manifest_hash,
                "logical_payload_fingerprint": prepared.logical_payload_fingerprint,
                "producer_artifact_id": output_publication.artifact_id,
                "output_port_key": payload.output_port_key,
            },
            content_payload={
                "partition_descriptor_hash": prepared.partition_descriptor_hash,
                "object_content_hash": prepared.content_hash,
                "coverage_document": prepared.coverage_document,
                "row_or_item_count": payload.row_or_item_count,
            },
            dependencies=(DependencyInput(output_publication.artifact_id, "producer", 0),),
            reason=f"publish {context.work_kind} Payload Manifest",
            draft_writer=lambda draft_connection, manifest_artifact_id: _write_manifest(
                draft_connection,
                manifest_artifact_id,
                producer_artifact_id=output_publication.artifact_id,
                context=context,
                payload=payload,
                prepared=prepared,
            ),
        )
        _insert_typed_projection(
            connection,
            context=context,
            payload=payload,
            prepared=prepared,
            artifact_id=output_publication.artifact_id,
            artifact_semantic_fingerprint=output_publication.semantic_fingerprint,
            manifest_artifact_id=manifest_publication.artifact_id,
        )
        _lock_active_claim(connection, context)
        connection.execute(
            text(
                "SELECT workspace.v022_finish_graph_work("
                ":item,:worker,:fence,'completed',CAST(:details AS jsonb))"
            ),
            {
                "item": context.graph_work_item_id,
                "worker": context.worker_key,
                "fence": context.fencing_token,
                "details": _json(
                    {
                        "work_kind": context.work_kind,
                        "artifact_id": str(output_publication.artifact_id),
                        "payload_manifest_id": str(prepared.payload_manifest_id),
                        "manifest_hash": prepared.manifest_hash,
                    }
                ),
            },
        )
    return PublishedTypedWorkOutput(
        context.graph_work_item_id,
        context.work_kind,
        output_publication.artifact_id,
        prepared.payload_manifest_id,
        manifest_publication.artifact_id,
        prepared.manifest_hash,
        output_publication.reused or manifest_publication.reused,
    )


def _write_manifest(
    connection: Connection,
    manifest_artifact_id: uuid.UUID,
    *,
    producer_artifact_id: uuid.UUID,
    context: _TypedWorkContext,
    payload: CanonicalRuntimePayload,
    prepared: _PreparedPayload,
) -> None:
    now = datetime.now(UTC)
    connection.execute(
        text(
            """
            INSERT INTO data.payload_object (
              payload_object_id,object_content_hash,storage_uri,byte_size,
              object_state,verification_status,verified_at
            ) VALUES (
              :id,:hash,:uri,:bytes,'published','verified',:verified
            ) ON CONFLICT (object_content_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_object_id,
            "hash": prepared.content_hash,
            "uri": prepared.storage_uri,
            "bytes": prepared.byte_size,
            "verified": now,
        },
    )
    stored = (
        connection.execute(
            text(
                "SELECT payload_object_id,storage_uri,byte_size,object_state,"
                "verification_status,verified_at FROM data.payload_object "
                "WHERE object_content_hash=:hash"
            ),
            {"hash": prepared.content_hash},
        )
        .mappings()
        .one()
    )
    if not (
        stored["payload_object_id"] == prepared.payload_object_id
        and stored["storage_uri"] == prepared.storage_uri
        and stored["byte_size"] == prepared.byte_size
        and stored["object_state"] == "published"
        and stored["verification_status"] == "verified"
        and stored["verified_at"] is not None
    ):
        raise V022RuntimeDataError(
            "typed_output_object_conflict", "Typed output Object identity conflicts"
        )
    partition_key = {
        "fields": {
            "work_execution_fingerprint": context.execution_fingerprint,
            "output_port_key": payload.output_port_key,
        },
        "partition_key_hash": sha256_hexdigest(
            {
                "work_execution_fingerprint": context.execution_fingerprint,
                "output_port_key": payload.output_port_key,
            }
        ),
    }
    statistics = {
        "canonical_document_fingerprint": payload.canonical_document_fingerprint,
        "runtime_output_contract": payload.contract_key,
    }
    connection.execute(
        text(
            """
            INSERT INTO data.payload_partition (
              payload_partition_id,payload_object_id,partition_descriptor_hash,
              byte_size,row_or_item_count,partition_key,coverage_document,statistics
            ) VALUES (
              :id,:object,:descriptor,:bytes,:items,CAST(:key AS jsonb),
              CAST(:coverage AS jsonb),CAST(:statistics AS jsonb)
            ) ON CONFLICT (partition_descriptor_hash) DO NOTHING
            """
        ),
        {
            "id": prepared.payload_partition_id,
            "object": prepared.payload_object_id,
            "descriptor": prepared.partition_descriptor_hash,
            "bytes": prepared.byte_size,
            "items": payload.row_or_item_count,
            "key": _json(partition_key),
            "coverage": _json(prepared.coverage_document),
            "statistics": _json(statistics),
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO data.payload_manifest (
              payload_manifest_id,artifact_id,payload_contract_version_id,
              physical_encoding_version_id,producer_artifact_id,
              producer_output_port_key,logical_payload_fingerprint,manifest_hash,
              partition_count,byte_size,row_or_item_count,coverage_document,
              retention_class,materialization_state
            ) VALUES (
              :id,:artifact,:contract,:encoding,:producer,:port,:logical,:hash,
              1,:bytes,:items,CAST(:coverage AS jsonb),'research','materialized'
            )
            """
        ),
        {
            "id": prepared.payload_manifest_id,
            "artifact": manifest_artifact_id,
            "contract": context.output_payload_contract_version_id,
            "encoding": context.physical_encoding_version_id,
            "producer": producer_artifact_id,
            "port": payload.output_port_key,
            "logical": prepared.logical_payload_fingerprint,
            "hash": prepared.manifest_hash,
            "bytes": prepared.byte_size,
            "items": payload.row_or_item_count,
            "coverage": _json(prepared.coverage_document),
        },
    )
    connection.execute(
        text(
            "INSERT INTO data.payload_manifest_partition "
            "(payload_manifest_id,payload_partition_id,ordinal) "
            "VALUES (:manifest,:partition,0)"
        ),
        {
            "manifest": prepared.payload_manifest_id,
            "partition": prepared.payload_partition_id,
        },
    )


def _insert_typed_projection(
    connection: Connection,
    *,
    context: _TypedWorkContext,
    payload: CanonicalRuntimePayload,
    prepared: _PreparedPayload,
    artifact_id: uuid.UUID,
    artifact_semantic_fingerprint: str,
    manifest_artifact_id: uuid.UUID,
) -> None:
    common: dict[str, object] = {
        "id": uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"bird-v022:{context.work_kind}-output:{context.execution_fingerprint}",
        ),
        "artifact": artifact_id,
        "work": context.graph_work_item_id,
        "manifest": prepared.payload_manifest_id,
        "manifest_artifact": manifest_artifact_id,
        "manifest_hash": prepared.manifest_hash,
        "execution": context.execution_fingerprint,
        "logical": prepared.logical_payload_fingerprint,
        "semantic": artifact_semantic_fingerprint,
        "worker": context.worker_key,
        "fence": context.fencing_token,
        "document": _json(payload.document),
    }
    if context.work_kind == "strategy_target":
        common["count"] = len(
            {row["decision_date"] for row in cast(list[dict[str, Any]], payload.document["rows"])}
        )
        connection.execute(
            text(
                """
                INSERT INTO strategy.v022_strategy_target_path (
                  strategy_target_path_id,artifact_id,graph_work_item_id,
                  payload_manifest_id,payload_manifest_artifact_id,manifest_hash,
                  work_execution_fingerprint,logical_payload_fingerprint,
                  output_fingerprint,artifact_semantic_fingerprint,decision_count,
                  target_document,worker_key,fencing_token
                ) VALUES (
                  :id,:artifact,:work,:manifest,:manifest_artifact,:manifest_hash,
                  :execution,:logical,:execution,:semantic,:count,
                  CAST(:document AS jsonb),:worker,:fence
                )
                """
            ),
            common,
        )
    elif context.work_kind == "defense_decision":
        common["count"] = len(cast(list[object], payload.document["rows"]))
        connection.execute(
            text(
                """
                INSERT INTO defense.v022_defense_decision_path (
                  defense_decision_path_id,artifact_id,graph_work_item_id,
                  payload_manifest_id,payload_manifest_artifact_id,manifest_hash,
                  work_execution_fingerprint,logical_payload_fingerprint,
                  output_fingerprint,artifact_semantic_fingerprint,decision_count,
                  decision_document,worker_key,fencing_token
                ) VALUES (
                  :id,:artifact,:work,:manifest,:manifest_artifact,:manifest_hash,
                  :execution,:logical,:execution,:semantic,:count,
                  CAST(:document AS jsonb),:worker,:fence
                )
                """
            ),
            common,
        )
    elif context.work_kind == "sleeve_merge":
        common["count"] = len(cast(list[object], payload.document["decision_identity"]))
        connection.execute(
            text(
                """
                INSERT INTO strategy.v022_merged_portfolio_target_path (
                  merged_portfolio_target_path_id,artifact_id,graph_work_item_id,
                  payload_manifest_id,payload_manifest_artifact_id,manifest_hash,
                  work_execution_fingerprint,logical_payload_fingerprint,
                  output_fingerprint,artifact_semantic_fingerprint,decision_count,
                  target_document,worker_key,fencing_token
                ) VALUES (
                  :id,:artifact,:work,:manifest,:manifest_artifact,:manifest_hash,
                  :execution,:logical,:execution,:semantic,:count,
                  CAST(:document AS jsonb),:worker,:fence
                )
                """
            ),
            common,
        )
    else:
        quality = cast(Mapping[str, object], payload.document["quality"])
        common.update(
            {
                "branch": context.values["compiled_strategy_branch_id"],
                "snapshot": context.values["configuration_snapshot_id"],
                "evaluation": context.values["evaluation_data_context_fingerprint"],
                "outcome": quality["outcome"],
                "quality": quality["quality_status"],
                "start": context.values["effective_start"],
                "end": context.values["effective_end"],
                "metrics": _json(quality["metric_document"]),
            }
        )
        connection.execute(
            text(
                """
                INSERT INTO experiment.v022_portfolio_cell_runtime_result (
                  portfolio_cell_runtime_result_id,artifact_id,graph_work_item_id,
                  payload_manifest_id,payload_manifest_artifact_id,manifest_hash,
                  work_execution_fingerprint,logical_payload_fingerprint,
                  result_fingerprint,artifact_semantic_fingerprint,
                  compiled_strategy_branch_id,configuration_snapshot_id,
                  evaluation_data_context_fingerprint,outcome,quality_status,
                  effective_start,effective_end,metric_document,result_document,
                  worker_key,fencing_token
                ) VALUES (
                  :id,:artifact,:work,:manifest,:manifest_artifact,:manifest_hash,
                  :execution,:logical,:execution,:semantic,:branch,:snapshot,
                  :evaluation,:outcome,:quality,:start,:end,CAST(:metrics AS jsonb),
                  CAST(:document AS jsonb),:worker,:fence
                )
                """
            ),
            common,
        )


def _lock_active_claim(connection: Connection, context: _TypedWorkContext) -> None:
    row = (
        connection.execute(
            text(
                """
            SELECT work.status,work.lease_owner,work.fencing_token,
                   work.lease_expires_at>now() AS lease_active,
                   work.cancel_requested_at,consumer.released_at,
                   run.status AS run_status
              FROM workspace.v022_graph_work_item work
              JOIN workspace.v022_graph_work_consumer consumer
                ON consumer.graph_work_item_id=work.graph_work_item_id AND
                   consumer.graph_run_id=:run
              JOIN workspace.v022_graph_run run ON run.graph_run_id=consumer.graph_run_id
             WHERE work.graph_work_item_id=:work FOR UPDATE OF work
            """
            ),
            {"run": context.graph_run_id, "work": context.graph_work_item_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or not (
        row["status"] == "running"
        and row["lease_owner"] == context.worker_key
        and row["fencing_token"] == context.fencing_token
        and row["lease_active"] is True
        and row["cancel_requested_at"] is None
        and row["released_at"] is None
        and row["run_status"] == "running"
    ):
        raise V022RuntimeContractError(
            "typed_work_fence_invalid", "Typed output cannot publish under a stale claim"
        )


def _read_object_bytes(root: Path, storage_uri: str) -> bytes:
    match = _OBJECT_URI.fullmatch(storage_uri)
    if match is None:
        raise V022RuntimeContractError(
            "typed_payload_object_uri_invalid", "Payload Object URI is not canonical"
        )
    path = (root / "sha256" / f"{match.group(1)}.{match.group(2)}").resolve()
    if path.parent != (root / "sha256").resolve():
        raise V022RuntimeContractError(
            "typed_payload_object_uri_escape", "Payload Object URI escapes its root"
        )
    try:
        return path.read_bytes()
    except OSError as error:
        raise V022RuntimeDataError(
            "typed_payload_object_unreadable", "Payload Object bytes are unavailable"
        ) from error


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise V022RuntimeContractError(
            "typed_runtime_datetime_naive", "Runtime datetime must be timezone-aware"
        )
    return parsed


def _positive_int(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise V022RuntimeContractError(
            "typed_runtime_parameter_invalid", f"{label} must be a positive integer"
        )
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
