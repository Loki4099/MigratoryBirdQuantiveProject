import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";

import { api, ApiClientError } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { DecisionExplorer } from "../components/DecisionExplorer";
import { ResearchKey, researchLabel } from "../components/ResearchText";
import { V022IdentityPanel } from "../components/V022IdentityPanel";

const ratio = (value: number | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value);
const decimal = (value: number | null | undefined) => value == null ? "—" :
  new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(value);
const percentageMetrics = new Set([
  "cumulative_return", "cagr", "annualized_volatility", "maximum_drawdown",
  "positive_daily_return_ratio", "best_daily_return", "worst_daily_return",
  "positive_monthly_return_ratio", "best_monthly_return", "worst_monthly_return",
  "cumulative_relative_return", "annualized_relative_wealth_growth", "cagr_spread",
  "tracking_error", "annualized_alpha",
]);
const metricValue = (metric: { metric_key: string; value: number | null; value_status: string; reason_code: string | null }) =>
  metric.value_status !== "defined" ? metric.reason_code :
    percentageMetrics.has(metric.metric_key) ? ratio(metric.value) : decimal(metric.value);

type GraphSuiteResults = Awaited<ReturnType<typeof api.graphSuiteResults>>;
type GraphSuiteResult = GraphSuiteResults["results"][number];
type GraphSuiteRuntimeReadiness = Awaited<ReturnType<typeof api.graphSuiteRuntimeReadiness>>;
type GraphSuiteLaunchBatch = Awaited<ReturnType<typeof api.graphSuiteLaunchBatchStatus>>;

interface SuiteProgressData {
  status?: string;
  status_counts: Record<string, number>;
  total: number;
  terminal: number;
  complete: boolean;
}

function SuiteProgress({ suite, chinese }: { suite: SuiteProgressData; chinese: boolean }) {
  if (suite.status === "not_started") {
    return <div className="experiment-not-started" role="status">
      <strong>{chinese ? "实验已提交，正在等待运行计划" : "Experiment submitted; waiting for its runtime plan"}</strong>
      <p>{chinese ? "持久运行服务会继续创建计划和工作项；关闭浏览器不会中断处理，本页面会自动刷新。" : "The durable runtime will create the plan and work items. Closing the browser does not interrupt processing, and this page refreshes automatically."}</p>
    </div>;
  }
  const completed = suite.status_counts.completed ?? suite.status_counts.accepted ?? 0;
  const failed = suite.status_counts.failed ?? 0;
  const cancelled = suite.status_counts.cancelled ?? 0;
  const running = suite.status_counts.running ?? 0;
  const queued = suite.status_counts.queued ?? 0;
  const percent = suite.total > 0 ? Math.min(100, suite.terminal / suite.total * 100) : 0;
  const title = suite.status === "materializing"
    ? (chinese ? "正在准备加工结果" : "Materializing processing outputs")
    : suite.complete
    ? failed > 0
      ? (chinese ? "实验运行失败" : "Experiment failed")
      : (chinese ? "实验计算已完成" : "Experiment complete")
    : running > 0
      ? (chinese ? "实验正在计算" : "Experiment running")
      : (chinese ? "实验已排队" : "Experiment queued");
  return <>
    <div className="experiment-progress-heading"><strong>{title}</strong><span>{suite.terminal} / {suite.total}</span></div>
    <div
      className={`experiment-progress-track${suite.complete ? " complete" : " running"}`}
      role="progressbar"
      aria-label={chinese ? "回测进度" : "Backtest progress"}
      aria-valuemin={0}
      aria-valuemax={suite.total}
      aria-valuenow={suite.terminal}
    ><i style={{ width: `${percent}%` }} /></div>
    <p className="experiment-progress-detail">
      {chinese
        ? `完成 ${completed} · 运行中 ${running} · 排队 ${queued} · 失败 ${failed} · 取消 ${cancelled}`
        : `Completed ${completed} · Running ${running} · Queued ${queued} · Failed ${failed} · Cancelled ${cancelled}`}
    </p>
  </>;
}

function SuiteRuntimeStatus({
  readiness,
  loading,
  queryError,
  chinese,
}: {
  readiness: GraphSuiteRuntimeReadiness | null;
  loading: boolean;
  queryError: string | null;
  chinese: boolean;
}) {
  if (loading && !readiness) {
    return <div className="graph-message" role="status">
      <strong>{chinese ? "正在检查回测运行服务" : "Checking the backtest runtime"}</strong>
    </div>;
  }
  if (queryError) {
    return <div className="graph-message error" role="alert">
      <strong>{chinese ? "无法确认回测运行服务状态" : "Unable to confirm the backtest runtime status"}</strong>
      <span>{queryError}</span>
    </div>;
  }
  if (!readiness) return null;

  const heartbeat = readiness.heartbeat_at
    ? new Intl.DateTimeFormat(chinese ? "zh-CN" : "en", {
      dateStyle: "medium",
      timeStyle: "medium",
    }).format(new Date(readiness.heartbeat_at))
    : null;
  const title = readiness.state === "working"
    ? (chinese ? "运行服务正在处理实验" : "The runtime is processing the experiment")
    : readiness.state === "ready"
      ? (chinese ? "运行服务在线，正在等待工作" : "The runtime is online and awaiting work")
      : readiness.state === "error"
        ? (chinese ? "回测运行服务异常，实验暂时无法继续" : "The backtest runtime failed; the experiment cannot continue")
        : readiness.state === "stale"
          ? (chinese ? "运行服务心跳已过期，无法确认实验是否仍在处理" : "The runtime heartbeat is stale; processing cannot be confirmed")
          : readiness.state === "stopped"
            ? (chinese ? "回测运行服务已停止" : "The backtest runtime has stopped")
            : (chinese ? "未检测到回测运行服务" : "No backtest runtime was detected");
  const detail = readiness.error_summary
    ?? (readiness.state === "stale" && heartbeat
      ? `${chinese ? "最后心跳" : "Last heartbeat"}: ${heartbeat}`
      : `${chinese ? "当前状态" : "Current state"}: suite_worker.${readiness.state}`);

  return <div
    className={`graph-message ${readiness.ready ? "success" : "error"}`}
    role={readiness.ready ? "status" : "alert"}
  >
    <strong>{title}</strong>
    <span>{detail}</span>
  </div>;
}

export function ExperimentsPage() {
  const [searchParams] = useSearchParams();
  const launchBatchId = searchParams.get("launch_batch") ?? "";
  const graphSuiteId = searchParams.get("graph_suite") ?? "";
  if (launchBatchId) return <V022LaunchBatchExperiment batchId={launchBatchId} />;
  if (graphSuiteId) return <V022GraphSuiteExperiment suiteId={graphSuiteId} />;
  return <V022ExperimentIndex />;
}

function V022LaunchBatchExperiment({ batchId }: { batchId: string }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage === "zh-CN";
  const batch = useQuery({
    queryKey: ["v022", "graph-suite-launch-batch", batchId],
    queryFn: () => api.graphSuiteLaunchBatchStatus(batchId),
    refetchInterval: (query) => query.state.data?.status === "completed"
      || query.state.data?.status === "failed"
      || query.state.data?.status === "cancelled"
      ? false
      : 2_000,
  });
  const runtimeReadiness = useQuery({
    queryKey: ["v022", "graph-suite-runtime", "readiness"],
    queryFn: api.graphSuiteRuntimeReadiness,
    enabled: batch.data?.status !== "completed",
    refetchInterval: batch.data?.status !== "completed" ? 2_000 : false,
    retry: false,
  });
  return <div className="page experiment-page">
    <header className="page-heading"><div>
      <p className="eyebrow">V0.22 CONTROLLED LAUNCH BATCH / FREQUENCY-SPECIFIC SUITES</p>
      <h1>{chinese ? "周频与月频实验批次" : "Weekly and monthly experiment batch"}</h1>
      <p>{chinese
        ? "两个频率使用同一源修订，分别编译为独立研究图、Suite 和排行榜成员。高内存任务由运行服务串行处理。"
        : "Both frequencies share one source revision but compile into independent graphs, Suites, and leaderboard members. The runtime processes memory-intensive work serially."}</p>
    </div><Link className="arrow-link" to="/experiments">{chinese ? "打开实验首页" : "Open experiment home"} →</Link></header>
    {batch.isLoading && <LoadingState />}
    {batch.error && <ErrorState error={batch.error} retry={() => void batch.refetch()} />}
    {batch.data && <>
      <section className="catalog-section launch-batch-summary">
        <div className="section-heading"><div><p className="eyebrow">BATCH / {batch.data.status}</p><h2>{chinese ? "双频运行进度" : "Frequency-specific progress"}</h2></div><QualityBadge state={batch.data.quality.state} /></div>
        <div className="launch-batch-children">
          {batch.data.children.map((child) => <LaunchBatchChildCard
            key={child.frequency}
            child={child}
            chinese={chinese}
          />)}
        </div>
      </section>
      {batch.data.status !== "completed" && <SuiteRuntimeStatus
        readiness={runtimeReadiness.data ?? null}
        loading={runtimeReadiness.isLoading}
        queryError={runtimeReadiness.error instanceof Error
          ? runtimeReadiness.error.message
          : null}
        chinese={chinese}
      />}
      {batch.data.status === "completed" && <V022Leaderboard chinese={chinese} />}
    </>}
  </div>;
}

function LaunchBatchChildCard({ child, chinese }: {
  child: GraphSuiteLaunchBatch["children"][number];
  chinese: boolean;
}) {
  return <article className="launch-batch-child">
    <header><div><p className="eyebrow">{child.frequency}</p><h3>{child.frequency === "weekly"
      ? (chinese ? "周频实验" : "Weekly experiment")
      : (chinese ? "月频实验" : "Monthly experiment")}</h3></div>
      {child.research_suite_id && <Link className="arrow-link" to={`/experiments?graph_suite=${encodeURIComponent(child.research_suite_id)}&contract=v0.22`}>{chinese ? "查看独立运行" : "Open Suite"} →</Link>}
    </header>
    <SuiteProgress suite={{
      status: child.status,
      status_counts: child.status_counts ?? {},
      total: child.total,
      terminal: child.terminal,
      complete: child.complete,
    }} chinese={chinese} />
  </article>;
}

function V022ExperimentIndex() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage === "zh-CN";
  const [view, setView] = useState<"leaderboard" | "history">("leaderboard");
  const suites = useQuery({
    queryKey: ["v022", "graph-suites"],
    queryFn: () => api.graphSuites(),
    refetchInterval: (query) => query.state.data?.items.some((item) => !item.complete)
      ? 3_000
      : false,
  });
  const currentDraft = useQuery({
    queryKey: ["v022", "current-graph-draft"],
    queryFn: async () => {
      try {
        return await api.graphDraftByKey("browser_default_v1");
      } catch (caught) {
        if (caught instanceof ApiClientError && caught.status === 404) return null;
        throw caught;
      }
    },
    retry: false,
  });
  const currentCompile = useQuery({
    queryKey: [
      "v022",
      "current-graph-draft",
      currentDraft.data?.graph_draft_id,
      currentDraft.data?.revision,
      "compile",
    ],
    queryFn: async () => {
      const draft = currentDraft.data;
      if (!draft) return null;
      try {
        return await api.currentGraphDraftCompile(draft.graph_draft_id);
      } catch (caught) {
        if (caught instanceof ApiClientError && caught.status === 404) return null;
        throw caught;
      }
    },
    enabled: Boolean(currentDraft.data),
    retry: false,
  });
  return <div className="page experiment-page">
    <header className="page-heading"><div><p className="eyebrow">V0.22 RANKING COHORT / PORTFOLIO CELLS</p><h1>{chinese ? "实验排行榜与运行历史" : "Experiment leaderboard and run history"}</h1><p>{chinese ? "同一频率、同一冻结环境中的每个具体实验配置单独排名；运行队列与历史记录保留在独立页签。" : "Each exact configuration ranks independently within one frozen frequency-specific environment. Runtime history remains in a separate tab."}</p></div></header>
    <nav className="product-tabs" aria-label={chinese ? "实验页面" : "Experiment views"}>
      <button type="button" className={view === "leaderboard" ? "active" : ""} onClick={() => setView("leaderboard")}>{chinese ? "排行榜" : "Leaderboard"}</button>
      <button type="button" className={view === "history" ? "active" : ""} onClick={() => setView("history")}>{chinese ? "运行与历史" : "Runs & history"}</button>
    </nav>
    {view === "leaderboard" ? <V022Leaderboard chinese={chinese} /> : <>
    <section className="catalog-section current-research-launch">
      <div className="section-heading"><div>
        <p className="eyebrow">CURRENT RESEARCH / COMPILE / START</p>
        <h2>{chinese ? "启动当前研究" : "Start the current research"}</h2>
        <p>{chinese
          ? "编译只冻结配置，不会自动运行回测。确认当前编译身份后，再明确启动一次实验。"
          : "Compilation freezes the configuration but does not run a backtest. Review the current compile, then explicitly start one experiment."}</p>
      </div></div>
      {(currentDraft.isLoading || currentCompile.isLoading) && <LoadingState />}
      {(currentDraft.error || currentCompile.error) && <ErrorState
        error={currentDraft.error ?? currentCompile.error!}
        retry={() => {
          void currentDraft.refetch();
          void currentCompile.refetch();
        }}
      />}
      {!currentDraft.isLoading && !currentDraft.error && !currentDraft.data && <div className="factor-empty-note">
        <p>{chinese ? "还没有当前研究，请先选择资产和因子。" : "There is no current research yet. Select assets and factors first."}</p>
        <Link className="arrow-link" to="/research-context">{chinese ? "开始研究" : "Start research"} →</Link>
      </div>}
      {currentDraft.data && !currentCompile.isLoading && !currentCompile.error && !currentCompile.data && <div className="factor-empty-note">
        <p>{chinese ? "当前研究尚未编译。请在策略页顶部完成配置检查与编译。" : "The current research has not been compiled. Review and compile it at the top of the Strategy page."}</p>
        <Link className="arrow-link" to="/strategy-configuration">{chinese ? "前往配置检查" : "Open configuration review"} →</Link>
      </div>}
      {currentDraft.data && currentCompile.data && <div className="graph-ready">
        <strong>{chinese ? "当前修订已经编译，可以进入实验确认" : "The current revision is compiled and ready for experiment confirmation"}</strong>
        <code>{currentCompile.data.graph_fingerprint}</code>
        <Link className="arrow-link" to="/experiment-launch">{chinese ? "确认并启动实验" : "Review and start experiment"} →</Link>
      </div>}
    </section>
    {suites.isLoading && <LoadingState />}
    {suites.error && <ErrorState error={suites.error} retry={() => void suites.refetch()} />}
    {suites.data && <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">PERSISTED / NEWEST FIRST</p><h2>{chinese ? "v0.22 实验历史" : "v0.22 experiment history"}</h2><p>{chinese ? `共 ${suites.data.total_count} 个实验；列表来自后端持久身份，不依赖当前浏览器。` : `${suites.data.total_count} experiments from persisted backend identities, independent of this browser.`}</p></div><QualityBadge state={suites.data.quality.state} /></div>
      {suites.data.items.length ? <div className="v022-suite-history">{suites.data.items.map((suite) => <article className="v022-suite-history-card" key={suite.research_suite_id}>
        <div className="section-heading"><div><p className="eyebrow">{researchLabel(suite.status, i18n.resolvedLanguage)} · {suite.suite_mode}</p><h3>{new Intl.DateTimeFormat(chinese ? "zh-CN" : "en", { dateStyle: "medium", timeStyle: "short" }).format(new Date(suite.created_at))}</h3></div><Link className="arrow-link" to={`/experiments?graph_suite=${suite.research_suite_id}`}>{suite.complete ? (chinese ? "查看结果" : "Open results") : (chinese ? "查看进度" : "Open progress")} →</Link></div>
        <SuiteProgress suite={suite} chinese={chinese} />
        <div className="experiment-detail-strip">
          <div><span>{chinese ? "策略分支" : "Branches"}</span><strong>{suite.strategy_branch_count}</strong></div>
          <div><span>Portfolio Cells</span><strong>{suite.backtest_cell_count}</strong></div>
          <div><span>Graph</span><strong>{suite.graph_fingerprint.slice(0, 12)}…</strong></div>
          <div><span>Suite</span><strong>{suite.suite_fingerprint.slice(0, 12)}…</strong></div>
        </div>
      </article>)}</div> : <div className="factor-empty-note"><p>{chinese ? "还没有 v0.22 实验。请先在策略页完成配置检查、编译并创建实验。" : "No v0.22 experiments yet. Review and compile a graph on the Strategy page, then create an experiment."}</p><Link className="arrow-link" to="/workspace-v022/strategy">{chinese ? "前往策略与编译" : "Open strategy and compile"} →</Link></div>}
    </section>}
    </>}
  </div>;
}

function V022Leaderboard({ chinese }: { chinese: boolean }) {
  const [frequency, setFrequency] = useState<"weekly" | "monthly">("weekly");
  const [sort, setSort] = useState<"sharpe_ratio" | "cagr" | "cagr_spread" | "maximum_drawdown">("sharpe_ratio");
  const leaderboard = useQuery({
    queryKey: ["v022", "experiment-leaderboard", frequency, sort],
    queryFn: () => api.v022ExperimentLeaderboard({ frequency, sort }),
    retry: false,
  });
  const promotion = useMutation({
    mutationFn: async (row: NonNullable<typeof leaderboard.data>["rows"][number]) => {
      const display = row.display as { aggregation?: { name?: string }; strategy?: { name?: string } };
      return api.promoteAndEnrollV022Product(row.result_evidence_snapshot_id, {
        idempotencyKey: crypto.randomUUID(),
        productKey: `result_${row.result_evidence_snapshot_id.replaceAll("-", "").slice(0, 24)}`,
        name: [display.aggregation?.name, display.strategy?.name].filter(Boolean).join(" · ") || `v0.22 ${row.result_evidence_snapshot_id.slice(0, 8)}`,
        description: "Promoted from one exact frozen v0.22 leaderboard row.",
      });
    },
    onSuccess: () => void leaderboard.refetch(),
  });

  return <section className="catalog-section v022-leaderboard">
    <div className="section-heading"><div><p className="eyebrow">STRICT COMPARISON / ONE CELL PER ROW</p><h2>{chinese ? "统一实验环境排行榜" : "Frozen-environment leaderboard"}</h2><p>{chinese ? "周频与月频永不混排；年化超额严格等于策略 CAGR 减 SPY CAGR。" : "Weekly and monthly results never mix. Annualized excess is strategy CAGR minus SPY CAGR."}</p></div><div className="experiment-filters"><label className="experiment-filter">{chinese ? "排序" : "Sort"}<select value={sort} onChange={(event) => setSort(event.target.value as typeof sort)}><option value="sharpe_ratio">Sharpe</option><option value="cagr">CAGR</option><option value="cagr_spread">{chinese ? "年化超额" : "Annualized excess"}</option><option value="maximum_drawdown">{chinese ? "最大回撤" : "Maximum drawdown"}</option></select></label></div></div>
    <nav className="frequency-tabs" aria-label={chinese ? "实验频率" : "Experiment frequency"}>{(["weekly", "monthly"] as const).map((item) => <button type="button" key={item} className={frequency === item ? "active" : ""} onClick={() => setFrequency(item)}>{item === "weekly" ? (chinese ? "周频" : "Weekly") : (chinese ? "月频" : "Monthly")}</button>)}</nav>
    {leaderboard.isLoading && <LoadingState />}
    {leaderboard.error && <div className="factor-empty-note"><strong>{chinese ? "该频率尚未发布统一排行榜" : "No strict leaderboard has been published for this frequency"}</strong><p>{leaderboard.error instanceof Error ? leaderboard.error.message : String(leaderboard.error)}</p></div>}
    {leaderboard.data && <>
      {leaderboard.data.comparison_context && <div className="scope-strip experiment-scope-strip"><div><span>{chinese ? "评价区间" : "Evaluation"}</span><strong>{leaderboard.data.comparison_context.evaluation_start} → {leaderboard.data.comparison_context.evaluation_end}</strong></div><div><span>{chinese ? "暖机起点" : "Warm-up start"}</span><strong>{leaderboard.data.comparison_context.warmup_start}</strong></div><div><span>{chinese ? "基准" : "Benchmark"}</span><strong>SPY</strong></div><div><span>{chinese ? "成本" : "Cost"}</span><strong>{leaderboard.data.comparison_context.cost_bps_per_side} bps/side</strong></div></div>}
      <div className="experiment-table v022-ranking-table">
        <div className="experiment-table-head"><span># / {chinese ? "配置" : "Configuration"}</span><span>{chinese ? "输入摘要" : "Inputs"}</span><span>CAGR</span><span>{chinese ? "年化超额" : "Excess CAGR"}</span><span>Sharpe</span><span>{chinese ? "最大回撤" : "Max drawdown"}</span><span>{chinese ? "操作" : "Actions"}</span></div>
        {(leaderboard.data.rows ?? []).map((row) => {
          const display = row.display as { direct_inputs?: Array<{ name?: string }>; aggregation?: { name?: string }; strategy?: { name?: string }; defense?: { name?: string; none?: boolean } };
          const resultPath = `/experiments/results/${row.result_evidence_snapshot_id}`;
          return <div className="v022-ranking-row" key={row.result_evidence_snapshot_id}>
            <Link className="ranking-result-link" to={resultPath} aria-label={chinese ? `查看第 ${row.rank} 名实验的详细回测` : `Open detailed backtest for rank ${row.rank}`}>
              <strong><b>{row.rank}</b> · {display.aggregation?.name ?? "—"}<small>{display.strategy?.name ?? "—"} · {display.defense?.none ? (chinese ? "无防御" : "No defense") : display.defense?.name ?? "—"}</small></strong>
              <span>{chinese ? "查看指标与回撤图表" : "Open metrics and drawdown charts"} →</span>
            </Link>
            <span>{(display.direct_inputs ?? []).map((item) => item.name).filter(Boolean).join(" · ") || "—"}</span>
            <code>{ratio(Number(row.cagr))}</code><code>{ratio(Number(row.cagr_spread))}</code><code>{decimal(Number(row.sharpe_ratio))}</code><code>{ratio(Number(row.maximum_drawdown))}</code>
            <span className="ranking-actions"><Link className="secondary-button" to={resultPath}>{chinese ? "查看详细回测" : "Full backtest"}</Link>{row.product_candidate && row.execution_version_id ? <Link to={row.product_enrollment_id ? `/products/${row.product_enrollment_id}` : "/products"}>Product</Link> : <button type="button" disabled={promotion.isPending} onClick={() => { if (window.confirm(chinese ? "确认只升级这一条实验配置并开始样本外观察？" : "Promote only this result and start OOS observation?")) promotion.mutate(row); }}>{chinese ? "升级 Product" : "Promote"}</button>}</span>
          </div>;
        })}
      </div>
      {promotion.error && <ErrorState error={promotion.error} retry={() => undefined} />}
    </>}
  </section>;
}

function V022GraphSuiteExperiment({ suiteId }: { suiteId: string }) {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage === "zh-CN";
  const [completedView, setCompletedView] = useState<"leaderboard" | "run">("leaderboard");
  const suite = useQuery({
    queryKey: ["v022", "graph-suite", suiteId],
    queryFn: () => api.graphSuiteStatus(suiteId),
    refetchInterval: (query) => query.state.data?.complete ? false : 2_000,
  });
  const results = useQuery({
    queryKey: ["v022", "graph-suite", suiteId, "results"],
    queryFn: () => api.graphSuiteResults(suiteId),
    enabled: suite.data?.complete === true,
  });
  const runtimeReadiness = useQuery({
    queryKey: ["v022", "graph-suite-runtime", "readiness"],
    queryFn: api.graphSuiteRuntimeReadiness,
    enabled: suite.data?.complete === false,
    refetchInterval: suite.data?.complete === false ? 2_000 : false,
    retry: false,
  });
  const waitingRuntimeBlocked = suite.data?.status === "not_started"
    && (Boolean(runtimeReadiness.error) || runtimeReadiness.data?.ready === false);

  return <div className="page experiment-page">
    <header className="page-heading"><div><p className="eyebrow">V0.22 GRAPH SUITE / TYPED PORTFOLIO RESULT</p><h1>{chinese ? "v0.22 策略实验" : "v0.22 strategy experiment"}</h1><p>{chinese ? "运行中查看真实进度；完成后进入统一排行榜，本次 Suite 详情保留为审计入口。" : "Track live progress while running. Completed results enter the frozen leaderboard, while this Suite remains available for audit."}</p></div><Link className="arrow-link" to="/experiments">{chinese ? "打开实验首页" : "Open experiment home"} →</Link></header>
    {suite.data?.complete && <nav className="product-tabs" aria-label={chinese ? "完成实验视图" : "Completed experiment views"}>
      <button type="button" className={completedView === "leaderboard" ? "active" : ""} onClick={() => setCompletedView("leaderboard")}>{chinese ? "统一排行榜" : "Leaderboard"}</button>
      <button type="button" className={completedView === "run" ? "active" : ""} onClick={() => setCompletedView("run")}>{chinese ? "本次运行详情" : "This run"}</button>
    </nav>}
    {suite.data?.complete && completedView === "leaderboard" ? <V022Leaderboard chinese={chinese} /> : <>
    <section className="workspace-release-gate experiment-progress" role="status">
      {suite.isLoading && <><strong>{chinese ? "正在读取实验队列" : "Loading experiment queue"}</strong><span>{suiteId}</span></>}
      {suite.error && <ErrorState error={suite.error} retry={() => void suite.refetch()} />}
      {suite.data && !waitingRuntimeBlocked && <SuiteProgress suite={suite.data} chinese={chinese} />}
      {suite.data && !suite.data.complete && <SuiteRuntimeStatus
        readiness={runtimeReadiness.data ?? null}
        loading={runtimeReadiness.isLoading}
        queryError={runtimeReadiness.error instanceof Error
          ? runtimeReadiness.error.message
          : null}
        chinese={chinese}
      />}
    </section>
    {suite.data?.complete && results.isLoading && <LoadingState />}
    {results.error && <ErrorState error={results.error} retry={() => void results.refetch()} />}
    {results.data && <V022TypedResults data={results.data} chinese={chinese} />}
    </>}
  </div>;
}

function V022TypedResults({ data, chinese }: { data: GraphSuiteResults; chinese: boolean }) {
  return <section className="catalog-section">
    <div className="section-heading"><div><p className="eyebrow">PUBLISHED / IMMUTABLE / SUITE-SCOPED</p><h2>{chinese ? "Portfolio Cell 结果" : "Portfolio Cell results"}</h2></div><QualityBadge state={data.quality.state} /></div>
    <div className="scope-strip experiment-scope-strip">
      <div><span>{chinese ? "预期结果" : "Expected"}</span><strong>{data.expected_result_count}</strong></div>
      <div><span>{chinese ? "已发布" : "Published"}</span><strong>{data.result_count}</strong></div>
      <div><span>{chinese ? "状态" : "Status"}</span><strong>{data.status}</strong></div>
      <div><span>{chinese ? "完整" : "Complete"}</span><strong>{data.complete ? (chinese ? "是" : "yes") : (chinese ? "否" : "no")}</strong></div>
    </div>
    {data.results.map((result) => <V022ResultCard key={result.result_artifact_id} result={result} chinese={chinese} />)}
  </section>;
}

function V022ResultCard({ result, chinese }: { result: GraphSuiteResult; chinese: boolean }) {
  const [view, setView] = useState<"overview" | "elements" | "quality" | "lineage">("overview");
  const diagnostic = result.diagnostic;
  const diagnosticStages = ([1, 2, 3] as const).map((stageNo) => ({
    stageNo,
    elements: diagnostic.elements.filter(
      (element) => element.diagnostic_document.stage_no === stageNo,
    ),
  })).filter((stage) => stage.elements.length > 0);
  const evidenceId = diagnostic.evidence.result_evidence_snapshot_id;
  return <article className="catalog-card v022-result-card">
      <div className="section-heading"><div><p className="eyebrow">{result.outcome} / {result.quality_status}</p><h3>{result.effective_start} → {result.effective_end}</h3></div><div className="experiment-detail-actions">{evidenceId && <Link className="arrow-link" to={`/experiments/results/${evidenceId}`}>{chinese ? "查看完整回测" : "Open full backtest"} →</Link>}<Link className="arrow-link" to={`/artifacts/${result.result_artifact_id}`}>{chinese ? "查看血缘" : "Open lineage"} →</Link></div></div>
      <div className="experiment-detail-strip">
        <div><span>Research Cell</span><strong>{result.research_cell_id}</strong></div>
        <div><span>{chinese ? "配置快照" : "Configuration"}</span><strong>{result.configuration_snapshot_id}</strong></div>
        <div><span>Result fingerprint</span><strong>{result.result_fingerprint.slice(0, 16)}…</strong></div>
        <div><span>Manifest</span><strong>{result.payload_manifest_id}</strong></div>
      </div>
      <div className="v022-result-tabs" role="tablist" aria-label={chinese ? "结果视图" : "Result views"}>
        {(["overview", "elements", "quality", "lineage"] as const).map((item) => <button
          aria-selected={view === item}
          className={view === item ? "active" : ""}
          key={item}
          onClick={() => setView(item)}
          role="tab"
          type="button"
        >{{ overview: chinese ? "收益概览" : "Performance", elements: chinese ? "元素诊断" : "Element diagnostics", quality: chinese ? "数据质量" : "Data quality", lineage: chinese ? "证据与血缘" : "Evidence & lineage" }[item]}</button>)}
      </div>
      {view === "overview" && <div className="experiment-metrics v022-diagnostic-metrics">
        {diagnostic.metrics.map((metric) => <div key={`${metric.metric_group}:${metric.metric_key}`}>
          <span><ResearchKey value={metric.metric_key} /> · {metric.metric_group === "absolute" ? (chinese ? "组合" : "portfolio") : (chinese ? "相对基准" : "relative")}</span>
          <strong>{formatDiagnosticMetric(metric)}</strong>
          <small>{chinese ? `${metric.observation_count} 个观测` : `${metric.observation_count} observations`}</small>
        </div>)}
        {!diagnostic.metrics.length && <p className="factor-empty-note">{chinese ? "该终态结果没有可发布的收益指标。" : "This terminal result has no publishable performance metrics."}</p>}
      </div>}
      {view === "elements" && <div className="v022-element-diagnostics">
        <p className="v022-diagnostic-note">{chinese
          ? "以下指标覆盖实际执行血缘中的加工层 1–3，并使用同一冻结样本与评价目标。它们只用于辅助研究，不表示因果归因；无预测方向的中间量只计算覆盖率与分布指标。"
          : "Diagnostics cover processing stages 1–3 on the exact executed lineage and the same frozen sample and target. They are non-causal; unsigned intermediates only receive coverage and distribution metrics."}</p>
        {diagnosticStages.map(({ stageNo, elements }) => <section className="v022-diagnostic-stage" key={stageNo}>
          <div className="section-heading"><div>
            <p className="eyebrow">PROCESSING STAGE {stageNo}</p>
            <h3>{chinese ? `加工层 ${stageNo} 诊断` : `Processing stage ${stageNo} diagnostics`}</h3>
            <p>{chinese
              ? `本层共 ${elements.length} 个已执行元素；指标使用同一冻结样本和评价目标，仅用于辅助研究。`
              : `${elements.length} executed elements on the same frozen sample and target; diagnostic only.`}</p>
          </div></div>
          {elements.map((element) => {
          const document = element.diagnostic_document;
          return <article className="v022-element-diagnostic-card" key={element.result_element_diagnostic_id}>
            <header><div><p className="eyebrow">STAGE {document.stage_no} / {document.research_direction}</p><h4><ResearchKey value={document.feature_variant_key} /></h4></div><Link className="arrow-link" to={`/artifacts/${element.artifact_id}`}>{chinese ? "诊断血缘" : "Diagnostic lineage"} →</Link></header>
            <div className="v022-element-context">
              <div><span>{chinese ? "评价目标" : "Evaluation target"}</span><strong><ResearchKey value={document.target_key} /></strong></div>
              <div><span>{chinese ? "覆盖区间" : "Coverage"}</span><strong>{document.coverage_start} → {document.coverage_end}</strong></div>
              <div><span>{chinese ? "有效 IC 期数" : "Valid IC periods"}</span><strong>{document.valid_ic_count} / {document.evaluation_period_count}</strong></div>
              <div><span>{chinese ? "观测覆盖" : "Observed coverage"}</span><strong>{document.observed_value_count} / {document.expected_observation_count}</strong></div>
            </div>
            <div className="experiment-metrics v022-element-metrics">{document.metrics.map((metric) => <div key={metric.metric_key}>
              <span><ResearchKey value={metric.metric_key} /></span>
              <strong>{formatElementMetric(metric)}</strong>
              {metric.reason_code && <small>{metric.reason_code}</small>}
            </div>)}</div>
          </article>;
        })}</section>)}
        {!diagnostic.elements.length && <p className="factor-empty-note">{chinese
          ? "该结果的逐元素诊断尚未发布。"
          : "Direct-element diagnostics have not been published for this result yet."}</p>}
      </div>}
      {view === "quality" && <div className="v022-diagnostic-grid">
        <div><span>{chinese ? "质量结论" : "Quality status"}</span><strong>{diagnostic.quality.status}</strong></div>
        <div><span>{chinese ? "有效会话" : "Path sessions"}</span><strong>{diagnostic.quality.path_session_count}</strong></div>
        <div><span>{chinese ? "基准" : "Benchmark"}</span><strong>{diagnostic.execution.benchmark_asset_key ?? "—"}</strong></div>
        <div><span>{chinese ? "单边成本" : "Cost per side"}</span><strong>{diagnostic.execution.basis_points_per_side == null ? "—" : `${diagnostic.execution.basis_points_per_side} bps`}</strong></div>
        <div><span>{chinese ? "执行延迟" : "Execution delay"}</span><strong>{diagnostic.execution.execution_delay_sessions == null ? "—" : `${diagnostic.execution.execution_delay_sessions} session`}</strong></div>
        <div><span>{chinese ? "输入截止" : "Input cutoff"}</span><strong>{diagnostic.execution.evaluation_input_cutoff_at ?? "—"}</strong></div>
        {diagnostic.quality.reason_code && <div className="wide"><span>{chinese ? "原因" : "Reason"}</span><strong>{diagnostic.quality.reason_code}</strong></div>}
      </div>}
      {view === "lineage" && <div className="v022-diagnostic-grid">
        <div><span>Result Artifact</span><Link to={`/artifacts/${result.result_artifact_id}`}>{result.result_artifact_id}</Link></div>
        <div><span>Manifest Artifact</span><Link to={`/artifacts/${result.payload_manifest_artifact_id}`}>{result.payload_manifest_artifact_id}</Link></div>
        <div><span>{chinese ? "结果证据" : "Result evidence"}</span><strong>{diagnostic.evidence.publication_status}</strong></div>
        <div><span>{chinese ? "证据类别" : "Evidence class"}</span><strong>{diagnostic.evidence.evidence_class ?? "—"}</strong></div>
        {diagnostic.evidence.result_evidence_artifact_id && <div><span>Evidence Artifact</span><Link to={`/artifacts/${diagnostic.evidence.result_evidence_artifact_id}`}>{diagnostic.evidence.result_evidence_artifact_id}</Link></div>}
        <div><span>Common Panel</span><strong>{diagnostic.evidence.common_evaluation_panel_id ?? "—"}</strong></div>
        <div className="wide"><span>Evaluation Context</span><strong>{diagnostic.execution.evaluation_data_context_fingerprint ?? "—"}</strong></div>
      </div>}
    </article>;
}

const formatDiagnosticMetric = (metric: GraphSuiteResult["diagnostic"]["metrics"][number]) => {
  if (metric.value_status !== "defined" || metric.value == null) return metric.reason_code ?? "—";
  const value = Number(metric.value);
  if (!Number.isFinite(value)) return metric.value;
  return percentageMetrics.has(metric.metric_key) ? ratio(value) : decimal(value);
};

const elementPercentageMetrics = new Set(["coverage_ratio", "positive_ic_ratio"]);

const formatElementMetric = (metric: GraphSuiteResult["diagnostic"]["elements"][number]["diagnostic_document"]["metrics"][number]) => {
  if (metric.value == null) return metric.reason_code ?? "—";
  const value = Number(metric.value);
  if (!Number.isFinite(value)) return metric.value;
  return elementPercentageMetrics.has(metric.metric_key) ? ratio(value) : decimal(value);
};

export function LegacyExperimentsPage() {
  const { t, i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage === "zh-CN";
  const [searchParams, setSearchParams] = useSearchParams();
  const [status, setStatus] = useState("all");
  const [interval, setInterval] = useState("full_history");
  const [frequency, setFrequency] = useState("weekly");
  const [cost, setCost] = useState("5");
  const [rankingMetric, setRankingMetric] = useState("strategy.sharpe_ratio");
  const [resultId, setResultId] = useState(searchParams.get("result") ?? "");
  const [promotionOpen, setPromotionOpen] = useState(false);
  const [productName, setProductName] = useState("");
  const [selectionReason, setSelectionReason] = useState("");
  const [promotionNote, setPromotionNote] = useState("");
  const [page, setPage] = useState(1);
  const detailDialogRef = useRef<HTMLElement>(null);
  const promotionDialogRef = useRef<HTMLElement>(null);
  const submittedSuiteId = searchParams.get("suite") ?? "";
  const graphSuiteId = searchParams.get("graph_suite") ?? "";
  const pageSize = 50;
  const overview = useQuery({
    queryKey: ["experiments", "overview", submittedSuiteId, status, interval, frequency, cost, rankingMetric, page],
    queryFn: () => api.experimentOverview({
      researchSuiteId: submittedSuiteId || undefined,
      status,
      templateKey: interval,
      frequency: frequency as "weekly" | "monthly",
      costBpsPerSide: Number(cost),
      rankingMetric,
      limit: pageSize,
      offset: (page - 1) * pageSize,
    }),
  });
  const predictiveOverview = useQuery({
    queryKey: ["experiments", "predictive", submittedSuiteId, status, frequency],
    queryFn: () => api.experimentOverview({
      researchSuiteId: submittedSuiteId || undefined,
      status,
      templateKey: "predictive_diagnostic",
      frequency: frequency as "weekly" | "monthly",
      rankingMetric: "predictive.mean_rank_ic",
      limit: 200,
      offset: 0,
    }),
  });
  const submittedSuite = useQuery({
    queryKey: ["workspace", "suite", submittedSuiteId],
    queryFn: () => api.workspaceSuiteStatus(submittedSuiteId),
    enabled: Boolean(submittedSuiteId),
    refetchInterval: (query) => query.state.data?.complete ? false : 2_000,
  });
  const graphSuite = useQuery({
    queryKey: ["v022", "graph-suite", graphSuiteId],
    queryFn: () => api.graphSuiteStatus(graphSuiteId),
    enabled: Boolean(graphSuiteId),
    refetchInterval: (query) => query.state.data?.complete ? false : 2_000,
  });
  useEffect(() => {
    if (!submittedSuite.data?.complete) return;
    void overview.refetch();
    void predictiveOverview.refetch();
  // Refresh the result catalogs once the submitted Suite reaches a terminal state.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submittedSuite.data?.complete]);
  const specifications = overview.data?.specifications ?? [];
  const predictiveSpecifications = predictiveOverview.data?.specifications ?? [];
  const pageCount = Math.max(1, Math.ceil((overview.data?.filtered_specification_count ?? 0) / pageSize));
  const visibleSpecifications = specifications;
  const activeResultId = resultId;
  const activeIsPredictive = predictiveOverview.data?.specifications.some(
    (item) => item.result_artifact_id === activeResultId && item.template_key === "predictive_diagnostic",
  ) ?? false;
  const detail = useQuery({
    queryKey: ["experiments", "result", activeResultId],
    queryFn: () => api.experimentResult(activeResultId), enabled: Boolean(activeResultId),
  });
  const qualification = useQuery({
    queryKey: ["experiments", "qualification", activeResultId],
    queryFn: () => api.promotionQualification(activeResultId),
    enabled: Boolean(activeResultId) && !activeIsPredictive, retry: false,
  });
  const promote = useMutation({
    mutationFn: () => api.promoteResult(activeResultId, {
      name: productName, selectionReason, note: promotionNote,
    }),
    onSuccess: () => setPromotionOpen(false),
  });
  const closeDetail = () => {
    setResultId("");
    const next = new URLSearchParams(searchParams);
    next.delete("result");
    setSearchParams(next, { replace: true });
  };
  useEffect(() => {
    if (!resultId) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    detailDialogRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || promotionOpen) return;
      closeDetail();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previous?.focus();
    };
  // Opening a result establishes one modal interaction session.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resultId]);
  useEffect(() => {
    if (!promotionOpen) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    promotionDialogRef.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPromotionOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("keydown", closeOnEscape);
      previous?.focus();
    };
  }, [promotionOpen]);

  if (overview.isLoading || predictiveOverview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (predictiveOverview.error) return <ErrorState error={predictiveOverview.error} retry={() => void predictiveOverview.refetch()} />;
  if (!overview.data) return <EmptyState />;
  const data = overview.data;
  const accepted = data.accepted_count;
  const failed = data.failed_count;

  return <div className="page experiment-page">
    <header className="page-heading"><div><p className="eyebrow">SUITE / CELL / ACCEPTED RESULT</p><h1>{t("experiment.title")}</h1><p>{t("experiment.subtitle")}</p></div><QualityBadge state={data.quality.state} /></header>
    <section className="scope-strip experiment-scope-strip">
      <div><span>{t("experiment.suites")}</span><strong>{data.suites.length}</strong></div>
      <div><span>{t("experiment.cells")}</span><strong>{data.total_specification_count}</strong></div>
      <div><span>{t("experiment.accepted")}</span><strong>{accepted}</strong></div>
      <div><span>{t("experiment.failed")}</span><strong>{failed}</strong></div>
    </section>
    <V022IdentityPanel kind="experiment" />
    {graphSuiteId && <section className="workspace-release-gate experiment-progress" role="status">
      {graphSuite.isLoading && <><strong>{chinese ? "v0.22 实验已提交，正在读取队列状态" : "v0.22 experiment submitted; loading queue status"}</strong><span>{graphSuiteId}</span></>}
      {graphSuite.error && <><strong>{chinese ? "暂时无法读取 v0.22 实验进度" : "The v0.22 experiment status is temporarily unavailable"}</strong><span>{graphSuiteId}</span></>}
      {graphSuite.data && <SuiteProgress suite={graphSuite.data} chinese={chinese} />}
    </section>}
    {submittedSuiteId && <section className="workspace-release-gate experiment-progress" role="status">
      {submittedSuite.isLoading && <><strong>{chinese ? "实验已提交，正在读取队列状态" : "Experiment submitted; loading queue status"}</strong><span>{submittedSuiteId}</span></>}
      {submittedSuite.error && <><strong>{chinese ? "实验已提交，但暂时无法读取进度" : "Experiment submitted, but progress is temporarily unavailable"}</strong><span>{submittedSuiteId}</span></>}
      {submittedSuite.data && <SuiteProgress suite={submittedSuite.data} chinese={chinese} />}
    </section>}

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">MODEL / TARGET / RANK IC</p><h2>{chinese ? "预测诊断" : "Predictive diagnostics"}</h2></div></div>
      <p className="scope-note">{chinese ? "模型输出在共同有效资产集合内，对冻结目标计算逐期 Rank IC；覆盖率和目标非退化率均由结果门禁复核。" : "Model outputs are evaluated period by period against the frozen target within the common valid-asset set. Result gates verify both coverage and target non-degeneracy."}</p>
      <div className="experiment-table">
        <div className="experiment-table-head"><span>{chinese ? "模型" : "Model"}</span><span>{chinese ? "目标" : "Target"}</span><span>{chinese ? "平均 Rank IC" : "Mean Rank IC"}</span><span>{chinese ? "覆盖率" : "Coverage"}</span><span>{chinese ? "非退化率" : "Nondegenerate"}</span><span>{chinese ? "期数" : "Periods"}</span><span>{t("common.status")}</span></div>
        {predictiveSpecifications.map((item) => <button type="button" className={activeResultId === item.result_artifact_id ? "active" : ""} key={`${item.suite_artifact_id}-${item.artifact_id}`} disabled={!item.result_artifact_id} onClick={() => { if (item.result_artifact_id) { setResultId(item.result_artifact_id); const next = new URLSearchParams(searchParams); next.set("result", item.result_artifact_id); setSearchParams(next, { replace: true }); } }}>
          <strong><ResearchKey value={item.model_specification_key} /><small>{researchLabel(item.frequency, i18n.resolvedLanguage)}</small>{item.status === "failed" && <small className="experiment-failure-summary" title={item.error_summary ?? undefined}>{chinese ? `尝试 ${item.attempt_number ?? "—"} 次` : `Attempt ${item.attempt_number ?? "—"}`} · {item.error_summary ?? (chinese ? "实验单元失败" : "Experiment Cell failed")}</small>}</strong>
          <span><ResearchKey value={item.benchmark_key} /></span>
          <code>{decimal(item.core_metrics["predictive.mean_rank_ic"])}</code><code>{ratio(item.core_metrics["predictive.target_period_coverage"])}</code><code>{ratio(item.core_metrics["predictive.nondegenerate_target_ratio"])}</code><code>{decimal(item.core_metrics["predictive.aligned_target_period_count"])}</code>
          <span className={`experiment-status ${item.status}`}>{item.availability_status === "capacity_rejected" ? researchLabel("capacity_rejected", i18n.resolvedLanguage) : t(`experiment.${item.status}`)}</span>
        </button>)}
      </div>
      {predictiveSpecifications.length === 0 && <p className="factor-empty-note">{t("common.noData")}</p>}
    </section>

    <section className="catalog-section">
      <div className="section-heading"><div><p className="eyebrow">COMPARABLE MARKET ASSUMPTIONS</p><h2>{t("experiment.cells")}</h2></div>
        <div className="experiment-filters">
          <label className="experiment-filter">{t("experiment.interval")}<select value={interval} onChange={(event) => { setInterval(event.target.value); setPage(1); }}>{["full_history", "trailing_3_years", "trailing_1_year"].map((key) => <option key={key} value={key}>{researchLabel(key, i18n.resolvedLanguage)}</option>)}</select></label>
          <label className="experiment-filter">{t("experiment.frequency")}<select value={frequency} onChange={(event) => { setFrequency(event.target.value); setPage(1); }}>{["weekly", "monthly"].map((key) => <option key={key} value={key}>{researchLabel(key, i18n.resolvedLanguage)}</option>)}</select></label>
          <label className="experiment-filter">{t("experiment.cost")}<select value={cost} onChange={(event) => { setCost(event.target.value); setPage(1); }}>{["5", "10"].map((value) => <option key={value} value={value}>{value} bps + impact</option>)}</select></label>
          <label className="experiment-filter">{chinese ? "排序指标" : "Ranking metric"}<select value={rankingMetric} onChange={(event) => { setRankingMetric(event.target.value); setPage(1); }}><option value="strategy.sharpe_ratio">{chinese ? "净 Sharpe" : "Net Sharpe"}</option><option value="strategy.cagr">{chinese ? "净 CAGR" : "Net CAGR"}</option><option value="strategy.maximum_drawdown">{chinese ? "最大回撤" : "Maximum drawdown"}</option><option value="relative.annualized_relative_wealth_growth">{chinese ? "相对财富增长" : "Relative wealth growth"}</option></select></label>
          <label className="experiment-filter">{t("common.status")}<select value={status} onChange={(event) => { setStatus(event.target.value); setPage(1); }}><option value="all">{t("experiment.all")}</option><option value="accepted">{t("experiment.accepted")}</option><option value="failed">{t("experiment.failed")}</option><option value="pending">{t("experiment.pending")}</option></select></label>
        </div>
      </div>
      <p className="scope-note">{t("experiment.comparisonNote")}</p>
      <div className="experiment-table">
        <div className="experiment-table-head"><span>{t("experiment.strategy")}</span><span>{t("experiment.assumptions")}</span><span>{t("experiment.netCagr")}</span><span>{t("experiment.benchmarkCagr")}</span><span>{t("experiment.sharpe")}</span><span>{t("experiment.drawdown")}</span><span>{t("common.status")}</span></div>
        {visibleSpecifications.map((item) => <button type="button" className={activeResultId === item.result_artifact_id ? "active" : ""} key={`${item.suite_artifact_id}-${item.artifact_id}`} disabled={!item.result_artifact_id} onClick={() => { if (item.result_artifact_id) { setResultId(item.result_artifact_id); const next = new URLSearchParams(searchParams); next.set("result", item.result_artifact_id); setSearchParams(next, { replace: true }); } }}>
          <strong><ResearchKey value={item.model_specification_key} /><small>{item.suite_mode === "exploratory" ? (chinese ? "探索性 · " : "Exploratory · ") : item.suite_mode === "formal" ? (chinese ? "正式 · " : "Formal · ") : (chinese ? "历史 · " : "Legacy · ")}{researchLabel(item.variant_key, i18n.resolvedLanguage)} · {researchLabel(item.frequency, i18n.resolvedLanguage)}</small>{item.status === "failed" && <small className="experiment-failure-summary" title={item.error_summary ?? undefined}>{chinese ? `尝试 ${item.attempt_number ?? "—"} 次` : `Attempt ${item.attempt_number ?? "—"}`} · {item.error_summary ?? (chinese ? "实验单元失败" : "Experiment Cell failed")}</small>}</strong>
          <span><ResearchKey value={item.template_key} /><small>{item.cost_bps_per_side} bps/side · {researchLabel(item.benchmark_key, i18n.resolvedLanguage)}</small></span>
          <code>{ratio(item.core_metrics["strategy.cagr"])}</code><code>{ratio(item.core_metrics["benchmark.cagr"])}</code><code>{decimal(item.core_metrics["strategy.sharpe_ratio"])}</code><code>{ratio(item.core_metrics["strategy.maximum_drawdown"])}</code>
          <span className={`experiment-status ${item.status}`}>{item.availability_status === "capacity_rejected" ? researchLabel("capacity_rejected", i18n.resolvedLanguage) : t(`experiment.${item.status}`)}</span>
        </button>)}
      </div>
      {specifications.length === 0 && <p className="factor-empty-note">{t("common.noData")}</p>}
      {data.filtered_specification_count > pageSize && <nav className="experiment-pagination" aria-label={chinese ? "实验结果分页" : "Experiment result pages"}>
        <button type="button" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))}>←</button>
        <span>{page} / {pageCount}<small>{data.filtered_specification_count} {chinese ? "条结果" : "results"}</small></span>
        <button type="button" disabled={page >= pageCount} onClick={() => setPage((value) => Math.min(pageCount, value + 1))}>→</button>
      </nav>}
    </section>

    {resultId && <div className="experiment-detail-backdrop" onMouseDown={closeDetail}>
      <aside className="experiment-detail-drawer" ref={detailDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={t("experiment.detail")} onMouseDown={(event) => event.stopPropagation()}>
    {detail.isLoading && <LoadingState />}
    {detail.error && <ErrorState error={detail.error} retry={() => void detail.refetch()} />}
    {detail.data && <section className="catalog-section experiment-detail">
      <div className="section-heading"><div><p className="eyebrow">IMMUTABLE RESULT AUDIT</p><h2>{t("experiment.detail")}</h2></div><div className="experiment-detail-actions"><Link className="arrow-link" to={`/artifacts/${detail.data.result_artifact_id}`}>{t("experiment.lineage")} →</Link><button type="button" aria-label={chinese ? "关闭实验详情" : "Close experiment detail"} onClick={closeDetail}>×</button></div></div>
      <div className="experiment-detail-strip"><div><span>{t("experiment.resolved")}</span><strong>{detail.data.resolved_start ?? "—"} → {detail.data.resolved_end ?? "—"}</strong></div><div><span>{t("experiment.observations")}</span><strong>{detail.data.observation_count}</strong></div><div><span>{t("experiment.run")}</span><strong>#{detail.data.specification.attempt_number} · {researchLabel(detail.data.run_status, i18n.resolvedLanguage)}</strong></div><div><span>{t("common.quality")}</span><strong>{researchLabel(detail.data.specification.quality_status, i18n.resolvedLanguage)}</strong></div></div>
      {detail.data.specification.template_key !== "predictive_diagnostic" && <div className="experiment-chart-grid">
        <ExperimentChart title={detail.data.specification.availability_status === "capacity_rejected" ? (chinese ? "总财富（容量拒绝前诊断路径）" : "Gross wealth (diagnostic path before capacity rejection)") : (chinese ? "净值曲线 vs SPY 基准" : "Net wealth vs SPY benchmark")} series={detail.data.nav_series} fields={detail.data.specification.availability_status === "capacity_rejected" ? ["strategy_wealth"] : ["strategy_wealth", "benchmark_wealth"]} />
        {detail.data.specification.availability_status !== "capacity_rejected" && <ExperimentChart title={chinese ? "超额净值" : "Excess wealth"} series={detail.data.nav_series} fields={["excess_wealth"]} />}
        <ExperimentChart title={chinese ? "回撤" : "Drawdown"} series={detail.data.nav_series} fields={["drawdown"]} />
      </div>}
      {detail.data.specification.template_key !== "predictive_diagnostic" && <article className="experiment-promotion-card">
        <div><p className="eyebrow">RESEARCH CANDIDATE GATE</p>
          {qualification.isLoading ? <h3>{chinese ? "正在核验升级资格…" : "Checking promotion eligibility…"}</h3> : qualification.error ? <ErrorState error={qualification.error} retry={() => void qualification.refetch()} /> : <>
            <h3>{qualification.data?.eligible ? (chinese ? "可升级为样本外研究候选" : "Eligible for OOS research-candidate promotion") : (chinese ? "暂不可升级为研究候选" : "Not currently eligible for research-candidate promotion")}</h3>
            {!qualification.data?.eligible && <p>{(qualification.data?.reason_codes ?? detail.data.promotion_reason_codes).map((code) => researchLabel(code, i18n.resolvedLanguage)).join(" · ")}</p>}
            {(qualification.data?.warning_codes ?? []).length > 0 && <p className="candidate-warning-count">{chinese ? "研究限制：" : "Research limitations: "}{(qualification.data?.warning_codes ?? []).map((code) => researchLabel(code, i18n.resolvedLanguage)).join(" · ")}</p>}
          </>}
        </div>
        <button type="button" disabled={qualification.isLoading || Boolean(qualification.error) || !qualification.data?.eligible} onClick={() => setPromotionOpen(true)}>{chinese ? "升级为 Product 候选" : "Promote to Product candidate"}</button>
      </article>}
      {promote.data && <p className="scope-note">{chinese ? "Product 候选已激活：" : "Product candidate activated: "}<Link to={`/products/${promote.data.product_enrollment_id}`}>{promote.data.product_enrollment_id}</Link></p>}
      <div className="experiment-detail-grid">
        <article><h3>{t("experiment.metrics")}</h3><div className="experiment-metrics">{detail.data.metrics.map((metric) => <div key={`${metric.series_role}-${metric.metric_key}`}><span>{researchLabel(metric.series_role, i18n.resolvedLanguage)} · {researchLabel(metric.metric_key, i18n.resolvedLanguage)}<code>{metric.metric_key}</code></span><strong>{metricValue(metric)}</strong></div>)}</div></article>
        <article><h3>{t("experiment.checks")}</h3>{detail.data.quality_checks.map((check) => <div className="experiment-audit-row" key={`${check.check_key}-${check.scope_key}`}><span className={`experiment-status ${check.status}`}>{researchLabel(check.status, i18n.resolvedLanguage)}</span><div><ResearchKey value={check.check_key} /><p>{check.message}</p></div></div>)}</article>
        <article><h3>{t("experiment.events")}</h3>{detail.data.events.map((event) => <div className="experiment-audit-row" key={event.sequence_number}><code>{event.sequence_number}</code><div><ResearchKey value={event.event_type} /><p>{event.message}</p></div></div>)}</article>
      </div>
    </section>}
    {detail.data && detail.data.specification.template_key !== "predictive_diagnostic" && <DecisionExplorer resultArtifactId={detail.data.result_artifact_id} />}
      </aside>
    </div>}
    {promotionOpen && <div className="promotion-modal-backdrop" onMouseDown={() => setPromotionOpen(false)}>
      <section className="promotion-modal" ref={promotionDialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={chinese ? "升级为 Product 研究候选" : "Promote to Product candidate"} onMouseDown={(event) => event.stopPropagation()}>
        <div className="section-heading"><div><p className="eyebrow">MANUAL PROMOTION</p><h2>{chinese ? "升级为 Product 研究候选" : "Promote to Product research candidate"}</h2></div><button type="button" aria-label={chinese ? "关闭升级弹窗" : "Close promotion dialog"} onClick={() => setPromotionOpen(false)}>×</button></div>
        <label>{chinese ? "候选名称" : "Candidate name"}<input value={productName} onChange={(event) => setProductName(event.target.value)} /></label>
        <label>{chinese ? "选择理由" : "Selection reason"}<textarea value={selectionReason} onChange={(event) => setSelectionReason(event.target.value)} /></label>
        <label>{chinese ? "备注（可选）" : "Note (optional)"}<textarea value={promotionNote} onChange={(event) => setPromotionNote(event.target.value)} /></label>
        {promote.error && <ErrorState error={promote.error} retry={() => promote.mutate()} />}
        <footer><button type="button" onClick={() => setPromotionOpen(false)}>{chinese ? "取消" : "Cancel"}</button><button type="button" disabled={!productName.trim() || !selectionReason.trim() || promote.isPending} onClick={() => promote.mutate()}>{chinese ? "确认升级并开始样本外观察" : "Confirm promotion and start OOS observation"}</button></footer>
      </section>
    </div>}
  </div>;
}

type NavPoint = Awaited<ReturnType<typeof api.experimentResult>>["nav_series"][number];
type NavField = "strategy_wealth" | "benchmark_wealth" | "excess_wealth" | "drawdown";

function ExperimentChart({ title, series, fields }: { title: string; series: NavPoint[]; fields: NavField[] }) {
  const { i18n } = useTranslation();
  if (series.length < 2) return <article className="experiment-chart"><h3>{title}</h3><p>{i18n.resolvedLanguage === "en" ? "No published path." : "暂无已发布路径。"}</p></article>;
  const values = series.flatMap((point) => fields.map((field) => point[field]));
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const span = maximum - minimum || 1;
  const paths = fields.map((field) => series.map((point, index) => {
    const x = index / (series.length - 1) * 100;
    const y = 42 - ((point[field] - minimum) / span) * 38;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" "));
  return <article className="experiment-chart"><h3>{title}</h3><svg viewBox="0 0 100 46" role="img" aria-label={title}>{paths.map((path, index) => <polyline key={fields[index]} className={`series-${index}`} points={path} />)}</svg><footer><span>{series[0].nav_date}</span><span>{series.at(-1)?.nav_date}</span></footer></article>;
}
