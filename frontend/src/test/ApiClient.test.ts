import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";

beforeEach(() => {
  vi.restoreAllMocks();
});

test("mutation actor claims come from the trusted session and are cached", async () => {
  const requests: Array<{ url: string; body: Record<string, unknown> | null }> = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    requests.push({
      url,
      body: init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : null,
    });
    if (url.endsWith("/api/v2/session")) {
      return new Response(JSON.stringify({
        context: { api_version: "v2", system_version: "0.22.0", read_only: false },
        quality: { state: "ok", codes: [] },
        actor_key: "trusted-researcher",
        roles: ["researcher"],
        authentication_source: "test",
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  await api.applyGraphDraftEvent("draft-1", {
    expectedRevision: 1,
    eventType: "select_feature_occurrence",
    event: { feature_key: "feature-1", stage_no: 3 },
  });
  await api.submitWorkspaceSuite(1);
  await api.submitGraphSuite(
    "44444444-4444-4444-8444-444444444444",
    "99999999-9999-4999-8999-999999999999",
    "33333333-3333-4333-8333-333333333333",
    7,
  );
  await api.graphSuites(25, 50);
  await api.graphSuiteResults("44444444-4444-4444-8444-444444444444");

  expect(requests.filter(({ url }) => url.endsWith("/api/v2/session"))).toHaveLength(1);
  expect(requests.find(({ url }) => url.endsWith("/events"))?.body?.actor_key)
    .toBe("trusted-researcher");
  expect(requests.find(({ url }) => url.endsWith("/workspace/suites"))?.body?.researcher_id)
    .toBe("trusted-researcher");
  expect(requests.find(({ url }) => url.endsWith("/workspace/graph-suites"))?.body)
    .toMatchObject({
      actor_key: "trusted-researcher",
      compiled_research_graph_id: "44444444-4444-4444-8444-444444444444",
      graph_draft_id: "33333333-3333-4333-8333-333333333333",
      graph_draft_revision: 7,
      idempotency_key: "99999999-9999-4999-8999-999999999999",
      suite_mode: "exploratory",
    });
  expect(requests.some(({ url }) => url.endsWith(
    "/workspace/graph-suites/44444444-4444-4444-8444-444444444444/results",
  ))).toBe(true);
  expect(requests.some(({ url }) => url.endsWith(
    "/workspace/graph-suites?limit=25&offset=50",
  ))).toBe(true);
});

test("release control is read from the authoritative v0.22 endpoint", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    context: { api_version: "v2", system_version: "0.22.0", read_only: false },
    quality: { state: "ok", codes: [] },
    state: "explicit_eligible",
    transition_sequence: 2,
    transition_artifact_id: "00000000-0000-0000-0000-000000000022",
    default_contract: "v0.21",
    maintenance_read_only: false,
    shadow_runtime_allowed: true,
    v021_research_creation_allowed: true,
    v022_explicit_creation_allowed: true,
  }), { status: 200, headers: { "Content-Type": "application/json" } }));

  const release = await api.releaseControl();

  expect(fetch).toHaveBeenCalledWith("/api/v2/release-control", expect.any(Object));
  expect(release.state).toBe("explicit_eligible");
  expect(release.default_contract).toBe("v0.21");
  expect(release.v022_explicit_creation_allowed).toBe(true);
});

test("graph preview serializes strategy parameter preset selections exactly", async () => {
  let body: Record<string, unknown> | null = null;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_input, init) => {
    body = init?.body
      ? JSON.parse(String(init.body)) as Record<string, unknown>
      : null;
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });

  await api.graphWorkspacePreview({
    frequency: "weekly",
    explicitFeatures: [{ featureKey: "return_continuation__w120", stageNo: 3 }],
    aggregationFamilyKeys: ["flat_equal_weight_mean"],
    aggregationParameterPresetKeys: { flat_equal_weight_mean: ["signal_equal_v1"] },
    strategyKeys: ["cross_section_rank_top_k_parity"],
    strategyParameterPresetKeys: { cross_section_rank_top_k_parity: ["k1", "k2"] },
    defenseKeys: ["none"],
  });

  expect(body).toEqual({
    frequency: "weekly",
    explicit_features: [{ feature_key: "return_continuation__w120", stage_no: 3 }],
    aggregation_family_keys: ["flat_equal_weight_mean"],
    aggregation_parameter_preset_keys: { flat_equal_weight_mean: ["signal_equal_v1"] },
    strategy_keys: ["cross_section_rank_top_k_parity"],
    strategy_parameter_preset_keys: { cross_section_rank_top_k_parity: ["k1", "k2"] },
    defense_keys: ["none"],
  });
});

test("graph stage pagination rejects values outside the backend contract", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  await api.graphStageFamilies("draft-1", { stageNo: 1, limit: 50 });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v2/workspace/graph-drafts/draft-1/stages/1/families?limit=50",
    expect.any(Object),
  );
  expect(() => api.graphStageFamilies("draft-1", { stageNo: 1, limit: 51 }))
    .toThrow("between 1 and 50");
});

test("graph Draft recovery uses the stable server key endpoint", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  await api.graphDraftByKey("browser_default_v1");
  expect(fetch).toHaveBeenCalledWith(
    "/api/v2/workspace/graph-drafts/by-key/browser_default_v1",
    expect.any(Object),
  );
});

test("current Graph Draft compile is restored through a read-only endpoint", async () => {
  const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({}), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }));

  await api.currentGraphDraftCompile("draft-1");
  expect(fetch).toHaveBeenCalledWith(
    "/api/v2/workspace/graph-drafts/draft-1/current-compile",
    expect.any(Object),
  );
});
