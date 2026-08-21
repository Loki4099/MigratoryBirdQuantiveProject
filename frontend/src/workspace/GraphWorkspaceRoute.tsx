import { useTranslation } from "react-i18next";
import { Outlet } from "react-router-dom";

import { GraphWorkspacePage } from "../pages/GraphWorkspacePage";
import { useV022ReleaseControl } from "../release/useV022ReleaseControl";
import { GraphDraftProvider } from "./GraphDraftContext";

export type GraphWorkspaceRouteView = "context" | 1 | 2 | 3 | "aggregation" | "strategy" | "launch";

export default function GraphWorkspaceRoute() {
  const { i18n } = useTranslation();
  const chinese = (i18n.resolvedLanguage ?? "zh-CN") === "zh-CN";
  const release = useV022ReleaseControl();
  if (release.isLoading) return <p role="status">{chinese ? "正在检查 v0.22 发布状态…" : "Checking v0.22 release state…"}</p>;
  if (release.error || release.data?.v022_explicit_creation_allowed !== true) {
    const maintenance = release.data?.state === "maintenance_read_only";
    const explanation = release.error
      ? (chinese ? "无法验证权威发布状态，因此研究编辑按失败关闭处理。" : "The authoritative release state could not be verified, so research editing is fail-closed.")
      : maintenance
      ? (chinese ? "系统处于维护只读状态；历史读取保留，但所有新研究写入均被禁止。" : "The system is in maintenance read-only mode. Historical reads remain available, but all new research mutations are blocked.")
      : (chinese
        ? "当前发布状态只允许 v0.21 研究创建。完成真实 Shadow Plan 与 Parity Gate 后，这里会开放三个加工层、聚合层，以及含检查与编译的策略页面。"
        : "The current release state only permits v0.21 research creation. The three processing layers, aggregation, and strategy page with review and compile open after genuine Shadow Plan and Parity Gate evidence.");
    return <div className="page graph-workspace-page">
      <header className="page-heading"><div>
        <p className="eyebrow">RELEASE CONTROL / v0.22</p>
        <h1>{chinese ? "v0.22 研究编辑尚未开放" : "v0.22 research editing is not open"}</h1>
        <p>{explanation}</p>
      </div></header>
      <section className="workspace-release-gate">
        <strong>{release.data?.state ?? "unavailable"}</strong>
        <span>{chinese ? "未创建 Graph Draft，也未绕过发布门禁。" : "No Graph Draft was created and no release gate was bypassed."}</span>
      </section>
    </div>;
  }
  return <GraphDraftProvider><Outlet /></GraphDraftProvider>;
}

export function GraphWorkspaceViewRoute({ view }: { view: GraphWorkspaceRouteView }) {
  return <GraphWorkspacePage view={view} />;
}
