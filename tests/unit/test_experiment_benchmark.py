import uuid
from datetime import date
from decimal import Decimal

from style_rotation.experiment.benchmark import calculate_benchmark_targets


def _assets() -> tuple[dict[uuid.UUID, str], tuple[uuid.UUID, str]]:
    candidates = {uuid.uuid4(): key for key in ("iwf", "iwd", "iwo", "iwn")}
    return candidates, (uuid.uuid4(), "spy")


def test_spy_buy_hold_has_one_fully_invested_decision() -> None:
    candidates, spy = _assets()
    targets = calculate_benchmark_targets(
        benchmark_key="spy_buy_and_hold",
        reference_decision_dates=(date(2024, 1, 5), date(2024, 1, 12)),
        candidate_assets=candidates,
        product_benchmark_asset=spy,
    )
    assert len(targets) == 1
    assert targets[0].asset_weights[0].asset_id == spy[0]
    assert targets[0].asset_weights[0].target_weight == Decimal(1)
    assert targets[0].reserve_target_weight == 0


def test_four_etf_buy_hold_only_builds_at_common_start() -> None:
    candidates, spy = _assets()
    targets = calculate_benchmark_targets(
        benchmark_key="four_etf_equal_weight_buy_and_hold",
        reference_decision_dates=(date(2024, 1, 5), date(2024, 1, 12)),
        candidate_assets=candidates,
        product_benchmark_asset=spy,
    )
    assert len(targets) == 1
    assert {item.target_weight for item in targets[0].asset_weights} == {Decimal("0.25")}


def test_same_schedule_equal_weight_rebalances_on_every_reference_decision() -> None:
    candidates, spy = _assets()
    dates = (date(2024, 1, 5), date(2024, 1, 12), date(2024, 1, 19))
    targets = calculate_benchmark_targets(
        benchmark_key="four_etf_equal_weight_same_schedule_rebalanced",
        reference_decision_dates=dates,
        candidate_assets=candidates,
        product_benchmark_asset=spy,
    )
    assert tuple(item.decision_date for item in targets) == dates
    assert all(sum(item.target_weight for item in target.asset_weights) == 1 for target in targets)
