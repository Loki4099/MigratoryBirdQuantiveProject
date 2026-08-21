from __future__ import annotations

from style_rotation.v022.admission import StructuralEstimate, structural_admission


def _estimate(**overrides: int) -> StructuralEstimate:
    values = {
        "explicit_stage3_inputs": 1,
        "feature_occurrences": 8,
        "ancestor_occurrences": 7,
        "graph_edges": 7,
        "aggregation_candidates": 1,
        "aggregation_instances": 1,
        "strategy_candidates": 1,
        "defense_candidates": 1,
        "strategy_branches": 1,
        "backtest_cells": 7,
        "work_items": 7,
        **overrides,
    }
    return StructuralEstimate(**values)


def test_structural_admission_reports_every_frozen_dimension() -> None:
    report = structural_admission(_estimate())

    assert report["policy_id"] == "v022-m0-policy-v0.22.0"
    assert report["state"] == "accepted"
    assert len(report["checks"]) == 10
    assert report["reason_codes"] == []


def test_structural_admission_rejects_without_hiding_other_estimates() -> None:
    report = structural_admission(_estimate(strategy_branches=257, backtest_cells=2049))

    assert report["state"] == "rejected"
    assert report["reason_codes"] == [
        "resource_limit_exceeded:strategy_branches",
        "resource_limit_exceeded:backtest_cells",
    ]
    assert report["estimates"]["aggregation_instances"] == 1
