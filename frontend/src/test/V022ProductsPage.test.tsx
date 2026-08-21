import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import i18n from "../i18n";
import { V022ProductsPage } from "../pages/V022ProductsPage";

const enrollmentId = "00000000-0000-4000-8000-000000000021";
const evidenceId = "00000000-0000-4000-8000-000000000022";
const item = {
  product_enrollment_id: enrollmentId,
  enrollment_artifact_id: "00000000-0000-4000-8000-000000000023",
  product_key: "momentum_top10",
  name: "Momentum Top 10",
  execution_version_number: 1,
  execution_fingerprint: "a".repeat(64),
  source_result_evidence_snapshot_id: evidenceId,
  configuration_snapshot_id: "00000000-0000-4000-8000-000000000024",
  configuration_fingerprint: "b".repeat(64),
  configuration: {},
  display: {
    direct_inputs: [{ name: "12-month momentum", variant_key: "momentum_w252" }],
    aggregation: {
      name: "Ridge regression ensemble",
      family_key: "ridge_regression",
      trainable_ensemble: {
        member_count: 2,
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
    strategy: { name: "Top 10", parameter_preset: { name: "Top 10" } },
    defense: { name: "No defense", none: true },
  },
  lifecycle: "active",
  health: "observing",
  first_eligible_decision_session_id: "00000000-0000-4000-8000-000000000025",
  frequency: "weekly" as const,
  first_eligible_decision_session: "2026-07-03",
  next_pending_decision_session: "2026-07-03",
  next_pending_decision_cutoff_at: "2026-07-03T20:00:00Z",
  decision_pipeline_state: "scheduled" as const,
  next_product_input_snapshot_id: null,
  next_product_input_available_at: null,
  next_product_runtime_execution_id: null,
  decision_count: 0,
  completed_decision_count: 0,
  missing_decision_count: 0,
  latest_decision_session: null,
  latest_decision_status: null,
  oos_anchor_cutoff_at: "2026-06-30T20:00:00Z",
  activation_effective_at: "2026-07-01T00:00:00Z",
  product_data_disclosure_id: "00000000-0000-4000-8000-000000000026",
  product_data_disclosure_fingerprint: "c".repeat(64),
  product_eligibility: "eligible_with_warnings" as const,
  warning_codes: [
    "free_data_research_product",
    "closure_review_weekly_adjusted_return_over_50_percent",
  ],
};

beforeEach(async () => {
  vi.restoreAllMocks();
  await i18n.changeLanguage("en");
  vi.spyOn(api, "v022Products").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    items: [item],
  });
  vi.spyOn(api, "v022Experiment").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    result_evidence_snapshot_id: evidenceId,
    evidence_artifact_id: "00000000-0000-4000-8000-000000000031",
    result_artifact_id: "00000000-0000-4000-8000-000000000032",
    evidence_class: "backtest",
    configuration_snapshot_id: item.configuration_snapshot_id,
    configuration_fingerprint: item.configuration_fingerprint,
    configuration: { frequency: "weekly" },
    display: item.display,
    created_at: "2026-07-01T00:00:00Z",
    evidence: {}, evidence_quality: {}, comparisons: [], matched_baselines: [],
    comparison_context: null,
    outcome: "accepted", quality_status: "passed",
    effective_start: "2007-01-03", effective_end: "2026-06-30",
    core_metrics: { cagr: "0.18", benchmark_cagr: "0.10", cagr_spread: "0.08", sharpe_ratio: "1.4", maximum_drawdown: "-0.22" },
    metrics: {},
    product: { is_candidate: true, is_enrolled: true, product_candidate_definition_id: null, execution_version_id: null, qualification_version_id: null, product_enrollment_id: enrollmentId },
  } as never);
  vi.spyOn(api, "v022ExperimentSeries").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    result_evidence_snapshot_id: evidenceId,
    effective_start: "2007-01-03", effective_end: "2026-06-30",
    total_points: 2, returned_points: 2,
    points: [
      { session_date: "2007-01-03", strategy_nav: "1", benchmark_nav: "1", excess_nav: "1", drawdown: "0" },
      { session_date: "2026-06-30", strategy_nav: "4", benchmark_nav: "2", excess_nav: "2", drawdown: "-0.10" },
    ],
  });
});

test("v0.22 Product catalog links each enrollment to its own detail", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/products"]}><V022ProductsPage /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("Momentum Top 10")).toBeInTheDocument();
  expect(screen.getByText(/permanent quality disclosure/)).toBeInTheDocument();
  expect(screen.getByText("2026-07-03")).toBeInTheDocument();
  expect(screen.getByText("Scheduled")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Momentum Top 10/ })).toHaveAttribute("href", `/products/${enrollmentId}`);
});

test("v0.22 Product detail links back to its exact frozen experiment", async () => {
  vi.spyOn(api, "v022Product").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    ...item,
    qualification: { status: "published" },
    monitoring_policy: {},
    lifecycle_events: [],
    monitoring_snapshots: [],
    decisions: [],
    latest_decision: null,
    data_disclosure: { product_class: "research_product" },
    active_ensemble_state: {
      product_ensemble_state_id: "00000000-0000-4000-8000-000000000027",
      artifact_id: "00000000-0000-4000-8000-000000000028",
      state_version_number: 1,
      member_count: 2,
      state_fingerprint: "d".repeat(64),
      activated_session: "2026-07-03",
      state_document: {
        failure_policy: "retain_previous_complete_state",
        members: [
          { ordinal: 0, target_key: "forward_return_h5", training_preset_key: "ridge_default_v1", adapter_key: "ridge_v1" },
          { ordinal: 1, target_key: "forward_return_h5", training_preset_key: "ridge_strong_v1", adapter_key: "ridge_v1" },
        ],
      },
    },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/products/${enrollmentId}`]}><Routes><Route path="/products/:enrollmentId" element={<V022ProductsPage />} /></Routes></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("12-month momentum")).toBeInTheDocument();
  expect(screen.getByText("2 internal members")).toBeInTheDocument();
  expect(screen.getByText("Forward return H5: Ridge default · Ridge strong")).toBeInTheDocument();
  expect(screen.getByText("Active model state")).toBeInTheDocument();
  expect(screen.getByText("Retain prior complete state")).toBeInTheDocument();
  expect(screen.getByText("ridge_default_v1")).toBeInTheDocument();
  expect(screen.getByText("Research Product using free data sources")).toBeInTheDocument();
  expect(screen.getByText("free_data_research_product")).toBeInTheDocument();
  expect(screen.getByText("Daily adjusted moves above 50% remain recorded for review in the weekly closure")).toBeInTheDocument();
  expect(await screen.findByText("Frozen research backtest")).toBeInTheDocument();
  expect(screen.getByText("Strategy NAV vs SPY")).toBeInTheDocument();
  expect(screen.getByText("18%")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /Open full evidence/ })).toHaveAttribute("href", `/experiments/results/${evidenceId}`);
  expect(screen.getByText(/first eligible decision session is 2026-07-03/i)).toBeInTheDocument();
  expect(screen.getAllByText("Scheduled").length).toBeGreaterThan(0);
});

test("v0.22 Product detail explains when a due session is waiting for data", async () => {
  vi.spyOn(api, "v022Product").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    ...item,
    decision_pipeline_state: "waiting_for_input",
    qualification: { status: "published" },
    monitoring_policy: {},
    lifecycle_events: [],
    monitoring_snapshots: [],
    decisions: [],
    latest_decision: null,
    data_disclosure: { product_class: "research_product" },
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/products/${enrollmentId}`]}><Routes><Route path="/products/:enrollmentId" element={<V022ProductsPage />} /></Routes></MemoryRouter></QueryClientProvider>);
  expect((await screen.findAllByText(/waiting for eligible data/i)).length).toBeGreaterThan(0);
  expect(screen.getByText(/waiting for a same-methodology, fully covered Dataset Gate/i)).toBeInTheDocument();
});
