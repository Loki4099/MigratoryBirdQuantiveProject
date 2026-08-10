import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { api } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";

type Suite = Awaited<ReturnType<typeof api.experimentOverview>>["suites"][number];

const isV021Suite = (suite: Suite): suite is Suite & { research_suite_id: string } =>
  typeof suite.research_suite_id === "string" && suite.research_suite_id.length > 0;

const statusLabel = (key: string, chinese: boolean) => {
  if (!chinese) return key;
  return ({ queued: "排队", running: "运行中", completed: "完成", accepted: "已接受", failed: "失败", cancelled: "已取消" } as Record<string, string>)[key] ?? key;
};

export function RunsPage() {
  const { i18n } = useTranslation();
  const chinese = i18n.resolvedLanguage !== "en";
  const overview = useQuery({
    queryKey: ["runs", "suites"],
    queryFn: () => api.experimentOverview({ limit: 1, offset: 0 }),
    refetchInterval: 5_000,
  });
  // Legacy v0.2 Experiment Suites expose only an Artifact ID. Runs is a queue view,
  // so it must never send those IDs to the v0.21 research-suite status endpoint.
  const suites = (overview.data?.suites ?? []).filter(isV021Suite);
  const statuses = useQueries({
    queries: suites.map((suite) => ({
      queryKey: ["workspace", "suite", suite.research_suite_id],
      queryFn: () => api.workspaceSuiteStatus(suite.research_suite_id),
      refetchInterval: (query: { state: { data?: { complete: boolean } } }) => query.state.data?.complete ? false : 2_000,
    })),
  });

  if (overview.isLoading) return <LoadingState />;
  if (overview.error) return <ErrorState error={overview.error} retry={() => void overview.refetch()} />;
  if (!overview.data) return <EmptyState />;

  const active = statuses.filter((item) => item.data && !item.data.complete).length;
  return <div className="page runs-page">
    <header className="page-heading">
      <div><p className="eyebrow">PERSISTENT QUEUE / RESEARCH SUITES</p><h1>{chinese ? "运行记录" : "Runs"}</h1><p>{chinese ? "查看当前与最近实验批次的持久队列状态；离开实验页面不会丢失进度。" : "Track persistent queue state for current and recent Suites. Progress remains visible after leaving Experiments."}</p></div>
      <QualityBadge state={active > 0 ? "partial" : "ok"} />
    </header>
    <section className="scope-strip"><div><span>{chinese ? "可见实验套件" : "Visible Suites"}</span><strong>{suites.length}</strong></div><div><span>{chinese ? "运行中" : "Active"}</span><strong>{active}</strong></div><div><span>{chinese ? "队列来源" : "Queue source"}</span><strong>PostgreSQL</strong></div></section>
    {suites.length === 0 ? <EmptyState /> : <section className="run-suite-list">{suites.map((suite, index) => {
      const status = statuses[index];
      const total = status.data?.total ?? suite.specification_count;
      const terminal = status.data?.terminal ?? 0;
      const progress = total ? Math.min(100, terminal / total * 100) : 0;
      const experimentSearch = new URLSearchParams({ suite: suite.research_suite_id, lang: chinese ? "zh-CN" : "en" });
      return <article key={suite.research_suite_id}>
        <div><span>{suite.suite_key} · v{suite.version_number}</span><h2>{suite.name}</h2><p>{suite.description}</p><small>{chinese ? "研究 Suite ID" : "Research Suite ID"}</small><code>{suite.research_suite_id}</code></div>
        {status.error ? <ErrorState error={status.error} retry={() => void status.refetch()} /> : <div className="run-suite-status"><strong>{status.isLoading ? (chinese ? "正在读取" : "Loading") : status.data?.complete ? (chinese ? "已完成" : "Complete") : (chinese ? "运行中" : "Active")}</strong><span>{terminal} / {total}</span><div className="experiment-progress-track" role="progressbar" aria-label={chinese ? `${suite.name} 回测进度` : `${suite.name} backtest progress`} aria-valuemin={0} aria-valuemax={total} aria-valuenow={terminal}><i style={{ width: `${progress}%` }} /></div><small>{Object.entries(status.data?.status_counts ?? {}).map(([key, count]) => `${statusLabel(key, chinese)} ${count}`).join(" · ") || (status.isLoading ? (chinese ? "正在读取…" : "Loading…") : "—")}</small></div>}
        <Link className="arrow-link" to={`/experiments?${experimentSearch.toString()}`}>{chinese ? "查看实验与进度" : "Open experiment and progress"} →</Link>
      </article>;
    })}</section>}
  </div>;
}
