from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, cast

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.metrics.types import MetricValue
from style_rotation.v022.defense_runtime import (
    INDICATOR_QUANTUM,
    DefenseDecision,
    MergedPortfolioTarget,
)
from style_rotation.v022.portfolio_runtime import PortfolioCellEvaluation
from style_rotation.v022.runtime_contract import V022RuntimeContractError
from style_rotation.v022.strategy_compat_runtime import StrategyUnitRiskTarget

RuntimeOutputContractKey = Literal[
    "strategy_unit_risk_target",
    "defense_budget_decision",
    "merged_portfolio_target",
    "portfolio_cell_result",
]
PortfolioOutcome = Literal["accepted", "data_quality_failed", "capacity_rejected"]
PortfolioQualityStatus = Literal["passed", "warning", "failed"]

CONTRACT_VERSION = 1
PHYSICAL_ENCODING_KEY = "canonical_parquet"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ZERO = Decimal(0)
ONE = Decimal(1)

_PORTFOLIO_SERIES = (
    ("gross_path", "strategy_gross"),
    ("net_path", "strategy_net"),
    ("benchmark_gross_path", "benchmark_gross"),
    ("benchmark_net_path", "benchmark_net"),
)
_PLAN_SPECIFIC_KEYS = frozenset(
    {
        "research_cell_id",
        "research_suite_id",
        "research_suite_branch_id",
        "suite_runtime_plan_id",
        "portfolio_cell_work_spec_id",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalRuntimePayload:
    """Pure, content-addressed hand-off to the v0.22.2 payload publisher.

    The payload deliberately carries no database identity.  The Work execution
    fingerprint is the global execution identity (E).  This boundary computes a
    canonical document content fingerprint only.  It cannot claim the Payload
    Manifest logical fingerprint (L), which also depends on the pinned Contract
    UUID and encoded partition descriptors and therefore belongs to the publisher.
    """

    contract_key: RuntimeOutputContractKey
    output_port_key: RuntimeOutputContractKey
    work_execution_fingerprint: str
    canonical_document_fingerprint: str
    row_or_item_count: int
    document: dict[str, Any]
    contract_version: int = CONTRACT_VERSION
    physical_encoding_key: str = PHYSICAL_ENCODING_KEY


@dataclass(frozen=True, slots=True)
class PortfolioExecutionIdentity:
    """Reusable Portfolio execution identity with no Suite/Cell binding."""

    compiled_strategy_branch_id: uuid.UUID
    configuration_snapshot_id: uuid.UUID
    evaluation_data_context_fingerprint: str
    effective_start: date
    effective_end: date
    benchmark_asset_id: uuid.UUID
    benchmark_asset_key: str
    cost_policy_key: str
    cost_bps_per_side: Decimal
    execution_delay_sessions: int
    initial_capital_identity_only: Decimal

    def __post_init__(self) -> None:
        _require_sha256(
            self.evaluation_data_context_fingerprint,
            reason_code="portfolio_evaluation_data_context_fingerprint_invalid",
        )
        if (
            not isinstance(self.effective_start, date)
            or isinstance(self.effective_start, datetime)
            or not isinstance(self.effective_end, date)
            or isinstance(self.effective_end, datetime)
            or self.effective_start > self.effective_end
        ):
            raise V022RuntimeContractError(
                "portfolio_effective_interval_invalid",
                "Portfolio execution identity requires an exact ordered date interval",
            )
        if not self.benchmark_asset_key.strip() or not self.cost_policy_key.strip():
            raise V022RuntimeContractError(
                "portfolio_evaluation_identity_blank",
                "Benchmark and cost-policy identity keys must be nonblank",
            )
        if self.execution_delay_sessions < 1:
            raise V022RuntimeContractError(
                "portfolio_execution_delay_invalid",
                "Portfolio execution delay must be at least one session",
            )
        if (
            not self.cost_bps_per_side.is_finite()
            or self.cost_bps_per_side <= ZERO
            or not self.initial_capital_identity_only.is_finite()
            or self.initial_capital_identity_only <= ZERO
        ):
            raise V022RuntimeContractError(
                "portfolio_evaluation_identity_numeric_invalid",
                "Cost and initial-capital identity values must be finite and positive",
            )


@dataclass(frozen=True, slots=True)
class PortfolioSessionPitEvidence:
    session_date: date
    input_known_at: datetime

    def __post_init__(self) -> None:
        _require_aware(
            self.input_known_at,
            reason_code="portfolio_path_input_known_at_naive",
        )


@dataclass(frozen=True, slots=True)
class PortfolioEvaluationPitEvidence:
    """Caller-frozen PIT evidence; no adapter code consults a runtime clock."""

    evaluation_input_cutoff_at: datetime
    sessions: tuple[PortfolioSessionPitEvidence, ...]

    def __post_init__(self) -> None:
        _require_aware(
            self.evaluation_input_cutoff_at,
            reason_code="portfolio_evaluation_input_cutoff_naive",
        )
        dates = tuple(item.session_date for item in self.sessions)
        if dates != tuple(sorted(set(dates))):
            raise V022RuntimeContractError(
                "portfolio_path_pit_order_invalid",
                "Portfolio path PIT sessions must be unique and sorted",
            )
        if any(
            item.input_known_at > self.evaluation_input_cutoff_at for item in self.sessions
        ):
            raise V022RuntimeContractError(
                "portfolio_path_input_after_cutoff",
                "Portfolio path input cannot be known after the frozen evaluation cutoff",
            )


@dataclass(frozen=True, slots=True)
class PortfolioRuntimeResultEnvelope:
    """Accepted evaluation or an explicit terminal non-path result."""

    outcome: PortfolioOutcome
    quality_status: PortfolioQualityStatus
    evaluation: PortfolioCellEvaluation | None
    reason_code: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome == "accepted":
            if self.evaluation is None or self.quality_status not in {"passed", "warning"}:
                raise V022RuntimeContractError(
                    "portfolio_accepted_envelope_invalid",
                    "Accepted Portfolio results require an evaluation and passed/warning quality",
                )
            if self.quality_status == "passed" and (
                self.reason_code is not None or self.details
            ):
                raise V022RuntimeContractError(
                    "portfolio_passed_quality_details_forbidden",
                    "Passed Portfolio quality cannot carry a warning or failure reason",
                )
            if self.quality_status == "warning" and not _nonblank(self.reason_code):
                raise V022RuntimeContractError(
                    "portfolio_warning_reason_missing",
                    "Warning Portfolio quality requires an explicit reason code",
                )
            return
        if (
            self.evaluation is not None
            or self.quality_status != "failed"
            or not _nonblank(self.reason_code)
        ):
            raise V022RuntimeContractError(
                "portfolio_terminal_envelope_invalid",
                "Terminal Portfolio results require failed quality, a reason, and no path",
            )

    @classmethod
    def accepted(
        cls,
        evaluation: PortfolioCellEvaluation,
        *,
        quality_status: Literal["passed", "warning"] = "passed",
        reason_code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> PortfolioRuntimeResultEnvelope:
        return cls(
            outcome="accepted",
            quality_status=quality_status,
            evaluation=evaluation,
            reason_code=reason_code,
            details=details or {},
        )

    @classmethod
    def terminal(
        cls,
        outcome: Literal["data_quality_failed", "capacity_rejected"],
        *,
        reason_code: str,
        details: Mapping[str, Any] | None = None,
    ) -> PortfolioRuntimeResultEnvelope:
        return cls(
            outcome=outcome,
            quality_status="failed",
            evaluation=None,
            reason_code=reason_code,
            details=details or {},
        )


def adapt_strategy_unit_risk_targets(
    targets: tuple[StrategyUnitRiskTarget, ...],
    *,
    work_execution_fingerprint: str,
) -> CanonicalRuntimePayload:
    _require_work_fingerprint(work_execution_fingerprint)
    _require_unique_sorted_decisions(
        tuple(item.decision_date for item in targets),
        reason_code="strategy_output_decisions_invalid",
    )
    rows = [
        {
            "decision_date": target.decision_date.isoformat(),
            "decision_cutoff_at": _datetime_text(target.decision_cutoff_at),
            "input_known_at": _datetime_text(target.input_known_at),
            "asset_id": str(position.asset_id),
            "asset_key": position.asset_key,
            "model_score": _decimal_text(position.model_score),
            "rank": position.rank,
            "slot_share": _decimal_text(position.slot_share),
            "unit_risk_weight": _decimal_text(position.unit_risk_weight),
            "retained_by_buffer": position.retained_by_buffer,
        }
        for target in targets
        for position in target.positions
    ]
    return _payload(
        "strategy_unit_risk_target",
        work_execution_fingerprint=work_execution_fingerprint,
        row_or_item_count=len(rows),
        document={"rows": rows},
    )


def adapt_defense_budget_decisions(
    decisions: tuple[DefenseDecision, ...],
    *,
    work_execution_fingerprint: str,
    defense_version_id: uuid.UUID,
    timing_policy_version_id: uuid.UUID,
    allocation_policy_version_id: uuid.UUID,
) -> CanonicalRuntimePayload:
    _require_work_fingerprint(work_execution_fingerprint)
    _require_unique_sorted_decisions(
        tuple(item.decision_date for item in decisions),
        reason_code="defense_output_decisions_invalid",
    )
    rows = [
        {
            "decision_date": item.decision_date.isoformat(),
            "decision_cutoff_at": _datetime_text(item.decision_cutoff_at),
            # Fixed Defense intentionally publishes no fabricated market-input time.
            "input_known_at": (
                _datetime_text(item.input_known_at)
                if item.input_known_at is not None
                else None
            ),
            "defense_version_id": str(defense_version_id),
            "timing_policy_version_id": str(timing_policy_version_id),
            "allocation_policy_version_id": str(allocation_policy_version_id),
            "regime_key": item.regime_key,
            "reason_code": item.reason_code,
            "indicator_value": (
                _decimal_text(item.indicator_value)
                if item.indicator_value is not None
                else None
            ),
            "risk_budget": _decimal_text(item.risk_budget),
            "defense_budget": _decimal_text(item.defense_budget),
        }
        for item in decisions
    ]
    return _payload(
        "defense_budget_decision",
        work_execution_fingerprint=work_execution_fingerprint,
        row_or_item_count=len(rows),
        document={"rows": rows},
    )


def adapt_merged_portfolio_targets(
    targets: tuple[MergedPortfolioTarget, ...],
    *,
    work_execution_fingerprint: str,
    compiled_strategy_branch_id: uuid.UUID,
) -> CanonicalRuntimePayload:
    _require_work_fingerprint(work_execution_fingerprint)
    _require_unique_sorted_decisions(
        tuple(item.decision_date for item in targets),
        reason_code="merged_output_decisions_invalid",
    )
    decision_identity: list[dict[str, Any]] = []
    contributions: list[dict[str, Any]] = []
    net_targets: list[dict[str, Any]] = []
    reserve_targets: list[dict[str, Any]] = []
    for target in targets:
        decision = target.decision_date.isoformat()
        decision_identity.append(
            {
                "decision_date": decision,
                "decision_cutoff_at": _datetime_text(target.decision_cutoff_at),
                "input_known_at": _datetime_text(target.input_known_at),
                "compiled_strategy_branch_id": str(compiled_strategy_branch_id),
                "risk_budget": _decimal_text(target.risk_budget),
                "defense_budget": _decimal_text(target.defense_budget),
                "reserve_target_weight": _decimal_text(target.reserve_target_weight),
            }
        )
        contributions.extend(
            {
                "decision_date": decision,
                "sleeve_role": item.sleeve,
                "source_ordinal": item.source_ordinal,
                "asset_id": str(item.asset_id) if item.asset_id is not None else None,
                "asset_key": item.asset_key,
                "sleeve_weight": _decimal_text(item.sleeve_weight),
                "portfolio_weight": _decimal_text(item.portfolio_weight),
            }
            for item in target.contributions
        )
        net_targets.extend(
            {
                "decision_date": decision,
                "asset_id": str(item.asset_id),
                "asset_key": item.asset_key,
                "target_weight": _decimal_text(item.target_weight),
            }
            for item in target.net_asset_weights
        )
        reserve_targets.append(
            {
                "decision_date": decision,
                "reserve_target_weight": _decimal_text(target.reserve_target_weight),
            }
        )
    return _payload(
        "merged_portfolio_target",
        work_execution_fingerprint=work_execution_fingerprint,
        row_or_item_count=len(decision_identity),
        document={
            "decision_identity": decision_identity,
            "ordered_sleeve_contributions": contributions,
            "ordered_net_asset_targets": net_targets,
            "reserve_target": reserve_targets,
        },
    )


def adapt_portfolio_cell_result(
    result: PortfolioRuntimeResultEnvelope,
    *,
    identity: PortfolioExecutionIdentity,
    pit_evidence: PortfolioEvaluationPitEvidence,
    work_execution_fingerprint: str,
) -> CanonicalRuntimePayload:
    """Adapt one reusable result without inferring PIT evidence or wall-clock time."""

    _require_work_fingerprint(work_execution_fingerprint)
    execution_identity = {
        "work_execution_fingerprint": work_execution_fingerprint,
        "compiled_strategy_branch_id": str(identity.compiled_strategy_branch_id),
        "configuration_snapshot_id": str(identity.configuration_snapshot_id),
        "evaluation_data_context_fingerprint": (
            identity.evaluation_data_context_fingerprint
        ),
    }
    evaluation_context: dict[str, Any] = {
        "effective_start": identity.effective_start.isoformat(),
        "effective_end": identity.effective_end.isoformat(),
        "evaluation_input_cutoff_at": _datetime_text(
            pit_evidence.evaluation_input_cutoff_at
        ),
        "benchmark_identity": {
            "asset_id": str(identity.benchmark_asset_id),
            "asset_key": identity.benchmark_asset_key,
        },
        "cost_policy_identity": {
            "policy_key": identity.cost_policy_key,
            "basis_points_per_side": _decimal_text(identity.cost_bps_per_side),
        },
        "execution_delay_sessions": identity.execution_delay_sessions,
        "initial_capital_identity_only": _decimal_text(
            identity.initial_capital_identity_only
        ),
    }
    paths: dict[str, list[dict[str, Any]]] = {
        section: [] for section, _series_key in _PORTFOLIO_SERIES
    }
    absolute_metrics: list[dict[str, Any]] = []
    relative_metrics: list[dict[str, Any]] = []
    if result.evaluation is not None:
        evaluation = result.evaluation
        dates = tuple(item.nav_date for item in evaluation.gross.daily_nav)
        evidence_by_date = {
            item.session_date: item.input_known_at for item in pit_evidence.sessions
        }
        if set(evidence_by_date) != set(dates) or len(evidence_by_date) != len(dates):
            raise V022RuntimeContractError(
                "portfolio_path_pit_coverage_mismatch",
                "Caller-frozen PIT evidence must cover every path session exactly",
                details={
                    "path_sessions": [item.isoformat() for item in dates],
                    "evidence_sessions": [item.isoformat() for item in evidence_by_date],
                },
            )
        source_paths: tuple[tuple[str, str, tuple[tuple[date, Decimal], ...]], ...] = (
            (
                "gross_path",
                "strategy_gross",
                tuple((item.nav_date, item.gross_nav) for item in evaluation.gross.daily_nav),
            ),
            (
                "net_path",
                "strategy_net",
                tuple((item.nav_date, item.net_nav) for item in evaluation.net.daily_nav),
            ),
            (
                "benchmark_gross_path",
                "benchmark_gross",
                tuple(
                    (item.nav_date, item.gross_nav)
                    for item in evaluation.benchmark_gross.daily_nav
                ),
            ),
            (
                "benchmark_net_path",
                "benchmark_net",
                tuple(
                    (item.nav_date, item.net_nav)
                    for item in evaluation.benchmark_net.daily_nav
                ),
            ),
        )
        for section, series_key, source in source_paths:
            paths[section] = _normalized_path_rows(
                source,
                series_key=series_key,
                work_execution_fingerprint=work_execution_fingerprint,
                input_known_at_by_date=evidence_by_date,
            )
        absolute_metrics = _metric_rows(evaluation.absolute_metrics)
        relative_metrics = _metric_rows(evaluation.relative_metrics)
    elif pit_evidence.sessions:
        raise V022RuntimeContractError(
            "portfolio_terminal_path_evidence_forbidden",
            "A terminal Portfolio result cannot publish PIT evidence for absent paths",
        )

    metric_document = {
        "absolute_metrics": absolute_metrics,
        "relative_metrics": relative_metrics,
    }
    quality = {
        "outcome": result.outcome,
        "quality_status": result.quality_status,
        "effective_start": evaluation_context["effective_start"],
        "effective_end": evaluation_context["effective_end"],
        "metric_document": metric_document,
        "reason_code": result.reason_code,
        "details": _json_safe(dict(result.details)),
    }
    document = {
        "execution_identity": execution_identity,
        "evaluation_context": evaluation_context,
        **paths,
        "absolute_metrics": absolute_metrics,
        "relative_metrics": relative_metrics,
        "quality": quality,
    }
    return _payload(
        "portfolio_cell_result",
        work_execution_fingerprint=work_execution_fingerprint,
        row_or_item_count=1,
        document=document,
    )


def validate_canonical_runtime_payload(payload: CanonicalRuntimePayload) -> None:
    """Fail closed if a canonical hand-off no longer matches its exact contract."""

    if payload.contract_key != payload.output_port_key:
        raise V022RuntimeContractError(
            "runtime_output_port_contract_mismatch",
            "Runtime output port must equal its pinned v0.22.2 contract key",
        )
    if payload.contract_version != CONTRACT_VERSION:
        raise V022RuntimeContractError(
            "runtime_output_contract_version_invalid",
            "Runtime output contract version must be exactly one",
        )
    if payload.physical_encoding_key != PHYSICAL_ENCODING_KEY:
        raise V022RuntimeContractError(
            "runtime_output_encoding_invalid",
            "Runtime output physical encoding must be canonical_parquet",
        )
    _require_work_fingerprint(payload.work_execution_fingerprint)
    _require_sha256(
        payload.canonical_document_fingerprint,
        reason_code="runtime_output_document_fingerprint_invalid",
    )
    expected = _document_fingerprint(payload.contract_key, payload.document)
    if payload.canonical_document_fingerprint != expected:
        raise V022RuntimeContractError(
            "runtime_output_document_fingerprint_mismatch",
            "Canonical document fingerprint does not match the runtime document",
        )
    if payload.contract_key == "strategy_unit_risk_target":
        actual_count = _validate_strategy_document(payload.document)
    elif payload.contract_key == "defense_budget_decision":
        actual_count = _validate_defense_document(payload.document)
    elif payload.contract_key == "merged_portfolio_target":
        actual_count = _validate_merged_document(payload.document)
    else:
        actual_count = _validate_portfolio_document(
            payload.document,
            work_execution_fingerprint=payload.work_execution_fingerprint,
        )
    if payload.row_or_item_count != actual_count or actual_count < 1:
        raise V022RuntimeContractError(
            "runtime_output_item_count_mismatch",
            "Runtime output row/item count does not match its canonical document",
        )


def _payload(
    contract_key: RuntimeOutputContractKey,
    *,
    work_execution_fingerprint: str,
    row_or_item_count: int,
    document: dict[str, Any],
) -> CanonicalRuntimePayload:
    payload = CanonicalRuntimePayload(
        contract_key=contract_key,
        output_port_key=contract_key,
        work_execution_fingerprint=work_execution_fingerprint,
        canonical_document_fingerprint=_document_fingerprint(contract_key, document),
        row_or_item_count=row_or_item_count,
        document=document,
    )
    validate_canonical_runtime_payload(payload)
    return payload


def _document_fingerprint(
    contract_key: RuntimeOutputContractKey, document: Mapping[str, Any]
) -> str:
    return sha256_hexdigest(
        {
            "contract_key": contract_key,
            "contract_version": CONTRACT_VERSION,
            "document": document,
        }
    )


def _normalized_path_rows(
    source: tuple[tuple[date, Decimal], ...],
    *,
    series_key: str,
    work_execution_fingerprint: str,
    input_known_at_by_date: Mapping[date, datetime],
) -> list[dict[str, Any]]:
    if not source or any(not value.is_finite() or value <= ZERO for _day, value in source):
        raise V022RuntimeContractError(
            "portfolio_path_value_invalid",
            "Accepted Portfolio paths must contain finite positive values",
        )
    base = source[0][1]
    return [
        {
            "session_date": session.isoformat(),
            "input_known_at": _datetime_text(input_known_at_by_date[session]),
            "work_execution_fingerprint": work_execution_fingerprint,
            "series_key": series_key,
            "normalized_value": _decimal_text(value / base),
        }
        for session, value in source
    ]


def _metric_rows(metrics: Mapping[str, MetricValue]) -> list[dict[str, Any]]:
    if not metrics or any(not key.strip() for key in metrics):
        raise V022RuntimeContractError(
            "portfolio_metric_identity_invalid",
            "Portfolio metric mappings must be nonempty with nonblank keys",
        )
    return [
        {
            "metric_key": key,
            "value": _decimal_text(item.value) if item.value is not None else None,
            "reason_code": item.reason_code,
            "observation_count": item.observation_count,
        }
        for key, item in sorted(metrics.items())
    ]


def _validate_strategy_document(document: Mapping[str, Any]) -> int:
    _require_exact_keys(document, {"rows"}, "strategy_output_document_fields_invalid")
    rows = _require_rows(document["rows"], "strategy_output_rows_invalid")
    fields = {
        "decision_date",
        "decision_cutoff_at",
        "input_known_at",
        "asset_id",
        "asset_key",
        "model_score",
        "rank",
        "slot_share",
        "unit_risk_weight",
        "retained_by_buffer",
    }
    seen: set[tuple[str, str]] = set()
    assets_by_decision: dict[str, set[str]] = {}
    weights: dict[str, Decimal] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows:
        _require_exact_keys(row, fields, "strategy_output_row_fields_invalid")
        decision = _date_value(row["decision_date"], "strategy_output_decision_date_invalid")
        cutoff = _datetime_value(
            row["decision_cutoff_at"], "strategy_output_cutoff_invalid"
        )
        known = _datetime_value(row["input_known_at"], "strategy_output_known_at_invalid")
        if cutoff.date() != decision or known > cutoff:
            raise V022RuntimeContractError(
                "strategy_output_pit_invalid",
                "Strategy output Decision and PIT identity do not reconcile",
            )
        asset_id = _uuid_value(row["asset_id"], "strategy_output_asset_id_invalid")
        asset_key = _string_value(row["asset_key"], "strategy_output_asset_key_invalid")
        key = (decision.isoformat(), str(asset_id))
        if key in seen or asset_key in assets_by_decision.setdefault(key[0], set()):
            raise V022RuntimeContractError(
                "strategy_output_asset_duplicate",
                "Strategy output Asset identity must be unique per Decision",
            )
        seen.add(key)
        assets_by_decision[key[0]].add(asset_key)
        _finite_decimal(row["model_score"], "strategy_output_score_invalid")
        slot = _finite_decimal(row["slot_share"], "strategy_output_slot_share_invalid")
        weight = _finite_decimal(
            row["unit_risk_weight"], "strategy_output_weight_invalid"
        )
        if (
            not isinstance(row["rank"], int)
            or isinstance(row["rank"], bool)
            or row["rank"] < 1
            or slot <= ZERO
            or weight <= ZERO
            or not isinstance(row["retained_by_buffer"], bool)
        ):
            raise V022RuntimeContractError(
                "strategy_output_position_invalid",
                "Strategy output position rank, shares, and flags are invalid",
            )
        weights[key[0]] = weights.get(key[0], ZERO) + weight
        order.append((key[0], asset_key, str(asset_id)))
    if order != sorted(order) or any(weight != ONE for weight in weights.values()):
        raise V022RuntimeContractError(
            "strategy_output_order_or_budget_invalid",
            "Strategy output must be canonically ordered and conserve one unit of risk",
        )
    return len(rows)


def _validate_defense_document(document: Mapping[str, Any]) -> int:
    _require_exact_keys(document, {"rows"}, "defense_output_document_fields_invalid")
    rows = _require_rows(document["rows"], "defense_output_rows_invalid")
    fields = {
        "decision_date",
        "decision_cutoff_at",
        "input_known_at",
        "defense_version_id",
        "timing_policy_version_id",
        "allocation_policy_version_id",
        "regime_key",
        "reason_code",
        "indicator_value",
        "risk_budget",
        "defense_budget",
    }
    order: list[tuple[str, str]] = []
    for row in rows:
        _require_exact_keys(row, fields, "defense_output_row_fields_invalid")
        decision = _date_value(row["decision_date"], "defense_output_decision_date_invalid")
        cutoff = _datetime_value(row["decision_cutoff_at"], "defense_output_cutoff_invalid")
        if cutoff.date() != decision:
            raise V022RuntimeContractError(
                "defense_output_cutoff_date_mismatch",
                "Defense output cutoff date must equal its Decision date",
            )
        defense_id = _uuid_value(
            row["defense_version_id"], "defense_output_version_id_invalid"
        )
        _uuid_value(row["timing_policy_version_id"], "defense_output_timing_id_invalid")
        _uuid_value(
            row["allocation_policy_version_id"], "defense_output_allocation_id_invalid"
        )
        known_raw = row["input_known_at"]
        indicator_raw = row["indicator_value"]
        if (known_raw is None) != (indicator_raw is None):
            raise V022RuntimeContractError(
                "defense_output_optional_pit_incomplete",
                "Defense input known_at and indicator must be all-or-none",
            )
        if known_raw is not None:
            known = _datetime_value(known_raw, "defense_output_known_at_invalid")
            if known > cutoff:
                raise V022RuntimeContractError(
                    "defense_output_input_after_cutoff",
                    "Defense output input cannot be known after its cutoff",
                )
            _finite_decimal(indicator_raw, "defense_output_indicator_invalid")
        risk = _finite_decimal(row["risk_budget"], "defense_output_risk_budget_invalid")
        defense = _finite_decimal(
            row["defense_budget"], "defense_output_defense_budget_invalid"
        )
        if (
            risk < ZERO
            or defense < ZERO
            or risk + defense != ONE
            or not _nonblank(row["regime_key"])
            or not _nonblank(row["reason_code"])
        ):
            raise V022RuntimeContractError(
                "defense_output_decision_invalid",
                "Defense output regime and budgets are invalid",
            )
        order.append((decision.isoformat(), str(defense_id)))
    if order != sorted(set(order)):
        raise V022RuntimeContractError(
            "defense_output_order_invalid",
            "Defense output Decisions must be unique and canonically ordered",
        )
    return len(rows)


def _validate_merged_document(document: Mapping[str, Any]) -> int:
    sections = {
        "decision_identity",
        "ordered_sleeve_contributions",
        "ordered_net_asset_targets",
        "reserve_target",
    }
    _require_exact_keys(document, sections, "merged_output_document_fields_invalid")
    identities = _require_rows(
        document["decision_identity"], "merged_output_identity_rows_invalid"
    )
    contributions = _require_rows(
        document["ordered_sleeve_contributions"],
        "merged_output_contribution_rows_invalid",
        allow_empty=True,
    )
    nets = _require_rows(
        document["ordered_net_asset_targets"],
        "merged_output_net_rows_invalid",
        allow_empty=True,
    )
    reserves = _require_rows(
        document["reserve_target"], "merged_output_reserve_rows_invalid"
    )
    identity_fields = {
        "decision_date",
        "decision_cutoff_at",
        "input_known_at",
        "compiled_strategy_branch_id",
        "risk_budget",
        "defense_budget",
        "reserve_target_weight",
    }
    budget_by_date: dict[str, tuple[Decimal, Decimal, Decimal]] = {}
    branch_by_date: dict[str, str] = {}
    identity_order: list[str] = []
    for row in identities:
        _require_exact_keys(row, identity_fields, "merged_output_identity_fields_invalid")
        decision_date = _date_value(
            row["decision_date"], "merged_output_decision_date_invalid"
        )
        cutoff = _datetime_value(row["decision_cutoff_at"], "merged_output_cutoff_invalid")
        known = _datetime_value(row["input_known_at"], "merged_output_known_at_invalid")
        if cutoff.date() != decision_date or known > cutoff:
            raise V022RuntimeContractError(
                "merged_output_pit_invalid",
                "Merged output Decision and PIT identity do not reconcile",
            )
        key = decision_date.isoformat()
        if key in budget_by_date:
            raise V022RuntimeContractError(
                "merged_output_decision_duplicate",
                "Merged output Decisions must be unique",
            )
        branch = str(
            _uuid_value(
                row["compiled_strategy_branch_id"], "merged_output_branch_id_invalid"
            )
        )
        risk = _finite_decimal(row["risk_budget"], "merged_output_risk_budget_invalid")
        defense = _finite_decimal(
            row["defense_budget"], "merged_output_defense_budget_invalid"
        )
        reserve = _finite_decimal(
            row["reserve_target_weight"], "merged_output_reserve_weight_invalid"
        )
        if min(risk, defense, reserve) < ZERO or risk + defense != ONE:
            raise V022RuntimeContractError(
                "merged_output_budget_invalid", "Merged output budgets are invalid"
            )
        budget_by_date[key] = (risk, defense, reserve)
        branch_by_date[key] = branch
        identity_order.append(key)
    if identity_order != sorted(identity_order) or len(set(branch_by_date.values())) != 1:
        raise V022RuntimeContractError(
            "merged_output_identity_order_invalid",
            "Merged output requires sorted Decisions for one compiled Strategy branch",
        )
    contribution_fields = {
        "decision_date",
        "sleeve_role",
        "source_ordinal",
        "asset_id",
        "asset_key",
        "sleeve_weight",
        "portfolio_weight",
    }
    sleeve_order = {"risk": 0, "defense": 1, "reserve": 2}
    contribution_order: list[tuple[str, int, int, str]] = []
    contribution_totals: dict[str, dict[str, Decimal]] = {}
    contribution_assets: dict[str, dict[str, Decimal]] = {}
    for row in contributions:
        _require_exact_keys(
            row, contribution_fields, "merged_output_contribution_fields_invalid"
        )
        contribution_decision = _date_text(
            row["decision_date"], "merged_output_contribution_date_invalid"
        )
        if contribution_decision not in budget_by_date:
            raise V022RuntimeContractError(
                "merged_output_contribution_orphan",
                "Merged contribution has no Decision identity",
            )
        sleeve = _string_value(row["sleeve_role"], "merged_output_sleeve_invalid")
        if sleeve not in sleeve_order:
            raise V022RuntimeContractError(
                "merged_output_sleeve_invalid", "Merged contribution Sleeve is invalid"
            )
        ordinal = row["source_ordinal"]
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise V022RuntimeContractError(
                "merged_output_source_ordinal_invalid",
                "Merged contribution source ordinal must be nonnegative",
            )
        asset_key = _string_value(row["asset_key"], "merged_output_asset_key_invalid")
        asset_id_raw = row["asset_id"]
        if sleeve == "reserve":
            if asset_id_raw is not None:
                raise V022RuntimeContractError(
                    "merged_output_reserve_asset_invalid",
                    "Reserve contribution cannot carry an Asset id",
                )
        else:
            _uuid_value(asset_id_raw, "merged_output_asset_id_invalid")
        sleeve_weight = _finite_decimal(
            row["sleeve_weight"], "merged_output_sleeve_weight_invalid"
        )
        portfolio_weight = _finite_decimal(
            row["portfolio_weight"], "merged_output_portfolio_weight_invalid"
        )
        if sleeve_weight <= ZERO or portfolio_weight < ZERO:
            raise V022RuntimeContractError(
                "merged_output_contribution_weight_invalid",
                "Merged contribution weights must be finite and long-only",
            )
        totals = contribution_totals.setdefault(
            contribution_decision, {"risk": ZERO, "defense": ZERO, "reserve": ZERO}
        )
        totals[sleeve] += portfolio_weight
        if asset_id_raw is not None:
            asset_id = str(asset_id_raw)
            asset_weights = contribution_assets.setdefault(contribution_decision, {})
            asset_weights[asset_id] = asset_weights.get(asset_id, ZERO) + portfolio_weight
        contribution_order.append(
            (contribution_decision, sleeve_order[sleeve], ordinal, asset_key)
        )
    if contribution_order != sorted(contribution_order):
        raise V022RuntimeContractError(
            "merged_output_contribution_order_invalid",
            "Merged contributions are not in canonical order",
        )
    net_fields = {"decision_date", "asset_id", "asset_key", "target_weight"}
    net_by_date: dict[str, dict[str, Decimal]] = {}
    net_order: list[tuple[str, str, str]] = []
    for row in nets:
        _require_exact_keys(row, net_fields, "merged_output_net_fields_invalid")
        net_decision = _date_text(row["decision_date"], "merged_output_net_date_invalid")
        if net_decision not in budget_by_date:
            raise V022RuntimeContractError(
                "merged_output_net_orphan", "Merged net target has no Decision identity"
            )
        asset_id = str(_uuid_value(row["asset_id"], "merged_output_net_asset_id_invalid"))
        asset_key = _string_value(row["asset_key"], "merged_output_net_asset_key_invalid")
        weight = _finite_decimal(row["target_weight"], "merged_output_net_weight_invalid")
        if weight <= ZERO or asset_id in net_by_date.setdefault(net_decision, {}):
            raise V022RuntimeContractError(
                "merged_output_net_target_invalid",
                "Merged net target must be unique, finite, and positive",
            )
        net_by_date[net_decision][asset_id] = weight
        net_order.append((net_decision, asset_key, asset_id))
    if net_order != sorted(net_order):
        raise V022RuntimeContractError(
            "merged_output_net_order_invalid", "Merged net targets are not canonically ordered"
        )
    reserve_fields = {"decision_date", "reserve_target_weight"}
    reserve_by_date: dict[str, Decimal] = {}
    for row in reserves:
        _require_exact_keys(row, reserve_fields, "merged_output_reserve_fields_invalid")
        reserve_decision = _date_text(
            row["decision_date"], "merged_output_reserve_date_invalid"
        )
        if reserve_decision not in budget_by_date or reserve_decision in reserve_by_date:
            raise V022RuntimeContractError(
                "merged_output_reserve_identity_invalid",
                "Merged reserve target requires one row per Decision",
            )
        reserve_by_date[reserve_decision] = _finite_decimal(
            row["reserve_target_weight"], "merged_output_reserve_weight_invalid"
        )
    if list(reserve_by_date) != identity_order:
        raise V022RuntimeContractError(
            "merged_output_reserve_order_invalid",
            "Merged reserve targets must follow every Decision identity",
        )
    for decision_key, (risk, defense, reserve) in budget_by_date.items():
        totals = contribution_totals.get(
            decision_key, {"risk": ZERO, "defense": ZERO, "reserve": ZERO}
        )
        positive_contribution_assets = {
            asset_id: weight
            for asset_id, weight in contribution_assets.get(decision_key, {}).items()
            if weight > ZERO
        }
        if (
            totals["risk"] != risk
            or totals["defense"] + totals["reserve"] != defense
            or totals["reserve"] != reserve
            or reserve_by_date.get(decision_key) != reserve
            or net_by_date.get(decision_key, {}) != positive_contribution_assets
            or abs(sum(net_by_date.get(decision_key, {}).values(), reserve) - ONE)
            > INDICATOR_QUANTUM
        ):
            raise V022RuntimeContractError(
                "merged_output_attribution_mismatch",
                "Merged output attribution, net targets, and budgets do not reconcile",
            )
    return len(identities)


def _validate_portfolio_document(
    document: Mapping[str, Any], *, work_execution_fingerprint: str
) -> int:
    sections = {
        "execution_identity",
        "evaluation_context",
        "gross_path",
        "net_path",
        "benchmark_gross_path",
        "benchmark_net_path",
        "absolute_metrics",
        "relative_metrics",
        "quality",
    }
    _require_exact_keys(document, sections, "portfolio_output_document_fields_invalid")
    _reject_plan_specific_identity(document)
    execution = _mapping_value(
        document["execution_identity"], "portfolio_execution_identity_invalid"
    )
    _require_exact_keys(
        execution,
        {
            "work_execution_fingerprint",
            "compiled_strategy_branch_id",
            "configuration_snapshot_id",
            "evaluation_data_context_fingerprint",
        },
        "portfolio_execution_identity_fields_invalid",
    )
    if execution["work_execution_fingerprint"] != work_execution_fingerprint:
        raise V022RuntimeContractError(
            "portfolio_execution_fingerprint_mismatch",
            "Portfolio document must carry its exact global Work execution identity",
        )
    _uuid_value(execution["compiled_strategy_branch_id"], "portfolio_branch_id_invalid")
    _uuid_value(execution["configuration_snapshot_id"], "portfolio_snapshot_id_invalid")
    _require_sha256(
        _string_value(
            execution["evaluation_data_context_fingerprint"],
            "portfolio_evaluation_data_context_fingerprint_invalid",
        ),
        reason_code="portfolio_evaluation_data_context_fingerprint_invalid",
    )
    context = _mapping_value(document["evaluation_context"], "portfolio_context_invalid")
    _require_exact_keys(
        context,
        {
            "effective_start",
            "effective_end",
            "evaluation_input_cutoff_at",
            "benchmark_identity",
            "cost_policy_identity",
            "execution_delay_sessions",
            "initial_capital_identity_only",
        },
        "portfolio_context_fields_invalid",
    )
    benchmark = _mapping_value(
        context["benchmark_identity"], "portfolio_benchmark_identity_invalid"
    )
    _require_exact_keys(
        benchmark, {"asset_id", "asset_key"}, "portfolio_benchmark_identity_fields_invalid"
    )
    _uuid_value(benchmark["asset_id"], "portfolio_benchmark_asset_id_invalid")
    _string_value(benchmark["asset_key"], "portfolio_benchmark_asset_key_invalid")
    cost = _mapping_value(context["cost_policy_identity"], "portfolio_cost_identity_invalid")
    _require_exact_keys(
        cost, {"policy_key", "basis_points_per_side"}, "portfolio_cost_identity_fields_invalid"
    )
    _string_value(cost["policy_key"], "portfolio_cost_policy_key_invalid")
    if _finite_decimal(cost["basis_points_per_side"], "portfolio_cost_bps_invalid") <= ZERO:
        raise V022RuntimeContractError(
            "portfolio_cost_bps_invalid", "Portfolio cost basis points must be positive"
        )
    delay = context["execution_delay_sessions"]
    if not isinstance(delay, int) or isinstance(delay, bool) or delay < 1:
        raise V022RuntimeContractError(
            "portfolio_execution_delay_invalid", "Portfolio execution delay is invalid"
        )
    if (
        _finite_decimal(
            context["initial_capital_identity_only"], "portfolio_initial_capital_invalid"
        )
        <= ZERO
    ):
        raise V022RuntimeContractError(
            "portfolio_initial_capital_invalid", "Portfolio initial capital must be positive"
        )
    evaluation_input_cutoff = _datetime_value(
        context["evaluation_input_cutoff_at"],
        "portfolio_evaluation_input_cutoff_invalid",
    )
    quality = _mapping_value(document["quality"], "portfolio_quality_invalid")
    _require_exact_keys(
        quality,
        {
            "outcome",
            "quality_status",
            "effective_start",
            "effective_end",
            "metric_document",
            "reason_code",
            "details",
        },
        "portfolio_quality_fields_invalid",
    )
    outcome = quality["outcome"]
    status = quality["quality_status"]
    if outcome not in {"accepted", "data_quality_failed", "capacity_rejected"}:
        raise V022RuntimeContractError(
            "portfolio_outcome_invalid", "Portfolio terminal outcome is invalid"
        )
    if status not in {"passed", "warning", "failed"}:
        raise V022RuntimeContractError(
            "portfolio_quality_status_invalid", "Portfolio quality status is invalid"
        )
    absolute = _validate_metric_rows(document["absolute_metrics"], "absolute")
    relative = _validate_metric_rows(document["relative_metrics"], "relative")
    metric_document = _mapping_value(
        quality["metric_document"], "portfolio_metric_document_invalid"
    )
    if metric_document != {
        "absolute_metrics": document["absolute_metrics"],
        "relative_metrics": document["relative_metrics"],
    }:
        raise V022RuntimeContractError(
            "portfolio_metric_document_mismatch",
            "Portfolio metric document must equal its typed metric sections",
        )
    path_dates: list[tuple[str, ...]] = []
    input_known_at_by_session: dict[str, str] = {}
    for section, series_key in _PORTFOLIO_SERIES:
        rows = _require_rows(
            document[section], f"portfolio_{section}_invalid", allow_empty=True
        )
        dates: list[str] = []
        for row in rows:
            _require_exact_keys(
                row,
                {
                    "session_date",
                    "input_known_at",
                    "work_execution_fingerprint",
                    "series_key",
                    "normalized_value",
                },
                "portfolio_path_fields_invalid",
            )
            session = _date_text(row["session_date"], "portfolio_path_date_invalid")
            known = _datetime_value(
                row["input_known_at"], "portfolio_path_known_at_invalid"
            )
            if known > evaluation_input_cutoff:
                raise V022RuntimeContractError(
                    "portfolio_path_input_after_cutoff",
                    "Portfolio path input cannot be known after its evaluation cutoff",
                )
            known_text = _datetime_text(known)
            prior_known = input_known_at_by_session.setdefault(session, known_text)
            if prior_known != known_text:
                raise V022RuntimeContractError(
                    "portfolio_path_pit_identity_mismatch",
                    "All Portfolio series must use the same frozen PIT evidence per session",
                )
            if row["work_execution_fingerprint"] != work_execution_fingerprint:
                raise V022RuntimeContractError(
                    "portfolio_path_execution_fingerprint_mismatch",
                    "Every Portfolio path row must carry the global Work identity",
                )
            if row["series_key"] != series_key:
                raise V022RuntimeContractError(
                    "portfolio_path_series_key_invalid",
                    "Portfolio path series key does not match its typed section",
                )
            value = _finite_decimal(
                row["normalized_value"], "portfolio_path_value_invalid"
            )
            if value <= ZERO:
                raise V022RuntimeContractError(
                    "portfolio_path_value_invalid",
                    "Portfolio normalized paths must remain positive",
                )
            dates.append(session)
        if dates != sorted(set(dates)):
            raise V022RuntimeContractError(
                "portfolio_path_order_invalid",
                "Portfolio path sessions must be unique and sorted",
            )
        if rows and _finite_decimal(
            rows[0]["normalized_value"], "portfolio_path_value_invalid"
        ) != ONE:
            raise V022RuntimeContractError(
                "portfolio_path_initial_value_invalid",
                "Every normalized Portfolio path must begin at exactly one",
            )
        path_dates.append(tuple(dates))
    effective_start = _date_text(
        context["effective_start"], "portfolio_effective_start_invalid"
    )
    effective_end = _date_text(
        context["effective_end"], "portfolio_effective_end_invalid"
    )
    if effective_start > effective_end:
        raise V022RuntimeContractError(
            "portfolio_effective_interval_invalid",
            "Portfolio frozen effective interval must be ordered",
        )
    if quality["effective_start"] != effective_start or quality["effective_end"] != effective_end:
        raise V022RuntimeContractError(
            "portfolio_effective_interval_mismatch",
            "Portfolio context and quality envelope intervals must match",
        )
    if outcome == "accepted":
        if status not in {"passed", "warning"} or not absolute or not relative:
            raise V022RuntimeContractError(
                "portfolio_accepted_quality_invalid",
                "Accepted Portfolio result requires four paths and typed metrics",
            )
        if not path_dates[0] or any(dates != path_dates[0] for dates in path_dates[1:]):
            raise V022RuntimeContractError(
                "portfolio_path_alignment_invalid",
                "Accepted Portfolio paths must have the same nonempty sessions",
            )
        if (
            path_dates[0][0] < effective_start
            or effective_end != path_dates[0][-1]
            or (status == "passed" and quality["reason_code"] is not None)
            or (status == "warning" and not _nonblank(quality["reason_code"]))
        ):
            raise V022RuntimeContractError(
                "portfolio_accepted_envelope_invalid",
                "Accepted Portfolio interval and quality envelope do not reconcile",
            )
    elif (
        status != "failed"
        or any(path_dates)
        or absolute
        or relative
        or not _nonblank(quality["reason_code"])
    ):
        raise V022RuntimeContractError(
            "portfolio_terminal_envelope_invalid",
            "Terminal Portfolio results retain the frozen range and require no paths/metrics",
        )
    return 1


def _validate_metric_rows(value: Any, kind: str) -> list[dict[str, Any]]:
    rows = _require_rows(value, f"portfolio_{kind}_metrics_invalid", allow_empty=True)
    keys: list[str] = []
    for row in rows:
        _require_exact_keys(
            row,
            {"metric_key", "value", "reason_code", "observation_count"},
            f"portfolio_{kind}_metric_fields_invalid",
        )
        key = _string_value(row["metric_key"], f"portfolio_{kind}_metric_key_invalid")
        count = row["observation_count"]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise V022RuntimeContractError(
                f"portfolio_{kind}_metric_count_invalid",
                "Portfolio metric observation count must be nonnegative",
            )
        if (row["value"] is None) == (row["reason_code"] is None):
            raise V022RuntimeContractError(
                f"portfolio_{kind}_metric_value_invalid",
                "Portfolio metric must carry exactly one of value or reason",
            )
        if row["value"] is not None:
            _finite_decimal(row["value"], f"portfolio_{kind}_metric_value_invalid")
        elif not _nonblank(row["reason_code"]):
            raise V022RuntimeContractError(
                f"portfolio_{kind}_metric_reason_invalid",
                "Undefined Portfolio metric requires a nonblank reason",
            )
        keys.append(key)
    if keys != sorted(set(keys)):
        raise V022RuntimeContractError(
            f"portfolio_{kind}_metric_order_invalid",
            "Portfolio metrics must be unique and sorted by key",
        )
    return rows


def _require_unique_sorted_decisions(
    decisions: tuple[date, ...], *, reason_code: str
) -> None:
    if not decisions or decisions != tuple(sorted(set(decisions))):
        raise V022RuntimeContractError(
            reason_code, "Runtime output Decisions must be nonempty, unique, and sorted"
        )


def _require_work_fingerprint(value: str) -> None:
    _require_sha256(value, reason_code="runtime_work_execution_fingerprint_invalid")


def _require_sha256(value: str, *, reason_code: str) -> None:
    if SHA256_PATTERN.fullmatch(value) is None:
        raise V022RuntimeContractError(
            reason_code, "Runtime identity must be a lowercase SHA-256 fingerprint"
        )


def _require_aware(value: datetime, *, reason_code: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise V022RuntimeContractError(
            reason_code, "Runtime PIT timestamps must be timezone-aware"
        )


def _datetime_text(value: datetime) -> str:
    _require_aware(value, reason_code="runtime_output_datetime_naive")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise V022RuntimeContractError(
            "runtime_output_decimal_nonfinite",
            "Canonical runtime decimals must remain finite",
        )
    if value.is_zero():
        return "0"
    normalized = format(value.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        converted: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip() or key in converted:
                raise V022RuntimeContractError(
                    "runtime_output_detail_key_invalid",
                    "Quality detail keys must be unique nonblank strings",
                )
            converted[key] = _json_safe(item)
        return {key: converted[key] for key in sorted(converted)}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise V022RuntimeContractError(
        "runtime_output_detail_value_invalid",
        f"Unsupported terminal quality detail value: {type(value).__name__}",
    )


def _require_exact_keys(
    value: Mapping[str, Any], expected: set[str], reason_code: str
) -> None:
    if set(value) != expected:
        raise V022RuntimeContractError(
            reason_code,
            "Runtime output fields do not match the exact v0.22.2 contract",
            details={"expected": sorted(expected), "actual": sorted(value)},
        )


def _require_rows(
    value: Any, reason_code: str, *, allow_empty: bool = False
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or (not value and not allow_empty) or any(
        not isinstance(item, dict) for item in value
    ):
        raise V022RuntimeContractError(
            reason_code, "Runtime output section must be a canonical row list"
        )
    return cast(list[dict[str, Any]], value)


def _mapping_value(value: Any, reason_code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise V022RuntimeContractError(
            reason_code, "Runtime output section must be a canonical object"
        )
    return cast(dict[str, Any], value)


def _string_value(value: Any, reason_code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise V022RuntimeContractError(
            reason_code, "Runtime output identity must be a nonblank string"
        )
    return value


def _date_value(value: Any, reason_code: str) -> date:
    text = _string_value(value, reason_code)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as error:
        raise V022RuntimeContractError(reason_code, "Runtime date is invalid") from error
    if parsed.isoformat() != text:
        raise V022RuntimeContractError(reason_code, "Runtime date is not canonical")
    return parsed


def _date_text(value: Any, reason_code: str) -> str:
    return _date_value(value, reason_code).isoformat()


def _datetime_value(value: Any, reason_code: str) -> datetime:
    text = _string_value(value, reason_code)
    if not text.endswith("Z"):
        raise V022RuntimeContractError(
            reason_code, "Runtime datetime must use canonical UTC Z encoding"
        )
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise V022RuntimeContractError(reason_code, "Runtime datetime is invalid") from error
    if _datetime_text(parsed) != text:
        raise V022RuntimeContractError(reason_code, "Runtime datetime is not canonical")
    return parsed


def _uuid_value(value: Any, reason_code: str) -> uuid.UUID:
    text = _string_value(value, reason_code)
    try:
        parsed = uuid.UUID(text)
    except ValueError as error:
        raise V022RuntimeContractError(reason_code, "Runtime UUID is invalid") from error
    if str(parsed) != text:
        raise V022RuntimeContractError(reason_code, "Runtime UUID is not canonical")
    return parsed


def _finite_decimal(value: Any, reason_code: str) -> Decimal:
    text = _string_value(value, reason_code)
    try:
        parsed = Decimal(text)
    except InvalidOperation as error:
        raise V022RuntimeContractError(reason_code, "Runtime decimal is invalid") from error
    if not parsed.is_finite() or _decimal_text(parsed) != text:
        raise V022RuntimeContractError(reason_code, "Runtime decimal is not canonical and finite")
    return parsed


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reject_plan_specific_identity(value: Any) -> None:
    if isinstance(value, Mapping):
        forbidden = set(value).intersection(_PLAN_SPECIFIC_KEYS)
        if forbidden:
            raise V022RuntimeContractError(
                "portfolio_plan_specific_identity_forbidden",
                "Reusable Portfolio payload cannot contain Suite/Cell/Plan identity",
                details={"forbidden_fields": sorted(forbidden)},
            )
        for child in value.values():
            _reject_plan_specific_identity(child)
    elif isinstance(value, list):
        for child in value:
            _reject_plan_specific_identity(child)
