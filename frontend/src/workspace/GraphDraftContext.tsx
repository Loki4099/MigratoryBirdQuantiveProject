import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  type ReactNode,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  api,
  ApiClientError,
  type GraphChangePreviewResponse,
  type GraphDraftCompileResponse,
  type GraphDraftDerivedViewResponse,
  type GraphDraftSnapshotResponse,
  type GraphSuiteLaunchBatchResponse,
  type GraphSuiteSubmitResponse,
} from "../api/client";
import { V022_RELEASE_CONTROL_QUERY_KEY } from "../release/useV022ReleaseControl";

const STORAGE_KEY = "style-rotation-v022-graph-draft";
const COMPILE_STORAGE_KEY = "style-rotation-v022-last-compile";
const QUERY_KEY = ["v022-graph-draft", "browser_default_v1"] as const;
const REVISION_CHANNEL = "style-rotation-v022-graph-revisions";

export type GraphDerivedView = GraphDraftDerivedViewResponse;
type StageNo = 0 | 1 | 2 | 3;

interface StoredIdentity {
  graphDraftId?: string;
  createIdempotencyKey: string;
}

interface GraphDraftContextValue {
  snapshot: GraphDraftSnapshotResponse | undefined;
  derived: GraphDerivedView | undefined;
  pendingImpact: GraphChangePreviewResponse | null;
  lastCompile: GraphDraftCompileResponse | null;
  lastSuite: GraphSuiteSubmitResponse | null;
  lastLaunchBatch: GraphSuiteLaunchBatchResponse | null;
  loading: boolean;
  busy: boolean;
  pendingCommandCount: number;
  pendingOccurrences: string[];
  queuePaused: boolean;
  locked: boolean;
  error: string | null;
  toggleFeature: (featureKey: string, stageNo: StageNo, selected: boolean) => Promise<void>;
  selectFeatureBatch: (
    occurrences: Array<{ featureKey: string; stageNo: StageNo }>,
  ) => Promise<void>;
  selectAllStage: (stageNo: StageNo) => Promise<void>;
  clearStage: (stageNo: StageNo) => Promise<void>;
  toggleAggregation: (familyKey: string, selected: boolean) => Promise<void>;
  setAggregationPresets: (familyKey: string, presetKeys: string[]) => Promise<void>;
  setAggregationTargets: (familyKey: string, targetKeys: string[]) => Promise<void>;
  setAggregationTrainingPresets: (familyKey: string, presetKeys: string[]) => Promise<void>;
  setStrategyPresets: (strategyKey: string, presetKeys: string[]) => Promise<void>;
  selectAllStrategies: () => Promise<void>;
  clearStrategies: () => Promise<void>;
  toggleDefense: (defenseKey: string, selected: boolean) => Promise<void>;
  selectAllDefenses: () => Promise<void>;
  clearDefenses: () => Promise<void>;
  setFrequency: (frequency: "weekly" | "monthly") => Promise<void>;
  setAssetSelection: (securityIds: string[]) => Promise<boolean>;
  loadRepresentative: () => Promise<void>;
  cloneRevision: () => Promise<void>;
  previewCatalogRebase: () => Promise<void>;
  confirmImpact: () => Promise<void>;
  cancelImpact: () => void;
  compile: () => Promise<GraphDraftCompileResponse | undefined>;
  submitSuite: () => Promise<GraphSuiteSubmitResponse | undefined>;
  submitLaunchBatch: (
    frequencies: Array<"weekly" | "monthly">,
  ) => Promise<GraphSuiteLaunchBatchResponse | undefined>;
  resetCurrentResearch: () => Promise<void>;
  reload: () => Promise<void>;
}

interface QueuedGraphCommand {
  eventType: string;
  event: Record<string, unknown>;
  occurrenceLabels: string[];
  resolve: () => void;
}

interface GraphRevisionNotice {
  sourceId: string;
  graphDraftId: string;
  revision: number;
}

const Context = createContext<GraphDraftContextValue | null>(null);

export function GraphDraftProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingImpact, setPendingImpact] = useState<GraphChangePreviewResponse | null>(null);
  const [lastCompile, setLastCompile] = useState<GraphDraftCompileResponse | null>(() => {
    try {
      const stored = window.sessionStorage.getItem(COMPILE_STORAGE_KEY);
      return stored ? JSON.parse(stored) as GraphDraftCompileResponse : null;
    } catch {
      return null;
    }
  });
  const [lastSuite, setLastSuite] = useState<GraphSuiteSubmitResponse | null>(null);
  const [lastLaunchBatch, setLastLaunchBatch] =
    useState<GraphSuiteLaunchBatchResponse | null>(null);
  const [pendingCommandCount, setPendingCommandCount] = useState(0);
  const [pendingOccurrences, setPendingOccurrences] = useState<string[]>([]);
  const [queuePaused, setQueuePaused] = useState(false);
  const commandQueue = useRef<QueuedGraphCommand[]>([]);
  const processingQueue = useRef(false);
  const pausedQueue = useRef(false);
  const exclusiveChange = useRef(false);
  const sourceId = useRef(crypto.randomUUID());
  const revisionChannel = useRef<BroadcastChannel | null>(null);
  const query = useQuery({
    queryKey: QUERY_KEY,
    queryFn: restoreOrCreate,
    retry: false,
  });

  const current = () => queryClient.getQueryData<GraphDraftSnapshotResponse>(QUERY_KEY);
  const invalidateReleaseAdmission = (caught: unknown) => {
    if (caught instanceof ApiClientError && caught.code === "mutation_admission_blocked") {
      void queryClient.invalidateQueries({ queryKey: V022_RELEASE_CONTROL_QUERY_KEY });
    }
  };
  const storeSnapshot = (snapshot: GraphDraftSnapshotResponse) => {
    queryClient.setQueryData(QUERY_KEY, snapshot);
    setLastCompile((compiled) => {
      const currentCompile = compiled
        && compiled.graph_draft_id === snapshot.graph_draft_id
        && compiled.graph_draft_revision === snapshot.revision
        ? compiled
        : null;
      if (!currentCompile) window.sessionStorage.removeItem(COMPILE_STORAGE_KEY);
      return currentCompile;
    });
    setLastSuite(null);
    setLastLaunchBatch(null);
    revisionChannel.current?.postMessage({
      sourceId: sourceId.current,
      graphDraftId: snapshot.graph_draft_id,
      revision: snapshot.revision,
    } satisfies GraphRevisionNotice);
  };
  const reloadAfterConflict = async () => {
    await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    await queryClient.refetchQueries({ queryKey: QUERY_KEY });
  };
  const applyDirect = async (
    snapshot: GraphDraftSnapshotResponse,
    eventType: string,
    event: Record<string, unknown>,
  ) => {
    try {
      const next = await api.applyGraphDraftEvent(snapshot.graph_draft_id, {
        expectedRevision: snapshot.revision,
        eventType,
        event,
      });
      storeSnapshot(next);
      return next;
    } catch (caught) {
      invalidateReleaseAdmission(caught);
      if (caught instanceof ApiClientError && caught.code === "draft_revision_conflict") {
        await reloadAfterConflict();
      }
      throw caught;
    }
  };
  const run = async (action: () => Promise<void>) => {
    if (exclusiveChange.current) return;
    exclusiveChange.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (caught) {
      invalidateReleaseAdmission(caught);
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
      exclusiveChange.current = false;
    }
  };
  const runExclusive = async (action: () => Promise<void>) => {
    if (processingQueue.current || commandQueue.current.length) {
      setError("Wait for queued Workspace commands before this operation");
      return;
    }
    await run(action);
  };
  const refreshPendingOccurrences = () => {
    setPendingOccurrences([
      ...new Set(commandQueue.current.flatMap((item) => item.occurrenceLabels)),
    ]);
  };
  const processCommandQueue = async () => {
    if (processingQueue.current || pausedQueue.current) return;
    processingQueue.current = true;
    try {
      while (commandQueue.current.length && !pausedQueue.current) {
        const command = commandQueue.current[0];
        const snapshot = current();
        if (!snapshot) break;
        try {
          const next = await api.applyGraphDraftEvent(snapshot.graph_draft_id, {
            expectedRevision: snapshot.revision,
            eventType: command.eventType,
            event: command.event,
          });
          storeSnapshot(next);
          command.resolve();
        } catch (caught) {
          invalidateReleaseAdmission(caught);
          command.resolve();
          if (caught instanceof ApiClientError && caught.code === "draft_revision_conflict") {
            pausedQueue.current = true;
            setQueuePaused(true);
            setError(caught.message);
          } else if (
            caught instanceof ApiClientError
            && caught.code === "cascade_confirmation_required"
            && command.eventType === "deselect_feature_occurrence"
          ) {
            const featureKey = String(command.event.feature_key);
            const stageNo = Number(command.event.stage_no) as StageNo;
            setPendingImpact(await api.previewGraphDraftChange(snapshot.graph_draft_id, {
              expectedRevision: snapshot.revision,
              featureKey,
              stageNo,
            }));
            pausedQueue.current = true;
            setQueuePaused(true);
          } else {
            setError(caught instanceof Error ? caught.message : String(caught));
          }
        } finally {
          commandQueue.current.shift();
          setPendingCommandCount((count) => Math.max(0, count - 1));
          refreshPendingOccurrences();
        }
      }
    } finally {
      processingQueue.current = false;
    }
  };
  const enqueueEvent = (
    eventType: string,
    event: Record<string, unknown>,
    occurrenceLabels: string[] = [],
  ) => new Promise<void>((resolve) => {
    if (current()?.status !== "draft") {
      setError("The current research is locked; reset it before changing upstream settings");
      resolve();
      return;
    }
    if (exclusiveChange.current || pendingImpact || pausedQueue.current) {
      setError("Workspace mutation queue is paused by an exclusive change");
      resolve();
      return;
    }
    setError(null);
    commandQueue.current.push({ eventType, event, occurrenceLabels, resolve });
    setPendingCommandCount((count) => count + 1);
    refreshPendingOccurrences();
    void processCommandQueue();
  });
  const resumeQueue = async () => {
    await reloadAfterConflict();
    pausedQueue.current = false;
    setQueuePaused(false);
    setError(null);
    void processCommandQueue();
  };

  useEffect(() => {
    invalidateReleaseAdmission(query.error);
  // Release admission is refreshed only when the Graph query itself fails.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query.error]);

  useEffect(() => {
    const snapshot = query.data;
    if (!snapshot) return undefined;
    let cancelled = false;
    void api.currentGraphDraftCompile(snapshot.graph_draft_id).then((compiled) => {
      if (
        cancelled
        || compiled.graph_draft_id !== snapshot.graph_draft_id
        || compiled.graph_draft_revision !== snapshot.revision
      ) return;
      window.sessionStorage.setItem(COMPILE_STORAGE_KEY, JSON.stringify(compiled));
      setLastCompile(compiled);
    }).catch((caught: unknown) => {
      if (cancelled) return;
      if (caught instanceof ApiClientError && caught.status === 404) {
        window.sessionStorage.removeItem(COMPILE_STORAGE_KEY);
        setLastCompile(null);
        return;
      }
      setError(caught instanceof Error ? caught.message : String(caught));
    });
    return () => {
      cancelled = true;
    };
  }, [query.data]);

  useEffect(() => {
    if (!("BroadcastChannel" in window)) return undefined;
    const channel = new BroadcastChannel(REVISION_CHANNEL);
    revisionChannel.current = channel;
    channel.onmessage = (message: MessageEvent<GraphRevisionNotice>) => {
      const notice = message.data;
      const snapshot = queryClient.getQueryData<GraphDraftSnapshotResponse>(QUERY_KEY);
      if (
        !snapshot
        || notice.sourceId === sourceId.current
        || notice.graphDraftId !== snapshot.graph_draft_id
        || notice.revision <= snapshot.revision
      ) return;
      if (processingQueue.current || commandQueue.current.length) {
        pausedQueue.current = true;
        setQueuePaused(true);
        setError(
          `Another tab advanced this Draft to revision ${notice.revision}; reload before resuming queued commands`,
        );
      }
      void reloadAfterConflict();
    };
    return () => {
      channel.close();
      if (revisionChannel.current === channel) revisionChannel.current = null;
    };
  // The channel reads authoritative state through the stable QueryClient.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [queryClient]);

  const value = useMemo<GraphDraftContextValue>(() => ({
    snapshot: query.data,
    derived: query.data?.derived_view,
    pendingImpact,
    lastCompile,
    lastSuite,
    lastLaunchBatch,
    loading: query.isLoading,
    busy,
    pendingCommandCount,
    pendingOccurrences,
    queuePaused,
    locked: query.data?.status !== "draft",
    error: error ?? (query.error instanceof Error ? query.error.message : null),
    toggleFeature: (featureKey, stageNo, selected) => enqueueEvent(
      selected ? "deselect_feature_occurrence" : "select_feature_occurrence",
      { feature_key: featureKey, stage_no: stageNo },
      [`${featureKey}@${stageNo}`],
    ),
    selectFeatureBatch: (occurrences) => enqueueEvent(
      "batch_select_feature_occurrences",
      {
        occurrences: occurrences.map((item) => ({
          feature_key: item.featureKey,
          stage_no: item.stageNo,
        })),
      },
      occurrences.map((item) => `${item.featureKey}@${item.stageNo}`),
    ),
    selectAllStage: (stageNo) => enqueueEvent(
      "select_all_legal_feature_occurrences",
      { stage_no: stageNo },
    ),
    clearStage: (stageNo) => enqueueEvent(
      "clear_stage_feature_occurrences",
      { stage_no: stageNo },
    ),
    toggleAggregation: (familyKey, selected) => enqueueEvent(
        selected ? "deselect_aggregation_family" : "select_aggregation_family",
        { family_key: familyKey },
    ),
    setAggregationPresets: (familyKey, presetKeys) => enqueueEvent(
      "set_aggregation_parameter_presets",
      { family_key: familyKey, preset_keys: presetKeys },
    ),
    setAggregationTargets: (familyKey, targetKeys) => enqueueEvent(
      "set_aggregation_targets",
      { family_key: familyKey, target_keys: targetKeys },
    ),
    setAggregationTrainingPresets: (familyKey, presetKeys) => enqueueEvent(
      "set_aggregation_training_presets",
      { family_key: familyKey, preset_keys: presetKeys },
    ),
    setStrategyPresets: (strategyKey, presetKeys) => enqueueEvent(
      "set_strategy_parameter_presets",
      { strategy_key: strategyKey, preset_keys: presetKeys },
      [`strategy:${strategyKey}`],
    ),
    selectAllStrategies: () => enqueueEvent(
      "select_all_compatible_strategy_presets",
      {},
    ),
    clearStrategies: () => enqueueEvent("clear_strategy_presets", {}),
    toggleDefense: (defenseKey, selected) => enqueueEvent(
      selected ? "deselect_defense" : "select_defense",
      { defense_key: defenseKey },
      [`defense:${defenseKey}`],
    ),
    selectAllDefenses: () => enqueueEvent("select_all_compatible_defenses", {}),
    clearDefenses: () => enqueueEvent("clear_defenses", {}),
    setFrequency: async (frequency) => {
      const snapshot = current();
      if (!snapshot || snapshot.intent.frequency === frequency) return;
      await enqueueEvent("set_frequency", { frequency });
    },
    setAssetSelection: async (securityIds) => {
      let saved = false;
      await runExclusive(async () => {
        const snapshot = current();
        if (!snapshot) return;
        await applyDirect(snapshot, "set_asset_selection", {
          security_ids: securityIds,
        });
        saved = true;
      });
      return saved;
    },
    loadRepresentative: () => runExclusive(async () => {
      let snapshot = current();
      if (!snapshot) return;
      const representative = [
        "return_continuation__w120",
        "price_cross_above_ma__s1_l200",
        "low_illiquidity_quality__w20",
      ];
      for (const featureKey of representative) {
        const explicit = snapshot.intent.explicit_features as Array<{
          feature_key: string;
          stage_no: number;
        }>;
        if (!explicit.some((item) => item.feature_key === featureKey && item.stage_no === 3)) {
          snapshot = await applyDirect(snapshot, "select_feature_occurrence", {
            feature_key: featureKey,
            stage_no: 3,
          });
        }
      }
    }),
    cloneRevision: () => runExclusive(async () => {
      const snapshot = current();
      if (!snapshot) return;
      const clone = await api.cloneGraphDraftRevision(snapshot.graph_draft_id, {
        sourceRevision: snapshot.revision,
        draftKey: `browser_clone_${crypto.randomUUID()}`,
        name: `${snapshot.name} / revision ${snapshot.revision} clone`,
      });
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
        graphDraftId: clone.graph_draft_id,
        createIdempotencyKey: crypto.randomUUID(),
      }));
      storeSnapshot(clone);
      setPendingImpact(null);
      setLastCompile(null);
    }),
    previewCatalogRebase: () => runExclusive(async () => {
      const snapshot = current();
      if (!snapshot) return;
      setPendingImpact(await api.previewGraphCatalogRebase(
        snapshot.graph_draft_id,
        snapshot.revision,
      ));
    }),
    confirmImpact: () => run(async () => {
      const snapshot = current();
      if (!snapshot || !pendingImpact) return;
      const next = await api.confirmGraphDraftChange(
        snapshot.graph_draft_id,
        pendingImpact.impact_token,
        pendingImpact.base_revision,
      );
      storeSnapshot(next);
      setPendingImpact(null);
      pausedQueue.current = false;
      setQueuePaused(false);
      void processCommandQueue();
    }),
    cancelImpact: () => {
      setPendingImpact(null);
      pausedQueue.current = false;
      setQueuePaused(false);
      void processCommandQueue();
    },
    compile: async () => {
      const snapshot = current();
      if (!snapshot) return undefined;
      if (processingQueue.current || commandQueue.current.length) {
        setError("Wait for queued Workspace commands before compiling");
        return undefined;
      }
      if (exclusiveChange.current) return undefined;
      exclusiveChange.current = true;
      setBusy(true);
      setError(null);
      try {
        const compiled = await api.compileGraphDraft(
          snapshot.graph_draft_id,
          snapshot.revision,
        );
        window.sessionStorage.setItem(COMPILE_STORAGE_KEY, JSON.stringify(compiled));
        setLastCompile(compiled);
        return compiled;
      } catch (caught) {
        invalidateReleaseAdmission(caught);
        setError(caught instanceof Error ? caught.message : String(caught));
        return undefined;
      } finally {
        setBusy(false);
        exclusiveChange.current = false;
      }
    },
    submitSuite: async () => {
      const snapshot = current();
      const compiled = lastCompile;
      if (
        !snapshot
        || !compiled
        || compiled.graph_draft_id !== snapshot.graph_draft_id
        || compiled.graph_draft_revision !== snapshot.revision
      ) {
        setError("Compile the current Graph Draft before creating an experiment");
        return undefined;
      }
      if (exclusiveChange.current || processingQueue.current || commandQueue.current.length) {
        setError("Wait for pending Workspace commands before creating an experiment");
        return undefined;
      }
      exclusiveChange.current = true;
      setBusy(true);
      setError(null);
      try {
        const storageKey = `v022-suite-command:${compiled.compiled_research_graph_id}`;
        const idempotencyKey = window.sessionStorage.getItem(storageKey) ?? crypto.randomUUID();
        window.sessionStorage.setItem(storageKey, idempotencyKey);
        const submitted = await api.submitGraphSuite(
          compiled.compiled_research_graph_id,
          idempotencyKey,
          snapshot.graph_draft_id,
          snapshot.revision,
        );
        setLastSuite(submitted);
        await reloadAfterConflict();
        return submitted;
      } catch (caught) {
        invalidateReleaseAdmission(caught);
        setError(caught instanceof Error ? caught.message : String(caught));
        return undefined;
      } finally {
        setBusy(false);
        exclusiveChange.current = false;
      }
    },
    submitLaunchBatch: async (frequencies) => {
      const snapshot = current();
      const compiled = lastCompile;
      if (
        !snapshot
        || !compiled
        || compiled.graph_draft_id !== snapshot.graph_draft_id
        || compiled.graph_draft_revision !== snapshot.revision
      ) {
        setError("Compile the current Graph Draft before creating an experiment");
        return undefined;
      }
      if (exclusiveChange.current || processingQueue.current || commandQueue.current.length) {
        setError("Wait for pending Workspace commands before creating an experiment");
        return undefined;
      }
      exclusiveChange.current = true;
      setBusy(true);
      setError(null);
      try {
        const normalized = [...new Set(frequencies)].sort();
        const storageKey = [
          "v022-suite-launch-batch",
          compiled.compiled_research_graph_id,
          normalized.join("+"),
        ].join(":");
        const idempotencyKey = window.sessionStorage.getItem(storageKey)
          ?? crypto.randomUUID();
        window.sessionStorage.setItem(storageKey, idempotencyKey);
        const submitted = await api.submitGraphSuiteLaunchBatch({
          compiledResearchGraphId: compiled.compiled_research_graph_id,
          idempotencyKey,
          graphDraftId: snapshot.graph_draft_id,
          graphDraftRevision: snapshot.revision,
          frequencies,
        });
        setLastLaunchBatch(submitted);
        await reloadAfterConflict();
        return submitted;
      } catch (caught) {
        invalidateReleaseAdmission(caught);
        setError(caught instanceof Error ? caught.message : String(caught));
        return undefined;
      } finally {
        setBusy(false);
        exclusiveChange.current = false;
      }
    },
    resetCurrentResearch: () => runExclusive(async () => {
      const snapshot = current();
      if (!snapshot) return;
      const next = await api.resetGraphDraft(
        snapshot.graph_draft_id,
        snapshot.revision,
      );
      window.sessionStorage.removeItem(
        `v022-suite-command:${lastCompile?.compiled_research_graph_id ?? ""}`,
      );
      storeSnapshot(next);
      setPendingImpact(null);
      setLastCompile(null);
      window.sessionStorage.removeItem(COMPILE_STORAGE_KEY);
      setLastSuite(null);
      setLastLaunchBatch(null);
      await queryClient.invalidateQueries({ queryKey: ["v022"] });
    }),
    reload: () => run(queuePaused ? resumeQueue : reloadAfterConflict),
  // Functions intentionally close over the current query-cache helpers.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [query.data, query.error, query.isLoading, pendingImpact, lastCompile, lastSuite, lastLaunchBatch, busy, error, pendingCommandCount, pendingOccurrences, queuePaused]);

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useGraphDraft() {
  const value = useContext(Context);
  if (!value) throw new Error("useGraphDraft requires GraphDraftProvider");
  return value;
}

async function restoreOrCreate(): Promise<GraphDraftSnapshotResponse> {
  const stored = readIdentity();
  if (stored.graphDraftId) {
    try {
      return await api.graphDraft(stored.graphDraftId);
    } catch (caught) {
      if (!(caught instanceof ApiClientError) || caught.status !== 404) throw caught;
    }
  }
  try {
    const existing = await api.graphDraftByKey("browser_default_v1");
    writeIdentity(existing.graph_draft_id, stored.createIdempotencyKey);
    return existing;
  } catch (caught) {
    if (!(caught instanceof ApiClientError) || caught.status !== 404) throw caught;
  }
  const identity = {
    createIdempotencyKey: stored.createIdempotencyKey || crypto.randomUUID(),
  };
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(identity));
  const snapshot = await api.createGraphDraft({
    idempotencyKey: identity.createIdempotencyKey,
  });
  writeIdentity(snapshot.graph_draft_id, identity.createIdempotencyKey);
  return snapshot;
}

function writeIdentity(graphDraftId: string, createIdempotencyKey: string) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify({
    graphDraftId,
    createIdempotencyKey,
  }));
}

function readIdentity(): StoredIdentity {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "{}") as Partial<StoredIdentity>;
    return {
      graphDraftId: typeof parsed.graphDraftId === "string" ? parsed.graphDraftId : undefined,
      createIdempotencyKey: typeof parsed.createIdempotencyKey === "string"
        ? parsed.createIdempotencyKey
        : crypto.randomUUID(),
    };
  } catch {
    return { createIdempotencyKey: crypto.randomUUID() };
  }
}
