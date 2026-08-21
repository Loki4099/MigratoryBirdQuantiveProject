import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import "./V022IdentityPanel.css";

type IdentityKind = "experiment" | "product";

const record = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};

const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];

const text = (value: unknown, fallback = "—"): string =>
  typeof value === "string" && value.length > 0 ? value : fallback;

const shortId = (value: string): string => value.length > 13 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;

function ConfigurationIdentity({ configuration, display, chinese }: {
  configuration: Record<string, unknown>;
  display: Record<string, unknown>;
  chinese: boolean;
}) {
  const aggregation = record(configuration.aggregation);
  const aggregationDisplay = record(display.aggregation);
  const strategy = record(configuration.strategy);
  const strategyDisplay = record(display.strategy);
  const defense = configuration.defense === null ? null : record(configuration.defense);
  const defenseDisplay = record(display.defense);
  const semanticInputs = list(configuration.direct_inputs).map(record);
  const displayInputs = list(display.direct_inputs).map(record);

  return <div className="v022-identity-configuration">
    <article>
      <span>{chinese ? "聚合器" : "Aggregator"}</span>
      <strong>{text(aggregationDisplay.name, text(aggregation.family_key))}</strong>
      <code>{text(aggregation.family_key)} · {text(aggregation.execution_mode)}</code>
      {typeof aggregation.target_version_id === "string" && <small>Target {shortId(aggregation.target_version_id)}</small>}
      {typeof aggregation.training_preset_version_id === "string" && <small>Training {shortId(aggregation.training_preset_version_id)}</small>}
    </article>
    <article>
      <span>{chinese ? "横截面策略" : "Cross-sectional strategy"}</span>
      <strong>{text(strategyDisplay.name, text(strategy.variant_key))}</strong>
      <code>{text(strategy.family_key)} · {text(strategy.variant_key)}</code>
    </article>
    <article>
      <span>{chinese ? "防御性择时" : "Defensive timing"}</span>
      <strong>{defense === null ? (chinese ? "不启用防御" : "No defense") : text(defenseDisplay.name, text(defense.variant_key))}</strong>
      <code>{defense === null ? "none" : `${text(defense.family_key)} · ${text(defense.variant_key)}`}</code>
    </article>
    <article className="v022-direct-inputs">
      <span>{chinese ? `进入聚合的信号（${semanticInputs.length}）` : `Direct signals (${semanticInputs.length})`}</span>
      <div className="v022-chip-list">
        {semanticInputs.map((input, index) => {
          const label = displayInputs[index] ?? {};
          const key = text(input.variant_key, `input-${index + 1}`);
          return <span key={`${key}-${index}`} title={`${text(input.family_key)} / ${key}`}>
            {text(label.name, key)}
            <small>{key}</small>
          </span>;
        })}
        {semanticInputs.length === 0 && <em>{chinese ? "没有直连信号" : "No direct signals"}</em>}
      </div>
    </article>
  </div>;
}

function ExperimentIdentityPanel({ chinese }: { chinese: boolean }) {
  const catalog = useQuery({ queryKey: ["v022", "experiments"], queryFn: api.v022Experiments });
  const [selectedId, setSelectedId] = useState("");
  const items = catalog.data?.items ?? [];

  const activeId = selectedId || items[0]?.result_evidence_snapshot_id || "";

  const detail = useQuery({
    queryKey: ["v022", "experiment", activeId],
    queryFn: () => api.v022Experiment(activeId),
    enabled: Boolean(activeId),
  });
  const selected = items.find((item) => item.result_evidence_snapshot_id === activeId) ?? items[0];

  return <IdentityShell
    chinese={chinese}
    eyebrow="v0.22 / RESULT EVIDENCE / FROZEN CONFIGURATION"
    title={chinese ? "实验冻结身份" : "Frozen experiment identity"}
    description={chinese ? "直接展示最终使用的聚合器、进入聚合的有序信号、策略与防御配置。" : "The exact aggregator, ordered direct signals, strategy, and defense configuration used by a result."}
    loading={catalog.isLoading}
    error={catalog.error ?? detail.error}
    empty={!catalog.isLoading && !catalog.error && items.length === 0}
  >
    {selected && <>
      <IdentitySelector
        label={chinese ? "结果证据" : "Result evidence"}
        value={activeId}
        onChange={setSelectedId}
        options={items.map((item) => ({
          id: item.result_evidence_snapshot_id,
          label: `${item.evidence_class} · ${shortId(item.result_artifact_id)}`,
        }))}
      />
      <ConfigurationIdentity configuration={selected.configuration} display={selected.display} chinese={chinese} />
      <div className="v022-identity-facts">
        <span>{chinese ? "证据类别" : "Evidence class"}<strong>{selected.evidence_class}</strong></span>
        <span>{chinese ? "配置指纹" : "Configuration fingerprint"}<code>{shortId(selected.configuration_fingerprint)}</code></span>
        <span>{chinese ? "比较关系" : "Comparisons"}<strong>{detail.data?.comparisons.length ?? "—"}</strong></span>
        <span>{chinese ? "匹配基线" : "Matched baselines"}<strong>{detail.data?.matched_baselines.length ?? "—"}</strong></span>
      </div>
    </>}
  </IdentityShell>;
}

function ProductIdentityPanel({ chinese }: { chinese: boolean }) {
  const catalog = useQuery({ queryKey: ["v022", "products"], queryFn: api.v022Products });
  const [selectedId, setSelectedId] = useState("");
  const items = catalog.data?.items ?? [];

  const activeId = selectedId || items[0]?.product_enrollment_id || "";

  const detail = useQuery({
    queryKey: ["v022", "product", activeId],
    queryFn: () => api.v022Product(activeId),
    enabled: Boolean(activeId),
  });
  const selected = items.find((item) => item.product_enrollment_id === activeId) ?? items[0];
  const latestDecision = record(detail.data?.latest_decision);

  return <IdentityShell
    chinese={chinese}
    eyebrow="v0.22 / PRODUCT EXECUTION / OOS IDENTITY"
    title={chinese ? "Product 连续运行身份" : "Product continuous identity"}
    description={chinese ? "冻结配置与执行版本分离展示，并锚定激活后的样本外监控。" : "Frozen configuration and execution versions, anchored to prospective post-activation monitoring."}
    loading={catalog.isLoading}
    error={catalog.error ?? detail.error}
    empty={!catalog.isLoading && !catalog.error && items.length === 0}
  >
    {selected && <>
      <p className="v022-identity-state warning">
        {chinese
          ? "数据口径提示：v0.22 研究与 Product 使用后复权历史行情；请结合激活后的真实样本外监控判断表现。"
          : "Data-basis notice: v0.22 research and Products use back-adjusted historical prices; interpret them together with prospective post-activation monitoring."}
      </p>
      <IdentitySelector
        label="Product"
        value={activeId}
        onChange={setSelectedId}
        options={items.map((item) => ({
          id: item.product_enrollment_id,
          label: `${item.name} · execution v${item.execution_version_number}`,
        }))}
      />
      <ConfigurationIdentity configuration={selected.configuration} display={selected.display} chinese={chinese} />
      <div className="v022-identity-facts">
        <span>{chinese ? "生命周期" : "Lifecycle"}<strong>{selected.lifecycle}</strong></span>
        <span>{chinese ? "健康状态" : "Health"}<strong>{selected.health}</strong></span>
        <span>{chinese ? "执行版本" : "Execution version"}<strong>v{selected.execution_version_number}</strong></span>
        <span>{chinese ? "监控快照" : "Monitoring snapshots"}<strong>{detail.data?.monitoring_snapshots.length ?? "—"}</strong></span>
        <span>{chinese ? "OOS 决策" : "OOS decisions"}<strong>{detail.data?.decisions.length ?? "—"}</strong></span>
        <span>{chinese ? "最近决策" : "Latest decision"}<strong>{text(latestDecision.decision_status, chinese ? "尚无决策" : "No decision")}</strong></span>
        <span>OOS anchor<code>{new Date(selected.oos_anchor_cutoff_at).toLocaleDateString()}</code></span>
      </div>
    </>}
  </IdentityShell>;
}

function IdentitySelector({ label, value, onChange, options }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: { id: string; label: string }[];
}) {
  return <label className="v022-identity-selector">
    <span>{label}</span>
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
    </select>
  </label>;
}

function IdentityShell({ chinese, eyebrow, title, description, loading, error, empty, children }: {
  chinese: boolean;
  eyebrow: string;
  title: string;
  description: string;
  loading: boolean;
  error: Error | null;
  empty: boolean;
  children: React.ReactNode;
}) {
  return <section className="v022-identity-panel" aria-label={title}>
    <header>
      <div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2><p>{description}</p></div>
      <span className="v022-contract-badge">READ ONLY · v0.22</span>
    </header>
    {loading && <p className="v022-identity-state">{chinese ? "正在读取冻结身份…" : "Loading frozen identity…"}</p>}
    {error && <p className="v022-identity-state error">{chinese ? "暂时无法读取 v0.22 身份。旧版页面功能不受影响。" : "The v0.22 identity is temporarily unavailable. Existing page functions remain available."}</p>}
    {empty && <p className="v022-identity-state">{chinese ? "尚未发布 v0.22 冻结身份。" : "No v0.22 frozen identity has been published yet."}</p>}
    {!loading && !error && !empty && children}
  </section>;
}

export function V022IdentityPanel({ kind }: { kind: IdentityKind }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  return kind === "experiment"
    ? <ExperimentIdentityPanel chinese={chinese} />
    : <ProductIdentityPanel chinese={chinese} />;
}
