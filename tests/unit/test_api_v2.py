from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from style_rotation.api.app import create_app


class FakeArtifactReader:
    def __init__(self) -> None:
        self.artifact_id = uuid.uuid4()
        self.row: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "artifact_type": "research_catalog",
            "artifact_key": "factor_catalog",
            "version_number": 2001,
            "status": "published",
            "semantic_fingerprint": "a" * 64,
            "content_hash": "b" * 64,
            "published_at": datetime(2026, 8, 2, tzinfo=UTC),
            "created_at": datetime(2026, 8, 2, tzinfo=UTC),
        }

    def database_revision(self) -> str:
        return "20260802_02_v02_lineage"

    def list_artifacts(
        self,
        *,
        statuses: list[str],
        artifact_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if self.row["status"] not in statuses:
            return [], 0
        if artifact_type and self.row["artifact_type"] != artifact_type:
            return [], 0
        return [self.row][offset : offset + limit], 1

    def artifact_detail(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        if artifact_id != self.artifact_id:
            raise LookupError("Artifact not found")
        return {
            "artifact": self.row,
            "direct_dependencies": [],
            "direct_dependents": [],
            "has_manifest": True,
        }

    def lineage_manifest(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        if artifact_id != self.artifact_id:
            raise LookupError("Lineage manifest not found")
        return {
            "artifact": self.row,
            "manifest_hash": "c" * 64,
            "canonical_version": "canonical-json-v2",
            "manifest": {
                "root_artifact_id": str(artifact_id),
                "artifacts": [],
                "dependencies": [],
            },
            "created_at": datetime(2026, 8, 2, tzinfo=UTC),
        }

    def data_overview(self) -> dict[str, Any]:
        return {"sources": [], "datasets": [], "bundle": None, "eligibility": None}

    def factor_overview(self) -> dict[str, Any]:
        identifiers = [uuid.uuid4() for _ in range(7)]
        return {
            "diagnostic_artifact_id": identifiers[0],
            "factor_catalog_artifact_id": identifiers[1],
            "universe_artifact_id": identifiers[2],
            "data_bundle_artifact_id": identifiers[3],
            "eligibility_artifact_id": identifiers[4],
            "factor_engine_artifact_id": identifiers[5],
            "diagnostic_engine_artifact_id": identifiers[6],
            "coverage_start": date(2026, 1, 1),
            "coverage_end": date(2026, 1, 31),
            "dataset_count": 1,
            "asset_count": 5,
            "observation_count": 100,
            "pair_count": 0,
            "high_correlation_threshold": 0.85,
            "datasets": [
                {
                    "factor_dataset_artifact_id": uuid.uuid4(),
                    "factor_key": "total_return",
                    "measurement_family": "return",
                    "formula": "close[t] / close[t-window] - 1",
                    "output_unit": "ratio",
                    "variant_key": "total_return__w20",
                    "parameters": {"window": 20},
                    "preset_type": "canonical",
                    "coverage_start": date(2026, 1, 1),
                    "coverage_end": date(2026, 1, 31),
                    "row_count": 100,
                    "observation_count": 100,
                    "asset_count": 5,
                    "missing_count": 0,
                    "mean": 0.01,
                    "standard_deviation": 0.02,
                    "minimum": -0.04,
                    "p05": -0.03,
                    "p25": -0.01,
                    "median": 0.01,
                    "p75": 0.03,
                    "p95": 0.05,
                    "maximum": 0.06,
                    "zero_variance": False,
                }
            ],
            "correlations": [],
            "issues": [],
        }

    def signal_overview(self, frequency: str) -> dict[str, Any]:
        identifiers = [uuid.uuid4() for _ in range(8)]
        metric = {
            "window_key": "full",
            "window_start": date(2025, 1, 3),
            "window_end": date(2026, 1, 30),
            "period_count": 52,
            "valid_ic_count": 51,
            "undefined_ic_count": 1,
            "mean_rank_ic": 0.18,
            "median_rank_ic": 0.2,
            "positive_ic_ratio": 0.62,
            "information_ratio": 1.1,
            "mean_top_bottom_spread": 0.003,
            "event_rate": None,
            "event_asset_concentration": None,
            "non_neutral_rate": 1.0,
            "mean_top2_turnover": 0.22,
        }
        return {
            "evaluation_artifact_id": identifiers[0],
            "signal_catalog_artifact_id": identifiers[1],
            "universe_artifact_id": identifiers[2],
            "data_bundle_artifact_id": identifiers[3],
            "eligibility_artifact_id": identifiers[4],
            "signal_engine_artifact_id": identifiers[5],
            "evaluation_engine_artifact_id": identifiers[6],
            "forward_return_artifact_id": identifiers[7],
            "target_key": f"{frequency}_next_open_to_next_open",
            "frequency": frequency,
            "coverage_start": date(2025, 1, 3),
            "coverage_end": date(2026, 1, 30),
            "signal_count": 1,
            "common_period_count": 52,
            "pair_count": 0,
            "high_correlation_threshold": 0.85,
            "signals": [
                {
                    "signal_dataset_artifact_id": uuid.uuid4(),
                    "signal_key": "return_continuation__total_return__w252",
                    "template_key": "return_continuation",
                    "economic_family": "momentum",
                    "rationale_type": "academic",
                    "rationale": "Persistent relative performance may continue.",
                    "research_tier": "canonical",
                    "product_eligible": True,
                    "direction": "higher_is_better",
                    "normalization": "cross_sectional_centered_rank_-1_1",
                    "output_type": "continuous",
                    "factor_variant_key": "total_return__w252",
                    "full": metric,
                    "stability": [{**metric, "window_key": "year:2025"}],
                }
            ],
            "pairs": [],
            "issues": [
                {
                    "signal_key": "return_continuation__total_return__w252",
                    "severity": "warning",
                    "issue_code": "short_evaluation_sample",
                    "message": "Short sample",
                    "details": {"period_count": 52},
                }
            ],
        }

    def model_overview(self, frequency: str) -> dict[str, Any]:
        identifiers = [uuid.uuid4() for _ in range(8)]
        metric = {
            "window_key": "full",
            "window_start": date(2025, 1, 3),
            "window_end": date(2026, 1, 30),
            "period_count": 52,
            "valid_ic_count": 51,
            "undefined_ic_count": 1,
            "mean_rank_ic": 0.21,
            "median_rank_ic": 0.2,
            "positive_ic_ratio": 0.64,
            "information_ratio": 1.2,
            "mean_top_bottom_spread": 0.0035,
            "non_neutral_rate": 1.0,
            "mean_top2_turnover": 0.2,
            "mean_score_dispersion": 0.41,
            "mean_confidence": 0.55,
        }
        specification = "dimension_equal_weight__momentum_trend+volatility_risk"
        return {
            "evaluation_artifact_id": identifiers[0],
            "model_catalog_artifact_id": identifiers[1],
            "universe_artifact_id": identifiers[2],
            "data_bundle_artifact_id": identifiers[3],
            "eligibility_artifact_id": identifiers[4],
            "model_engine_artifact_id": identifiers[5],
            "evaluation_engine_artifact_id": identifiers[6],
            "forward_return_artifact_id": identifiers[7],
            "target_key": f"{frequency}_next_open_to_next_open",
            "frequency": frequency,
            "coverage_start": date(2025, 1, 3),
            "coverage_end": date(2026, 1, 30),
            "model_count": 1,
            "common_period_count": 52,
            "pair_count": 0,
            "ablation_count": 1,
            "high_correlation_threshold": 0.85,
            "models": [
                {
                    "model_dataset_artifact_id": uuid.uuid4(),
                    "specification_key": specification,
                    "specification_type": "dimension_subset_equal_weight",
                    "model_key": "classic_market_composite",
                    "model_family": "cross_sectional_composite",
                    "hypothesis": "Complementary dimensions may improve robustness.",
                    "overall_method_key": "weighted_mean",
                    "tie_output": "not_applicable",
                    "output_type": "continuous_score",
                    "active_dimension_count": 2,
                    "component_count": 2,
                    "research_tier": "canonical",
                    "dimensions": [
                        {
                            "dimension_key": "momentum_trend",
                            "method_key": "weighted_mean",
                            "input_transform": "identity",
                            "weight": 0.5,
                            "components": [
                                {
                                    "signal_key": "return_continuation__total_return__w252",
                                    "input_transform": "identity",
                                    "weight": 1.0,
                                }
                            ],
                        }
                    ],
                    "full": metric,
                    "stability": [{**metric, "window_key": "year:2025"}],
                }
            ],
            "pairs": [],
            "ablations": [
                {
                    "full_specification_key": specification,
                    "ablated_specification_key": "dimension_equal_weight__momentum_trend",
                    "removed_dimension_key": "volatility_risk",
                    "window_key": "full",
                    "period_count": 52,
                    "delta_mean_rank_ic": 0.03,
                    "delta_information_ratio": 0.1,
                    "delta_mean_top_bottom_spread": 0.0004,
                }
            ],
            "issues": [
                {
                    "specification_key": specification,
                    "severity": "warning",
                    "issue_code": "short_evaluation_sample",
                    "message": "Short sample",
                    "details": {"period_count": 52},
                }
            ],
        }

    def strategy_overview(self) -> dict[str, Any]:
        identifiers = [uuid.uuid4() for _ in range(10)]
        return {
            "rules": {
                "definition_artifact_id": identifiers[0],
                "version_artifact_id": identifiers[1],
                "strategy_key": "us_style_cross_sectional_rotation",
                "strategy_family": "cross_sectional_top_k_rotation",
                "hypothesis": "Higher-scored candidates may outperform.",
                "version_number": 1,
                "selection_contract": "rank_model_scores",
                "allocation_contract": "equal_slot_budget",
                "reserve_contract": "unused_slot_budget_to_synthetic_reserve",
                "compatible_model_output_types": ["continuous_score", "directional_score"],
                "candidate_input_policy": "complete_eligible_universe",
                "missing_input_policy": "fail_formal_run",
                "variants": [
                    {
                        "artifact_id": identifiers[2],
                        "variant_key": "top_k_equal_weight__k2",
                        "template_key": "top_k_equal_weight",
                        "target_k": 2,
                        "research_tier": "canonical",
                        "selection_order": "rank_then_select",
                        "trend_filter": "none",
                        "auxiliary_signal_key": None,
                        "auxiliary_eligible_state": None,
                        "empty_slot_policy": "not_applicable",
                        "tie_policy": "proportional_share_of_remaining_slot_budget",
                        "slot_weight_rule": "1 / K",
                        "reserve_rule": "unused_slot_budget_to_synthetic_reserve",
                    }
                ],
                "schedules": [
                    {
                        "artifact_id": identifiers[3],
                        "schedule_key": "weekly_last_common_session_close",
                        "frequency": "weekly",
                        "decision_timing": "last_common_session_close",
                        "decision_data_policy": "include_decision_close",
                    }
                ],
                "execution_policy": {
                    "artifact_id": identifiers[4],
                    "policy_key": "next_common_session_open",
                    "delay_common_sessions": 1,
                    "execution_price": "adjusted_open",
                    "missing_execution_policy": "fail_formal_run",
                },
            },
            "products": [
                {
                    "artifact_id": identifiers[5],
                    "product_key": "product-a",
                    "version_number": 1,
                    "model_specification_key": "dimension_equal_weight__momentum_trend",
                    "model_specification_type": "dimension_subset_equal_weight",
                    "model_output_type": "continuous_score",
                    "variant_key": "top_k_equal_weight__k2",
                    "target_k": 2,
                    "research_tier": "canonical",
                    "universe_key": "us_style_rotation_core",
                    "schedule_key": "weekly_last_common_session_close",
                    "frequency": "weekly",
                    "execution_policy_key": "next_common_session_open",
                    "execution_price": "adjusted_open",
                    "target_path_count": 1,
                }
            ],
            "target_paths": [
                {
                    "artifact_id": identifiers[6],
                    "product_artifact_id": identifiers[5],
                    "product_key": "product-a",
                    "model_dataset_artifact_id": identifiers[7],
                    "model_specification_key": "dimension_equal_weight__momentum_trend",
                    "variant_key": "top_k_equal_weight__k2",
                    "target_k": 2,
                    "frequency": "weekly",
                    "coverage_start": date(2026, 1, 2),
                    "coverage_end": date(2026, 1, 9),
                    "decision_count": 2,
                    "position_count": 8,
                }
            ],
        }

    def strategy_target_path(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        overview = self.strategy_overview()
        target = overview["target_paths"][0]
        target["artifact_id"] = artifact_id
        return {
            "target_path": target,
            "universe_artifact_id": uuid.uuid4(),
            "data_bundle_artifact_id": uuid.uuid4(),
            "eligibility_artifact_id": uuid.uuid4(),
            "engine_artifact_id": uuid.uuid4(),
            "auxiliary_signal_dataset_artifact_id": None,
            "decisions": [
                {
                    "decision_date": date(2026, 1, 9),
                    "target_k": 2,
                    "actual_holding_count": 2,
                    "boundary_tie_count": 0,
                    "reserve_target_weight": 0,
                    "positions": [
                        {
                            "asset_key": "iwd",
                            "symbol": "IWD",
                            "model_score": 0.8,
                            "model_rank": 1,
                            "selection_rank": 1,
                            "trend_state": None,
                            "strategy_eligible": True,
                            "selected": True,
                            "target_weight": 0.5,
                            "decision_reason": "selected_by_rank",
                        }
                    ],
                }
            ],
        }

    def experiment_overview(self) -> dict[str, Any]:
        suite_id, specification_id, result_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        return {
            "suites": [{
                "artifact_id": suite_id, "suite_key": "formal-v02", "version_number": 1,
                "name": "Formal v0.2 experiments", "description": "Comparable cells",
                "specification_count": 1,
            }],
            "specifications": [{
                "artifact_id": specification_id, "result_artifact_id": result_id,
                "suite_artifact_id": suite_id, "cell_key": "weekly-k2-5bps-full",
                "ordinal": 0, "product_key": "product-a",
                "model_specification_key": "dimension_equal_weight__momentum_trend",
                "variant_key": "top_k_equal_weight__k2", "frequency": "weekly",
                "benchmark_key": "spy_buy_and_hold", "benchmark_category": "product_primary",
                "cost_bps_per_side": 5, "template_key": "full_history",
                "initialization_policy": "carry_in", "as_of_date": date(2026, 1, 9),
                "simulation_end": date(2026, 1, 9), "status": "accepted",
                "availability_status": "eligible", "quality_status": "normal",
                "attempt_number": 1, "error_summary": None,
                "core_metrics": {"strategy.cagr": 0.12, "benchmark.cagr": 0.09,
                                 "strategy.sharpe_ratio": 1.1,
                                 "strategy.maximum_drawdown": -0.08},
            }],
        }

    def experiment_result(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        specification = self.experiment_overview()["specifications"][0]
        specification["result_artifact_id"] = artifact_id
        run_id = uuid.uuid4()
        return {
            "result_artifact_id": artifact_id, "specification": specification,
            "interval_result_artifact_id": uuid.uuid4(),
            "requested_start": date(2025, 1, 2), "requested_end": date(2026, 1, 9),
            "resolved_start": date(2025, 1, 2), "resolved_end": date(2026, 1, 9),
            "normalization_nav_date": date(2025, 1, 2), "observation_count": 252,
            "metric_value_count": 36, "run_attempt_id": run_id, "run_status": "completed",
            "started_at": datetime(2026, 1, 10, tzinfo=UTC),
            "completed_at": datetime(2026, 1, 10, tzinfo=UTC),
            "metrics": [{"series_role": "strategy", "metric_scope": "absolute",
                         "metric_key": "cagr", "name": "CAGR", "unit": "annual_ratio",
                         "value": 0.12, "value_status": "defined", "reason_code": None,
                         "observation_count": 252}],
            "events": [{"sequence_number": 1, "event_type": "run_started",
                        "severity": "info", "message": "Started",
                        "occurred_at": datetime(2026, 1, 10, tzinfo=UTC)}],
            "quality_checks": [{"check_key": "outputs_published", "scope_key": "global",
                                "status": "passed", "severity": "info",
                                "message": "All outputs published"}],
            "artifacts": [{"artifact_id": artifact_id, "role": "output",
                           "artifact_type": "experiment_result",
                           "artifact_key": "result-a"}],
        }

    def product_ranking(
        self, *, cohort_artifact_id: uuid.UUID | None, metric_key: str
    ) -> dict[str, Any]:
        cohort_id = cohort_artifact_id or uuid.uuid4()
        return {
            "cohorts": [{
                "artifact_id": cohort_id, "cohort_key": "weekly-full-5bps",
                "version_number": 1, "name": "Weekly Full 5 bps",
                "description": "Strict comparison context", "context_fingerprint": "d" * 64,
                "template_key": "full_history", "initialization_policy": "carry_in",
                "as_of_date": date(2026, 1, 9),
                "common_data_ready_date": date(2025, 1, 1),
                "common_simulation_start": date(2025, 1, 2),
                "common_metric_start": date(2025, 1, 2),
                "common_metric_end": date(2026, 1, 9), "currency": "USD",
                "member_count": 2, "benchmark_key": "spy_buy_and_hold",
                "cost_bps_per_side": 5, "required_warmup_observations": 253,
            }],
            "active_cohort_artifact_id": cohort_id, "selected_metric": metric_key,
            "ranking_direction": "higher_is_better", "candidate_count": 2,
            "ranked_count": 1,
            "entries": [{
                "rank": 1, "result_artifact_id": uuid.uuid4(),
                "product_artifact_id": uuid.uuid4(), "product_key": "product-a",
                "model_specification_key": "dimension_equal_weight__momentum_trend",
                "variant_key": "top_k_equal_weight__k2", "target_k": 2,
                "frequency": "weekly", "metric_value": 1.1, "value_status": "defined",
                "reason_code": None, "observation_count": 252,
                "core_metrics": {"strategy.cagr": 0.12},
            }, {
                "rank": None, "result_artifact_id": uuid.uuid4(),
                "product_artifact_id": uuid.uuid4(), "product_key": "product-b",
                "model_specification_key": "single_signal__rsi", "variant_key": "top_k__k2",
                "target_k": 2, "frequency": "monthly", "metric_value": None,
                "value_status": "undefined", "reason_code": "insufficient_observations",
                "observation_count": 20, "core_metrics": {},
            }],
        }

    def product_compare(self, *, result_artifact_ids: tuple[uuid.UUID, ...]) -> dict[str, Any]:
        return {
            "mode": "controlled", "changed_dimensions": ["k"],
            "blocking_context_fields": [],
            "entries": [{
                "result_artifact_id": item, "product_key": f"product-{ordinal}",
                "model_specification_key": "dimension_equal_weight__momentum_trend",
                "strategy_template_key": "top_k_equal_weight",
                "variant_key": f"top_k_equal_weight__k{ordinal + 1}",
                "target_k": ordinal + 1, "frequency": "weekly",
                "cost_bps_per_side": 5, "template_key": "full_history",
                "initialization_policy": "carry_in", "availability_status": "eligible",
                "quality_status": "normal", "resolved_start": date(2025, 1, 2),
                "resolved_end": date(2026, 1, 9),
                "metrics": [{"series_role": "strategy", "metric_scope": "absolute",
                    "metric_key": "cagr", "name": "CAGR", "unit": "annual_ratio",
                    "value": 0.1 + ordinal / 100, "value_status": "defined",
                    "reason_code": None, "observation_count": 252}],
            } for ordinal, item in enumerate(result_artifact_ids, start=1)],
        }

    def decision_explorer(
        self, *, result_artifact_id: uuid.UUID, decision_date: date | None
    ) -> dict[str, Any]:
        selected = decision_date or date(2026, 1, 9)
        return {
            "result_artifact_id": result_artifact_id,
            "target_path_artifact_id": uuid.uuid4(), "model_dataset_artifact_id": uuid.uuid4(),
            "model_specification_artifact_id": uuid.uuid4(), "universe_artifact_id": uuid.uuid4(),
            "data_bundle_artifact_id": uuid.uuid4(), "eligibility_artifact_id": uuid.uuid4(),
            "model_method_key": "weighted_mean",
            "available_dates": [date(2026, 1, 9), date(2026, 1, 2)],
            "selected_date": selected, "target_k": 2, "actual_holding_count": 2,
            "reserve_target_weight": 0,
            "positions": [{"asset_key": "iwd", "symbol": "IWD", "selected": True,
                "model_score": 0.8, "model_rank": 1, "trend_state": "positive",
                "target_weight": 0.5, "decision_reason": "selected_by_rank",
                "components": [{"dimension_key": "momentum_trend", "dimension_weight": 1,
                    "signal_key": "return_continuation", "signal_version_artifact_id": uuid.uuid4(),
                    "signal_dataset_artifact_id": uuid.uuid4(), "signal_score": 0.8,
                    "signal_state": "positive", "input_transform": "identity",
                    "component_weight": 1, "transformed_signal_score": 0.8,
                    "weighted_component_input": 0.8, "overall_contribution": 0.8,
                    "factor_key": "total_return", "factor_variant_key": "total_return__w252",
                    "factor_dataset_artifact_id": uuid.uuid4(), "factor_value": 0.12,
                    "data_bundle_artifact_id": uuid.uuid4()}]}],
        }


def _client() -> tuple[TestClient, FakeArtifactReader]:
    reader = FakeArtifactReader()
    return TestClient(create_app(reader)), reader


def test_health_capabilities_and_openapi_are_v2_read_only() -> None:
    client, _reader = _client()
    health = client.get("/api/v2/health")
    assert health.status_code == 200
    assert health.json()["context"] == {
        "api_version": "v2",
        "system_version": "0.2.0",
        "read_only": True,
    }
    capabilities = client.get("/api/v2/capabilities").json()
    assert capabilities["languages"] == ["zh-CN", "en"]
    assert "tainted" in capabilities["interface_states"]

    openapi = client.get("/api/v2/openapi.json").json()
    assert openapi["info"]["version"] == "0.2.0"
    assert all(
        set(methods).issubset({"get", "parameters"}) for methods in openapi["paths"].values()
    )


def test_artifact_list_supports_quality_pagination_filters_and_etag() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/artifacts?status=published&limit=10")
    assert response.status_code == 200
    assert response.json()["quality"] == {"state": "ok", "codes": []}
    assert response.json()["items"][0]["quality"]["state"] == "ok"
    assert response.json()["total"] == 1
    etag = response.headers["etag"]

    cached = client.get(
        "/api/v2/artifacts?status=published&limit=10",
        headers={"If-None-Match": etag},
    )
    assert cached.status_code == 304
    assert cached.content == b""

    empty = client.get("/api/v2/artifacts?artifact_type=other")
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_artifact_detail_lineage_and_errors_use_stable_contracts() -> None:
    client, reader = _client()
    detail = client.get(f"/api/v2/artifacts/{reader.artifact_id}")
    assert detail.status_code == 200
    assert detail.json()["lineage_url"].endswith("/lineage")

    lineage = client.get(f"/api/v2/artifacts/{reader.artifact_id}/lineage")
    assert lineage.status_code == 200
    assert lineage.json()["canonical_version"] == "canonical-json-v2"

    missing = client.get(f"/api/v2/artifacts/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    invalid = client.get("/api/v2/artifacts?status=unknown")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_request"

    assert client.post("/api/v2/artifacts").status_code == 405


def test_data_overview_reports_an_incomplete_published_chain() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/data/overview")
    assert response.status_code == 200
    assert response.json()["quality"] == {
        "state": "partial",
        "codes": ["data.incomplete_chain"],
    }
    assert response.json()["datasets"] == []
    assert "etag" in response.headers
    capabilities = client.get("/api/v2/capabilities").json()
    data_domain = next(item for item in capabilities["domains"] if item["key"] == "data")
    assert data_domain["availability"] == "available"


def test_factor_overview_reports_factor_properties_without_strategy_metrics() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/factors/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["quality"]["state"] == "ok"
    assert payload["datasets"][0]["variant_key"] == "total_return__w20"
    assert payload["datasets"][0]["median"] == 0.01
    assert "sharpe" not in response.text.lower()
    factor_domain = next(
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "factor"
    )
    assert factor_domain["availability"] == "available"


def test_signal_overview_is_frequency_explicit_and_keeps_strategy_metrics_out() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/signals/overview?frequency=monthly")
    assert response.status_code == 200
    payload = response.json()
    assert payload["frequency"] == "monthly"
    assert payload["quality"] == {
        "state": "warning",
        "codes": ["signal.diagnostic_warning"],
    }
    assert payload["signals"][0]["full"]["mean_rank_ic"] == 0.18
    assert payload["signals"][0]["stability"][0]["window_key"] == "year:2025"
    assert "sharpe" not in response.text.lower()
    signal_domain = next(
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "signal"
    )
    assert signal_domain["availability"] == "available"
    invalid = client.get("/api/v2/signals/overview?frequency=daily")
    assert invalid.status_code == 422


def test_model_overview_exposes_composition_diagnostics_and_controlled_ablation() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/models/overview?frequency=weekly")
    assert response.status_code == 200
    payload = response.json()
    assert payload["quality"] == {
        "state": "warning",
        "codes": ["model.diagnostic_warning"],
    }
    assert payload["models"][0]["dimensions"][0]["dimension_key"] == "momentum_trend"
    assert payload["models"][0]["full"]["mean_score_dispersion"] == 0.41
    assert payload["ablations"][0]["removed_dimension_key"] == "volatility_risk"
    assert "sharpe" not in response.text.lower()
    model_domain = next(
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "model"
    )
    assert model_domain["availability"] == "available"


def test_strategy_api_separates_rules_products_and_target_decisions() -> None:
    client, _reader = _client()
    overview = client.get("/api/v2/strategies/overview")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["rules"]["variants"][0]["target_k"] == 2
    assert payload["products"][0]["target_path_count"] == 1
    assert "sharpe" not in overview.text.lower()
    target_id = payload["target_paths"][0]["artifact_id"]
    detail = client.get(f"/api/v2/strategies/targets/{target_id}")
    assert detail.status_code == 200
    assert detail.json()["decisions"][0]["positions"][0]["decision_reason"] == "selected_by_rank"
    strategy_domain = next(
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "strategy"
    )
    assert strategy_domain["availability"] == "available"


def test_experiment_api_exposes_performance_only_at_strategy_aggregation_layer() -> None:
    client, _reader = _client()
    overview = client.get("/api/v2/experiments/overview")
    assert overview.status_code == 200
    payload = overview.json()
    assert payload["specifications"][0]["status"] == "accepted"
    assert payload["specifications"][0]["core_metrics"]["strategy.cagr"] == 0.12
    result_id = payload["specifications"][0]["result_artifact_id"]
    detail = client.get(f"/api/v2/experiments/results/{result_id}")
    assert detail.status_code == 200
    assert detail.json()["metrics"][0]["metric_key"] == "cagr"
    assert detail.json()["quality_checks"][0]["status"] == "passed"
    experiment_domain = next(
        item for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "experiment"
    )
    assert experiment_domain["availability"] == "available"


def test_product_ranking_is_scoped_to_one_immutable_cohort_and_one_metric() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/rankings/products?metric=net_sharpe")
    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_metric"] == "net_sharpe"
    assert payload["candidate_count"] == 2
    assert payload["ranked_count"] == 1
    assert payload["entries"][0]["rank"] == 1
    assert payload["entries"][1]["rank"] is None
    assert payload["entries"][1]["reason_code"] == "insufficient_observations"
    invalid = client.get("/api/v2/rankings/products?metric=black_box_score")
    assert invalid.status_code == 422


def test_controlled_compare_and_decision_explorer_are_explicit_read_only_views() -> None:
    client, _reader = _client()
    left, right = uuid.uuid4(), uuid.uuid4()
    comparison = client.get(
        f"/api/v2/compare/products?result_artifact_id={left}&result_artifact_id={right}"
    )
    assert comparison.status_code == 200
    assert comparison.json()["mode"] == "controlled"
    assert comparison.json()["changed_dimensions"] == ["k"]
    assert comparison.json()["entries"][0]["metrics"][0]["metric_key"] == "cagr"
    duplicate = client.get(
        f"/api/v2/compare/products?result_artifact_id={left}&result_artifact_id={left}"
    )
    assert duplicate.status_code == 422

    decision = client.get(f"/api/v2/experiments/results/{left}/decisions")
    assert decision.status_code == 200
    payload = decision.json()
    assert payload["positions"][0]["components"][0]["factor_key"] == "total_return"
    assert payload["positions"][0]["components"][0]["overall_contribution"] == 0.8


def test_committed_openapi_contract_matches_application() -> None:
    contract_path = Path(__file__).parents[2] / "v0.2" / "openapi.v2.json"
    committed = json.loads(contract_path.read_text(encoding="utf-8"))
    generated = create_app(FakeArtifactReader()).openapi()
    assert committed == generated
