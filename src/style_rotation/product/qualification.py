from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class QualificationCell:
    cell_fingerprint: str
    strategy_fingerprint: str
    suite_fingerprint: str
    comparison_context_fingerprint: str
    window_key: Literal["full_common_history", "trailing_3_years", "trailing_1_year"]
    cost_bps_per_side: Literal[5, 10]
    status: Literal[
        "accepted", "capacity_rejected", "quality_failed", "failed", "running", "queued"
    ]
    formal_eligible: bool


@dataclass(frozen=True, slots=True)
class QualificationDecision:
    eligible: bool
    reason_codes: tuple[str, ...]
    warning_codes: tuple[str, ...] = ()


def evaluate_product_qualification(
    cells: tuple[QualificationCell, ...],
    *,
    pit_universe_gate_closed: bool,
    terminal_event_gate_closed: bool,
    impact_policy_gate_closed: bool,
) -> QualificationDecision:
    reasons: list[str] = []
    if len(cells) != 6:
        reasons.append("qualification_requires_exactly_six_cells")
    expected = {
        (window, cost)
        for window in ("full_common_history", "trailing_3_years", "trailing_1_year")
        for cost in (5, 10)
    }
    actual = {(item.window_key, item.cost_bps_per_side) for item in cells}
    if actual != expected:
        reasons.append("qualification_matrix_incomplete_or_duplicated")
    for identity in ("strategy_fingerprint", "suite_fingerprint", "comparison_context_fingerprint"):
        if len({getattr(item, identity) for item in cells}) > 1:
            reasons.append(f"qualification_{identity}_mismatch")
    if any(item.status != "accepted" for item in cells):
        reasons.append("qualification_contains_nonaccepted_cell")
    if any(not item.formal_eligible for item in cells):
        reasons.append("qualification_contains_exploratory_cell")
    if not pit_universe_gate_closed:
        reasons.append("pit_universe_gate_open")
    if not terminal_event_gate_closed:
        reasons.append("terminal_event_gate_open")
    if not impact_policy_gate_closed:
        reasons.append("impact_policy_gate_open")
    return QualificationDecision(not reasons, tuple(dict.fromkeys(reasons)))


def evaluate_research_candidate_qualification(
    cells: tuple[QualificationCell, ...],
    *,
    pit_universe_gate_closed: bool,
    terminal_event_gate_closed: bool,
    impact_policy_gate_closed: bool,
) -> QualificationDecision:
    """Qualify an incubating candidate without claiming formal deployability."""
    blockers: list[str] = []
    warnings: list[str] = []
    if len(cells) != 6:
        blockers.append("qualification_requires_exactly_six_cells")
    expected = {
        (window, cost)
        for window in ("full_common_history", "trailing_3_years", "trailing_1_year")
        for cost in (5, 10)
    }
    actual = {(item.window_key, item.cost_bps_per_side) for item in cells}
    if actual != expected:
        blockers.append("qualification_matrix_incomplete_or_duplicated")
    for identity in ("strategy_fingerprint", "suite_fingerprint", "comparison_context_fingerprint"):
        if len({getattr(item, identity) for item in cells}) > 1:
            blockers.append(f"qualification_{identity}_mismatch")
    if any(item.status not in {"accepted", "capacity_rejected"} for item in cells):
        blockers.append("qualification_contains_failed_or_incomplete_cell")
    if any(item.status == "capacity_rejected" for item in cells):
        warnings.append("candidate_capacity_not_100m_eligible")
    if any(not item.formal_eligible for item in cells):
        warnings.append("candidate_contains_exploratory_cell")
    if not pit_universe_gate_closed:
        warnings.append("candidate_non_pit_survivorship_warning")
    if not terminal_event_gate_closed:
        warnings.append("candidate_terminal_event_coverage_warning")
    if not impact_policy_gate_closed:
        warnings.append("candidate_uncalibrated_impact_warning")
    return QualificationDecision(
        not blockers,
        tuple(dict.fromkeys(blockers)),
        tuple(dict.fromkeys(warnings)),
    )
