import i18n from "i18next";
import { initReactI18next } from "react-i18next";

export const supportedLanguages = ["zh-CN", "en"] as const;
export type SupportedLanguage = (typeof supportedLanguages)[number];

const resources = {
  "zh-CN": {
    translation: {
      brand: { name: "候鸟研究所", stage: "雏鸟阶段 · v0.2" },
      nav: {
        overview: "概览",
        dashboard: "研究台",
        research: "研究",
        assets: "资产",
        data: "数据",
        factors: "因子",
        signals: "信号",
        models: "模型",
        products: "产品",
        strategies: "策略",
        experiments: "实验",
        compare: "比较",
        system: "系统",
        artifacts: "发布物",
        runs: "运行记录",
        api: "API"
      },
      common: {
        loading: "正在读取已发布结果…",
        retry: "重新加载",
        noData: "暂无符合条件的已发布数据",
        readOnly: "只读研究环境",
        planned: "计划中",
        available: "已可用",
        version: "版本",
        status: "状态",
        quality: "质量",
        dependencies: "直接依赖",
        dependents: "直接下游",
        back: "返回发布物",
        open: "查看"
      },
      dashboard: {
        eyebrow: "RESEARCH FOUNDATION",
        title: "从可追溯的研究对象开始",
        subtitle: "当前阶段已建立版本身份、发布冻结和完整血缘。后续因子、信号、模型和策略将逐层接入同一条研究链。",
        systemHealth: "系统地基",
        publishedObjects: "已发布目录",
        currentScope: "当前能力边界",
        nextMilestone: "下一阶段",
        nextValue: "Catalog 与 Data",
        traceHint: "每个正式结果都可以回到准确的定义、内容哈希和上游版本。"
      },
      artifact: {
        title: "不可变发布物",
        subtitle: "这里显示研究对象的身份与质量，不把策略表现混入前置层级。",
        allStatuses: "正式与异常状态",
        key: "对象键",
        type: "类型",
        fingerprint: "语义指纹",
        contentHash: "内容哈希",
        lineage: "血缘清单",
        manifest: "Manifest 哈希",
        canonical: "序列化规则"
      },
      assets: {
        title: "资产与研究范围",
        subtitle: "资产目录定义可交易对象的稳定身份、上市信息与分类；研究宇宙再明确候选资产和产品基准。这里不展示策略收益。",
        universe: "研究宇宙",
        asOf: "目录截至",
        candidates: "候选资产",
        benchmark: "产品基准",
        members: "当前宇宙成员",
        style: "风格分类",
        listing: "上市地",
        calendar: "交易日历",
        requirements: "后续数据必须满足的契约",
        rateNote: "DGS3MO 是现金收益模拟所需的参考利率序列，不是可交易资产，也不属于研究宇宙。"
      },
      api: {
        title: "只读 API",
        subtitle: "OpenAPI 是前后端共享契约。计算、发布、失效和重建仍只通过 CLI 执行。",
        docs: "打开交互式 API 文档",
        contract: "下载 OpenAPI JSON",
        ruleTitle: "边界规则",
        ruleBody: "API 只暴露 GET 查询，不在浏览器中提供任何写入或研究计算入口。"
      },
      planned: {
        title: "页面结构已经预留",
        body: "该领域将在 {{milestone}} 交付真实数据库、服务、API 与页面。现在不展示模拟研究结果。"
      },
      states: {
        ok: "正常",
        partial: "不完整",
        warning: "需注意",
        error: "不可用",
        published: "已发布",
        draft: "草稿",
        retired: "已退役",
        superseded: "已替代",
        tainted: "受污染",
        invalidated: "已失效"
      }
    }
  },
  en: {
    translation: {
      brand: { name: "Migration Lab", stage: "Fledgling stage · v0.2" },
      nav: {
        overview: "Overview",
        dashboard: "Research desk",
        research: "Research",
        assets: "Assets",
        data: "Data",
        factors: "Factors",
        signals: "Signals",
        models: "Models",
        products: "Products",
        strategies: "Strategies",
        experiments: "Experiments",
        compare: "Compare",
        system: "System",
        artifacts: "Artifacts",
        runs: "Runs",
        api: "API"
      },
      common: {
        loading: "Reading published results…",
        retry: "Reload",
        noData: "No matching published data",
        readOnly: "Read-only research environment",
        planned: "Planned",
        available: "Available",
        version: "Version",
        status: "Status",
        quality: "Quality",
        dependencies: "Direct dependencies",
        dependents: "Direct dependents",
        back: "Back to artifacts",
        open: "Open"
      },
      dashboard: {
        eyebrow: "RESEARCH FOUNDATION",
        title: "Begin with traceable research objects",
        subtitle: "Version identity, publication freeze, and complete lineage are live. Factors, signals, models, and strategies will join the same chain milestone by milestone.",
        systemHealth: "System foundation",
        publishedObjects: "Published catalogs",
        currentScope: "Current capability boundary",
        nextMilestone: "Next milestone",
        nextValue: "Catalog & Data",
        traceHint: "Every formal result can return to its exact definition, content hash, and upstream versions."
      },
      artifact: {
        title: "Immutable artifacts",
        subtitle: "Research identity and quality live here; strategy performance is not projected onto upstream layers.",
        allStatuses: "Formal and exceptional states",
        key: "Object key",
        type: "Type",
        fingerprint: "Semantic fingerprint",
        contentHash: "Content hash",
        lineage: "Lineage manifest",
        manifest: "Manifest hash",
        canonical: "Serialization rule"
      },
      assets: {
        title: "Assets and research scope",
        subtitle: "The catalog defines stable tradable identities, listings, and classifications. The universe then assigns candidate and product-benchmark roles without mixing in strategy performance.",
        universe: "Research universe",
        asOf: "Catalog as of",
        candidates: "Candidates",
        benchmark: "Product benchmark",
        members: "Current universe members",
        style: "Style exposure",
        listing: "Listing",
        calendar: "Trading calendar",
        requirements: "Required downstream data contract",
        rateNote: "DGS3MO is a reference-rate series used to simulate cash returns. It is not a tradable asset and is not a universe member."
      },
      api: {
        title: "Read-only API",
        subtitle: "OpenAPI is the shared frontend/backend contract. Calculation, publication, invalidation, and rebuild remain CLI-only.",
        docs: "Open interactive API docs",
        contract: "Download OpenAPI JSON",
        ruleTitle: "Boundary rule",
        ruleBody: "The API exposes GET queries only. The browser has no write or research-computation entry point."
      },
      planned: {
        title: "The page boundary is reserved",
        body: "This domain receives its real database, service, API, and page in {{milestone}}. No simulated research results are shown now."
      },
      states: {
        ok: "Healthy",
        partial: "Partial",
        warning: "Attention",
        error: "Unavailable",
        published: "Published",
        draft: "Draft",
        retired: "Retired",
        superseded: "Superseded",
        tainted: "Tainted",
        invalidated: "Invalidated"
      }
    }
  }
} as const;

function initialLanguage(): SupportedLanguage {
  const urlLanguage = new URLSearchParams(window.location.search).get("lang");
  if (urlLanguage === "en" || urlLanguage === "zh-CN") return urlLanguage;
  const stored = window.localStorage.getItem("style-rotation-language");
  return stored === "en" ? "en" : "zh-CN";
}

void i18n.use(initReactI18next).init({
  resources,
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export async function setLanguage(language: SupportedLanguage): Promise<void> {
  window.localStorage.setItem("style-rotation-language", language);
  const url = new URL(window.location.href);
  url.searchParams.set("lang", language);
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  document.documentElement.lang = language;
  await i18n.changeLanguage(language);
}

export default i18n;
