from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from style_rotation.api.actor_context import TrustedLocalActorContext
from style_rotation.api.app import create_app
from style_rotation.api.query import _v021_nav_series
from style_rotation.signal.export_jobs import SignalExportJob, ValidatedSignalExport
from style_rotation.v022.product_monitoring import LifecyclePublication
from style_rotation.v022.suite_launch_batch import SuiteLaunchBatchRequest
from style_rotation.workspace.catalog import build_component_document
from style_rotation.workspace.options import build_workspace_options
from style_rotation.workspace.preview import build_compile_preview


def test_v021_chart_series_is_bounded_while_preserving_endpoints() -> None:
    points = [
        {
            "nav_date": f"{2000 + index // 365:04d}-01-01",
            "strategy_wealth": 1.0 + index / 10_000,
            "benchmark_wealth": 1.0 + index / 20_000,
        }
        for index in range(5_023)
    ]

    result = _v021_nav_series({"nav_series": points})

    assert len(result) == 600
    assert result[0]["nav_date"] == points[0]["nav_date"]
    assert result[-1]["nav_date"] == points[-1]["nav_date"]


class FakeArtifactReader:
    def __init__(self) -> None:
        self.artifact_id = uuid.uuid4()
        self.experiment_research_suite_id: uuid.UUID | None = None
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

    def asset_catalog(
        self,
        *,
        search: str | None,
        category: str | None,
        maturity: str | None,
        tradability: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        del maturity, tradability
        security_id = uuid.UUID("00000000-0000-0000-0000-000000000021")
        item = {
            "security_id": security_id,
            "asset_id": uuid.uuid4(),
            "asset_key": "aapl",
            "name": "Apple Inc.",
            "category_key": "stocks",
            "asset_class": "Equity",
            "instrument_type": "Common Stock",
            "status": "active",
            "symbol": "AAPL",
            "aliases": ["Apple", "APPL"],
            "venue_mic": "XNAS",
            "currency": "USD",
            "calendar_key": "XNYS",
            "tradability": "tradable",
            "tags": ["technology", "large_cap"],
            "maturity": "research_ready",
            "target_maturity": "strategy_ready",
            "missing_requirements": ["pit_master_data"],
            "canonical_data_available": True,
            "selectable": True,
            "v022_candidate_selectable": True,
            "v022_candidate_reason_codes": [],
            "v022_candidate_dataset_key": "us_sp500_historical_daily_free_research_v1",
            "v022_candidate_dataset_version": 4,
        }
        matches = category in (None, "stocks") and (
            search is None or search.casefold() in "aapl apple appl technology"
        )
        items = [item] if matches and offset == 0 and limit else []
        return {
            "release_artifact_id": self.artifact_id,
            "release_version_number": 21001,
            "catalog_version": "0.21.0",
            "as_of_date": "2026-08-05",
            "total": 1 if matches else 0,
            "limit": limit,
            "offset": offset,
            "categories": [
                {
                    "category_key": "stocks",
                    "name": "Stocks",
                    "description": "US stable security identities",
                    "asset_count": 1,
                }
            ],
            "asset_sets": [],
            "items": items,
        }

    def asset_series(
        self, security_id: uuid.UUID, *, start: str | None, end: str | None
    ) -> dict[str, Any]:
        del start, end
        if security_id != uuid.UUID("00000000-0000-0000-0000-000000000021"):
            raise LookupError("Published canonical series not found for asset")
        return {
            "security_id": security_id,
            "asset_key": "aapl",
            "symbol": "AAPL",
            "dataset_artifact_id": self.artifact_id,
            "dataset_version_number": 1,
            "coverage_start": date(2026, 8, 3),
            "coverage_end": date(2026, 8, 4),
            "points": [
                {
                    "session_date": date(2026, 8, 3),
                    "open": 200.0,
                    "high": 205.0,
                    "low": 199.0,
                    "close": 204.0,
                    "adjusted_close": 204.0,
                    "volume": 1000000,
                },
                {
                    "session_date": date(2026, 8, 4),
                    "open": 204.0,
                    "high": 206.0,
                    "low": 202.0,
                    "close": 205.0,
                    "adjusted_close": 205.0,
                    "volume": 900000,
                },
            ],
        }

    def workspace_options(
        self,
        *,
        frequency: str,
        selected_factor_variants: tuple[str, ...],
        selected_signals: tuple[str, ...],
        selected_models: tuple[str, ...] = (),
        selected_strategies: tuple[str, ...] = (),
        selected_assets: tuple[uuid.UUID, ...] = (),
        selected_asset_data_inputs: dict[str, tuple[str, ...]] | None = None,
    ) -> dict[str, Any]:
        document = build_component_document(
            Path("v0.2/catalogs/factors.v0.2.0.json"),
            Path("v0.2/catalogs/signals.v0.2.0.json"),
            Path("v0.21/catalogs/workspace_contracts.v0.21.0.json"),
        )
        return {
            "catalog_artifact_id": self.artifact_id,
            **build_workspace_options(
                document,
                frequency=frequency,  # type: ignore[arg-type]
                selected_factor_variants=selected_factor_variants,
                selected_signals=selected_signals,
                selected_models=selected_models,
                selected_strategies=selected_strategies,
                selected_assets=tuple(
                    {
                        "security_id": security_id,
                        "instrument_type": "Equity ETF",
                        "selectable": True,
                        "pit_sector_available": False,
                    }
                    for security_id in selected_assets
                ),
                selected_asset_data_inputs=selected_asset_data_inputs,
            ),
        }

    def factor_values(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        return {
            "variant_key": "total_return__w120",
            "content_hash": "f" * 64,
            "rows": [
                {
                    "observation_date": date(2026, 8, 4),
                    "asset_key": "aapl",
                    "symbol": "AAPL",
                    "value": 0.12,
                }
            ],
        }

    def signal_values(self, version_key: str) -> dict[str, Any]:
        return {
            "version_key": version_key,
            "content_hash": "e" * 64,
            "rows": [
                {
                    "observation_date": date(2026, 8, 4),
                    "asset_key": "aapl",
                    "symbol": "AAPL",
                    "score": 0.8,
                    "state": "active",
                    "event": False,
                }
            ],
        }

    def workspace_compile_preview(
        self,
        *,
        frequency: str,
        asset_security_ids: tuple[uuid.UUID, ...],
        asset_data_inputs: dict[str, tuple[str, ...]],
        factor_variant_keys: tuple[str, ...],
        signal_version_keys: tuple[str, ...],
        model_preset_keys: tuple[str, ...],
        model_target_keys: tuple[str, ...],
        strategy_preset_keys: tuple[str, ...],
    ) -> dict[str, Any]:
        document = build_component_document(
            Path("v0.2/catalogs/factors.v0.2.0.json"),
            Path("v0.2/catalogs/signals.v0.2.0.json"),
            Path("v0.21/catalogs/workspace_contracts.v0.21.0.json"),
        )
        result = build_compile_preview(
            document,
            frequency=frequency,  # type: ignore[arg-type]
            asset_security_ids=tuple(str(item) for item in asset_security_ids),
            asset_data_inputs=asset_data_inputs,
            selected_assets=tuple(
                {
                    "security_id": item,
                    "instrument_type": "Equity ETF",
                    "selectable": True,
                    "pit_sector_available": False,
                }
                for item in asset_security_ids
            ),
            factor_variant_keys=factor_variant_keys,
            signal_version_keys=signal_version_keys,
            model_preset_keys=model_preset_keys,
            strategy_preset_keys=strategy_preset_keys,
        )
        return {"catalog_artifact_id": self.artifact_id, **result}

    def product_catalog(self) -> dict[str, Any]:
        return {"items": []}

    def product_detail(self, enrollment_id: uuid.UUID) -> dict[str, Any]:
        raise LookupError(f"Product Research Candidate not found: {enrollment_id}")

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

    def experiment_overview(
        self,
        *,
        research_suite_id: uuid.UUID | None = None,
        status: str | None = None,
        template_key: str | None = None,
        frequency: str | None = None,
        cost_bps_per_side: float | None = None,
        ranking_metric: str = "strategy.sharpe_ratio",
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        del ranking_metric
        self.experiment_research_suite_id = research_suite_id
        suite_id, specification_id, result_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        item = {
            "artifact_id": specification_id,
            "result_artifact_id": result_id,
            "suite_artifact_id": suite_id,
            "cell_key": "weekly-k2-5bps-full",
            "ordinal": 0,
            "product_key": "product-a",
            "model_specification_key": "dimension_equal_weight__momentum_trend",
            "variant_key": "top_k_equal_weight__k2",
            "frequency": "weekly",
            "benchmark_key": "spy_buy_and_hold",
            "benchmark_category": "product_primary",
            "cost_bps_per_side": 5,
            "template_key": "full_history",
            "initialization_policy": "carry_in",
            "as_of_date": date(2026, 1, 9),
            "simulation_end": date(2026, 1, 9),
            "status": "accepted",
            "availability_status": "eligible",
            "quality_status": "normal",
            "attempt_number": 1,
            "error_summary": None,
            "core_metrics": {
                "strategy.cagr": 0.12,
                "benchmark.cagr": 0.09,
                "strategy.sharpe_ratio": 1.1,
                "strategy.maximum_drawdown": -0.08,
            },
        }
        matches = (
            (status is None or item["status"] == status)
            and (template_key is None or item["template_key"] == template_key)
            and (frequency is None or item["frequency"] == frequency)
            and (
                cost_bps_per_side is None
                or item["cost_bps_per_side"] == cost_bps_per_side
            )
        )
        specifications = [item][offset : offset + limit] if matches else []
        return {
            "suites": [
                {
                    "artifact_id": suite_id,
                    "suite_key": "formal-v02",
                    "version_number": 1,
                    "name": "Formal v0.2 experiments",
                    "description": "Comparable cells",
                    "specification_count": 1,
                }
            ],
            "specifications": specifications,
            "total_specification_count": 1,
            "filtered_specification_count": 1 if matches else 0,
            "accepted_count": 1,
            "failed_count": 0,
            "running_count": 0,
            "pending_count": 0,
            "limit": limit,
            "offset": offset,
        }

    def experiment_result(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        specification = self.experiment_overview()["specifications"][0]
        specification["result_artifact_id"] = artifact_id
        run_id = uuid.uuid4()
        return {
            "result_artifact_id": artifact_id,
            "specification": specification,
            "interval_result_artifact_id": uuid.uuid4(),
            "requested_start": date(2025, 1, 2),
            "requested_end": date(2026, 1, 9),
            "resolved_start": date(2025, 1, 2),
            "resolved_end": date(2026, 1, 9),
            "normalization_nav_date": date(2025, 1, 2),
            "observation_count": 252,
            "metric_value_count": 36,
            "run_attempt_id": run_id,
            "run_status": "completed",
            "started_at": datetime(2026, 1, 10, tzinfo=UTC),
            "completed_at": datetime(2026, 1, 10, tzinfo=UTC),
            "metrics": [
                {
                    "series_role": "strategy",
                    "metric_scope": "absolute",
                    "metric_key": "cagr",
                    "name": "CAGR",
                    "unit": "annual_ratio",
                    "value": 0.12,
                    "value_status": "defined",
                    "reason_code": None,
                    "observation_count": 252,
                }
            ],
            "events": [
                {
                    "sequence_number": 1,
                    "event_type": "run_started",
                    "severity": "info",
                    "message": "Started",
                    "occurred_at": datetime(2026, 1, 10, tzinfo=UTC),
                }
            ],
            "quality_checks": [
                {
                    "check_key": "outputs_published",
                    "scope_key": "global",
                    "status": "passed",
                    "severity": "info",
                    "message": "All outputs published",
                }
            ],
            "artifacts": [
                {
                    "artifact_id": artifact_id,
                    "role": "output",
                    "artifact_type": "experiment_result",
                    "artifact_key": "result-a",
                }
            ],
            "nav_series": [
                {
                    "nav_date": date(2025, 1, 2),
                    "strategy_wealth": 1.0,
                    "benchmark_wealth": 1.0,
                    "excess_wealth": 1.0,
                    "drawdown": 0.0,
                },
                {
                    "nav_date": date(2026, 1, 9),
                    "strategy_wealth": 1.12,
                    "benchmark_wealth": 1.09,
                    "excess_wealth": 1.12 / 1.09,
                    "drawdown": 0.0,
                },
            ],
            "promotion_eligible": False,
            "promotion_reason_codes": [
                "v021_six_cell_qualification_bundle_missing",
                "pit_universe_gate_open",
                "terminal_event_gate_open",
                "impact_policy_gate_open",
            ],
            "qualification_bundle_artifact_id": None,
        }

    def v022_experiment_leaderboard(
        self,
        *,
        frequency: str,
        ranking_cohort_release_id: uuid.UUID | None,
        sort: str,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        release_id = ranking_cohort_release_id or uuid.UUID(int=101)
        return {
            "comparison_context": {
                "ranking_cohort_release_id": release_id,
                "ranking_cohort_artifact_id": uuid.UUID(int=102),
                "ranking_version_number": 1,
                "evaluation_cohort_version_id": uuid.UUID(int=103),
                "evaluation_cohort_fingerprint": "c" * 64,
                "cohort_key": f"sp500_{frequency}_v1",
                "frequency": frequency,
                "warmup_start": date(2004, 12, 31),
                "evaluation_start": date(2007, 1, 3),
                "evaluation_end": date(2026, 6, 30),
                "benchmark_key": "spy",
                "cost_bps_per_side": "5.000000",
                "execution_delay_sessions": 1,
                "price_semantics": (
                    "historical_constituent_pit__frozen_retrospective_yahoo_prices"
                ),
                "member_count": 1,
            },
            "available_frequencies": ["weekly", "monthly"],
            "sort": sort,
            "total": 1,
            "limit": limit,
            "offset": offset,
            "rows": [
                {
                    "rank": 1,
                    "result_evidence_snapshot_id": uuid.UUID(int=104),
                    "result_artifact_id": uuid.UUID(int=105),
                    "configuration_snapshot_id": uuid.UUID(int=106),
                    "configuration_fingerprint": "d" * 64,
                    "configuration": {"frequency": frequency},
                    "display": {"name": "K2 weekly"},
                    "cagr": "0.12",
                    "benchmark_cagr": "0.08",
                    "cagr_spread": "0.04",
                    "sharpe_ratio": "1.50",
                    "maximum_drawdown": "-0.20",
                    "product_candidate": False,
                    "product_definition_id": None,
                    "execution_version_id": None,
                    "product_enrollment_id": None,
                }
            ],
        }

    def v022_experiment_identity_detail(self, evidence_id: uuid.UUID) -> dict[str, Any]:
        return {
            "result_evidence_snapshot_id": evidence_id,
            "evidence_artifact_id": uuid.UUID(int=201),
            "result_artifact_id": uuid.UUID(int=202),
            "evidence_class": "locked_historical_test",
            "configuration_snapshot_id": uuid.UUID(int=203),
            "configuration_fingerprint": "d" * 64,
            "configuration": {"frequency": "weekly"},
            "display": {"name": "K2 weekly"},
            "created_at": datetime(2026, 8, 16, tzinfo=UTC),
            "comparison_context": {
                "evaluation_cohort_version_id": uuid.UUID(int=204),
                "evaluation_cohort_fingerprint": "c" * 64,
                "cohort_key": "sp500_weekly_v1",
                "frequency": "weekly",
                "warmup_start": date(2004, 12, 31),
                "evaluation_start": date(2007, 1, 3),
                "evaluation_end": date(2026, 6, 30),
                "benchmark_key": "spy",
                "cost_bps_per_side": "5.000000",
                "execution_delay_sessions": 1,
                "price_semantics": (
                    "historical_constituent_pit__frozen_retrospective_yahoo_prices"
                ),
            },
            "outcome": "accepted",
            "quality_status": "passed",
            "effective_start": date(2007, 1, 3),
            "effective_end": date(2026, 6, 30),
            "core_metrics": {
                "cagr": "0.12",
                "benchmark_cagr": "0.08",
                "cagr_spread": "0.04",
                "sharpe_ratio": "1.50",
                "maximum_drawdown": "-0.20",
            },
            "metrics": {"absolute_metrics": [], "relative_metrics": []},
            "product": {"is_candidate": False, "is_enrolled": False},
            "evidence": {"evidence_class": "locked_historical_test"},
            "evidence_quality": {"state": "passed", "outcome": "accepted"},
            "comparisons": [],
            "matched_baselines": [],
        }

    def v022_experiment_result_series(
        self, evidence_id: uuid.UUID, *, max_points: int
    ) -> dict[str, Any]:
        points = [
            {
                "session_date": date(2007, 1, 3),
                "strategy_nav": "1",
                "benchmark_nav": "1",
                "excess_nav": "1",
                "drawdown": "0",
            },
            {
                "session_date": date(2026, 6, 30),
                "strategy_nav": "2",
                "benchmark_nav": "1.5",
                "excess_nav": "1.333333333333333333333333333",
                "drawdown": "0",
            },
        ][:max_points]
        return {
            "result_evidence_snapshot_id": evidence_id,
            "effective_start": date(2007, 1, 3),
            "effective_end": date(2026, 6, 30),
            "total_points": 2,
            "returned_points": len(points),
            "points": points,
        }

    def product_ranking(
        self, *, cohort_artifact_id: uuid.UUID | None, metric_key: str
    ) -> dict[str, Any]:
        cohort_id = cohort_artifact_id or uuid.uuid4()
        return {
            "cohorts": [
                {
                    "artifact_id": cohort_id,
                    "cohort_key": "weekly-full-5bps",
                    "version_number": 1,
                    "name": "Weekly Full 5 bps",
                    "description": "Strict comparison context",
                    "context_fingerprint": "d" * 64,
                    "template_key": "full_history",
                    "initialization_policy": "carry_in",
                    "target_k": 2,
                    "frequency": "weekly",
                    "as_of_date": date(2026, 1, 9),
                    "common_data_ready_date": date(2025, 1, 1),
                    "common_simulation_start": date(2025, 1, 2),
                    "common_metric_start": date(2025, 1, 2),
                    "common_metric_end": date(2026, 1, 9),
                    "currency": "USD",
                    "member_count": 2,
                    "benchmark_key": "spy_buy_and_hold",
                    "cost_bps_per_side": 5,
                    "required_warmup_observations": 253,
                }
            ],
            "active_cohort_artifact_id": cohort_id,
            "selected_metric": metric_key,
            "ranking_direction": "higher_is_better",
            "candidate_count": 2,
            "ranked_count": 1,
            "entries": [
                {
                    "rank": 1,
                    "result_artifact_id": uuid.uuid4(),
                    "product_artifact_id": uuid.uuid4(),
                    "product_key": "product-a",
                    "model_specification_key": "dimension_equal_weight__momentum_trend",
                    "variant_key": "top_k_equal_weight__k2",
                    "target_k": 2,
                    "frequency": "weekly",
                    "metric_value": 1.1,
                    "value_status": "defined",
                    "reason_code": None,
                    "observation_count": 252,
                    "core_metrics": {"strategy.cagr": 0.12},
                },
                {
                    "rank": None,
                    "result_artifact_id": uuid.uuid4(),
                    "product_artifact_id": uuid.uuid4(),
                    "product_key": "product-b",
                    "model_specification_key": "single_signal__rsi",
                    "variant_key": "top_k__k2",
                    "target_k": 2,
                    "frequency": "monthly",
                    "metric_value": None,
                    "value_status": "undefined",
                    "reason_code": "insufficient_observations",
                    "observation_count": 20,
                    "core_metrics": {},
                },
            ],
        }

    def product_compare(self, *, result_artifact_ids: tuple[uuid.UUID, ...]) -> dict[str, Any]:
        return {
            "mode": "controlled",
            "changed_dimensions": ["k"],
            "blocking_context_fields": [],
            "entries": [
                {
                    "result_artifact_id": item,
                    "product_key": f"product-{ordinal}",
                    "model_specification_key": "dimension_equal_weight__momentum_trend",
                    "strategy_template_key": "top_k_equal_weight",
                    "variant_key": f"top_k_equal_weight__k{ordinal + 1}",
                    "target_k": ordinal + 1,
                    "frequency": "weekly",
                    "cost_bps_per_side": 5,
                    "template_key": "full_history",
                    "initialization_policy": "carry_in",
                    "availability_status": "eligible",
                    "quality_status": "normal",
                    "resolved_start": date(2025, 1, 2),
                    "resolved_end": date(2026, 1, 9),
                    "metrics": [
                        {
                            "series_role": "strategy",
                            "metric_scope": "absolute",
                            "metric_key": "cagr",
                            "name": "CAGR",
                            "unit": "annual_ratio",
                            "value": 0.1 + ordinal / 100,
                            "value_status": "defined",
                            "reason_code": None,
                            "observation_count": 252,
                        }
                    ],
                }
                for ordinal, item in enumerate(result_artifact_ids, start=1)
            ],
        }

    def decision_explorer(
        self, *, result_artifact_id: uuid.UUID, decision_date: date | None
    ) -> dict[str, Any]:
        selected = decision_date or date(2026, 1, 9)
        return {
            "result_artifact_id": result_artifact_id,
            "target_path_artifact_id": uuid.uuid4(),
            "model_dataset_artifact_id": uuid.uuid4(),
            "model_specification_artifact_id": uuid.uuid4(),
            "universe_artifact_id": uuid.uuid4(),
            "data_bundle_artifact_id": uuid.uuid4(),
            "eligibility_artifact_id": uuid.uuid4(),
            "model_method_key": "weighted_mean",
            "available_dates": [date(2026, 1, 9), date(2026, 1, 2)],
            "selected_date": selected,
            "target_k": 2,
            "actual_holding_count": 2,
            "reserve_target_weight": 0,
            "positions": [
                {
                    "asset_key": "iwd",
                    "symbol": "IWD",
                    "selected": True,
                    "model_score": 0.8,
                    "model_rank": 1,
                    "trend_state": "positive",
                    "target_weight": 0.5,
                    "decision_reason": "selected_by_rank",
                    "components": [
                        {
                            "dimension_key": "momentum_trend",
                            "dimension_weight": 1,
                            "dimension_transform": "identity",
                            "signal_key": "return_continuation",
                            "signal_version_artifact_id": uuid.uuid4(),
                            "signal_dataset_artifact_id": uuid.uuid4(),
                            "signal_score": 0.8,
                            "signal_state": "positive",
                            "input_transform": "identity",
                            "component_weight": 1,
                            "transformed_signal_score": 0.8,
                            "weighted_component_input": 0.8,
                            "overall_contribution": 0.8,
                            "factor_key": "total_return",
                            "factor_variant_key": "total_return__w252",
                            "factor_dataset_artifact_id": uuid.uuid4(),
                            "factor_value": 0.12,
                            "data_bundle_artifact_id": uuid.uuid4(),
                        }
                    ],
                }
            ],
        }


def _client() -> tuple[TestClient, FakeArtifactReader]:
    reader = FakeArtifactReader()
    return TestClient(create_app(reader)), reader


class FakeSignalExportJobs:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None
        self.job = SignalExportJob(
            export_job_id=uuid.uuid4(),
            work_item_id=uuid.uuid4(),
            request_fingerprint="f" * 64,
            status="queued",
            stage="queued",
            attempt_count=0,
            max_attempts=3,
            failure_class=None,
            failure_details={},
            content_hash=None,
            byte_size=None,
            filename=None,
            expires_at=None,
        )
        self.artifact: ValidatedSignalExport | None = None

    def enqueue(self, request: dict[str, Any]) -> SignalExportJob:
        self.request = request
        return self.job

    def get(self, export_job_id: uuid.UUID) -> SignalExportJob:
        if export_job_id != self.job.export_job_id:
            raise LookupError("missing export")
        return self.job

    def validated_download(self, export_job_id: uuid.UUID) -> ValidatedSignalExport:
        self.get(export_job_id)
        if self.artifact is None:
            raise LookupError("missing export artifact")
        return self.artifact


class FakeProductCommands:
    def __init__(self) -> None:
        self.predictive_result_artifact_id = uuid.uuid4()
        self.predictive_cell_artifact_id = uuid.uuid4()

    def evaluate_promotion(self, result_artifact_id: uuid.UUID) -> dict[str, Any]:
        return {
            "eligible": True,
            "reason_codes": [],
            "warning_codes": ["candidate_exploratory_suite"],
            "compiled_strategy_version_id": uuid.uuid4(),
            "source_suite_artifact_id": uuid.uuid4(),
            "comparison_context_id": uuid.uuid4(),
            "qualification_bundle_artifact_id": None,
            "result_artifact_ids": [result_artifact_id],
            "cell_artifact_ids": [uuid.uuid4()],
            "predictive_result_artifact_ids": [self.predictive_result_artifact_id],
            "predictive_cell_artifact_ids": [self.predictive_cell_artifact_id],
            "selection_context": {"selection_mode": "manual_experiment_detail_promotion"},
        }


class FakeV022ProductPromotion:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    def promote_and_enroll(self, **request: Any) -> dict[str, Any]:
        self.request = request
        identifiers = [uuid.uuid4() for _ in range(14)]
        return {
            "result_evidence_snapshot_id": request["result_evidence_snapshot_id"],
            "product_definition_id": identifiers[0],
            "product_definition_artifact_id": identifiers[1],
            "execution_version_id": identifiers[2],
            "execution_version_artifact_id": identifiers[3],
            "qualification_version_id": identifiers[4],
            "qualification_version_artifact_id": identifiers[5],
            "monitoring_policy_version_id": identifiers[6],
            "monitoring_policy_version_artifact_id": identifiers[7],
            "product_data_disclosure_id": identifiers[8],
            "product_data_disclosure_artifact_id": identifiers[9],
            "product_data_disclosure_fingerprint": "d" * 64,
            "product_eligibility": "eligible_with_warnings",
            "warning_codes": ["free_data_research_product"],
            "product_enrollment_id": identifiers[10],
            "enrollment_artifact_id": identifiers[11],
            "decision_schedule_version_id": identifiers[12],
            "decision_schedule_artifact_id": identifiers[13],
            "first_eligible_decision_session_id": uuid.uuid4(),
            "product_ensemble_state_id": uuid.uuid4(),
            "product_ensemble_state_artifact_id": uuid.uuid4(),
            "product_ensemble_state_fingerprint": "e" * 64,
            "version_number": request["version_number"],
            "lifecycle": "active",
            "reused": False,
        }


class FakeV022ProductLifecycle:
    def __init__(self) -> None:
        self.request: dict[str, Any] | None = None

    def publish(self, **request: Any) -> LifecyclePublication:
        self.request = request
        return LifecyclePublication(
            uuid.uuid4(),
            uuid.uuid4(),
            request["expected_sequence"],
            "active",
            request["target"],
            "c" * 64,
            False,
        )


class FakeCommandIdempotency:
    def execute(self, **command: Any) -> dict[str, Any]:
        return command["operation"]()


class FakeSuiteLaunchBatches:
    def __init__(self) -> None:
        self.request: SuiteLaunchBatchRequest | None = None
        self.batch_id = uuid.uuid4()

    def submit(self, request: SuiteLaunchBatchRequest) -> dict[str, Any]:
        self.request = request
        return self._document(reused=False)

    def status(self, suite_launch_batch_id: uuid.UUID) -> dict[str, Any]:
        assert suite_launch_batch_id == self.batch_id
        return self._document(reused=True)

    def _document(self, *, reused: bool) -> dict[str, Any]:
        assert self.request is not None
        return {
            "suite_launch_batch_id": self.batch_id,
            "source_graph_draft_id": self.request.source_graph_draft_id,
            "source_graph_draft_revision": self.request.source_graph_draft_revision,
            "batch_fingerprint": "a" * 64,
            "status": "submitted",
            "children": [
                {
                    "frequency": frequency,
                    "graph_draft_id": uuid.uuid4(),
                    "graph_draft_revision": 1,
                    "compiled_research_graph_id": uuid.uuid4(),
                    "research_suite_id": uuid.uuid4(),
                    "status": "not_started",
                    "total": 0,
                    "terminal": 0,
                    "status_counts": {},
                    "complete": False,
                }
                for frequency in self.request.frequencies
            ],
            "reused": reused,
        }


def test_graph_suite_launch_batch_submits_both_frequencies_and_is_readable() -> None:
    batches = FakeSuiteLaunchBatches()
    draft_id = uuid.uuid4()
    graph_id = uuid.uuid4()
    command_id = uuid.uuid4()
    client = TestClient(
        create_app(
            FakeArtifactReader(),
            graph_suite_batches=batches,
            actor_context=TrustedLocalActorContext(
                actor_key="local", operator_enabled=False
            ),
        )
    )

    submitted = client.post(
        "/api/v2/workspace/graph-suite-launch-batches",
        json={
            "source_graph_draft_id": str(draft_id),
            "source_graph_draft_revision": 7,
            "source_compiled_research_graph_id": str(graph_id),
            "actor_key": "local",
            "idempotency_key": str(command_id),
            "frequencies": ["weekly", "monthly"],
        },
    )

    assert submitted.status_code == 200
    assert [item["frequency"] for item in submitted.json()["children"]] == [
        "weekly",
        "monthly",
    ]
    assert batches.request is not None
    assert batches.request.source_graph_draft_id == draft_id
    assert batches.request.source_compiled_research_graph_id == graph_id
    replay = client.get(
        f"/api/v2/workspace/graph-suite-launch-batches/{batches.batch_id}"
    )
    assert replay.status_code == 200
    assert replay.json()["reused"] is True


def test_promotion_qualification_response_includes_predictive_evidence_ids() -> None:
    commands = FakeProductCommands()
    result_artifact_id = uuid.uuid4()
    client = TestClient(
        create_app(FakeArtifactReader(), commands=commands)  # type: ignore[arg-type]
    )

    response = client.get(
        f"/api/v2/experiments/results/{result_artifact_id}/qualification"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["predictive_result_artifact_ids"] == [
        str(commands.predictive_result_artifact_id)
    ]
    assert payload["predictive_cell_artifact_ids"] == [
        str(commands.predictive_cell_artifact_id)
    ]


def test_v022_product_lifecycle_uses_operator_identity_and_sequence() -> None:
    lifecycle = FakeV022ProductLifecycle()
    enrollment_id = uuid.uuid4()
    client = TestClient(
        create_app(
            FakeArtifactReader(),
            v022_product_lifecycle=lifecycle,  # type: ignore[arg-type]
            v022_command_idempotency=FakeCommandIdempotency(),  # type: ignore[arg-type]
            actor_context=TrustedLocalActorContext(
                actor_key="local_operator", operator_enabled=True
            ),
        )
    )

    response = client.post(
        f"/api/v2/v022/products/{enrollment_id}/lifecycle",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "local_operator",
            "expected_sequence": 1,
            "target": "suspended",
            "reason_code": "operator_review",
            "reason": "Pause scheduled decisions for review.",
            "requested_at": "2026-08-13T12:00:00+00:00",
            "effective_at": "2026-08-13T12:05:00+00:00",
        },
    )

    assert response.status_code == 200
    assert response.json()["product_enrollment_id"] == str(enrollment_id)
    assert response.json()["to_lifecycle"] == "suspended"
    assert response.json()["sequence_number"] == 1
    assert lifecycle.request is not None
    assert lifecycle.request["requested_by"] == "local_operator"


def test_health_capabilities_and_openapi_expose_only_controlled_commands() -> None:
    client, _reader = _client()
    health = client.get("/api/v2/health")
    assert health.status_code == 200
    assert health.json()["context"] == {
        "api_version": "v2",
        "system_version": "0.22.0",
        "read_only": False,
    }
    capabilities = client.get("/api/v2/capabilities").json()
    assert capabilities["languages"] == ["zh-CN", "en"]
    assert "tainted" in capabilities["interface_states"]

    openapi = client.get("/api/v2/openapi.json").json()
    assert openapi["info"]["version"] == "0.22.0"
    for path, methods in openapi["paths"].items():
        if path in {
            "/api/v2/workspace/compile-preview",
            "/api/v2/workspace/graph-preview",
        }:
            allowed = {"get", "parameters", "post"}
        elif path == "/api/v2/workspace/drafts/{researcher_id}/{draft_key}":
            allowed = {"get", "parameters", "put"}
        elif path in {
            "/api/v2/workspace/graph-drafts",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/clones",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/events",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/change-previews",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/rebase-previews",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/change-previews/"
            "{impact_token}/confirm",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/compile",
            "/api/v2/workspace/graph-drafts/{graph_draft_id}/reset",
            "/api/v2/workspace/graph-suites",
            "/api/v2/workspace/graph-suite-launch-batches",
            "/api/v2/workspace/suites",
            "/api/v2/signals/research-export.zip",
            "/api/v2/workspace/suites/{research_suite_id}/cancel",
            "/api/v2/experiments/results/{artifact_id}/promote",
            "/api/v2/v022/experiments/{evidence_id}/promote",
            "/api/v2/v022/experiment-results/{evidence_id}/promote-and-enroll",
            "/api/v2/v022/product-candidates/{execution_version_id}/enroll",
                "/api/v2/v022/products/{enrollment_id}/lifecycle",
                "/api/v2/v022/asset-data-exports/preview",
                "/api/v2/v022/asset-data-exports",
                "/api/v2/v022/asset-data-exports/{export_job_id}/cancel",
                "/api/v2/products/{enrollment_id}/lifecycle",
            "/api/v2/products/{enrollment_id}/reviews",
            "/api/v2/products/alerts/{alert_id}/status",
        }:
            allowed = {"get", "parameters", "post"}
        else:
            allowed = {"get", "parameters"}
        assert set(methods).issubset(allowed)


def test_v022_leaderboard_keeps_frequency_and_sort_in_one_frozen_context() -> None:
    client, _reader = _client()

    response = client.get(
        "/api/v2/v022/experiments/leaderboard",
        params={"frequency": "monthly", "sort": "cagr_spread"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["comparison_context"]["frequency"] == "monthly"
    assert payload["comparison_context"]["evaluation_start"] == "2007-01-03"
    assert payload["sort"] == "cagr_spread"
    assert payload["rows"][0]["cagr_spread"] == "0.04"
    assert payload["rows"][0]["rank"] == 1


def test_v022_experiment_detail_projects_frozen_metrics_and_product_state() -> None:
    client, _reader = _client()
    evidence_id = uuid.UUID(int=104)

    response = client.get(f"/api/v2/v022/experiments/{evidence_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["comparison_context"]["frequency"] == "weekly"
    assert payload["comparison_context"]["evaluation_start"] == "2007-01-03"
    assert payload["core_metrics"]["cagr"] == "0.12"
    assert payload["core_metrics"]["cagr_spread"] == "0.04"
    assert payload["product"]["is_candidate"] is False


def test_v022_experiment_series_returns_strategy_benchmark_excess_and_drawdown() -> None:
    client, _reader = _client()
    evidence_id = uuid.UUID(int=104)

    response = client.get(
        f"/api/v2/v022/experiments/{evidence_id}/series",
        params={"max_points": 600},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["effective_start"] == "2007-01-03"
    assert payload["effective_end"] == "2026-06-30"
    assert payload["returned_points"] == 2
    assert payload["points"][-1]["strategy_nav"] == "2"
    assert payload["points"][-1]["benchmark_nav"] == "1.5"
    assert payload["points"][-1]["excess_nav"].startswith("1.3333")


def test_v022_promote_and_enroll_is_one_evidence_scoped_command() -> None:
    promotion = FakeV022ProductPromotion()
    client = TestClient(
        create_app(
            FakeArtifactReader(),
            v022_product_promotions=promotion,  # type: ignore[arg-type]
        )
    )
    evidence_id = uuid.UUID(int=104)

    response = client.post(
        f"/api/v2/v022/experiment-results/{evidence_id}/promote-and-enroll",
        json={
            "idempotency_key": str(uuid.uuid4()),
            "researcher_id": "local",
            "product_key": "weekly_k2_candidate",
            "name": "Weekly K2 Candidate",
            "description": "Promoted from one exact v0.22 result.",
            "version_number": 1,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["result_evidence_snapshot_id"] == str(evidence_id)
    assert payload["lifecycle"] == "active"
    assert len(payload["product_ensemble_state_fingerprint"]) == 64
    assert payload["quality"]["codes"] == ["free_data_research_product"]
    assert promotion.request is not None
    assert promotion.request["result_evidence_snapshot_id"] == evidence_id


def test_signal_research_export_enqueues_and_exposes_persistent_status() -> None:
    reader = FakeArtifactReader()
    export_jobs = FakeSignalExportJobs()
    client = TestClient(create_app(reader, signal_export_jobs=export_jobs))  # type: ignore[arg-type]
    security_id = "00000000-0000-0000-0000-000000000021"
    response = client.post(
        "/api/v2/signals/research-export.zip",
        json={
            "frequency": "weekly",
            "asset_security_ids": [security_id],
            "asset_data_inputs": {security_id: ["canonical_market_bars"]},
            "signal_version_keys": ["return_continuation__total_return__w20"],
            "include_targets": True,
        },
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["status_url"].endswith(str(export_jobs.job.export_job_id))
    assert payload["download_url"] is None
    assert export_jobs.request is not None
    assert export_jobs.request["asset_data_inputs"] == {
        security_id: ["canonical_market_bars"]
    }
    status = client.get(payload["status_url"])
    assert status.status_code == 200
    assert status.json()["work_item_id"] == str(export_jobs.job.work_item_id)


def test_signal_research_export_download_uses_validated_file_response(tmp_path: Path) -> None:
    reader = FakeArtifactReader()
    export_jobs = FakeSignalExportJobs()
    content = b"PK\x03\x04streamed-export"
    path = tmp_path / "export.zip"
    path.write_bytes(content)
    export_jobs.job = replace(
        export_jobs.job,
        status="completed",
        stage="completed",
        content_hash="a" * 64,
        byte_size=len(content),
        filename="research.zip",
        expires_at=datetime.now(UTC) + timedelta(days=14),
    )
    export_jobs.artifact = ValidatedSignalExport(
        path=path,
        filename="research.zip",
        content_hash="a" * 64,
        byte_size=len(content),
    )
    client = TestClient(create_app(reader, signal_export_jobs=export_jobs))  # type: ignore[arg-type]
    response = client.get(
        f"/api/v2/signals/research-exports/{export_jobs.job.export_job_id}/download"
    )
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["x-artifact-content-hash"] == "a" * 64
    assert response.headers["content-disposition"].endswith('filename="research.zip"')


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


def test_asset_catalog_search_series_and_canonical_csv_download() -> None:
    client, _reader = _client()
    catalog = client.get("/api/v2/catalog/assets?search=apple&category=stocks")
    assert catalog.status_code == 200
    payload = catalog.json()
    assert payload["catalog_version"] == "0.21.0"
    assert payload["items"][0]["symbol"] == "AAPL"
    assert payload["items"][0]["aliases"] == ["Apple", "APPL"]
    assert payload["items"][0]["canonical_data_available"] is True
    assert payload["items"][0]["v022_candidate_selectable"] is True
    assert payload["items"][0]["v022_candidate_dataset_version"] == 4
    security_id = payload["items"][0]["security_id"]

    series = client.get(f"/api/v2/catalog/assets/{security_id}/series")
    assert series.status_code == 200
    assert len(series.json()["points"]) == 2
    assert series.json()["points"][-1]["adjusted_close"] == 205.0

    download = client.get(f"/api/v2/catalog/assets/{security_id}/download.csv")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")
    assert "aapl_canonical.csv" in download.headers["content-disposition"]
    assert "session_date,open,high,low,close,adjusted_close,volume" in download.text
    missing = client.get(f"/api/v2/catalog/assets/{uuid.uuid4()}/series")
    assert missing.status_code == 404


def test_workspace_options_propagate_legality_from_factor_to_signal_to_model() -> None:
    client, _reader = _client()
    response = client.get(
        "/api/v2/workspace/options",
        params=[
            ("frequency", "weekly"),
            ("selected_factor_variant", "total_return__w120"),
            (
                "selected_signal",
                "return_continuation__total_return__w120",
            ),
        ],
    )
    assert response.status_code == 200
    payload = response.json()
    factor = next(item for item in payload["factor_families"] if item["key"] == "total_return")
    assert (
        next(item for item in factor["variants"] if item["key"] == "total_return__w120")["selected"]
        is True
    )
    signal = next(
        item for item in payload["signal_families"] if item["key"] == "return_continuation"
    )
    selected = next(
        item
        for item in signal["versions"]
        if item["version_key"] == "return_continuation__total_return__w120"
    )
    assert selected["selectable"] is True
    linear = next(
        preset
        for family in payload["model_families"]
        for preset in family["presets"]
        if preset["preset_key"] == "linear_weighted__signal_equal_v1"
    )
    assert linear["selectable"] is True
    assert linear["accepted_signal_keys"] == ["return_continuation__total_return__w120"]


def test_workspace_compile_preview_returns_parallel_branches_and_fixed_cells() -> None:
    client, _reader = _client()
    assets = [str(uuid.uuid4()) for _ in range(4)]
    response = client.post(
        "/api/v2/workspace/compile-preview",
        json={
            "frequency": "weekly",
            "asset_security_ids": assets,
            "factor_variant_keys": ["total_return__w120"],
            "signal_version_keys": ["return_continuation__total_return__w120"],
            "model_preset_keys": ["single_signal__identity_v1"],
            "strategy_preset_keys": ["multi_etf_top_k__k2__none__none__none"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["compiled"]["runnable"] is True
    assert payload["compiled"]["portfolio_cell_count"] == 6
    assert payload["blockers"] == []


def test_workspace_options_preserve_an_explicit_empty_asset_input_choice() -> None:
    client, _reader = _client()
    security_id = str(uuid.uuid4())
    response = client.get(
        "/api/v2/workspace/options",
        params=[
            ("frequency", "weekly"),
            ("selected_asset", security_id),
            ("selected_asset_data_input", f"{security_id}:"),
            ("selected_factor_variant", "total_return__w120"),
        ],
    )
    assert response.status_code == 200
    factor = next(
        variant
        for family in response.json()["factor_families"]
        for variant in family["variants"]
        if variant["key"] == "total_return__w120"
    )
    assert factor["selectable"] is False
    assert factor["reason_codes"] == ["asset_data_input_missing"]


def test_workspace_compile_preview_does_not_restore_explicitly_unselected_bars() -> None:
    client, _reader = _client()
    assets = [str(uuid.uuid4()) for _ in range(2)]
    response = client.post(
        "/api/v2/workspace/compile-preview",
        json={
            "frequency": "weekly",
            "asset_security_ids": assets,
            "asset_data_inputs": {assets[0]: [], assets[1]: ["canonical_market_bars"]},
            "factor_variant_keys": ["total_return__w120"],
            "signal_version_keys": ["return_continuation__total_return__w120"],
            "model_preset_keys": ["single_signal__identity_v1"],
            "strategy_preset_keys": ["multi_etf_top_k__k1__none__none__none"],
        },
    )
    assert response.status_code == 200
    assert response.json()["compiled"]["runnable"] is False
    assert any(
        blocker["layer"] == "factor"
        and blocker["reason_codes"] == ["asset_data_input_missing"]
        for blocker in response.json()["blockers"]
    )


def test_product_catalog_uses_research_candidate_contract() -> None:
    client, _reader = _client()
    assert client.get("/api/v2/products").json()["items"] == []
    assert client.get(f"/api/v2/products/{uuid.uuid4()}").status_code == 404


def test_factor_dataset_download_is_artifact_bound_csv() -> None:
    client, _reader = _client()
    response = client.get(f"/api/v2/factors/datasets/{uuid.uuid4()}/download.csv")
    assert response.status_code == 200
    assert "total_return__w120.csv" in response.headers["content-disposition"]
    assert response.headers["x-artifact-content-hash"] == "f" * 64
    assert "2026-08-04,aapl,AAPL,0.12" in response.text


def test_signal_version_download_is_parameter_bound_csv() -> None:
    client, _reader = _client()
    version_key = "return_continuation__total_return__w120"
    response = client.get(f"/api/v2/signals/versions/{version_key}/download.csv")
    assert response.status_code == 200
    assert f"{version_key}.csv" in response.headers["content-disposition"]
    assert response.headers["x-artifact-content-hash"] == "e" * 64
    assert "2026-08-04,aapl,AAPL,0.8,active,False" in response.text


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
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "experiment"
    )
    assert experiment_domain["availability"] == "available"


def test_experiment_overview_is_server_filtered_and_paginated() -> None:
    client, _reader = _client()
    first = client.get(
        "/api/v2/experiments/overview",
        params={
            "template_key": "full_history",
            "frequency": "weekly",
            "cost_bps_per_side": 5,
            "ranking_metric": "strategy.sharpe_ratio",
            "limit": 1,
            "offset": 0,
        },
    )
    assert first.status_code == 200
    assert first.json()["total_specification_count"] == 1
    assert first.json()["filtered_specification_count"] == 1
    assert len(first.json()["specifications"]) == 1
    second = client.get(
        "/api/v2/experiments/overview",
        params={"template_key": "full_history", "limit": 1, "offset": 1},
    )
    assert second.status_code == 200
    assert second.json()["filtered_specification_count"] == 1
    assert second.json()["specifications"] == []
    excluded = client.get(
        "/api/v2/experiments/overview",
        params={"template_key": "trailing_3_years"},
    )
    assert excluded.status_code == 200
    assert excluded.json()["filtered_specification_count"] == 0


def test_experiment_overview_forwards_exact_research_suite_scope() -> None:
    client, reader = _client()
    research_suite_id = uuid.uuid4()

    response = client.get(
        "/api/v2/experiments/overview",
        params={"research_suite_id": str(research_suite_id)},
    )

    assert response.status_code == 200
    assert reader.experiment_research_suite_id == research_suite_id


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
