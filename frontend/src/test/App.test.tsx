import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { AppRoutes } from "../App";
import i18n from "../i18n";

const health = {
  context: { api_version: "v2", system_version: "0.2.0", read_only: true },
  quality: { state: "ok", codes: [] },
  database_revision: "20260802_02_v02_lineage",
};
const capabilities = {
  context: health.context, quality: health.quality,
  domains: [{ key: "lineage", purpose: "Lineage", upstream: [], delivery_milestone: "M1C", availability: "available" }],
  endpoints: ["health", "artifacts"], interface_states: ["loading", "tainted"], languages: ["zh-CN", "en"],
};
const artifacts = {
  context: health.context, quality: health.quality, items: [], total: 0, limit: 100, offset: 0,
};
const dataOverview = {
  context: health.context,
  quality: { state: "warning", codes: ["data.ineligible_assets"] },
  sources: [],
  datasets: [{
    artifact_id: "00000000-0000-0000-0000-000000000001",
    dataset_key: "us_style_daily_bars",
    version_number: 1,
    dataset_kind: "canonical",
    value_kind: "daily_bar",
    coverage_start: "2020-01-02",
    coverage_end: "2026-07-31",
    row_count: 6500,
    coverage: [{ subject_key: "IWD", asset_key: "iwd", coverage_start: "2020-01-02", coverage_end: "2026-07-31", observation_count: 1600, missing_count: 0 }],
    issues: [], quality: { state: "ok", codes: [] },
  }],
  bundle: null,
  eligibility: null,
};
const factorOverview = {
  context: health.context,
  quality: health.quality,
  diagnostic_artifact_id: "00000000-0000-0000-0000-000000000010",
  factor_catalog_artifact_id: "00000000-0000-0000-0000-000000000011",
  universe_artifact_id: "00000000-0000-0000-0000-000000000012",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000013",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000014",
  factor_engine_artifact_id: "00000000-0000-0000-0000-000000000015",
  diagnostic_engine_artifact_id: "00000000-0000-0000-0000-000000000016",
  coverage_start: "2025-01-02", coverage_end: "2026-01-30",
  dataset_count: 1, asset_count: 5, observation_count: 100, pair_count: 1,
  high_correlation_threshold: 0.85,
  datasets: [{
    factor_dataset_artifact_id: "00000000-0000-0000-0000-000000000017",
    factor_key: "total_return", measurement_family: "return",
    formula: "close[t] / close[t-window] - 1", output_unit: "ratio",
    variant_key: "total_return__w20", parameters: { window: 20 }, preset_type: "canonical",
    coverage_start: "2025-01-02", coverage_end: "2026-01-30", row_count: 100,
    observation_count: 100, asset_count: 5, missing_count: 0,
    mean: 0.02, standard_deviation: 0.05, minimum: -0.1, p05: -0.05,
    p25: -0.01, median: 0.015, p75: 0.04, p95: 0.09, maximum: 0.12,
    zero_variance: false, quality: health.quality,
  }],
  correlations: [{
    left_variant_key: "total_return__w20", right_variant_key: "total_return__w60",
    left_factor_key: "total_return", right_factor_key: "total_return", observation_count: 100,
    spearman_correlation: 0.91, same_definition: true, high_correlation: true,
  }],
  issues: [],
};
const signalOverview = {
  context: health.context,
  quality: { state: "warning", codes: ["signal.diagnostic_warning"] },
  evaluation_artifact_id: "00000000-0000-0000-0000-000000000020",
  signal_catalog_artifact_id: "00000000-0000-0000-0000-000000000021",
  universe_artifact_id: "00000000-0000-0000-0000-000000000022",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000023",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000024",
  signal_engine_artifact_id: "00000000-0000-0000-0000-000000000025",
  evaluation_engine_artifact_id: "00000000-0000-0000-0000-000000000026",
  forward_return_artifact_id: "00000000-0000-0000-0000-000000000027",
  target_key: "weekly_next_open_to_next_open", frequency: "weekly",
  coverage_start: "2025-01-03", coverage_end: "2026-01-30",
  signal_count: 1, common_period_count: 52, pair_count: 1,
  high_correlation_threshold: 0.85,
  signals: [{
    signal_dataset_artifact_id: "00000000-0000-0000-0000-000000000028",
    signal_key: "return_continuation__total_return__w252",
    template_key: "return_continuation", economic_family: "momentum",
    rationale_type: "academic", rationale: "Persistent relative performance may continue.",
    research_tier: "canonical", product_eligible: true, direction: "higher_is_better",
    normalization: "cross_sectional_centered_rank_-1_1", output_type: "continuous",
    factor_variant_key: "total_return__w252", quality: health.quality,
    full: {
      window_key: "full", window_start: "2025-01-03", window_end: "2026-01-30",
      period_count: 52, valid_ic_count: 51, undefined_ic_count: 1,
      mean_rank_ic: 0.18, median_rank_ic: 0.2, positive_ic_ratio: 0.62,
      information_ratio: 1.1, mean_top_bottom_spread: 0.003, event_rate: null,
      event_asset_concentration: null, non_neutral_rate: 1, mean_top2_turnover: 0.22,
    },
    stability: [{
      window_key: "year:2025", window_start: "2025-01-03", window_end: "2025-12-26",
      period_count: 51, valid_ic_count: 50, undefined_ic_count: 1,
      mean_rank_ic: 0.17, median_rank_ic: 0.2, positive_ic_ratio: 0.6,
      information_ratio: 1, mean_top_bottom_spread: 0.0028, event_rate: null,
      event_asset_concentration: null, non_neutral_rate: 1, mean_top2_turnover: 0.2,
    }],
  }],
  pairs: [{
    left_signal_key: "return_continuation__total_return__w252",
    right_signal_key: "return_continuation__total_return__w120",
    score_observation_count: 208, score_spearman: 0.9, spread_period_count: 52,
    spread_correlation: 0.75, mean_top2_overlap: 0.8, high_correlation: true,
  }],
  issues: [{
    signal_key: "return_continuation__total_return__w252", severity: "warning",
    issue_code: "short_evaluation_sample", message: "Short sample", details: {},
  }],
};
const modelOverview = {
  context: health.context,
  quality: { state: "warning", codes: ["model.diagnostic_warning"] },
  evaluation_artifact_id: "00000000-0000-0000-0000-000000000030",
  model_catalog_artifact_id: "00000000-0000-0000-0000-000000000031",
  universe_artifact_id: "00000000-0000-0000-0000-000000000032",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000033",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000034",
  model_engine_artifact_id: "00000000-0000-0000-0000-000000000035",
  evaluation_engine_artifact_id: "00000000-0000-0000-0000-000000000036",
  forward_return_artifact_id: "00000000-0000-0000-0000-000000000037",
  target_key: "weekly_next_open_to_next_open", frequency: "weekly",
  coverage_start: "2025-01-03", coverage_end: "2026-01-30",
  model_count: 1, common_period_count: 52, pair_count: 1, ablation_count: 1,
  high_correlation_threshold: 0.85,
  models: [{
    model_dataset_artifact_id: "00000000-0000-0000-0000-000000000038",
    specification_key: "dimension_equal_weight__momentum_trend+volatility_risk",
    specification_type: "dimension_subset_equal_weight",
    model_key: "classic_market_composite", model_family: "cross_sectional_composite",
    hypothesis: "Complementary dimensions may improve robustness.",
    overall_method_key: "weighted_mean", tie_output: "not_applicable",
    output_type: "continuous_score", active_dimension_count: 2, component_count: 1,
    research_tier: "canonical", quality: { state: "warning", codes: ["model.diagnostic_warning"] },
    dimensions: [{ dimension_key: "momentum_trend", method_key: "weighted_mean", input_transform: "identity", weight: 0.5,
      components: [{ signal_key: "return_continuation__total_return__w252", input_transform: "identity", weight: 1 }] }],
    full: { window_key: "full", window_start: "2025-01-03", window_end: "2026-01-30", period_count: 52,
      valid_ic_count: 51, undefined_ic_count: 1, mean_rank_ic: 0.21, median_rank_ic: 0.2,
      positive_ic_ratio: 0.64, information_ratio: 1.2, mean_top_bottom_spread: 0.0035,
      non_neutral_rate: 1, mean_top2_turnover: 0.2, mean_score_dispersion: 0.41, mean_confidence: 0.55 },
    stability: [],
  }],
  pairs: [{ left_specification_key: "model_a", right_specification_key: "model_b",
    score_observation_count: 208, score_spearman: 0.91, spread_period_count: 52,
    spread_correlation: 0.7, mean_top2_overlap: 0.8, high_correlation: true }],
  ablations: [{ full_specification_key: "dimension_equal_weight__momentum_trend+volatility_risk",
    ablated_specification_key: "dimension_equal_weight__momentum_trend",
    removed_dimension_key: "volatility_risk", window_key: "full", period_count: 52,
    delta_mean_rank_ic: 0.03, delta_information_ratio: 0.1, delta_mean_top_bottom_spread: 0.0004 }],
  issues: [{ specification_key: "dimension_equal_weight__momentum_trend+volatility_risk",
    severity: "warning", issue_code: "short_evaluation_sample", message: "Short sample", details: {} }],
};
const strategyOverview = {
  context: health.context, quality: health.quality,
  rules: {
    definition_artifact_id: "00000000-0000-0000-0000-000000000040",
    version_artifact_id: "00000000-0000-0000-0000-000000000041",
    strategy_key: "us_style_cross_sectional_rotation",
    strategy_family: "cross_sectional_top_k_rotation",
    hypothesis: "Higher-scored candidates may outperform.", version_number: 1,
    selection_contract: "rank_model_scores", allocation_contract: "equal_slot_budget",
    reserve_contract: "unused_slot_budget_to_synthetic_reserve",
    compatible_model_output_types: ["continuous_score", "directional_score"],
    candidate_input_policy: "complete_eligible_universe", missing_input_policy: "fail_formal_run",
    variants: [{ artifact_id: "00000000-0000-0000-0000-000000000042",
      variant_key: "top_k_equal_weight__k2", template_key: "top_k_equal_weight",
      target_k: 2, research_tier: "canonical", selection_order: "rank_then_select",
      trend_filter: "none", auxiliary_signal_key: null, auxiliary_eligible_state: null,
      empty_slot_policy: "not_applicable", tie_policy: "proportional_share_of_remaining_slot_budget",
      slot_weight_rule: "1 / K", reserve_rule: "unused_slot_budget_to_synthetic_reserve" }],
    schedules: [{ artifact_id: "00000000-0000-0000-0000-000000000043",
      schedule_key: "weekly_last_common_session_close", frequency: "weekly",
      decision_timing: "last_common_session_close", decision_data_policy: "include_decision_close" }],
    execution_policy: { artifact_id: "00000000-0000-0000-0000-000000000044",
      policy_key: "next_common_session_open", delay_common_sessions: 1,
      execution_price: "adjusted_open", missing_execution_policy: "fail_formal_run" },
  },
  products: [{ artifact_id: "00000000-0000-0000-0000-000000000045", product_key: "product-a",
    version_number: 1, model_specification_key: "dimension_equal_weight__momentum_trend",
    model_specification_type: "dimension_subset_equal_weight", model_output_type: "continuous_score",
    variant_key: "top_k_equal_weight__k2", target_k: 2, research_tier: "canonical",
    universe_key: "us_style_rotation_core", schedule_key: "weekly_last_common_session_close",
    frequency: "weekly", execution_policy_key: "next_common_session_open",
    execution_price: "adjusted_open", target_path_count: 1 }],
  target_paths: [{ artifact_id: "00000000-0000-0000-0000-000000000046",
    product_artifact_id: "00000000-0000-0000-0000-000000000045", product_key: "product-a",
    model_dataset_artifact_id: "00000000-0000-0000-0000-000000000047",
    model_specification_key: "dimension_equal_weight__momentum_trend",
    variant_key: "top_k_equal_weight__k2", target_k: 2, frequency: "weekly",
    coverage_start: "2026-01-02", coverage_end: "2026-01-09", decision_count: 2,
    position_count: 8 }],
};
const strategyTarget = {
  context: health.context, quality: health.quality, target_path: strategyOverview.target_paths[0],
  universe_artifact_id: "00000000-0000-0000-0000-000000000048",
  data_bundle_artifact_id: "00000000-0000-0000-0000-000000000049",
  eligibility_artifact_id: "00000000-0000-0000-0000-000000000050",
  engine_artifact_id: "00000000-0000-0000-0000-000000000051",
  auxiliary_signal_dataset_artifact_id: null,
  decisions: [{ decision_date: "2026-01-09", target_k: 2, actual_holding_count: 2,
    boundary_tie_count: 0, reserve_target_weight: 0,
    positions: [{ asset_key: "iwd", symbol: "IWD", model_score: 0.8, model_rank: 1,
      selection_rank: 1, trend_state: null, strategy_eligible: true, selected: true,
      target_weight: 0.5, decision_reason: "selected_by_rank" }] }],
};

function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter></QueryClientProvider>);
}

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage("zh-CN");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const payload = url.includes("strategies/targets") ? strategyTarget : url.includes("strategies/overview") ? strategyOverview : url.includes("models/overview") ? modelOverview : url.includes("signals/overview") ? signalOverview : url.includes("factors/overview") ? factorOverview : url.includes("data/overview") ? dataOverview : url.includes("health") ? health : url.includes("capabilities") ? capabilities : artifacts;
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  });
});

test("renders real foundation data without inventing future research results", async () => {
  renderRoute("/?lang=zh-CN");
  expect(await screen.findByText("从可追溯的研究对象开始")).toBeInTheDocument();
  expect(screen.getByText("20260802_02_v02_lineage")).toBeInTheDocument();
  expect(screen.getByText("策略实验")).toBeInTheDocument();
});

test("language switch keeps the route and translates fixed UI text", async () => {
  renderRoute("/factors?lang=zh-CN");
  expect(await screen.findByRole("heading", { name: "因子诊断与参数实例" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "EN" }));
  expect(await screen.findByRole("heading", { name: "Factor diagnostics and variants" })).toBeInTheDocument();
  expect(screen.getByText("Parameter stability")).toBeInTheDocument();
});

test("artifact empty state is explicit", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/artifacts?lang=en");
  expect(await screen.findByText("No matching published data")).toBeInTheDocument();
});

test("data page renders published diagnostics without strategy metrics", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/data?lang=en");
  expect(await screen.findByRole("heading", { name: "Data quality and availability" })).toBeInTheDocument();
  expect(screen.getByText("us_style_daily_bars")).toBeInTheDocument();
  expect(screen.getByText("IWD · 1,600")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("factor page displays measurement diagnostics without strategy performance", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/factors?lang=en");
  expect((await screen.findAllByText("total_return__w20")).length).toBeGreaterThan(0);
  expect(screen.getByText("Redundancy alerts")).toBeInTheDocument();
  expect(screen.getAllByText("ρ 0.91")).toHaveLength(2);
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("signal page displays published directional diagnostics without strategy rankings", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/signals?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Signal evaluation and economic direction" })).toBeInTheDocument();
  expect(screen.getAllByText("return_continuation__total_return__w252").length).toBeGreaterThan(0);
  expect(screen.getByText("Mean Rank IC")).toBeInTheDocument();
  expect(screen.getByText("Signal redundancy alerts")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("model page displays composition, controlled ablation, and no strategy ranking", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/models?lang=en&frequency=weekly");
  expect(await screen.findByRole("heading", { name: "Model structure and independent diagnostics" })).toBeInTheDocument();
  expect(screen.getAllByText("dimension_equal_weight__momentum_trend+volatility_risk").length).toBeGreaterThan(0);
  expect(screen.getByText("Controlled dimension ablation")).toBeInTheDocument();
  expect(screen.getByText("Model redundancy alerts")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe/i)).not.toBeInTheDocument();
});

test("strategy page separates rules, products, and target decisions without performance", async () => {
  await i18n.changeLanguage("en");
  renderRoute("/strategies?lang=en");
  expect(await screen.findByRole("heading", { name: "Strategy rules and target weights" })).toBeInTheDocument();
  expect(screen.getAllByText("top_k_equal_weight__k2").length).toBeGreaterThan(0);
  expect(await screen.findByText("selected_by_rank")).toBeInTheDocument();
  expect(screen.getByText("50%")).toBeInTheDocument();
  expect(screen.queryByText(/Sharpe ratio/i)).not.toBeInTheDocument();
});
