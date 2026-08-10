import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { api, ApiClientError } from "../api/client";

export type WorkspaceFrequency = "weekly" | "monthly";

export interface WorkspaceSelection {
  assetSecurityIds: string[];
  assetDataInputs: Record<string, string[]>;
  factorVariantKeys: string[];
  signalVersionKeys: string[];
  modelPresetKeys: string[];
  modelTargetKeys: string[];
  strategyPresetKeys: string[];
  frequency: WorkspaceFrequency;
}

interface WorkspaceSelectionValue extends WorkspaceSelection {
  toggleAsset: (key: string) => void;
  setAssets: (keys: string[], selected: boolean) => void;
  toggleAssetInput: (securityId: string, inputKey: string) => void;
  toggleFactor: (key: string) => void;
  toggleSignal: (key: string) => void;
  toggleModel: (key: string) => void;
  toggleModelTarget: (key: string) => void;
  toggleStrategy: (key: string) => void;
  setFrequency: (frequency: WorkspaceFrequency) => void;
  replace: (selection: WorkspaceSelection) => void;
  clear: () => void;
  draftRevision: number | null;
  draftReady: boolean;
  draftMissing: boolean;
  draftSaving: boolean;
  draftDirty: boolean;
  draftError: unknown;
  draftConflict: boolean;
  saveNow: () => Promise<number | null>;
  reloadFromServer: () => Promise<void>;
}

const STORAGE_KEY = "style-rotation-v021-workspace-draft";
const EMPTY: WorkspaceSelection = {
  assetSecurityIds: [], assetDataInputs: {}, factorVariantKeys: [], signalVersionKeys: [],
  modelPresetKeys: [], modelTargetKeys: ["cross_sectional_relative_return__h5"],
  strategyPresetKeys: [], frequency: "weekly",
};
const Context = createContext<WorkspaceSelectionValue | null>(null);

export function WorkspaceSelectionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [initial] = useState(() => readSelection());
  const [selection, setSelection] = useState<WorkspaceSelection>(initial.selection);
  const selectionRef = useRef(selection);
  const initialized = useRef(false);
  const [draftReady, setDraftReady] = useState(false);
  const draftReadyRef = useRef(false);
  const [dirty, setDirty] = useState(false);
  const dirtyRef = useRef(false);
  const [revision, setRevision] = useState<number | null>(null);
  const revisionRef = useRef<number | null>(initial.revision);
  const lastSavedFingerprint = useRef<string | null>(null);
  const saveInFlight = useRef<Promise<number> | null>(null);
  const [conflict, setConflict] = useState(false);
  const conflictRef = useRef(false);

  const draft = useQuery({
    queryKey: ["workspace", "draft", "local", "default"],
    queryFn: () => api.workspaceDraft(),
    retry: false,
  });

  const persist = (next: WorkspaceSelection, storedRevision = revisionRef.current) => {
    selectionRef.current = next;
    setSelection(next);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ selection: next, revision: storedRevision }));
  };
  const update = (next: WorkspaceSelection) => {
    persist(next);
    dirtyRef.current = true;
    setDirty(true);
  };

  const saveDraft = useMutation({
    mutationFn: ({ snapshot, expectedRevision }: { snapshot: WorkspaceSelection; expectedRevision: number | null }) => api.saveWorkspaceDraft({
      name: "Local research draft",
      expectedRevision,
      selection: snapshot,
    }),
    onSuccess: (saved, { snapshot }) => {
      revisionRef.current = saved.revision;
      lastSavedFingerprint.current = fingerprint(snapshot);
      setRevision(saved.revision);
      queryClient.setQueryData(["workspace", "draft", "local", "default"], saved);
      if (fingerprint(selectionRef.current) === fingerprint(snapshot)) {
        persist(selectionRef.current, saved.revision);
        dirtyRef.current = false;
        setDirty(false);
      }
    },
    onError: (error) => {
      if (error instanceof ApiClientError && error.status === 409) {
        conflictRef.current = true;
        setConflict(true);
      }
    },
  });

  const saveLatest = async (): Promise<number | null> => {
    if (!draftReadyRef.current) return null;
    if (conflictRef.current) throw saveDraft.error ?? new Error("Workspace draft revision conflict");
    if (saveInFlight.current) {
      await saveInFlight.current;
      if (fingerprint(selectionRef.current) === lastSavedFingerprint.current) {
        return revisionRef.current;
      }
    }
    if (!dirtyRef.current && revisionRef.current !== null) return revisionRef.current;
    const snapshot = selectionRef.current;
    const operation = saveDraft.mutateAsync({
      snapshot,
      expectedRevision: revisionRef.current,
    }).then((saved) => saved.revision);
    saveInFlight.current = operation;
    try {
      const savedRevision = await operation;
      if (fingerprint(selectionRef.current) !== fingerprint(snapshot)) return saveLatest();
      return savedRevision;
    } finally {
      if (saveInFlight.current === operation) saveInFlight.current = null;
    }
  };

  const reloadFromServer = async () => {
    const refreshed = await draft.refetch();
    if (!refreshed.data?.selection) return;
    const serverSelection = fromApiSelection(refreshed.data.selection);
    revisionRef.current = refreshed.data.revision;
    lastSavedFingerprint.current = fingerprint(serverSelection);
    setRevision(refreshed.data.revision);
    persist(serverSelection, refreshed.data.revision);
    dirtyRef.current = false;
    setDirty(false);
    conflictRef.current = false;
    setConflict(false);
    saveDraft.reset();
    initialized.current = true;
    draftReadyRef.current = true;
    setDraftReady(true);
  };

  /* Server hydration is an intentional one-time state synchronization. */
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (initialized.current || draft.isLoading) return;
    if (draft.data?.selection) {
      revisionRef.current = draft.data.revision;
      setRevision(draft.data.revision);
      const serverSelection = fromApiSelection(draft.data.selection);
      lastSavedFingerprint.current = fingerprint(serverSelection);
      const serverIsNewer = initial.revision === null || draft.data.revision > initial.revision;
      if (!initial.persisted || serverIsNewer) {
        persist(serverSelection, draft.data.revision);
        dirtyRef.current = false;
        setDirty(false);
      } else {
        const differs = fingerprint(selectionRef.current) !== fingerprint(serverSelection);
        dirtyRef.current = differs;
        setDirty(differs);
      }
      initialized.current = true;
      draftReadyRef.current = true;
      setDraftReady(true);
      return;
    }
    if (draft.data) {
      initialized.current = true;
      draftReadyRef.current = true;
      setDraftReady(true);
      return;
    }
    if (draft.error instanceof ApiClientError && draft.error.status === 404) {
      if (initial.persisted) {
        dirtyRef.current = true;
        setDirty(true);
      }
      initialized.current = true;
      draftReadyRef.current = true;
      setDraftReady(true);
    }
  // Initialization deliberately prefers an existing local snapshot over an older server draft.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.data, draft.error, draft.isLoading]);
  /* eslint-enable react-hooks/set-state-in-effect */

  useEffect(() => {
    if (!draftReady || !dirty || saveDraft.isPending || saveDraft.isError || conflict) return;
    const timer = window.setTimeout(() => void saveLatest().catch(() => undefined), 900);
    return () => window.clearTimeout(timer);
  // The mutation object is intentionally omitted; only its observable pending state is relevant.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draftReady, dirty, saveDraft.isPending, saveDraft.isError, conflict, selection]);
  const toggle = (field: keyof Pick<WorkspaceSelection, "assetSecurityIds" | "factorVariantKeys" | "signalVersionKeys" | "modelPresetKeys" | "modelTargetKeys" | "strategyPresetKeys">, key: string) => {
    const values = selection[field];
    update({
      ...selection,
      [field]: values.includes(key) ? values.filter((item) => item !== key) : [...values, key],
    });
  };
  const value: WorkspaceSelectionValue = {
    ...selection,
    toggleAsset: (key) => {
      const isSelected = selection.assetSecurityIds.includes(key);
      const nextInputs = { ...selection.assetDataInputs };
      if (isSelected) delete nextInputs[key];
      else nextInputs[key] = ["canonical_market_bars"];
      update({
        ...selection,
        assetSecurityIds: isSelected
          ? selection.assetSecurityIds.filter((item) => item !== key)
          : [...selection.assetSecurityIds, key],
        assetDataInputs: nextInputs,
      });
    },
    setAssets: (keys, selected) => {
      const nextIds = selected
        ? [...new Set([...selection.assetSecurityIds, ...keys])]
        : selection.assetSecurityIds.filter((item) => !keys.includes(item));
      const nextInputs = { ...selection.assetDataInputs };
      keys.forEach((key) => {
        if (selected) nextInputs[key] ??= ["canonical_market_bars"];
        else delete nextInputs[key];
      });
      update({ ...selection, assetSecurityIds: nextIds, assetDataInputs: nextInputs });
    },
    toggleAssetInput: (securityId, inputKey) => {
      const selectedInputs = selection.assetDataInputs[securityId] ?? [];
      const nextInputs = {
        ...selection.assetDataInputs,
        [securityId]: selectedInputs.includes(inputKey)
          ? selectedInputs.filter((item) => item !== inputKey)
          : [...selectedInputs, inputKey],
      };
      update({
        ...selection,
        assetSecurityIds: selection.assetSecurityIds.includes(securityId)
          ? selection.assetSecurityIds
          : [...selection.assetSecurityIds, securityId],
        assetDataInputs: nextInputs,
      });
    },
    toggleFactor: (key) => toggle("factorVariantKeys", key),
    toggleSignal: (key) => toggle("signalVersionKeys", key),
    toggleModel: (key) => toggle("modelPresetKeys", key),
    toggleModelTarget: (key) => toggle("modelTargetKeys", key),
    toggleStrategy: (key) => toggle("strategyPresetKeys", key),
    setFrequency: (frequency) => update({ ...selection, frequency }),
    replace: update,
    clear: () => update(EMPTY),
    draftRevision: revision,
    draftReady,
    draftMissing: draft.error instanceof ApiClientError && draft.error.status === 404,
    draftSaving: saveDraft.isPending,
    draftDirty: dirty,
    draftError: saveDraft.error ?? (
      draft.error instanceof ApiClientError && draft.error.status === 404 ? null : draft.error
    ),
    draftConflict: conflict,
    saveNow: saveLatest,
    reloadFromServer,
  };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useWorkspaceSelection(): WorkspaceSelectionValue {
  const value = useContext(Context);
  if (!value) throw new Error("useWorkspaceSelection requires WorkspaceSelectionProvider");
  return value;
}

function readSelection(): { selection: WorkspaceSelection; persisted: boolean; revision: number | null } {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return { selection: EMPTY, persisted: false, revision: null };
    const decoded = JSON.parse(raw) as Record<string, unknown>;
    const parsed = (decoded.selection && typeof decoded.selection === "object" ? decoded.selection : decoded) as Partial<WorkspaceSelection>;
    const assetSecurityIds = parsed.assetSecurityIds ?? [];
    return { persisted: true, revision: typeof decoded.revision === "number" ? decoded.revision : null, selection: {
      assetSecurityIds,
      assetDataInputs: normalizeAssetDataInputs(assetSecurityIds, parsed.assetDataInputs),
      factorVariantKeys: parsed.factorVariantKeys ?? [],
      signalVersionKeys: parsed.signalVersionKeys ?? [],
      modelPresetKeys: parsed.modelPresetKeys ?? [],
      modelTargetKeys: parsed.modelTargetKeys ?? ["cross_sectional_relative_return__h5"],
      strategyPresetKeys: parsed.strategyPresetKeys ?? [],
      frequency: parsed.frequency === "monthly" ? "monthly" : "weekly",
    } };
  } catch {
    return { selection: EMPTY, persisted: false, revision: null };
  }
}

function fromApiSelection(selection: {
  frequency: WorkspaceFrequency;
  asset_security_ids: string[];
  asset_data_inputs?: Record<string, string[]>;
  factor_variant_keys: string[];
  signal_version_keys: string[];
  model_preset_keys: string[];
  model_target_keys?: string[];
  strategy_preset_keys: string[];
}): WorkspaceSelection {
  return {
    frequency: selection.frequency,
    assetSecurityIds: selection.asset_security_ids,
    assetDataInputs: normalizeAssetDataInputs(
      selection.asset_security_ids,
      selection.asset_data_inputs,
    ),
    factorVariantKeys: selection.factor_variant_keys,
    signalVersionKeys: selection.signal_version_keys,
    modelPresetKeys: selection.model_preset_keys,
    modelTargetKeys: selection.model_target_keys ?? ["cross_sectional_relative_return__h5"],
    strategyPresetKeys: selection.strategy_preset_keys,
  };
}

function fingerprint(selection: WorkspaceSelection): string {
  return JSON.stringify({
    assetSecurityIds: selection.assetSecurityIds,
    assetDataInputs: selection.assetDataInputs,
    factorVariantKeys: selection.factorVariantKeys,
    signalVersionKeys: selection.signalVersionKeys,
    modelPresetKeys: selection.modelPresetKeys,
    modelTargetKeys: selection.modelTargetKeys,
    strategyPresetKeys: selection.strategyPresetKeys,
    frequency: selection.frequency,
  });
}

function normalizeAssetDataInputs(
  assetSecurityIds: string[],
  inputs: Record<string, string[]> | undefined,
): Record<string, string[]> {
  return Object.fromEntries(assetSecurityIds.map((securityId) => [
    securityId,
    inputs === undefined ? ["canonical_market_bars"] : [...new Set(inputs[securityId] ?? [])],
  ]));
}
