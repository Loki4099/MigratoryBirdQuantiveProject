from datetime import date
from decimal import Decimal

from style_rotation.product.monitoring import MonitoringEvidence, evaluate_monitoring_health
from style_rotation.product.v021_monitoring import V021MonitoringCalculator


def test_monitoring_stays_observing_until_both_sample_minima_are_met() -> None:
    health = evaluate_monitoring_health(
        MonitoringEvidence(
            frequency="weekly",
            session_count=125,
            decision_count=25,
            data_contract_ok=True,
            capacity_ok=True,
        )
    )
    assert health.overall == "observing"
    assert health.performance_ready is False
    assert health.predictive_ready is False
    assert (
        evaluate_monitoring_health(
            MonitoringEvidence(
                frequency="weekly",
                session_count=126,
                decision_count=26,
                data_contract_ok=True,
                capacity_ok=True,
            )
        ).overall
        == "healthy"
    )


def test_hard_operational_evidence_applies_from_day_one() -> None:
    assert (
        evaluate_monitoring_health(
            MonitoringEvidence(
                frequency="monthly",
                session_count=0,
                decision_count=0,
                data_contract_ok=False,
                capacity_ok=True,
            )
        ).overall
        == "data_interrupted"
    )
    assert (
        evaluate_monitoring_health(
            MonitoringEvidence(
                frequency="monthly",
                session_count=0,
                decision_count=0,
                data_contract_ok=True,
                capacity_ok=False,
            )
        ).overall
        == "warning"
    )


def test_exploratory_candidate_uses_linear_costs_without_fake_impact_or_adv_gate() -> None:
    capacity_ok, primary, stress, audit = V021MonitoringCalculator._costs(
        [],
        [{"asset_key": "aapl", "target_weight": "0"}],
        [{"asset_key": "aapl", "target_weight": "1"}],
        date(2026, 8, 6),
        {
            "policy_key": "exploratory_linear_bps_only",
            "coefficient": "1",
            "maximum_bps": "1",
            "p0_finalized": False,
            "enabled": False,
        },
        Decimal("100000000"),
        Decimal("100000000"),
    )
    assert capacity_ok is True
    assert primary == Decimal("0.0005")
    assert stress == Decimal("0.001")
    assert audit[0]["impact_status"] == "uncalibrated_linear_bps_only"
