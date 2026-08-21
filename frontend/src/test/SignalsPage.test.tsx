import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import i18n from "../i18n";
import { SignalsPage } from "../pages/SignalsPage";

const workspace = {
  frequency: "weekly" as const,
  assetSecurityIds: ["00000000-0000-0000-0000-000000000001"],
  assetDataInputs: {
    "00000000-0000-0000-0000-000000000001": ["canonical_market_bars"],
  },
  factorVariantKeys: ["total_return__w20"],
  signalVersionKeys: ["return_continuation__total_return__w20"],
  setFrequency: vi.fn(),
  toggleSignal: vi.fn(),
};

vi.mock("../workspace/WorkspaceSelectionContext", () => ({
  useWorkspaceSelection: () => workspace,
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/signals?lang=en&frequency=weekly"]}>
        <SignalsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const baseJob = {
  context: { api_version: "v2", system_version: "0.22.0", read_only: false },
  quality: { state: "ok", codes: [] },
  export_job_id: "00000000-0000-0000-0000-000000000101",
  work_item_id: "00000000-0000-0000-0000-000000000102",
  request_fingerprint: "f".repeat(64),
  stage: "queued",
  attempt_count: 0,
  max_attempts: 3,
  status_url: "/api/v2/signals/research-exports/00000000-0000-0000-0000-000000000101",
  failure_details: {},
};

beforeEach(async () => {
  vi.restoreAllMocks();
  await i18n.changeLanguage("en");
  vi.spyOn(api, "workspaceOptions").mockResolvedValue({ signal_families: [] } as never);
});

test("Signal export polls a persistent job and downloads only after completion", async () => {
  vi.spyOn(api, "exportSelectedSignals").mockResolvedValue({
    ...baseJob,
    status: "queued",
    download_url: null,
  } as never);
  vi.spyOn(api, "signalExportStatus").mockResolvedValue({
    ...baseJob,
    status: "completed",
    stage: "completed",
    download_url: `${baseJob.status_url}/download`,
    content_hash: "a".repeat(64),
    byte_size: 123,
  } as never);
  const download = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "Export selected Signals" }));
  expect(await screen.findByText("Export status: completed")).toBeInTheDocument();
  expect(screen.getByRole("progressbar", { name: "Export progress" })).toHaveAttribute(
    "value",
    "1",
  );
  expect(download).toHaveBeenCalledTimes(1);
});

test("failed Signal export shows the failure and allows a retry", async () => {
  const submit = vi.spyOn(api, "exportSelectedSignals").mockResolvedValue({
    ...baseJob,
    status: "queued",
    download_url: null,
  } as never);
  vi.spyOn(api, "signalExportStatus").mockResolvedValue({
    ...baseJob,
    status: "failed",
    stage: "failed",
    quality: { state: "error", codes: ["signal.export_failed"] },
    failure_class: "data_quality",
    failure_details: { message: "Selected Signals have no values" },
    download_url: null,
  } as never);

  renderPage();
  const button = await screen.findByRole("button", { name: "Export selected Signals" });
  fireEvent.click(button);
  expect(await screen.findByText("Export status: failed")).toBeInTheDocument();
  expect(screen.getByText("Selected Signals have no values")).toBeInTheDocument();
  expect(button).not.toBeDisabled();
  fireEvent.click(button);
  await waitFor(() => expect(submit).toHaveBeenCalledTimes(2));
});
