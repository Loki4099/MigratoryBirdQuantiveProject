from __future__ import annotations

from dataclasses import dataclass

POLICY_ID = "v022-m0-policy-v0.22.0"


@dataclass(frozen=True, slots=True)
class StructuralEstimate:
    explicit_stage3_inputs: int
    feature_occurrences: int
    ancestor_occurrences: int
    graph_edges: int
    aggregation_candidates: int
    aggregation_instances: int
    strategy_candidates: int
    defense_candidates: int
    strategy_branches: int
    backtest_cells: int
    work_items: int


ADMISSION_LIMITS = {
    "explicit_stage3_inputs": 128,
    "feature_occurrences": 2000,
    "ancestor_occurrences": 2000,
    "graph_edges": 5000,
    "aggregation_candidates": 16,
    "strategy_candidates": 16,
    "defense_candidates": 8,
    "strategy_branches": 256,
    "backtest_cells": 2048,
    "work_items": 10000,
}


def structural_admission(estimate: StructuralEstimate) -> dict[str, object]:
    values = {
        "explicit_stage3_inputs": estimate.explicit_stage3_inputs,
        "feature_occurrences": estimate.feature_occurrences,
        "ancestor_occurrences": estimate.ancestor_occurrences,
        "graph_edges": estimate.graph_edges,
        "aggregation_candidates": estimate.aggregation_candidates,
        "strategy_candidates": estimate.strategy_candidates,
        "defense_candidates": estimate.defense_candidates,
        "strategy_branches": estimate.strategy_branches,
        "backtest_cells": estimate.backtest_cells,
        "work_items": estimate.work_items,
    }
    checks = [
        {
            "resource_key": key,
            "estimated": value,
            "limit": ADMISSION_LIMITS[key],
            "status": "accepted" if value <= ADMISSION_LIMITS[key] else "rejected",
        }
        for key, value in values.items()
    ]
    rejected = [
        f"resource_limit_exceeded:{item['resource_key']}"
        for item in checks
        if item["status"] == "rejected"
    ]
    return {
        "policy_id": POLICY_ID,
        "state": "rejected" if rejected else "accepted",
        "estimates": {
            **values,
            "aggregation_instances": estimate.aggregation_instances,
        },
        "checks": checks,
        "reason_codes": rejected,
    }
