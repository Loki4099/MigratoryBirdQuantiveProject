import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import i18n from "../i18n";
import { ExperimentsPage } from "../pages/ExperimentsPage";

vi.mock("../components/V022IdentityPanel", () => ({
  V022IdentityPanel: () => <div data-testid="identity-panel" />,
}));

beforeEach(async () => {
  vi.restoreAllMocks();
  await i18n.changeLanguage("en");
});

test("experiment history exposes the exact current compile launch action", async () => {
  vi.spyOn(api, "graphDraftByKey").mockResolvedValue({
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    revision: 7,
  } as never);
  vi.spyOn(api, "currentGraphDraftCompile").mockResolvedValue({
    graph_draft_id: "11111111-1111-4111-8111-111111111111",
    graph_draft_revision: 7,
    graph_fingerprint: "a".repeat(64),
  } as never);
  vi.spyOn(api, "graphSuites").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    items: [],
    total_count: 0,
    limit: 50,
    offset: 0,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/experiments"]}>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Runs & history" }));
  const launch = await screen.findByRole("link", {
    name: /Review and start experiment/,
  });
  expect(launch).toHaveAttribute("href", "/experiment-launch");
  expect(screen.getByText("a".repeat(64))).toBeInTheDocument();
});

test("leaderboard renders one exact cell per row and switches frequency", async () => {
  const leaderboard = vi.spyOn(api, "v022ExperimentLeaderboard").mockImplementation(async ({ frequency }) => ({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    available_frequencies: ["weekly", "monthly"],
    comparison_context: {
      evaluation_cohort_version_id: "00000000-0000-4000-8000-000000000001",
      evaluation_cohort_fingerprint: "a".repeat(64),
      cohort_key: `${frequency}_sp500_v1`,
      frequency,
      warmup_start: "2002-01-02",
      evaluation_start: "2007-01-03",
      evaluation_end: "2026-06-30",
      benchmark_key: "spy",
      cost_bps_per_side: "0.0005",
      execution_delay_sessions: 1,
      price_semantics: "historical_constituent_pit__retrospective_price_snapshot",
      ranking_cohort_release_id: "00000000-0000-4000-8000-000000000002",
      ranking_cohort_artifact_id: "00000000-0000-4000-8000-000000000003",
      ranking_version_number: 1,
      member_count: 1,
    },
    sort: "sharpe_ratio",
    total: 1,
    limit: 200,
    offset: 0,
    rows: [{
      rank: 1,
      result_evidence_snapshot_id: "00000000-0000-4000-8000-000000000004",
      result_artifact_id: "00000000-0000-4000-8000-000000000005",
      configuration_snapshot_id: "00000000-0000-4000-8000-000000000006",
      configuration_fingerprint: "b".repeat(64),
      configuration: {},
      display: {
        direct_inputs: [{ name: "12-month momentum" }],
        aggregation: { name: "Equal-weight mean" },
        strategy: { name: "Top 10" },
        defense: { name: "No defense", none: true },
      },
      cagr: "0.18",
      benchmark_cagr: "0.10",
      cagr_spread: "0.08",
      sharpe_ratio: "1.4",
      maximum_drawdown: "-0.22",
      product_candidate: true,
      product_definition_id: "00000000-0000-4000-8000-000000000007",
      execution_version_id: "00000000-0000-4000-8000-000000000008",
      product_enrollment_id: "00000000-0000-4000-8000-000000000009",
    }],
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={["/experiments"]}><ExperimentsPage /></MemoryRouter></QueryClientProvider>);

  expect(await screen.findByText("12-month momentum")).toBeInTheDocument();
  expect(screen.queryByTestId("identity-panel")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open detailed backtest for rank 1" })).toHaveAttribute("href", "/experiments/results/00000000-0000-4000-8000-000000000004");
  expect(screen.getByRole("link", { name: "Full backtest" })).toHaveAttribute("href", "/experiments/results/00000000-0000-4000-8000-000000000004");
  expect(screen.getByRole("link", { name: "Product" })).toHaveAttribute("href", "/products/00000000-0000-4000-8000-000000000009");
  fireEvent.click(screen.getByRole("button", { name: "Monthly" }));
  await waitFor(() => expect(leaderboard).toHaveBeenLastCalledWith({ frequency: "monthly", sort: "sharpe_ratio" }));
});

test("a completed Suite opens the leaderboard with frequency controls and keeps run details secondary", async () => {
  const suiteId = "22222222-2222-4222-8222-222222222222";
  vi.spyOn(api, "graphSuiteStatus").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
    status: "completed",
    total: 10,
    terminal: 10,
    complete: true,
    status_counts: { completed: 10 },
    suite_mode: "exploratory",
  });
  vi.spyOn(api, "graphSuiteResults").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
    status: "completed",
    complete: true,
    expected_result_count: 2,
    result_count: 0,
    results: [],
  });
  const leaderboard = vi.spyOn(api, "v022ExperimentLeaderboard").mockImplementation(async ({ frequency }) => ({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    available_frequencies: ["weekly", "monthly"],
    comparison_context: {
      evaluation_cohort_version_id: "00000000-0000-4000-8000-000000000001",
      evaluation_cohort_fingerprint: "a".repeat(64),
      cohort_key: `${frequency}_sp500_v1`,
      frequency,
      warmup_start: "2002-01-02",
      evaluation_start: "2007-01-03",
      evaluation_end: "2026-06-30",
      benchmark_key: "spy" as const,
      cost_bps_per_side: "0.0005",
      execution_delay_sessions: 1,
      price_semantics: "historical_constituent_pit__retrospective_price_snapshot",
      ranking_cohort_release_id: "00000000-0000-4000-8000-000000000002",
      ranking_cohort_artifact_id: "00000000-0000-4000-8000-000000000003",
      ranking_version_number: 1,
      member_count: 0,
    },
    sort: "sharpe_ratio",
    total: 0,
    limit: 200,
    offset: 0,
    rows: [],
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[`/experiments?graph_suite=${suiteId}`]}><ExperimentsPage /></MemoryRouter></QueryClientProvider>);

  expect(await screen.findByRole("button", { name: "Weekly" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Monthly" })).toBeInTheDocument();
  expect(screen.queryByTestId("identity-panel")).not.toBeInTheDocument();
  expect(screen.queryByText("Portfolio Cell results")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Monthly" }));
  await waitFor(() => expect(leaderboard).toHaveBeenLastCalledWith({ frequency: "monthly", sort: "sharpe_ratio" }));
  fireEvent.click(screen.getByRole("button", { name: "This run" }));
  expect(await screen.findByText("Portfolio Cell results")).toBeInTheDocument();
  expect(screen.getByText("10 / 10")).toBeInTheDocument();
});

test("a controlled launch batch shows independent weekly and monthly progress", async () => {
  const batchId = "11111111-1111-4111-8111-111111111111";
  vi.spyOn(api, "graphSuiteLaunchBatchStatus").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    suite_launch_batch_id: batchId,
    source_graph_draft_id: "22222222-2222-4222-8222-222222222222",
    source_graph_draft_revision: 8,
    batch_fingerprint: "a".repeat(64),
    status: "running",
    children: [
      {
        frequency: "weekly",
        graph_draft_id: "22222222-2222-4222-8222-222222222222",
        graph_draft_revision: 8,
        compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
        research_suite_id: "44444444-4444-4444-8444-444444444444",
        status: "targeting",
        total: 10,
        terminal: 3,
        status_counts: { completed: 3, running: 1, queued: 6 },
        complete: false,
      },
      {
        frequency: "monthly",
        graph_draft_id: "55555555-5555-4555-8555-555555555555",
        graph_draft_revision: 2,
        compiled_research_graph_id: "66666666-6666-4666-8666-666666666666",
        research_suite_id: "77777777-7777-4777-8777-777777777777",
        status: "not_started",
        total: 0,
        terminal: 0,
        status_counts: {},
        complete: false,
      },
    ],
    reused: true,
  });
  vi.spyOn(api, "graphSuiteRuntimeReadiness").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    ready: true,
    state: "working",
    worker_key: "test-worker",
    process_id: 1234,
    heartbeat_at: "2026-08-18T00:00:00Z",
    max_age_seconds: 10,
    error_summary: null,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiments?launch_batch=${batchId}`]}>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByRole("heading", {
    name: "Weekly and monthly experiment batch",
  })).toBeInTheDocument();
  expect(await screen.findByRole("heading", {
    name: "Weekly experiment",
  })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Monthly experiment" })).toBeInTheDocument();
  expect(screen.getByText("3 / 10")).toBeInTheDocument();
  expect(screen.getByText(/Completed 3 · Running 1 · Queued 6/)).toBeInTheDocument();
  expect(screen.getAllByRole("link", { name: /Open Suite/ })).toHaveLength(2);
});

test("a submitted Suite with no work yet remains a waiting runtime state", async () => {
  const suiteId = "22222222-2222-4222-8222-222222222222";
  vi.spyOn(api, "graphSuiteStatus").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
    status: "not_started",
    total: 0,
    terminal: 0,
    complete: false,
    status_counts: {},
    suite_mode: "exploratory",
  });
  vi.spyOn(api, "graphSuiteRuntimeReadiness").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    ready: true,
    state: "working",
    worker_key: "test-worker",
    process_id: 1234,
    heartbeat_at: "2026-08-14T00:00:00Z",
    max_age_seconds: 10,
    error_summary: null,
  });
  const results = vi.spyOn(api, "graphSuiteResults");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiments?graph_suite=${suiteId}`]}>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText(
    "Experiment submitted; waiting for its runtime plan",
  )).toBeInTheDocument();
  expect(screen.getByText(/Closing the browser does not interrupt/)).toBeInTheDocument();
  expect(await screen.findByText(
    "The runtime is processing the experiment",
  )).toBeInTheDocument();
  expect(results).not.toHaveBeenCalled();
});

test("processing materialization is shown as real progress before the Suite plan exists", async () => {
  const suiteId = "22222222-2222-4222-8222-222222222222";
  vi.spyOn(api, "graphSuiteStatus").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
    status: "materializing",
    total: 8,
    terminal: 6,
    complete: false,
    status_counts: { completed: 6, running: 1, queued: 1 },
    suite_mode: "exploratory",
  });
  vi.spyOn(api, "graphSuiteRuntimeReadiness").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    ready: true,
    state: "working",
    worker_key: "worker",
    process_id: 1,
    heartbeat_at: new Date().toISOString(),
    max_age_seconds: 10,
    error_summary: null,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiments?graph_suite=${suiteId}`]}>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText("Materializing processing outputs")).toBeInTheDocument();
  expect(screen.getByText("6 / 8")).toBeInTheDocument();
  expect(screen.queryByText(
    "Experiment submitted; waiting for its runtime plan",
  )).not.toBeInTheDocument();
});

test("a worker error replaces the misleading not-started waiting state", async () => {
  const suiteId = "22222222-2222-4222-8222-222222222222";
  vi.spyOn(api, "graphSuiteStatus").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
    status: "not_started",
    total: 0,
    terminal: 0,
    complete: false,
    status_counts: {},
    suite_mode: "exploratory",
  });
  vi.spyOn(api, "graphSuiteRuntimeReadiness").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "warning", codes: ["suite_worker.error"] },
    contract_version: "v0.22.0",
    ready: false,
    state: "error",
    worker_key: "test-worker",
    process_id: 1234,
    heartbeat_at: "2026-08-14T00:00:00Z",
    max_age_seconds: 10,
    error_summary: "ValueError: Artifact identity already exists with different semantics",
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiments?graph_suite=${suiteId}`]}>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText(
    "The backtest runtime failed; the experiment cannot continue",
  )).toBeInTheDocument();
  expect(screen.getByText(
    "ValueError: Artifact identity already exists with different semantics",
  )).toBeInTheDocument();
  expect(screen.queryByText(
    "Experiment submitted; waiting for its runtime plan",
  )).not.toBeInTheDocument();
});

test("a stale worker heartbeat is reported as uncertain rather than running", async () => {
  const suiteId = "22222222-2222-4222-8222-222222222222";
  vi.spyOn(api, "graphSuiteStatus").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    contract_version: "v0.22.0",
    research_suite_id: suiteId,
    compiled_research_graph_id: "33333333-3333-4333-8333-333333333333",
    status: "not_started",
    total: 0,
    terminal: 0,
    complete: false,
    status_counts: {},
    suite_mode: "exploratory",
  });
  vi.spyOn(api, "graphSuiteRuntimeReadiness").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "warning", codes: ["suite_worker.stale"] },
    contract_version: "v0.22.0",
    ready: false,
    state: "stale",
    worker_key: "test-worker",
    process_id: 1234,
    heartbeat_at: "2026-08-14T00:00:00Z",
    max_age_seconds: 10,
    error_summary: null,
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/experiments?graph_suite=${suiteId}`]}>
        <ExperimentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

  expect(await screen.findByText(
    "The runtime heartbeat is stale; processing cannot be confirmed",
  )).toBeInTheDocument();
  expect(screen.getByText(/Last heartbeat:/)).toBeInTheDocument();
  expect(screen.queryByText(
    "Experiment submitted; waiting for its runtime plan",
  )).not.toBeInTheDocument();
});
