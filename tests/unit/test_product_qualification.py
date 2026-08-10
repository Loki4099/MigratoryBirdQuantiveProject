from dataclasses import replace

from style_rotation.product.promotion import _has_quality_warning, _qualification_status
from style_rotation.product.qualification import (
    QualificationCell,
    evaluate_product_qualification,
    evaluate_research_candidate_qualification,
)


def _cells() -> tuple[QualificationCell, ...]:
    return tuple(
        QualificationCell(
            cell_fingerprint=f"{window}-{cost}",
            strategy_fingerprint="s" * 64,
            suite_fingerprint="u" * 64,
            comparison_context_fingerprint="c" * 64,
            window_key=window,
            cost_bps_per_side=cost,
            status="accepted",
            formal_eligible=True,
        )
        for window in ("full_common_history", "trailing_3_years", "trailing_1_year")
        for cost in (5, 10)
    )


def test_product_requires_exact_formal_six_cell_bundle_and_closed_p0_gates() -> None:
    accepted = evaluate_product_qualification(
        _cells(),
        pit_universe_gate_closed=True,
        terminal_event_gate_closed=True,
        impact_policy_gate_closed=True,
    )
    assert accepted.eligible is True
    blocked = evaluate_product_qualification(
        _cells(),
        pit_universe_gate_closed=False,
        terminal_event_gate_closed=False,
        impact_policy_gate_closed=False,
    )
    assert blocked.eligible is False
    assert blocked.reason_codes == (
        "pit_universe_gate_open",
        "terminal_event_gate_open",
        "impact_policy_gate_open",
    )


def test_capacity_rejected_cell_can_be_inspected_but_not_promoted() -> None:
    cells = list(_cells())
    cells[0] = replace(cells[0], status="capacity_rejected")
    decision = evaluate_product_qualification(
        tuple(cells),
        pit_universe_gate_closed=True,
        terminal_event_gate_closed=True,
        impact_policy_gate_closed=True,
    )
    assert decision.eligible is False
    assert "qualification_contains_nonaccepted_cell" in decision.reason_codes


def test_research_candidate_accepts_missing_formal_evidence_as_visible_warnings() -> None:
    cells = [replace(item, formal_eligible=False) for item in _cells()]
    cells[0] = replace(cells[0], status="capacity_rejected")
    decision = evaluate_research_candidate_qualification(
        tuple(cells),
        pit_universe_gate_closed=False,
        terminal_event_gate_closed=False,
        impact_policy_gate_closed=False,
    )
    assert decision.eligible is True
    assert decision.reason_codes == ()
    assert decision.warning_codes == (
        "candidate_capacity_not_100m_eligible",
        "candidate_contains_exploratory_cell",
        "candidate_non_pit_survivorship_warning",
        "candidate_terminal_event_coverage_warning",
        "candidate_uncalibrated_impact_warning",
    )


def test_accepted_exploratory_warning_cell_is_not_treated_as_failed() -> None:
    row = {
        "availability_status": "accepted",
        "quality_status": "warning",
        "diagnostics": {
            "quality_checks": [
                {"check_key": "capacity_adv_5_percent", "status": "warning"}
            ]
        },
    }
    assert _qualification_status(row) == "accepted"
    assert _has_quality_warning(row, "capacity_adv_5_percent") is True
