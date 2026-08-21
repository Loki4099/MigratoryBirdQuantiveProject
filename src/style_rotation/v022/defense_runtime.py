from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from typing import Literal

from style_rotation.v022.runtime_contract import (
    V022RuntimeContractError,
    V022RuntimeDataError,
)
from style_rotation.v022.strategy_compat_runtime import StrategyUnitRiskTarget

ZERO = Decimal(0)
ONE = Decimal(1)
INDICATOR_QUANTUM = Decimal("0.000000000000000001")
UPPER_THRESHOLD = Decimal("0.020000000000000000")
LOWER_THRESHOLD = Decimal("-0.020000000000000000")

TimingVariant = Literal["fixed20_budget", "spy_ma200_tiered_budget"]
DefenseRegime = Literal["fixed_budget", "above_upper", "middle", "below_lower"]
AllocationRole = Literal["defensive_asset", "reserve"]
Sleeve = Literal["risk", "defense", "reserve"]


@dataclass(frozen=True, slots=True)
class DefensePriceObservation:
    session_date: date
    known_at: datetime
    adjusted_close: Decimal

    def __post_init__(self) -> None:
        if self.known_at.utcoffset() is None:
            raise V022RuntimeContractError(
                "defense_known_at_naive",
                "Defense price known_at must be timezone-aware",
            )
        if not self.adjusted_close.is_finite() or self.adjusted_close <= ZERO:
            raise V022RuntimeDataError(
                "defense_price_invalid",
                "Defense adjusted close must be finite and positive",
                details={"session_date": self.session_date.isoformat()},
            )


@dataclass(frozen=True, slots=True)
class DefenseDecision:
    decision_date: date
    decision_cutoff_at: datetime
    timing_variant_key: TimingVariant
    regime_key: DefenseRegime
    reason_code: str
    risk_budget: Decimal
    defense_budget: Decimal
    indicator_value: Decimal | None
    input_known_at: datetime | None

    def __post_init__(self) -> None:
        if self.decision_cutoff_at.utcoffset() is None:
            raise V022RuntimeContractError(
                "defense_cutoff_naive",
                "Defense decision cutoff must be timezone-aware",
            )
        if self.decision_cutoff_at.date() != self.decision_date:
            raise V022RuntimeContractError(
                "defense_decision_cutoff_mismatch",
                "Defense Decision date must match its exact cutoff date",
            )
        if not self.reason_code.strip():
            raise V022RuntimeContractError(
                "defense_reason_blank", "Defense Decision reason code must be nonblank"
            )
        budgets = (self.risk_budget, self.defense_budget)
        if any(not value.is_finite() or not ZERO <= value <= ONE for value in budgets):
            raise V022RuntimeContractError(
                "defense_budget_invalid", "Defense budgets must be finite inside [0, 1]"
            )
        if sum(budgets, ZERO) != ONE:
            raise V022RuntimeContractError(
                "defense_budget_not_conserved", "Risk and Defense budgets must sum to one"
            )
        if (self.indicator_value is None) != (self.input_known_at is None):
            raise V022RuntimeContractError(
                "defense_indicator_identity_incomplete",
                "Defense indicator value and input known_at are all-or-none",
            )
        if self.indicator_value is not None and not self.indicator_value.is_finite():
            raise V022RuntimeContractError(
                "defense_indicator_nonfinite", "Defense indicator must remain finite"
            )
        if self.input_known_at is not None:
            if self.input_known_at.utcoffset() is None:
                raise V022RuntimeContractError(
                    "defense_input_known_at_naive",
                    "Defense input known_at must be timezone-aware",
                )
            if self.input_known_at > self.decision_cutoff_at:
                raise V022RuntimeContractError(
                    "defense_input_after_cutoff",
                    "Defense Decision cannot depend on input known after its cutoff",
                )
        if self.timing_variant_key not in {"fixed20_budget", "spy_ma200_tiered_budget"}:
            raise V022RuntimeContractError(
                "defense_timing_variant_unknown",
                f"Unknown v0.22 Defense Timing Variant: {self.timing_variant_key}",
            )
        if self.regime_key not in {"fixed_budget", "above_upper", "middle", "below_lower"}:
            raise V022RuntimeContractError(
                "defense_regime_unknown",
                f"Unknown v0.22 Defense regime: {self.regime_key}",
            )
        if self.timing_variant_key == "fixed20_budget":
            if (
                self.regime_key != "fixed_budget"
                or self.indicator_value is not None
                or self.risk_budget != Decimal("0.800000000000000000")
                or self.defense_budget != Decimal("0.200000000000000000")
                or self.reason_code != "published_fixed_budget"
            ):
                raise V022RuntimeContractError(
                    "fixed20_decision_invalid",
                    "Fixed20 Decision must match its exact published budget contract",
                )
            return
        if self.regime_key == "fixed_budget" or self.indicator_value is None:
            raise V022RuntimeContractError(
                "ma200_decision_invalid",
                "MA200 Decision requires a tier regime and exact indicator input",
            )
        if self.indicator_value > UPPER_THRESHOLD:
            expected = (
                "above_upper",
                "above_strict_upper_threshold",
                Decimal("1.000000000000000000"),
                Decimal("0.000000000000000000"),
            )
        elif self.indicator_value < LOWER_THRESHOLD:
            expected = (
                "below_lower",
                "below_strict_lower_threshold",
                Decimal("0.600000000000000000"),
                Decimal("0.400000000000000000"),
            )
        else:
            expected = (
                "middle",
                "inclusive_middle_band",
                Decimal("0.800000000000000000"),
                Decimal("0.200000000000000000"),
            )
        if (
            self.regime_key,
            self.reason_code,
            self.risk_budget,
            self.defense_budget,
        ) != expected:
            raise V022RuntimeContractError(
                "ma200_decision_tier_mismatch",
                "MA200 Decision regime and budgets must match its exact indicator tier",
            )


@dataclass(frozen=True, slots=True)
class DefenseAllocationMember:
    asset_id: uuid.UUID | None
    asset_key: str
    component_role: AllocationRole
    sleeve_weight: Decimal
    ordinal: int
    eligible: bool = True

    def __post_init__(self) -> None:
        if not self.asset_key.strip() or self.ordinal < 0:
            raise V022RuntimeContractError(
                "defense_member_identity_invalid",
                "Defense member requires a nonblank key and nonnegative ordinal",
            )
        if not self.sleeve_weight.is_finite() or not ZERO < self.sleeve_weight <= ONE:
            raise V022RuntimeContractError(
                "defense_member_weight_invalid",
                "Defense member sleeve weight must be finite and positive",
            )
        if (self.component_role == "reserve") != (self.asset_id is None):
            raise V022RuntimeContractError(
                "defense_member_role_identity_invalid",
                "Reserve has no Asset id; defensive Assets require an Asset id",
            )


@dataclass(frozen=True, slots=True)
class SleeveContribution:
    sleeve: Sleeve
    source_ordinal: int
    asset_id: uuid.UUID | None
    asset_key: str
    sleeve_weight: Decimal
    portfolio_weight: Decimal

    def __post_init__(self) -> None:
        if not self.asset_key.strip() or self.source_ordinal < 0:
            raise V022RuntimeContractError(
                "sleeve_contribution_identity_invalid",
                "Sleeve Contribution requires a nonblank key and nonnegative ordinal",
            )
        if (
            not self.sleeve_weight.is_finite()
            or not self.portfolio_weight.is_finite()
            or not ZERO < self.sleeve_weight <= ONE
            or not ZERO <= self.portfolio_weight <= ONE
        ):
            raise V022RuntimeContractError(
                "sleeve_contribution_weight_invalid",
                "Sleeve Contribution weights must be finite and long-only",
            )
        if (self.sleeve == "reserve") != (self.asset_id is None):
            raise V022RuntimeContractError(
                "sleeve_contribution_role_identity_invalid",
                "Only the reserve Sleeve may omit an Asset id",
            )


@dataclass(frozen=True, slots=True)
class NetTargetWeight:
    asset_id: uuid.UUID
    asset_key: str
    target_weight: Decimal

    def __post_init__(self) -> None:
        if not self.asset_key.strip():
            raise V022RuntimeContractError(
                "net_target_asset_key_blank", "Net Target Asset key must be nonblank"
            )
        if not self.target_weight.is_finite() or not ZERO < self.target_weight <= ONE:
            raise V022RuntimeContractError(
                "net_target_weight_invalid",
                "Published Net Target weights must be finite and positive",
            )


@dataclass(frozen=True, slots=True)
class MergedPortfolioTarget:
    decision_date: date
    decision_cutoff_at: datetime
    input_known_at: datetime
    risk_budget: Decimal
    defense_budget: Decimal
    contributions: tuple[SleeveContribution, ...]
    net_asset_weights: tuple[NetTargetWeight, ...]
    reserve_target_weight: Decimal

    def __post_init__(self) -> None:
        if self.decision_cutoff_at.utcoffset() is None:
            raise V022RuntimeContractError(
                "merged_cutoff_naive",
                "Merged Target Decision cutoff must be timezone-aware",
            )
        if self.input_known_at.utcoffset() is None:
            raise V022RuntimeContractError(
                "merged_input_known_at_naive",
                "Merged Target input known_at must be timezone-aware",
            )
        if self.decision_cutoff_at.date() != self.decision_date:
            raise V022RuntimeContractError(
                "merged_decision_cutoff_mismatch",
                "Merged Target Decision date must match its exact cutoff date",
            )
        if self.input_known_at > self.decision_cutoff_at:
            raise V022RuntimeContractError(
                "merged_input_after_cutoff",
                "Merged Target input cannot be known after its Decision cutoff",
            )
        if (
            any(
                not value.is_finite() or not ZERO <= value <= ONE
                for value in (self.risk_budget, self.defense_budget, self.reserve_target_weight)
            )
            or self.risk_budget + self.defense_budget != ONE
        ):
            raise V022RuntimeContractError(
                "merged_target_budget_invalid",
                "Merged Target budgets must be finite, long-only, and conserved",
            )
        sleeve_order = {"risk": 0, "defense": 1, "reserve": 2}
        canonical_contributions = tuple(
            sorted(
                self.contributions,
                key=lambda item: (sleeve_order[item.sleeve], item.source_ordinal),
            )
        )
        if canonical_contributions != self.contributions:
            raise V022RuntimeContractError(
                "sleeve_contribution_order_invalid",
                "Sleeve Contributions must use risk, defense, reserve canonical order",
            )
        if not self.contributions or len(
            {(item.sleeve, item.source_ordinal) for item in self.contributions}
        ) != len(self.contributions):
            raise V022RuntimeContractError(
                "sleeve_contribution_source_duplicate",
                "Sleeve Contribution sources must be nonempty and unique within each Sleeve",
            )
        for sleeve in ("risk", "defense"):
            sleeve_assets = tuple(item for item in self.contributions if item.sleeve == sleeve)
            sleeve_ids = tuple(item.asset_id for item in sleeve_assets)
            sleeve_keys = tuple(item.asset_key for item in sleeve_assets)
            if len(sleeve_ids) != len(set(sleeve_ids)) or len(sleeve_keys) != len(
                set(sleeve_keys)
            ):
                raise V022RuntimeContractError(
                    "sleeve_contribution_asset_duplicate",
                    "An Asset may appear only once inside each source Sleeve",
                )
        if sum(item.sleeve == "reserve" for item in self.contributions) > 1:
            raise V022RuntimeContractError(
                "sleeve_reserve_contribution_duplicate",
                "Merged Target may contain at most one reserve Contribution",
            )
        canonical_targets = tuple(
            sorted(self.net_asset_weights, key=lambda item: (item.asset_key, str(item.asset_id)))
        )
        if canonical_targets != self.net_asset_weights:
            raise V022RuntimeContractError(
                "net_target_order_invalid", "Net Target weights must be ordered by Asset key"
            )
        ids = tuple(item.asset_id for item in self.net_asset_weights)
        keys = tuple(item.asset_key for item in self.net_asset_weights)
        if len(ids) != len(set(ids)) or len(keys) != len(set(keys)):
            raise V022RuntimeContractError(
                "net_target_asset_duplicate",
                "Net Target Asset identities and keys must be unique",
            )
        risk_total = sum(
            (item.portfolio_weight for item in self.contributions if item.sleeve == "risk"),
            ZERO,
        )
        defense_total = sum(
            (
                item.portfolio_weight
                for item in self.contributions
                if item.sleeve in {"defense", "reserve"}
            ),
            ZERO,
        )
        reserve_total = sum(
            (item.portfolio_weight for item in self.contributions if item.sleeve == "reserve"),
            ZERO,
        )
        if (
            abs(risk_total - self.risk_budget) > INDICATOR_QUANTUM
            or abs(defense_total - self.defense_budget) > INDICATOR_QUANTUM
            or abs(reserve_total - self.reserve_target_weight) > INDICATOR_QUANTUM
        ):
            raise V022RuntimeContractError(
                "sleeve_attribution_not_conserved",
                "Sleeve Contributions do not reconcile to published budgets",
            )
        contribution_assets: dict[uuid.UUID, tuple[str, Decimal]] = {}
        key_to_id: dict[str, uuid.UUID] = {}
        reserve_keys: set[str] = set()
        for item in self.contributions:
            if item.asset_id is None:
                reserve_keys.add(item.asset_key)
                continue
            prior_key, prior_weight = contribution_assets.get(
                item.asset_id, (item.asset_key, ZERO)
            )
            if prior_key != item.asset_key:
                raise V022RuntimeContractError(
                    "sleeve_asset_identity_conflict",
                    "An Asset id cannot map to multiple keys across Sleeves",
                )
            prior_id = key_to_id.setdefault(item.asset_key, item.asset_id)
            if prior_id != item.asset_id:
                raise V022RuntimeContractError(
                    "sleeve_asset_key_conflict",
                    "An Asset key cannot map to multiple ids across Sleeves",
                )
            contribution_assets[item.asset_id] = (
                item.asset_key,
                prior_weight + item.portfolio_weight,
            )
        if reserve_keys.intersection(key_to_id):
            raise V022RuntimeContractError(
                "reserve_asset_key_conflict",
                "Reserve and investable Asset Contributions require distinct keys",
            )
        expected_targets = tuple(
            sorted(
                (
                    NetTargetWeight(asset_id, asset_key, weight)
                    for asset_id, (asset_key, weight) in contribution_assets.items()
                    if weight > ZERO
                ),
                key=lambda item: (item.asset_key, str(item.asset_id)),
            )
        )
        if expected_targets != self.net_asset_weights:
            raise V022RuntimeContractError(
                "net_target_attribution_mismatch",
                "Net Target must equal the sum of all Asset Sleeve Contributions",
            )
        if (
            abs(
                sum((item.target_weight for item in self.net_asset_weights), ZERO)
                + self.reserve_target_weight
                - ONE
            )
            > INDICATOR_QUANTUM
        ):
            raise V022RuntimeContractError(
                "merged_target_capital_not_conserved",
                "Net Asset and reserve target weights must sum to one",
            )


def evaluate_defense_timing(
    timing_variant_key: TimingVariant,
    *,
    decision_date: date,
    decision_cutoff_at: datetime,
    observations: tuple[DefensePriceObservation, ...] = (),
    expected_window_sessions: tuple[date, ...] = (),
) -> DefenseDecision:
    """Evaluate a published Defense Timing Policy from exact as-of input."""

    if decision_cutoff_at.utcoffset() is None:
        raise V022RuntimeContractError(
            "defense_cutoff_naive", "Defense decision cutoff must be timezone-aware"
        )
    if timing_variant_key == "fixed20_budget":
        if observations or expected_window_sessions:
            raise V022RuntimeContractError(
                "fixed20_input_forbidden",
                "Fixed20 Timing publishes no market-signal input dependency",
            )
        return DefenseDecision(
            decision_date=decision_date,
            decision_cutoff_at=decision_cutoff_at,
            timing_variant_key=timing_variant_key,
            regime_key="fixed_budget",
            reason_code="published_fixed_budget",
            risk_budget=Decimal("0.800000000000000000"),
            defense_budget=Decimal("0.200000000000000000"),
            indicator_value=None,
            input_known_at=None,
        )
    if timing_variant_key != "spy_ma200_tiered_budget":
        raise V022RuntimeContractError(
            "defense_timing_variant_unknown",
            f"Unknown v0.22 Defense Timing Variant: {timing_variant_key}",
        )
    dates = tuple(item.session_date for item in observations)
    if dates != tuple(sorted(set(dates))):
        raise V022RuntimeContractError(
            "defense_observation_order_invalid",
            "Defense observations must have unique sorted session dates",
        )
    if (
        len(expected_window_sessions) != 200
        or expected_window_sessions != tuple(sorted(set(expected_window_sessions)))
        or expected_window_sessions[-1] != decision_date
    ):
        raise V022RuntimeContractError(
            "defense_expected_window_invalid",
            "MA200 Timing requires the exact 200 unique sorted common-session dates",
        )
    if len(observations) < 200:
        raise V022RuntimeDataError(
            "spy_history_below_required",
            "SPY MA200 Timing requires at least 200 exact adjusted-close observations",
            details={"observation_count": len(observations), "required_count": 200},
        )
    if observations[-1].session_date != decision_date:
        raise V022RuntimeDataError(
            "timing_input_stale",
            "Latest SPY Timing observation must be the exact Decision session",
            details={
                "latest_session_date": observations[-1].session_date.isoformat(),
                "decision_date": decision_date.isoformat(),
            },
        )
    if any(item.session_date > decision_date for item in observations):
        raise V022RuntimeContractError(
            "defense_observation_after_decision",
            "Defense observations cannot include a future session",
        )
    window = observations[-200:]
    if tuple(item.session_date for item in window) != expected_window_sessions:
        raise V022RuntimeDataError(
            "defense_session_window_mismatch",
            "SPY MA200 input does not match the frozen 200 common-session window",
        )
    late = tuple(item for item in window if item.known_at > decision_cutoff_at)
    if late:
        raise V022RuntimeDataError(
            "defense_signal_available_after_cutoff",
            "SPY Timing input was not known by the exact Decision cutoff",
            details={"late_observation_count": len(late)},
        )
    with localcontext() as context:
        context.prec = 50
        moving_average = sum((item.adjusted_close for item in window), ZERO) / Decimal(200)
        distance = window[-1].adjusted_close / moving_average - ONE
        indicator = distance
    if distance > UPPER_THRESHOLD:
        regime: DefenseRegime = "above_upper"
        reason = "above_strict_upper_threshold"
        risk_budget = Decimal("1.000000000000000000")
        defense_budget = Decimal("0.000000000000000000")
    elif distance < LOWER_THRESHOLD:
        regime = "below_lower"
        reason = "below_strict_lower_threshold"
        risk_budget = Decimal("0.600000000000000000")
        defense_budget = Decimal("0.400000000000000000")
    else:
        regime = "middle"
        reason = "inclusive_middle_band"
        risk_budget = Decimal("0.800000000000000000")
        defense_budget = Decimal("0.200000000000000000")
    return DefenseDecision(
        decision_date=decision_date,
        decision_cutoff_at=decision_cutoff_at,
        timing_variant_key=timing_variant_key,
        regime_key=regime,
        reason_code=reason,
        risk_budget=risk_budget,
        defense_budget=defense_budget,
        indicator_value=indicator,
        input_known_at=max(item.known_at for item in window),
    )


def merge_sleeves(
    unit_risk_target: StrategyUnitRiskTarget,
    *,
    defense_decision: DefenseDecision | None = None,
    allocation_members: tuple[DefenseAllocationMember, ...] = (),
) -> MergedPortfolioTarget:
    """Scale Strategy and Defense Sleeves, retain attribution, then net overlap."""

    if defense_decision is None:
        if allocation_members:
            raise V022RuntimeContractError(
                "none_defense_allocation_forbidden",
                "A null Defense Package cannot publish allocation members",
            )
        risk_budget, defense_budget = ONE, ZERO
    else:
        if defense_decision.decision_date != unit_risk_target.decision_date:
            raise V022RuntimeContractError(
                "sleeve_decision_date_mismatch",
                "Strategy and Defense Decisions must share the exact Decision date",
            )
        if defense_decision.decision_cutoff_at != unit_risk_target.decision_cutoff_at:
            raise V022RuntimeContractError(
                "sleeve_decision_cutoff_mismatch",
                "Strategy and Defense Decisions must share the exact Decision cutoff",
            )
        if (
            defense_decision.input_known_at is not None
            and defense_decision.input_known_at > unit_risk_target.decision_cutoff_at
        ):
            raise V022RuntimeContractError(
                "defense_input_after_strategy_cutoff",
                "Defense market input cannot be known after the Strategy cutoff",
            )
        if not allocation_members:
            raise V022RuntimeContractError(
                "defense_allocation_empty",
                "A composed Defense Decision requires its exact Allocation Package",
            )
        risk_budget = defense_decision.risk_budget
        defense_budget = defense_decision.defense_budget
    ordered_members = tuple(sorted(allocation_members, key=lambda item: item.ordinal))
    if ordered_members != allocation_members or tuple(
        item.ordinal for item in ordered_members
    ) != tuple(range(len(ordered_members))):
        raise V022RuntimeContractError(
            "defense_allocation_order_invalid",
            "Defense Allocation members must have contiguous canonical ordinals",
        )
    member_ids = tuple(item.asset_id for item in ordered_members if item.asset_id is not None)
    member_keys = tuple(item.asset_key for item in ordered_members)
    if len(member_ids) != len(set(member_ids)) or len(member_keys) != len(set(member_keys)):
        raise V022RuntimeContractError(
            "defense_allocation_member_duplicate",
            "Defense Allocation member identities and keys must be unique",
        )
    if sum(item.component_role == "reserve" for item in ordered_members) > 1:
        raise V022RuntimeContractError(
            "defense_reserve_member_duplicate",
            "Defense Allocation may publish at most one reserve member",
        )
    if ordered_members and sum((item.sleeve_weight for item in ordered_members), ZERO) != ONE:
        raise V022RuntimeContractError(
            "defense_allocation_not_conserved",
            "Defense Allocation member Sleeve weights must sum exactly to one",
        )
    ineligible = tuple(item.asset_key for item in ordered_members if not item.eligible)
    if ineligible:
        raise V022RuntimeDataError(
            "defense_allocation_member_ineligible",
            "Exact Asset Context cannot execute every published Defense member",
            details={"asset_keys": ineligible},
        )

    risk_weights = [item.unit_risk_weight * risk_budget for item in unit_risk_target.positions]
    _absorb_scaling_residual(
        risk_weights,
        expected_total=risk_budget,
        reason_code="risk_sleeve_scaling_residual",
    )
    contributions: list[SleeveContribution] = [
        SleeveContribution(
            sleeve="risk",
            source_ordinal=ordinal,
            asset_id=item.asset_id,
            asset_key=item.asset_key,
            sleeve_weight=item.unit_risk_weight,
            portfolio_weight=risk_weights[ordinal],
        )
        for ordinal, item in enumerate(unit_risk_target.positions)
    ]
    allocation_sequence = tuple(
        item for item in ordered_members if item.component_role == "defensive_asset"
    ) + tuple(item for item in ordered_members if item.component_role == "reserve")
    allocation_weights = [item.sleeve_weight * defense_budget for item in allocation_sequence]
    _absorb_scaling_residual(
        allocation_weights,
        expected_total=defense_budget,
        reason_code="defense_sleeve_scaling_residual",
    )
    weight_by_ordinal = {
        item.ordinal: weight
        for item, weight in zip(allocation_sequence, allocation_weights, strict=True)
    }
    contributions.extend(
        SleeveContribution(
            sleeve="defense",
            source_ordinal=item.ordinal,
            asset_id=item.asset_id,
            asset_key=item.asset_key,
            sleeve_weight=item.sleeve_weight,
            portfolio_weight=weight_by_ordinal[item.ordinal],
        )
        for item in ordered_members
        if item.component_role == "defensive_asset"
    )
    contributions.extend(
        SleeveContribution(
            sleeve="reserve",
            source_ordinal=item.ordinal,
            asset_id=None,
            asset_key=item.asset_key,
            sleeve_weight=item.sleeve_weight,
            portfolio_weight=weight_by_ordinal[item.ordinal],
        )
        for item in ordered_members
        if item.component_role == "reserve"
    )

    asset_totals: dict[uuid.UUID, tuple[str, Decimal]] = {}
    key_to_id: dict[str, uuid.UUID] = {}
    for item in contributions:
        if item.asset_id is None:
            continue
        prior_key, prior_weight = asset_totals.get(item.asset_id, (item.asset_key, ZERO))
        if prior_key != item.asset_key:
            raise V022RuntimeContractError(
                "sleeve_asset_identity_conflict",
                "An Asset id cannot map to multiple keys across Sleeves",
            )
        prior_id = key_to_id.setdefault(item.asset_key, item.asset_id)
        if prior_id != item.asset_id:
            raise V022RuntimeContractError(
                "sleeve_asset_key_conflict",
                "An Asset key cannot map to multiple ids across Sleeves",
            )
        asset_totals[item.asset_id] = (item.asset_key, prior_weight + item.portfolio_weight)
    net_weights = tuple(
        sorted(
            (
                NetTargetWeight(asset_id, asset_key, weight)
                for asset_id, (asset_key, weight) in asset_totals.items()
                if weight > ZERO
            ),
            key=lambda item: (item.asset_key, str(item.asset_id)),
        )
    )
    reserve_target = sum(
        (item.portfolio_weight for item in contributions if item.sleeve == "reserve"),
        ZERO,
    )
    merged_input_known_at = unit_risk_target.input_known_at
    if (
        defense_decision is not None
        and defense_decision.input_known_at is not None
        and defense_decision.input_known_at > merged_input_known_at
    ):
        merged_input_known_at = defense_decision.input_known_at
    return MergedPortfolioTarget(
        decision_date=unit_risk_target.decision_date,
        decision_cutoff_at=unit_risk_target.decision_cutoff_at,
        input_known_at=merged_input_known_at,
        risk_budget=risk_budget,
        defense_budget=defense_budget,
        contributions=tuple(contributions),
        net_asset_weights=net_weights,
        reserve_target_weight=reserve_target,
    )


def _absorb_scaling_residual(
    weights: list[Decimal], *, expected_total: Decimal, reason_code: str
) -> None:
    for _ in range(2):
        residual = expected_total - sum(weights, ZERO)
        if residual == ZERO:
            return
        if not weights or abs(residual) > INDICATOR_QUANTUM:
            raise V022RuntimeContractError(
                reason_code,
                "Sleeve scaling residual exceeds the frozen 1e-18 correction boundary",
                details={"residual": str(residual)},
            )
        weights[0] += residual
    if sum(weights, ZERO) != expected_total:
        raise V022RuntimeContractError(
            reason_code,
            "Sleeve scaling residual could not be reconciled deterministically",
        )
