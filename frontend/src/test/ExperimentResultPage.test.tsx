import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import i18n from "../i18n";
import { ExperimentResultPage } from "../pages/ExperimentResultPage";

const evidenceId = "00000000-0000-4000-8000-000000000010";

beforeEach(async () => {
  vi.restoreAllMocks();
  await i18n.changeLanguage("en");
});

test("result detail displays the exact configuration, metrics, and published paths", async () => {
  vi.spyOn(api, "v022Experiment").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    result_evidence_snapshot_id: evidenceId,
    evidence_artifact_id: "00000000-0000-4000-8000-000000000011",
    result_artifact_id: "00000000-0000-4000-8000-000000000012",
    evidence_class: "backtest",
    configuration_snapshot_id: "00000000-0000-4000-8000-000000000013",
    configuration_fingerprint: "a".repeat(64),
    configuration: { frequency: "weekly" },
    display: {
      direct_inputs: [{ name: "12-month momentum", variant_key: "momentum_w252" }],
      aggregation: {
        name: "Ridge regression ensemble",
        family_key: "ridge_regression",
        trainable_ensemble: {
          member_count: 2,
          combination_policy: "equal_within_target_equal_across_targets_v1",
          target_groups: [{
            target_key: "forward_return_h5",
            target_name: "Forward return H5",
            members: [
              { training_preset_key: "ridge_default_v1", training_preset_name: "Ridge default" },
              { training_preset_key: "ridge_strong_v1", training_preset_name: "Ridge strong" },
            ],
          }],
        },
      },
      strategy: { name: "Cross-section Top 10", variant_key: "top_k_10", parameter_preset: { name: "Top 10" } },
      defense: { name: "No defense", variant_key: "none", none: true },
    },
    created_at: "2026-08-16T00:00:00Z",
    evidence: {
      trainable_aggregation_diagnostic: {
        diagnostic_fingerprint: "d".repeat(64),
        diagnostic_document: {
          member_count: 2,
          target_group_count: 1,
          panel_row_count: 1200,
          member_diagnostics: [
            {
              target_key: "forward_return_h5",
              training_preset_key: "ridge_default_v1",
              fold_count: 6,
              predictive: { mean_rank_ic: "0.051", median_rank_ic: "0.047", positive_ic_ratio: "0.61", ic_ir: "0.43" },
            },
            {
              target_key: "forward_return_h5",
              training_preset_key: "ridge_strong_v1",
              fold_count: 6,
              predictive: { mean_rank_ic: "0.044", median_rank_ic: "0.041", positive_ic_ratio: "0.58", ic_ir: "0.37" },
            },
          ],
          target_group_diagnostics: [{
            target_key: "forward_return_h5",
            predictive: { mean_rank_ic: "0.055", median_rank_ic: "0.05", positive_ic_ratio: "0.63", ic_ir: "0.46" },
            within_target_member_ablations: [{ omitted_training_preset_key: "ridge_strong_v1", full_minus_reduced_mean_rank_ic: "0.004" }],
          }],
          final_ensemble_by_target: [{
            target_key: "forward_return_h5",
            predictive: { mean_rank_ic: "0.057", median_rank_ic: "0.052", positive_ic_ratio: "0.64", ic_ir: "0.48" },
          }],
          pairwise_prediction_correlations: [{
            left_target_key: "forward_return_h5",
            left_training_preset_key: "ridge_default_v1",
            right_target_key: "forward_return_h5",
            right_training_preset_key: "ridge_strong_v1",
            mean_cross_sectional_rank_correlation: "0.72",
          }],
        },
      },
    },
    evidence_quality: {},
    comparisons: [],
    matched_baselines: [],
    comparison_context: {
      evaluation_cohort_version_id: "00000000-0000-4000-8000-000000000014",
      evaluation_cohort_fingerprint: "b".repeat(64),
      cohort_key: "sp500_weekly_v1",
      frequency: "weekly",
      warmup_start: "2002-01-02",
      evaluation_start: "2007-01-03",
      evaluation_end: "2026-06-30",
      benchmark_key: "spy",
      cost_bps_per_side: "0.0005",
      execution_delay_sessions: 1,
      price_semantics: "historical_constituent_pit__retrospective_price_snapshot",
    },
    outcome: "accepted",
    quality_status: "passed",
    effective_start: "2007-01-03",
    effective_end: "2026-06-30",
    core_metrics: {
      cagr: "0.18",
      benchmark_cagr: "0.10",
      cagr_spread: "0.08",
      sharpe_ratio: "1.4",
      maximum_drawdown: "-0.22",
    },
    metrics: {},
    product: {
      is_candidate: false,
      is_enrolled: false,
      product_candidate_definition_id: null,
      execution_version_id: null,
      qualification_version_id: null,
      product_enrollment_id: null,
    },
  });
  vi.spyOn(api, "v022ExperimentSeries").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    result_evidence_snapshot_id: evidenceId,
    effective_start: "2007-01-03",
    effective_end: "2026-06-30",
    total_points: 2,
    returned_points: 2,
    points: [
      { session_date: "2007-01-03", strategy_nav: "1", benchmark_nav: "1", excess_nav: "1", drawdown: "0" },
      { session_date: "2026-06-30", strategy_nav: "4", benchmark_nav: "2", excess_nav: "2", drawdown: "-0.10" },
    ],
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/experiments/results/${evidenceId}`]}><Routes><Route path="/experiments/results/:evidenceId" element={<ExperimentResultPage />} /></Routes></MemoryRouter></QueryClientProvider>);

  expect(await screen.findByText("12-month momentum")).toBeInTheDocument();
  expect(screen.getByText("Ridge regression ensemble")).toBeInTheDocument();
  expect(screen.getByText(/2 internal model members/)).toBeInTheDocument();
  expect(screen.getByText("Forward return H5")).toBeInTheDocument();
  expect(screen.getByText("Ridge default · Ridge strong")).toBeInTheDocument();
  expect(screen.getByText("Model-group out-of-fold diagnostics")).toBeInTheDocument();
  expect(screen.getAllByText("ridge_default_v1").length).toBeGreaterThan(0);
  expect(screen.getByText("Within-Target leave-one-member diagnostics")).toBeInTheDocument();
  expect(screen.getByText("Final Ensemble against Target")).toBeInTheDocument();
  expect(screen.getByText("Cross-section Top 10")).toBeInTheDocument();
  expect(screen.getByText("Strategy NAV vs SPY")).toBeInTheDocument();
  expect(screen.getByText("18%")).toBeInTheDocument();
  expect(screen.getByText("8%")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Promote Product" })).toBeEnabled();
});
