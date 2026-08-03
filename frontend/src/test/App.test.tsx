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

function renderRoute(path: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter></QueryClientProvider>);
}

beforeEach(async () => {
  window.localStorage.clear();
  await i18n.changeLanguage("zh-CN");
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
    const url = String(input);
    const payload = url.includes("factors/overview") ? factorOverview : url.includes("data/overview") ? dataOverview : url.includes("health") ? health : url.includes("capabilities") ? capabilities : artifacts;
    return new Response(JSON.stringify(payload), { status: 200, headers: { "Content-Type": "application/json" } });
  });
});

test("renders real foundation data without inventing future research results", async () => {
  renderRoute("/?lang=zh-CN");
  expect(await screen.findByText("从可追溯的研究对象开始")).toBeInTheDocument();
  expect(screen.getByText("20260802_02_v02_lineage")).toBeInTheDocument();
  expect(screen.getByText("信号库")).toBeInTheDocument();
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
