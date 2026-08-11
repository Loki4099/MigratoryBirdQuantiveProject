from __future__ import annotations

import uuid
from typing import Any

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.metrics.calculator import (
    calculate_factor_diagnostics,
    calculate_run_metrics,
    summarize_factor_diagnostics,
)
from style_rotation.metrics.contracts import PHASE6_CONTRACTS
from style_rotation.metrics.repository import MetricsRepository
from style_rotation.metrics.types import MetricBatchOutcome

METRIC_CONFIGURATION: dict[str, Any] = {
    "version": "0.1.0",
    "daily_return_type": "simple",
    "statistical_annualization_days": 252,
    "calendar_days_per_year": "365.2425",
    "cagr_annualization": "actual_calendar_years",
    "volatility_ddof": 1,
    "max_drawdown_sign": "nonpositive",
    "max_drawdown_initial_peak": "1.0",
    "risk_free": "prior_known_dgs3mo_factor_actual_calendar_gap_first_day_zero",
    "sortino_downside": "sqrt_mean_all_observations_min_excess_zero_squared",
    "tracking_benchmark": "four_etf_equal_weight_same_frequency_same_cost",
    "relative_return": "strategy_terminal_nav_divided_by_benchmark_terminal_nav_minus_one",
    "annualized_turnover": "sum_single_sided_turnover_divided_by_actual_calendar_years",
    "rank_ic": "four_etf_spearman_average_tie_ranks",
    "top_bottom": "deterministic_ticker_tie_break_equal_weight_open_to_open_cost_free",
    "top_bottom_summary": "arithmetic_mean_native_holding_period",
    "undefined_values": "null_with_stable_reason_code",
}


class MetricComputationService:
    def __init__(self, repository: MetricsRepository) -> None:
        self._repository = repository

    def run(
        self,
        *,
        metric_version_id: uuid.UUID,
        methodology_hash: str,
        source_engine_version_id: uuid.UUID | None = None,
    ) -> MetricBatchOutcome:
        self._repository.publish_contracts(PHASE6_CONTRACTS)
        source = self._repository.select_source_run_set(source_engine_version_id)
        signal_content_hash, clean_content_hash = self._repository.diagnostic_source_hashes(source)
        diagnostic_keys = {(run.factor_variant_id, run.rebalance_frequency) for run in source.runs}
        if len(diagnostic_keys) != 48:
            raise ValueError(
                f"Formal metric batch requires 48 diagnostic identities, got {len(diagnostic_keys)}"
            )
        diagnostic_fingerprints = {
            key: sha256_hexdigest(
                {
                    "signal_content_hash": signal_content_hash,
                    "clean_content_hash": clean_content_hash,
                    "factor_variant_id": key[0],
                    "rebalance_frequency": key[1],
                    "metric_version_id": metric_version_id,
                    "methodology_hash": methodology_hash,
                }
            )
            for key in diagnostic_keys
        }
        existing_by_fingerprint = self._repository.diagnostic_set_ids(
            diagnostic_fingerprints.values()
        )
        diagnostic_ids = {
            key: existing_by_fingerprint[fingerprint]
            for key, fingerprint in diagnostic_fingerprints.items()
            if fingerprint in existing_by_fingerprint
        }
        diagnostic_reused = len(diagnostic_ids)
        diagnostic_completed = 0

        if len(diagnostic_ids) != len(diagnostic_keys):
            events, prices, loaded_signal_hash, loaded_clean_hash = (
                self._repository.load_diagnostic_inputs(source)
            )
            if (loaded_signal_hash, loaded_clean_hash) != (
                signal_content_hash,
                clean_content_hash,
            ):
                raise ValueError("Diagnostic source datasets changed while inputs were loading")
            periods = calculate_factor_diagnostics(events, prices)
            summaries = summarize_factor_diagnostics(periods)
            summary_keys = {
                (summary.factor_variant_id, summary.rebalance_frequency) for summary in summaries
            }
            if summary_keys != diagnostic_keys:
                raise ValueError(
                    "Formal factor diagnostics do not exactly match the 48 source identities"
                )
            for summary in summaries:
                key = (summary.factor_variant_id, summary.rebalance_frequency)
                if key in diagnostic_ids:
                    continue
                group_periods = tuple(
                    period
                    for period in periods
                    if period.factor_variant_id == summary.factor_variant_id
                    and period.rebalance_frequency == summary.rebalance_frequency
                )
                diagnostic_ids[key] = self._repository.publish_diagnostic_set(
                    source=source,
                    metric_version_id=metric_version_id,
                    fingerprint=diagnostic_fingerprints[key],
                    summary=summary,
                    periods=group_periods,
                )
                diagnostic_completed += 1

        published_run_ids = self._repository.published_run_ids(
            (descriptor.run_id for descriptor in source.runs), metric_version_id
        )
        publications_completed = 0
        publications_reused = len(published_run_ids)
        pending_runs = tuple(
            descriptor for descriptor in source.runs if descriptor.run_id not in published_run_ids
        )
        reserve_factors = self._repository.reserve_factors(source) if pending_runs else {}
        for descriptor in pending_runs:
            run_input = self._repository.load_run_input(descriptor, reserve_factors)
            metrics = calculate_run_metrics(run_input)
            diagnostic_key = (
                descriptor.factor_variant_id,
                descriptor.rebalance_frequency,
            )
            metric_fingerprint = sha256_hexdigest(
                {
                    "run_fingerprint": descriptor.run_fingerprint,
                    "metric_version_id": metric_version_id,
                    "input_manifest_hash": run_input.input_manifest_hash,
                    "diagnostic_fingerprint": diagnostic_fingerprints[diagnostic_key],
                }
            )
            self._repository.publish_run_metrics(
                run_id=descriptor.run_id,
                metric_version_id=metric_version_id,
                diagnostic_set_id=diagnostic_ids[diagnostic_key],
                metric_fingerprint=metric_fingerprint,
                input_manifest_hash=run_input.input_manifest_hash,
                metrics=metrics,
            )
            publications_completed += 1
        return MetricBatchOutcome(
            metric_version_id=str(metric_version_id),
            diagnostic_sets_completed=diagnostic_completed,
            diagnostic_sets_reused=diagnostic_reused,
            publications_completed=publications_completed,
            publications_reused=publications_reused,
        )
