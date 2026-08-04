from style_rotation.experiment.compare import classify_comparison


def _row(**changes: object) -> dict[str, object]:
    row: dict[str, object] = {
        "model_specification_id": "model-a", "strategy_template_key": "top-k",
        "strategy_semantics": "rank", "target_k": 2, "frequency": "weekly",
        "cost_model_version_id": "linear", "cost_bps_per_side": 5,
        "interval_semantics": "full", "universe_version_id": "u",
        "data_bundle_version_id": "d", "eligibility_snapshot_id": "e",
        "execution_policy_version_id": "x", "reserve_return_model_version_id": "r",
        "benchmark_version_id": "b", "performance_metric_catalog_id": "m",
        "accounting_engine_version_id": "a", "benchmark_engine_version_id": "be",
        "performance_engine_version_id": "p", "currency": "USD",
    }
    row.update(changes)
    return row


def test_comparison_is_controlled_only_for_one_changed_dimension() -> None:
    result = classify_comparison((_row(), _row(target_k=3)))
    assert result.mode == "controlled"
    assert result.changed_dimensions == ("k",)
    assert result.blocking_context_fields == ()


def test_comparison_reports_multiple_changes_and_protected_context_mismatch() -> None:
    result = classify_comparison((
        _row(), _row(target_k=3, frequency="monthly", data_bundle_version_id="other")
    ))
    assert result.mode == "side_by_side"
    assert result.changed_dimensions == ("k", "frequency")
    assert result.blocking_context_fields == ("data_bundle_version_id",)
