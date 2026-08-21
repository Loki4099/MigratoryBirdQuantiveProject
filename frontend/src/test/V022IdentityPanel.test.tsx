import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import { V022IdentityPanel } from "../components/V022IdentityPanel";
import i18n from "../i18n";

const snapshotId = "00000000-0000-0000-0000-000000000101";
const artifactId = "00000000-0000-0000-0000-000000000102";

const configuration = {
  aggregation: { family_key: "flat_equal_weight_mean", execution_mode: "deterministic" },
  direct_inputs: [{ family_key: "return_continuation", variant_key: "return_continuation__w120" }],
  strategy: { family_key: "cross_section_rank_top_k", variant_key: "top_k_3" },
  defense: null,
};
const display = {
  aggregation: { name: "Equal-weight mean" },
  direct_inputs: [{ name: "Return continuation" }],
  strategy: { name: "Cross-section Top 3" },
  defense: { name: "No defense", none: true },
};

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

test("uses the Result Evidence Snapshot identity for the detail request", async () => {
  vi.spyOn(api, "v022Experiments").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    items: [{
      result_evidence_snapshot_id: snapshotId,
      evidence_artifact_id: artifactId,
      result_artifact_id: "00000000-0000-0000-0000-000000000103",
      evidence_class: "backtest",
      configuration_snapshot_id: "00000000-0000-0000-0000-000000000104",
      configuration_fingerprint: "a".repeat(64),
      configuration,
      display,
      created_at: "2026-08-11T08:00:00Z",
    }],
  });
  const detail = vi.spyOn(api, "v022Experiment").mockResolvedValue({
    context: { api_version: "v2", system_version: "0.22.0", read_only: true },
    quality: { state: "ok", codes: [] },
    result_evidence_snapshot_id: snapshotId,
    evidence_artifact_id: artifactId,
    result_artifact_id: "00000000-0000-0000-0000-000000000103",
    evidence_class: "backtest",
    configuration_snapshot_id: "00000000-0000-0000-0000-000000000104",
    configuration_fingerprint: "a".repeat(64),
    configuration,
    display,
    created_at: "2026-08-11T08:00:00Z",
    evidence: {},
    evidence_quality: {},
    comparisons: [],
    matched_baselines: [],
    comparison_context: null,
    outcome: "accepted",
    quality_status: "passed",
    effective_start: "2026-01-02",
    effective_end: "2026-07-17",
    core_metrics: {
      cagr: "0.12",
      benchmark_cagr: "0.08",
      cagr_spread: "0.04",
      sharpe_ratio: "1.1",
      maximum_drawdown: "-0.10",
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
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

  render(<QueryClientProvider client={queryClient}>
    <V022IdentityPanel kind="experiment" />
  </QueryClientProvider>);

  expect(await screen.findByText("Return continuation")).toBeInTheDocument();
  await waitFor(() => expect(detail).toHaveBeenCalledWith(snapshotId));
  expect(detail).not.toHaveBeenCalledWith(artifactId);
});
