from __future__ import annotations

from typing import cast

import pytest
from sqlalchemy.engine import RowMapping

from style_rotation.v022.product_monitoring import _evaluate_health

POLICY = {
    "minimum_completed_decisions": 1,
    "maximum_missing_fraction": "0.40",
    "coverage_warning_floor": "0.80",
    "coverage_watch_floor": "0.95",
}


@pytest.mark.parametrize(
    ("statuses", "coverage", "expected"),
    (
        ((), "1.00", "observing"),
        (("completed",), "0.96", "healthy"),
        (("completed",), "0.94", "watch"),
        (("completed",), "0.70", "warning"),
        (("completed", "missing"), "1.00", "data_interrupted"),
    ),
)
def test_monitoring_health_is_derived_from_frozen_policy(
    statuses: tuple[str, ...], coverage: str, expected: str
) -> None:
    decisions = cast(
        tuple[RowMapping, ...],
        tuple({"decision_status": status} for status in statuses),
    )
    health, document = _evaluate_health(
        POLICY,
        {"signal_coverage": coverage},
        decisions,
    )
    assert health == expected
    assert document["eligible_decision_count"] == len(statuses)
