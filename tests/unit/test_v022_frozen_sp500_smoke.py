from __future__ import annotations

from style_rotation.cli.v022_frozen_sp500_smoke import (
    _SMOKE_AGGREGATION,
    _SMOKE_AGGREGATION_PRESET,
    _SMOKE_DEFENSE,
    _SMOKE_VERSION,
    build_parser,
)
from style_rotation.v022.green_baseline_registry import (
    GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
)


def test_smoke_identity_moves_with_v5_registry_without_running_it() -> None:
    assert _SMOKE_VERSION == 16
    assert GREEN_BASELINE_REGISTRY_CATALOG_VERSION == "0.22.4"
    assert (_SMOKE_AGGREGATION, _SMOKE_AGGREGATION_PRESET) == (
        "flat_equal_weight_mean",
        "signal_equal_v1",
    )
    assert _SMOKE_DEFENSE == "none"


def test_smoke_cli_accepts_an_explicit_feature_and_identity_version() -> None:
    parsed = build_parser().parse_args(
        [
            "--frequency",
            "weekly",
            "--feature",
            "return_continuation__w60",
            "--smoke-version",
            "17",
        ]
    )
    assert parsed.feature == "return_continuation__w60"
    assert parsed.smoke_version == 17
