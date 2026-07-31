import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal

from style_rotation.domain.enums import RebalanceFrequency, StrategyTemplate
from style_rotation.domain.fingerprints import RunFingerprintInput


def sample_input() -> RunFingerprintInput:
    return RunFingerprintInput(
        data_version="data-001",
        cleaning_version="clean-001",
        factor_version="factor-001",
        strategy_version="strategy-001",
        engine_version="engine-001",
        factor_variant_key="momentum_simple_20",
        official_signal_start_date=date(2001, 7, 31),
        official_end_date=date(2026, 7, 30),
        rebalance_frequency=RebalanceFrequency.WEEKLY,
        strategy_template=StrategyTemplate.CROSS_SECTIONAL,
        transaction_cost_bps=Decimal("5"),
        parameters={"lookback": 20, "top_n": 2},
    )


class RunFingerprintTests(unittest.TestCase):
    def test_identical_input_has_identical_fingerprint(self) -> None:
        self.assertEqual(sample_input().fingerprint, sample_input().fingerprint)

    def test_parameter_order_does_not_change_fingerprint(self) -> None:
        left = sample_input()
        right = replace(left, parameters={"top_n": 2, "lookback": 20})
        self.assertEqual(left.fingerprint, right.fingerprint)

    def test_version_or_cost_change_changes_fingerprint(self) -> None:
        original = sample_input()
        self.assertNotEqual(
            original.fingerprint,
            replace(original, data_version="data-002").fingerprint,
        )
        self.assertNotEqual(
            original.fingerprint,
            replace(original, transaction_cost_bps=Decimal("10")).fingerprint,
        )

    def test_invalid_dates_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "start date"):
            replace(
                sample_input(),
                official_signal_start_date=date(2026, 8, 1),
                official_end_date=date(2026, 7, 31),
            )


if __name__ == "__main__":
    unittest.main()
