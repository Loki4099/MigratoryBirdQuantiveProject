import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { researchLabel } from "../components/ResearchText";

const ratio = (value: unknown) => typeof value === "number"
  ? new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value)
  : "—";
const decimal = (value: unknown) => typeof value === "number"
  ? new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value)
  : "—";
const percentageMetrics = new Set([
  "cumulative_return", "cagr", "annualized_volatility", "maximum_drawdown",
  "positive_daily_return_ratio", "best_daily_return", "worst_daily_return",
  "positive_monthly_return_ratio", "best_monthly_return", "worst_monthly_return",
  "cumulative_relative_return", "annualized_relative_wealth_growth", "cagr_spread",
  "tracking_error", "annualized_alpha",
]);
const metricValue = (metric: { metric_key: string; value: number | null; value_status: string; reason_code: string | null }) =>
  metric.value_status !== "defined" ? metric.reason_code ?? "—" :
    percentageMetrics.has(metric.metric_key) ? ratio(metric.value) : decimal(metric.value);

function CandidateEgg({ compact = false }: { compact?: boolean }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  return <div className={`candidate-egg ${compact ? "compact" : ""}`} aria-label={chinese ? "孵化中的研究候选" : "Incubating research candidate"}>
    <span className="egg-eye left" /><span className="egg-eye right" />
    <span className="egg-beak" /><span className="egg-smile" />
  </div>;
}

export function ProductsPage() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const { enrollmentId } = useParams();
  const catalog = useQuery({ queryKey: ["products"], queryFn: api.products });
  const detail = useQuery({
    queryKey: ["products", enrollmentId],
    queryFn: () => api.productDetail(enrollmentId!),
    enabled: Boolean(enrollmentId),
  });
  const [search, setSearch] = useState("");
  const [lifecycle, setLifecycle] = useState("all");
  const [health, setHealth] = useState("all");
  const items = useMemo(() => (catalog.data?.items ?? []).filter((item) => {
    const text = `${item.name} ${item.model_preset_key} ${item.strategy_family_key} ${item.strategy_preset_key} ${item.asset_context_key}`.toLowerCase();
    return (!search.trim() || text.includes(search.trim().toLowerCase()))
      && (lifecycle === "all" || item.lifecycle === lifecycle)
      && (health === "all" || item.health === health);
  }), [catalog.data, search, lifecycle, health]);

  if (catalog.isLoading || (enrollmentId && detail.isLoading)) return <LoadingState />;
  const error = catalog.error ?? detail.error;
  if (error) return <ErrorState error={error} retry={() => void (enrollmentId ? detail.refetch() : catalog.refetch())} />;
  if (enrollmentId && detail.data) {
    return <ProductDetail data={detail.data} onChanged={() => void Promise.all([detail.refetch(), catalog.refetch()])} chinese={chinese} />;
  }
  if (!catalog.data) return <EmptyState />;

  return <div className="page products-page">
    <header className="page-heading">
      <div><p className="eyebrow">PRODUCT / POST-ACTIVATION OOS</p><h1>{chinese ? "研究候选产品" : "Research Candidates"}</h1><p>{chinese ? "持续观察从实验中手动升级的候选；它们尚未经过实盘验证。" : "Continuously observe candidates manually promoted from Experiments. They are not live-validated Products."}</p></div>
      <QualityBadge state={catalog.data.quality.state} />
    </header>
    <section className="scope-strip">
      <div><span>{chinese ? "身份" : "Identity"}</span><strong>Research Candidate</strong></div>
      <div><span>{chinese ? "总数" : "Total"}</span><strong>{catalog.data.items.length}</strong></div>
      <div><span>Active</span><strong>{catalog.data.items.filter((item) => item.lifecycle === "active").length}</strong></div>
      <div><span>Alerts</span><strong>{catalog.data.items.reduce((sum, item) => sum + item.open_alert_count, 0)}</strong></div>
    </section>
    <section className="catalog-section product-toolbar">
      <label>{chinese ? "搜索" : "Search"}<input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder={chinese ? "名称、模型、策略、Universe" : "Name, Model, Strategy, Universe"} /></label>
      <label>Lifecycle<select value={lifecycle} onChange={(event) => setLifecycle(event.target.value)}>{["all", "active", "suspended", "retired", "invalidated"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>Health<select value={health} onChange={(event) => setHealth(event.target.value)}>{["all", "observing", "healthy", "watch", "warning", "data_interrupted"].map((value) => <option key={value}>{value}</option>)}</select></label>
    </section>
    {items.length === 0 ? <section className="workspace-default-card product-empty-card">
      <div><p className="eyebrow">NO ENROLLMENTS</p><h2>{chinese ? "还没有研究候选" : "No Research Candidate yet"}</h2><p>{chinese ? "从实验详情中手动选择值得进入样本外观察的结果。" : "Manually promote a result worth observing out of sample from Experiment details."}</p></div>
      <CandidateEgg /><Link className="arrow-link" to="/experiments">{chinese ? "打开实验" : "Open Experiments"} →</Link>
    </section> : <section className="product-card-grid">{items.map((item) => {
      const warnings = item.warning_codes ?? [];
      return <Link className="product-candidate-card" to={`/products/${item.enrollment_id}`} key={item.enrollment_id}>
        <div><span>{chinese ? "孵化中" : "Incubating"} · Research Candidate · v{item.version_number}</span><QualityBadge state={item.health === "healthy" ? "ok" : "partial"} /></div>
        <CandidateEgg compact /><h2>{item.name}</h2><p>{item.strategy_family_key} · {item.model_preset_key}</p>
        {warnings.length > 0 && <p className="candidate-warning-count">{warnings.length} {chinese ? "项研究限制待补全" : "research limitations remain"}</p>}
        <dl>
          <div><dt>Lifecycle / Health</dt><dd>{item.lifecycle} / {item.health}</dd></div>
          <div><dt>OOS start</dt><dd>{item.monitoring_start_at ?? "awaiting_first_decision"}</dd></div>
          <div><dt>Latest data</dt><dd>{item.latest_as_of_session ?? "—"}</dd></div>
          <div><dt>Primary OOS</dt><dd>{ratio(item.latest_metrics.cumulative_return)}</dd></div>
          <div><dt>Excess Wealth</dt><dd>{ratio(item.latest_metrics.excess_wealth_return)}</dd></div>
          <div><dt>Alerts</dt><dd>{item.open_alert_count}</dd></div>
        </dl>
      </Link>;
    })}</section>}
  </div>;
}

type Detail = Awaited<ReturnType<typeof api.productDetail>>;

function ProductDetail({ data, onChanged, chinese }: { data: Detail; onChanged: () => void; chinese: boolean }) {
  const [tab, setTab] = useState("overview");
  const [target, setTarget] = useState<"active" | "suspended" | "retired" | "">("");
  const [reason, setReason] = useState("");
  const [effectiveAt, setEffectiveAt] = useState("");
  const [reviewDecision, setReviewDecision] = useState<"continue" | "suspend" | "retire" | "replace">("continue");
  const [reviewReason, setReviewReason] = useState("");
  const lifecycleDialogRef = useRef<HTMLElement>(null);
  const lastAlertCommand = useRef<{ id: string; target: "acknowledged" | "resolved" } | null>(null);
  const recommendation = useQuery({
    queryKey: ["products", data.candidate.enrollment_id, "recommendation"],
    queryFn: () => api.productRecommendation(String(data.candidate.enrollment_id)),
    enabled: tab === "decisions",
    refetchInterval: tab === "decisions" ? 60_000 : false,
    staleTime: 30_000,
  });
  const lifecycle = useMutation({
    mutationFn: () => api.changeProductLifecycle(String(data.candidate.enrollment_id), {
      target: target || "suspended", expectedRevision: data.candidate.revision,
      reason, effectiveAt: new Date(effectiveAt).toISOString(),
    }),
    onSuccess: () => { setTarget(""); onChanged(); },
  });
  const alert = useMutation({
    mutationFn: (input: { id: string; target: "acknowledged" | "resolved" }) => {
      lastAlertCommand.current = input;
      return api.changeProductAlert(input.id, input.target);
    },
    onSuccess: onChanged,
  });
  const review = useMutation({
    mutationFn: () => api.recordProductReview(String(data.candidate.enrollment_id), { decision: reviewDecision, reason: reviewReason }),
    onSuccess: () => { setReviewReason(""); onChanged(); },
  });
  const tabs = [
    ["overview", chinese ? "总览" : "Overview"], ["backtest", chinese ? "冻结回测" : "Frozen backtest"], ["configuration", chinese ? "研究配置" : "Research configuration"],
    ["oos", chinese ? "样本外" : "OOS"], ["decisions", chinese ? "持仓决策" : "Holding decisions"], ["health", chinese ? "健康" : "Health"],
    ["qualification", chinese ? "资格" : "Qualification"], ["lineage", chinese ? "血缘" : "Lineage"],
  ];
  const warnings = data.candidate.warning_codes ?? [];
  useEffect(() => {
    if (!target) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    lifecycleDialogRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setTarget("");
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previous?.focus();
    };
  }, [target]);

  return <div className="page products-page">
    <Link className="arrow-link" to="/products">← Products</Link>
    <header className="page-heading product-detail-heading">
      <div><p className="eyebrow">{chinese ? "孵化中" : "INCUBATING"} / RESEARCH CANDIDATE / {data.candidate.lifecycle}</p><h1>{data.candidate.name}</h1><p>{chinese ? "未验证、非实盘就绪、非客户建议；冻结后桥接与激活后真实样本外分段记录。" : "Unvalidated, not live-ready, and not client advice. The post-freeze bridge and prospective OOS periods are recorded separately."}</p></div>
      <CandidateEgg /><QualityBadge state={data.candidate.health === "healthy" ? "ok" : "partial"} />
    </header>
    {warnings.length > 0 && <section className="candidate-warning-panel">
      <strong>{chinese ? "尚未补齐的正式部署证据" : "Formal deployment evidence still missing"}</strong>
      <ul>{warnings.map((code) => <li key={code}>{researchLabel(code, chinese ? "zh-CN" : "en")}</li>)}</ul>
      <p>{chinese ? "这些限制不阻止样本外观察，但禁止把候选描述为 PIT 无偏、100M 可部署或成本已校准。" : "These limitations do not block OOS observation, but the candidate must not be described as PIT-unbiased, USD 100M deployable, or impact-calibrated."}</p>
    </section>}
    {lifecycle.error && <ErrorState error={lifecycle.error} retry={() => lifecycle.mutate()} />}
    {alert.error && <ErrorState error={alert.error} retry={() => { if (lastAlertCommand.current) alert.mutate(lastAlertCommand.current); }} />}
    {review.error && <ErrorState error={review.error} retry={() => review.mutate()} />}
    <nav className="product-tabs" aria-label="Product detail sections">{tabs.map(([key, label]) => <button type="button" className={tab === key ? "active" : ""} onClick={() => setTab(key)} key={key}>{label}</button>)}</nav>
    {tab === "overview" && <section className="catalog-section">
      <div className="scope-strip"><div><span>Strategy</span><strong>{data.candidate.strategy_preset_key}</strong></div><div><span>Model</span><strong>{data.candidate.model_preset_key}</strong></div><div><span>Lifecycle</span><strong>{data.candidate.lifecycle} · r{data.candidate.revision}</strong></div><div><span>Health</span><strong>{data.candidate.health}</strong></div></div>
      <QualificationSummary backtest={data.qualification_backtest} />
      <ResearchChain data={data} compact />
      <h3>{chinese ? "选择理由" : "Selection reason"}</h3><p>{data.selection_reason}</p>
      {data.candidate.lifecycle === "active" && <button type="button" onClick={() => setTarget("suspended")}>{chinese ? "暂停" : "Suspend"}</button>}
      {data.candidate.lifecycle === "suspended" && <button type="button" onClick={() => setTarget("active")}>{chinese ? "恢复" : "Resume"}</button>}
      {["active", "suspended"].includes(data.candidate.lifecycle) && <button type="button" onClick={() => setTarget("retired")}>{chinese ? "退役" : "Retire"}</button>}
    </section>}
    {tab === "backtest" && <QualificationBacktest backtest={data.qualification_backtest} />}
    {tab === "configuration" && <ResearchChain data={data} />}
    {tab === "oos" && <ProductOos data={data} />}
    {tab === "decisions" && <ProductDecisions snapshots={data.snapshots} recommendation={recommendation.data} loading={recommendation.isLoading} error={recommendation.error} refresh={() => void recommendation.refetch()} />}
    {tab === "health" && <section className="catalog-section">
      <h2>{chinese ? "健康与告警" : "Health & Alerts"}</h2>
      {data.alerts.map((item) => <article className="product-alert" key={item.alert_id}><strong>{item.severity} · {item.alert_type}</strong><span>{item.status}</span><p>{JSON.stringify(item.evidence)}</p>{item.status === "open" && <button type="button" onClick={() => alert.mutate({ id: String(item.alert_id), target: "acknowledged" })}>Acknowledge</button>}{["open", "acknowledged"].includes(item.status) && <button type="button" onClick={() => alert.mutate({ id: String(item.alert_id), target: "resolved" })}>Resolve</button>}</article>)}
      <h3>{chinese ? "健康时间线" : "Health timeline"}</h3>{data.snapshots.map((snapshot) => <p key={snapshot.artifact_id}>{snapshot.as_of_session} · {snapshot.health} · {JSON.stringify(snapshot.health_components.reason_codes ?? [])}</p>)}
      <h3>{chinese ? "月度复核" : "Monthly Review"}</h3><div className="product-toolbar"><select value={reviewDecision} onChange={(event) => setReviewDecision(event.target.value as typeof reviewDecision)}>{["continue", "suspend", "retire", "replace"].map((item) => <option key={item}>{item}</option>)}</select><textarea value={reviewReason} onChange={(event) => setReviewReason(event.target.value)} placeholder={chinese ? "复核理由" : "Review reason"} /><button type="button" disabled={!reviewReason.trim() || review.isPending} onClick={() => review.mutate()}>{chinese ? "记录只追加复核" : "Record append-only review"}</button></div>
      {data.reviews.map((item) => <p key={item.product_review_id}>{item.reviewed_at} · {item.decision} · {item.reason}</p>)}
    </section>}
    {tab === "qualification" && <section className="catalog-section"><h2>Frozen Qualification Backtest</h2><p>{chinese ? "资格历史与激活后的 OOS 永久分离。" : "Qualification history remains permanently separate from post-activation OOS evidence."}</p><pre>{JSON.stringify(data.qualification_gate_results, null, 2)}</pre></section>}
    {tab === "lineage" && <section className="catalog-section"><h2>{chinese ? "血缘与导出" : "Lineage & Exports"}</h2><Link className="arrow-link" to={`/artifacts/${data.candidate.product_artifact_id}`}>{chinese ? "Product 版本" : "Product Version"} →</Link><Link className="arrow-link" to={`/artifacts/${data.candidate.qualification_artifact_id}`}>{chinese ? "资格结果包" : "Qualification Bundle"} →</Link>{data.research_chain && <><Link className="arrow-link" to={`/artifacts/${data.research_chain.source_suite_artifact_id}`}>{chinese ? "来源研究套件" : "Source Research Suite"} →</Link><Link className="arrow-link" to={`/experiments?result=${data.research_chain.selected_result_artifact_id}`}>{chinese ? "所选实验结果" : "Selected Experiment Result"} →</Link></>}</section>}
    {target && <div className="promotion-modal-backdrop" onMouseDown={() => setTarget("")}><section className="promotion-modal" ref={lifecycleDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={`Change Product lifecycle to ${target}`} onMouseDown={(event) => event.stopPropagation()}><h2>{target}</h2><label>{chinese ? "理由" : "Reason"}<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label><label>{chinese ? "生效时间" : "Effective time"}<input type="datetime-local" value={effectiveAt} onChange={(event) => setEffectiveAt(event.target.value)} /></label><footer><button type="button" onClick={() => setTarget("")}>{chinese ? "取消" : "Cancel"}</button><button type="button" disabled={!reason.trim() || !effectiveAt || lifecycle.isPending} onClick={() => lifecycle.mutate()}>{chinese ? "确认" : "Confirm"}</button></footer></section></div>}
  </div>;
}

type Backtest = NonNullable<Detail["qualification_backtest"]>;
type BacktestNavField = "strategy_wealth" | "benchmark_wealth" | "excess_wealth" | "drawdown";

function findMetric(backtest: Backtest | null | undefined, role: string, key: string) {
  return backtest?.metrics.find((item) => item.series_role === role && item.metric_key === key);
}

function QualificationSummary({ backtest }: { backtest: Backtest | null | undefined }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  if (!backtest) return <div className="product-archive-empty"><strong>{chinese ? "冻结回测档案尚不可读" : "Frozen backtest archive is unavailable"}</strong><p>{chinese ? "Product仍可继续样本外观察，但需要修复资格结果血缘。" : "OOS observation can continue, but qualification lineage must be repaired."}</p></div>;
  const items = [
    [chinese ? "策略 CAGR" : "Strategy CAGR", findMetric(backtest, "strategy", "cagr")],
    ["SPY CAGR", findMetric(backtest, "benchmark", "cagr")],
    [chinese ? "净 Sharpe" : "Net Sharpe", findMetric(backtest, "strategy", "sharpe_ratio")],
    [chinese ? "最大回撤" : "Maximum drawdown", findMetric(backtest, "strategy", "maximum_drawdown")],
    [chinese ? "相对财富增长" : "Relative wealth growth", findMetric(backtest, "relative", "annualized_relative_wealth_growth")],
    [chinese ? "跟踪误差" : "Tracking error", findMetric(backtest, "relative", "tracking_error")],
  ] as const;
  return <section className="product-qualification-summary">
    <div className="section-heading"><div><p className="eyebrow">FROZEN QUALIFICATION BACKTEST</p><h2>{chinese ? "升级时完整回测快照" : "Full backtest snapshot at promotion"}</h2></div><span>{backtest.resolved_start ?? "—"} → {backtest.resolved_end ?? "—"}</span></div>
    <div className="product-performance-strip">{items.map(([label, metric]) => <div key={label}><span>{label}</span><strong>{metric ? metricValue(metric) : "—"}</strong></div>)}</div>
  </section>;
}

function QualificationBacktest({ backtest }: { backtest: Backtest | null | undefined }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  if (!backtest) return <section className="catalog-section"><h2>{chinese ? "冻结回测档案不可用" : "Frozen backtest archive unavailable"}</h2></section>;
  return <section className="catalog-section product-backtest-archive">
    <div className="section-heading"><div><p className="eyebrow">IMMUTABLE QUALIFICATION RESULT</p><h2>{chinese ? "升级时冻结的完整回测表现" : "Full backtest performance frozen at promotion"}</h2><p>{chinese ? "该历史只用于解释升级决策，与激活后的样本外表现严格分离。" : "This history only explains the promotion decision and remains strictly separate from post-activation OOS performance."}</p></div><Link className="arrow-link" to={`/experiments?result=${backtest.result_artifact_id}`}>{chinese ? "打开原实验" : "Open source Experiment"} →</Link></div>
    <div className="scope-strip"><div><span>{chinese ? "有效区间" : "Resolved interval"}</span><strong>{backtest.resolved_start ?? "—"} → {backtest.resolved_end ?? "—"}</strong></div><div><span>{chinese ? "观测数" : "Observations"}</span><strong>{backtest.observation_count}</strong></div><div><span>{chinese ? "回测窗口" : "Backtest window"}</span><strong>{String(backtest.specification.template_key ?? "—")}</strong></div><div><span>{chinese ? "市场假设" : "Market assumptions"}</span><strong>{String(backtest.specification.frequency ?? "—")} · {String(backtest.specification.cost_bps_per_side ?? "—")} bps/side</strong></div></div>
    <div className="experiment-chart-grid"><BacktestChart title={chinese ? "净值曲线 vs SPY" : "Net wealth vs SPY"} series={backtest.nav_series} fields={["strategy_wealth", "benchmark_wealth"]} /><BacktestChart title={chinese ? "超额净值" : "Excess wealth"} series={backtest.nav_series} fields={["excess_wealth"]} /><BacktestChart title={chinese ? "回撤" : "Drawdown"} series={backtest.nav_series} fields={["drawdown"]} /></div>
    <h3>{chinese ? "完整绩效指标" : "Complete performance metrics"}</h3><div className="experiment-metrics">{backtest.metrics.map((metric) => <div key={`${metric.series_role}-${metric.metric_key}`}><span>{researchLabel(metric.series_role, chinese ? "zh-CN" : "en")} · {researchLabel(metric.metric_key, chinese ? "zh-CN" : "en")}<code>{metric.metric_key}</code></span><strong>{metricValue(metric)}</strong></div>)}</div>
  </section>;
}

function BacktestChart({ title, series, fields }: { title: string; series: Backtest["nav_series"]; fields: BacktestNavField[] }) {
  const { i18n } = useTranslation();
  if (series.length < 2) return <article className="experiment-chart"><h3>{title}</h3><p>{i18n.resolvedLanguage === "en" ? "No published path is available." : "暂无已发布路径。"}</p></article>;
  const values = series.flatMap((point) => fields.map((field) => point[field]));
  const minimum = Math.min(...values); const maximum = Math.max(...values); const span = maximum - minimum || 1;
  const paths = fields.map((field) => series.map((point, index) => `${(index / (series.length - 1) * 100).toFixed(2)},${(42 - (point[field] - minimum) / span * 38).toFixed(2)}`).join(" "));
  return <article className="experiment-chart"><h3>{title}</h3><svg viewBox="0 0 100 46" role="img" aria-label={title}>{paths.map((path, index) => <polyline key={fields[index]} className={`series-${index}`} points={path} />)}</svg><footer><span>{series[0].nav_date}</span><span>{series.at(-1)?.nav_date}</span></footer></article>;
}

function ResearchChain({ data, compact = false }: { data: Detail; compact?: boolean }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const chain = data.research_chain;
  if (!chain) return <div className="product-archive-empty"><strong>{chinese ? "研究配置链尚不可读" : "Research configuration lineage is unavailable"}</strong></div>;
  const factorsEnteringSignals = chain.factor_variant_keys.filter((factor) => chain.signal_version_keys.some((signal) => signal.endsWith(`__${factor}`)));
  const factorOnlySelections = chain.factor_variant_keys.filter((factor) => !factorsEnteringSignals.includes(factor));
  const stages = [
    [chinese ? "策略" : "Strategy", [data.candidate.strategy_preset_key]], [chinese ? "模型" : "Model", chain.model_preset_keys],
    [chinese ? "预测 Target" : "Prediction Target", chain.model_target_keys], [chinese ? "信号" : "Signals", chain.signal_version_keys],
    [chinese ? "进入信号的因子" : "Factors entering Signals", factorsEnteringSignals],
  ] as const;
  return <section className={`product-research-chain ${compact ? "compact" : ""}`}>
    <div className="section-heading"><div><p className="eyebrow">ASSET → FACTOR → SIGNAL → MODEL → STRATEGY</p><h2>{chinese ? "冻结研究配置链" : "Frozen research configuration lineage"}</h2></div><span>{chain.frequency} · {chain.assets.length} {chinese ? "个资产" : "Assets"}</span></div>
    <div className="research-chain-grid">{stages.map(([label, values]) => <article key={label}><span>{label}</span><div className="research-chip-list">{values.map((value) => <code key={value}>{value}</code>)}</div></article>)}</div>
    {factorOnlySelections.length > 0 && <p className="product-chain-note"><strong>{chinese ? "仅停留在因子层：" : "Factor-only selections: "}</strong>{factorOnlySelections.map((value) => <code key={value}>{value}</code>)}{chinese ? "。这些因子没有产生本 Product 所选信号，因此没有进入模型。" : ". These Factors did not produce selected Product Signals and therefore did not enter the Model."}</p>}
    {!compact && <><h3>{chinese ? "资产 Universe" : "Asset Universe"} ({chain.assets.length})</h3><div className="research-asset-grid">{chain.assets.map((asset) => <article key={asset.security_id}><strong>{asset.symbol}</strong><span>{asset.name}</span><code>{asset.asset_key}</code></article>)}</div><h3>{chinese ? "身份与资格结果" : "Identity and qualification results"}</h3><dl className="product-chain-identities"><div><dt>{chinese ? "选中分支" : "Selected branch"}</dt><dd><code>{chain.selected_branch_key}</code></dd></div><div><dt>{chinese ? "资格六格结果" : "Six qualification results"}</dt><dd>{chain.qualification_result_artifact_ids.map((id) => <Link key={id} to={`/experiments?result=${id}`}>{id.slice(0, 8)}</Link>)}</dd></div></dl></>}
  </section>;
}

function ProductOos({ data }: { data: Detail }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const { oos_window: window, snapshots } = data;
  const ordered = [...snapshots].reverse();
  const values = ordered.reduce<Array<{ date: string; primary: number; stress: number; benchmark: number; excess: number; drawdown: number }>>((result, item) => {
    const benchmark = Number(item.metrics.benchmark_nav ?? 1);
    const priorPeak = result.length === 0 ? 1 : Math.max(...result.map((point) => point.primary));
    const peak = Math.max(priorPeak, item.primary_nav);
    return [...result, { date: item.as_of_session, primary: item.primary_nav, stress: item.stress_nav, benchmark, excess: item.primary_nav / benchmark, drawdown: item.primary_nav / peak - 1 }];
  }, window.frozen_anchor_session ? [{ date: window.frozen_anchor_session, primary: 1, stress: 1, benchmark: 1, excess: 1, drawdown: 0 }] : []);
  const latest = ordered.at(-1);
  const latestPrimary = latest?.primary_nav;
  const latestBenchmark = latest ? Number(latest.metrics.benchmark_nav ?? 1) : undefined;
  const latestExcess = latestPrimary !== undefined && latestBenchmark !== undefined ? latestPrimary / latestBenchmark - 1 : undefined;
  const reason = window.status === "awaiting_post_freeze_data"
    ? (chinese ? "本地已发布行情尚未越过冻结点。发布冻结日后的新 Data Bundle 后，监控会自动开始。" : "Published local market data has not passed the frozen anchor. Monitoring starts automatically after a newer Data Bundle is published.")
    : window.status === "awaiting_first_snapshot"
      ? (chinese ? "已有冻结点后的行情，首个监控快照仍在排队或计算中。" : "Post-freeze data is available; the first monitoring snapshot is queued or being calculated.")
      : window.status === "awaiting_frozen_anchor"
        ? (chinese ? "冻结回测终点不可读，暂时无法建立样本外起点。" : "The frozen backtest endpoint is unavailable, so an OOS anchor cannot yet be established.")
        : (chinese ? "按冻结策略持续更新；所有收益均使用当时已发布的数据与既定交易成本。" : "The frozen strategy is updated continuously using only data published at the time and the fixed cost assumptions.");
  const signalNames = data.research_chain?.signal_version_keys ?? [];
  const maturedDecisions = latest?.decision_count ?? 0;
  const healthText = maturedDecisions === 0
    ? (chinese ? "尚无成熟的样本外决策，不能判断因子或信号是否失效。" : "No matured OOS decision exists yet, so Factor or Signal decay cannot be assessed.")
    : (chinese ? `已形成 ${maturedDecisions} 次样本外决策；当前只显示组合表现与模型分数离散度，达到足够目标到期样本后再判断信号失效。` : `${maturedDecisions} OOS decisions have been formed. Portfolio performance and score dispersion are shown now; Signal decay is assessed only after enough targets mature.`);
  const fields = ["primary", "stress", "benchmark", "excess"] as const;
  if (values.length < 2) return <section className="catalog-section product-oos-section">
    <div className="section-heading"><div><p className="eyebrow">POST-FREEZE PERFORMANCE TRACKING</p><h2>{chinese ? "冻结后表现跟踪" : "Post-freeze performance tracking"}</h2></div><span>{window.status}</span></div>
    <div className="scope-strip"><div><span>{chinese ? "冻结起点" : "Frozen anchor"}</span><strong>{window.frozen_anchor_session ?? "—"}</strong></div><div><span>{chinese ? "最新本地数据" : "Latest local data"}</span><strong>{window.latest_published_data_session ?? "—"}</strong></div><div><span>{chinese ? "冻结后交易日" : "Post-freeze sessions"}</span><strong>{window.post_freeze_session_count}</strong></div><div><span>{chinese ? "激活后真实 OOS" : "Prospective OOS sessions"}</span><strong>{window.prospective_oos_session_count}</strong></div></div>
    <div className="product-archive-empty"><strong>{chinese ? "暂时没有可计算的冻结后收益" : "No post-freeze return can be calculated yet"}</strong><p>{reason}</p></div>
    <div className="candidate-warning-panel"><strong>{chinese ? "因子 / 信号失效监控" : "Factor / Signal decay monitoring"}</strong><p>{healthText}</p>{signalNames.length > 0 && <div className="research-chip-list">{signalNames.map((signal) => <code key={signal}>{signal}</code>)}</div>}</div>
  </section>;
  const all = values.flatMap((item) => fields.map((field) => item[field]));
  const low = Math.min(...all); const span = Math.max(...all) - low || 1;
  const paths = fields.map((field) => values.map((item, index) => `${index / (values.length - 1) * 100},${42 - (item[field] - low) / span * 38}`).join(" "));
  const drawdown = values.map((item, index) => `${index / (values.length - 1) * 100},${4 + Math.abs(item.drawdown) * 38}`).join(" ");
  return <section className="catalog-section product-oos-section"><div className="section-heading"><div><p className="eyebrow">POST-FREEZE PERFORMANCE TRACKING</p><h2>{chinese ? "冻结后表现跟踪" : "Post-freeze performance tracking"}</h2><p>{reason}</p></div><span>{values[0].date} → {values.at(-1)?.date}</span></div><div className="scope-strip"><div><span>{chinese ? "冻结起点" : "Frozen anchor"}</span><strong>{window.frozen_anchor_session ?? "—"}</strong></div><div><span>{chinese ? "最新监控数据" : "Observed through"}</span><strong>{window.latest_snapshot_session ?? "—"}</strong></div><div><span>{chinese ? "策略累计收益" : "Strategy cumulative return"}</span><strong>{latestPrimary === undefined ? "—" : ratio(latestPrimary - 1)}</strong></div><div><span>{chinese ? "相对 SPY 超额" : "Excess vs SPY"}</span><strong>{ratio(latestExcess)}</strong></div></div><p className="product-chain-note">{chinese ? `冻结点至激活日（${window.activation_session}）属于参数冻结后的桥接观察；激活后的 ${window.prospective_oos_session_count} 个交易日才属于真实前瞻 OOS。` : `The interval from the frozen anchor to activation (${window.activation_session}) is a parameter-frozen bridge; only the ${window.prospective_oos_session_count} sessions after activation are prospective OOS.`}</p><div className="product-oos-chart"><svg viewBox="0 0 100 46" role="img" aria-label={chinese ? "主路径、压力路径、SPY 与超额样本外净值" : "Primary, Stress, SPY and Excess OOS wealth"}>{paths.map((path, index) => <polyline className={`series-${index}`} key={fields[index]} points={path} />)}</svg></div><p>{chinese ? "主路径 · 压力路径 · SPY · 超额净值" : "Primary · Stress · SPY · Excess Wealth"}</p><div className="product-oos-chart"><svg viewBox="0 0 100 46" role="img" aria-label={chinese ? "样本外回撤" : "OOS drawdown"}><polyline points={drawdown} /></svg></div><div className="candidate-warning-panel"><strong>{chinese ? "因子 / 信号失效监控" : "Factor / Signal decay monitoring"}</strong><p>{healthText}</p>{signalNames.length > 0 && <div className="research-chip-list">{signalNames.map((signal) => <code key={signal}>{signal}</code>)}</div>}</div></section>;
}

type Recommendation = Awaited<ReturnType<typeof api.productRecommendation>>;

function ProductDecisions({ snapshots, recommendation, loading, error, refresh }: { snapshots: Detail["snapshots"]; recommendation: Recommendation | undefined; loading: boolean; error: Error | null; refresh: () => void }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const latest = snapshots[0];
  const holdings = Array.isArray(latest?.metrics.holdings) ? latest.metrics.holdings as Array<{ asset_key?: string; target_weight?: string | number }> : [];
  return <section className="catalog-section product-recommendation">
    <div className="section-heading"><div><p className="eyebrow">LATEST PUBLISHED SIGNAL DECISION</p><h2>{chinese ? "最新研究建议仓位" : "Latest research allocation recommendation"}</h2><p>{chinese ? "基于最近一次已经完成并发布的信号计算；这是研究建议，不是订单，也不计入样本外业绩。" : "Based on the latest completed and published signal calculation. This is a research recommendation, not an order, and is excluded from OOS performance."}</p></div><button type="button" onClick={refresh} disabled={loading}>{chinese ? "立即检查更新" : "Check for updates"}</button></div>
    {loading && !recommendation ? <p>{chinese ? "正在计算最新建议…" : "Computing the latest recommendation…"}</p> : error ? <div className="product-archive-empty"><strong>{chinese ? "当前建议暂不可用" : "Current recommendation is unavailable"}</strong><p>{error.message}</p><button type="button" onClick={refresh}>{chinese ? "重试" : "Retry"}</button></div> : recommendation && <>
      <div className="scope-strip"><div><span>{chinese ? "数据截至" : "Data through"}</span><strong>{recommendation.data_as_of_session}</strong></div><div><span>{chinese ? "信号决策日" : "Signal decision"}</span><strong>{recommendation.decision_session}</strong></div><div><span>{chinese ? "建议执行/持有起点" : "Recommended execution / holding start"}</span><strong>{recommendation.recommended_execution_session ?? (chinese ? "等待下一交易日" : "Awaiting next session")}</strong></div><div><span>{chinese ? "下一预计信号日" : "Next expected signal"}</span><strong>{recommendation.next_expected_signal_session ?? (chinese ? "等待新交易日历" : "Awaiting updated calendar")}</strong></div></div>
      <div className="recommendation-status"><span>{chinese ? "频率：" : "Frequency: "}<strong>{recommendation.frequency}</strong></span><span>{chinese ? "有效/可排名：" : "Eligible / rankable: "}<strong>{recommendation.eligible_count} / {recommendation.rankable_count}</strong></span><span>{chinese ? "覆盖率：" : "Coverage: "}<strong>{ratio(recommendation.coverage_ratio)}</strong></span><span>{chinese ? "数据发布时间：" : "Published at: "}<strong>{new Date(recommendation.data_known_at).toLocaleString()}</strong></span></div>
      {recommendation.available ? <div className="recommendation-table"><div className="recommendation-row head"><span>{chinese ? "排名" : "Rank"}</span><span>{chinese ? "资产" : "Asset"}</span><span>{chinese ? "模型分数" : "Model score"}</span><span>{chinese ? "角色" : "Role"}</span><span>{chinese ? "建议权重" : "Target weight"}</span></div>{recommendation.positions.map((item) => <article className="recommendation-row" key={`${item.allocation_role}-${item.asset_key}`}><code>{item.rank ?? "—"}</code><div><strong>{item.symbol}</strong><span>{item.name}</span><code>{item.asset_key}</code></div><code>{decimal(item.model_score)}</code><span>{item.allocation_role}{item.retained_by_buffer ? ` · ${chinese ? "缓冲保留" : "buffer retained"}` : ""}</span><strong>{ratio(item.target_weight)}</strong></article>)}</div> : <div className="product-archive-empty"><strong>{chinese ? "本期无法形成建议" : "No recommendation can be formed for this period"}</strong><p>{recommendation.reason_codes.map((code) => researchLabel(code, chinese ? "zh-CN" : "en")).join(" · ")}</p></div>}
      <p className="recommendation-refresh-note">{chinese ? "页面每60秒检查一次最新已发布数据。新的周频/月频/日频信号决策发布后，本表自动替换；在此之前维持上一份建议，不会按墙上时钟伪造新信号。" : "The page checks for newly published data every 60 seconds. A new weekly, monthly, or daily signal decision replaces this table after publication; until then, the last recommendation remains visible and no signal is fabricated from wall-clock time."}</p>
    </>}
    <details className="oos-holdings"><summary>{chinese ? "当前样本外实际持仓（与建议分离）" : "Current OOS holdings (separate from recommendations)"}</summary>{!latest ? <p>{chinese ? "尚未产生激活后的首个合法 OOS 决策。" : "No legal post-activation OOS decision has been produced yet."}</p> : <><div className="scope-strip"><div><span>{chinese ? "截至" : "As of"}</span><strong>{latest.as_of_session}</strong></div><div><span>{chinese ? "模型决策" : "Model decision"}</span><strong>{String(latest.metrics.model_decision_date ?? "held")}</strong></div><div><span>{chinese ? "决策次数" : "Decision count"}</span><strong>{String(latest.metrics.decision_count ?? latest.decision_count)}</strong></div><div><span>{chinese ? "暂停期维持持仓" : "Suspension hold"}</span><strong>{String(latest.health_components.held_during_suspension ?? false)}</strong></div></div>{holdings.map((item) => <p key={item.asset_key}>{item.asset_key} · {ratio(Number(item.target_weight))}</p>)}<h3>{chinese ? "成本与 ADV" : "Cost & ADV"}</h3><pre>{JSON.stringify(latest.health_components.cost_capacity_audit ?? [], null, 2)}</pre></>}</details>
  </section>;
}
