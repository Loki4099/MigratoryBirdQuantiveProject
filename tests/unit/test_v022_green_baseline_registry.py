from __future__ import annotations

from style_rotation.v022.green_baseline_registry import (
    GREEN_BASELINE_BENCHMARK_DATASET_KEY,
    GREEN_BASELINE_BENCHMARK_DATASET_VERSION,
    GREEN_BASELINE_COHORT_VERSION,
    GREEN_BASELINE_GATE_VERSION,
    GREEN_BASELINE_REGISTRY_CATALOG_VERSION,
    GREEN_BASELINE_RISK_DATASET_KEY,
    GREEN_BASELINE_RISK_DATASET_VERSION,
)


def test_green_registry_freezes_clean_lineage_versions() -> None:
    assert GREEN_BASELINE_REGISTRY_CATALOG_VERSION == "0.22.4"
    assert GREEN_BASELINE_RISK_DATASET_KEY.endswith("frozen_v5_baseline")
    assert GREEN_BASELINE_RISK_DATASET_VERSION == 1
    assert GREEN_BASELINE_BENCHMARK_DATASET_KEY.endswith("frozen_v6_baseline")
    assert GREEN_BASELINE_BENCHMARK_DATASET_VERSION == 1
    assert GREEN_BASELINE_GATE_VERSION == 5
    assert GREEN_BASELINE_COHORT_VERSION == 11


def test_green_registry_source_has_no_blue_registry_dependency() -> None:
    import inspect

    from style_rotation.v022 import green_baseline_registry

    source = inspect.getsource(green_baseline_registry)
    assert "base_asset_registry" not in source
    assert "v021_asset_registry" not in source
    assert "uniformly_excluded_by_gate" in source
    assert "31 Gate exclusions that overlap v5" in source
    assert "weekly_runtime" in source
    assert "monthly_runtime" in source
