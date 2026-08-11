import { useQuery } from "@tanstack/react-query";
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { api, type AssetSeriesResponse } from "../api/client";
import { EmptyState, ErrorState, LoadingState, QualityBadge } from "../components/QueryState";
import { useWorkspaceSelection } from "../workspace/WorkspaceSelectionContext";

type AssetItem = NonNullable<Awaited<ReturnType<typeof api.assets>>>["items"][number];

export function AssetsPage() {
  const { i18n } = useTranslation();
  const isZh = i18n.resolvedLanguage !== "en";
  const copy = isZh ? zhCopy : enCopy;
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search.trim());
  const [category, setCategory] = useState("");
  const [maturity, setMaturity] = useState("");
  const [detail, setDetail] = useState<AssetItem | null>(null);
  const workspace = useWorkspaceSelection();
  const assets = useQuery({
    queryKey: ["catalog", "assets", deferredSearch, category, maturity],
    queryFn: () => api.allAssets({
      search: deferredSearch || undefined,
      category: category || undefined,
      maturity: maturity || undefined,
    }),
  });
  const series = useQuery({
    queryKey: ["catalog", "asset-series", detail?.security_id],
    queryFn: () => api.assetSeries(detail!.security_id),
    enabled: Boolean(detail?.canonical_data_available),
  });

  if (assets.isLoading) return <LoadingState />;
  if (assets.error) return <ErrorState error={assets.error} retry={() => void assets.refetch()} />;
  if (!assets.data) return <EmptyState />;

  const toggle = (item: AssetItem) => {
    if (!item.selectable) return;
    workspace.toggleAsset(item.security_id);
  };
  const selectableVisible = assets.data.items.filter((item) => item.selectable).map((item) => item.security_id);
  const allVisibleSelected = selectableVisible.length > 0 && selectableVisible.every((id) => workspace.assetSecurityIds.includes(id));

  return (
    <div className="page asset-catalog-page">
      <header className="page-heading">
        <div>
          <p className="eyebrow">CATALOG / STABLE SECURITY IDENTITY</p>
          <h1>{copy.title}</h1>
          <p>{copy.subtitle}</p>
        </div>
        <QualityBadge state={assets.data.quality.state} />
      </header>

      <section className="scope-strip asset-summary-strip">
        <div><span>{copy.release}</span><strong>v{assets.data.catalog_version}</strong></div>
        <div><span>{copy.asOf}</span><strong>{assets.data.as_of_date}</strong></div>
        <div><span>{copy.catalogSize}</span><strong>{category ? assets.data.total : sumCategories(assets.data.categories)}</strong></div>
        <div><span>{copy.matches}</span><strong>{assets.data.total}</strong></div>
        <div><span>{copy.selected}</span><strong>{workspace.assetSecurityIds.length}</strong></div>
      </section>

      <section className="asset-controls" aria-label={copy.filters}>
        <label className="asset-search">
          <span>{copy.search}</span>
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={copy.searchHint}
            type="search"
          />
        </label>
        <label>
          <span>{copy.maturity}</span>
          <select value={maturity} onChange={(event) => setMaturity(event.target.value)}>
            <option value="">{copy.all}</option>
            <option value="cataloged">Cataloged</option>
            <option value="reference_data">Reference data</option>
            <option value="canonical_ready">Canonical ready</option>
            <option value="research_ready">Research ready</option>
            <option value="strategy_ready">Strategy ready</option>
          </select>
        </label>
      </section>

      <nav className="asset-category-tabs" aria-label={copy.categories}>
        <button className={!category ? "active" : ""} onClick={() => setCategory("")}>
          {copy.all} <span>{sumCategories(assets.data.categories)}</span>
        </button>
        {assets.data.categories.map((item) => (
          <button
            className={category === item.category_key ? "active" : ""}
            key={item.category_key}
            onClick={() => setCategory(item.category_key)}
            title={item.description}
          >
            {item.name} <span>{item.asset_count}</span>
          </button>
        ))}
      </nav>

      {assets.data.asset_sets.length > 0 && !search && !category && !maturity ? (
        <section className="asset-set-strip">
          {assets.data.asset_sets.map((item) => (
            <article key={item.set_key}>
              <div><span>{item.set_type.replaceAll("_", " ")}</span><b>{item.formal_eligible ? copy.formal : copy.exploratory}</b></div>
              <h3>{item.name}</h3>
              <p>{item.notes}</p>
              <code>{item.member_security_ids.length || copy.dynamic} {copy.members}</code>
            </article>
          ))}
        </section>
      ) : null}

      <section className="catalog-section">
        <div className="section-heading">
          <div><p className="eyebrow">SEARCHABLE ASSET CARDS</p><h2>{copy.assets}</h2></div>
          <div className="asset-select-all"><label><input type="checkbox" checked={allVisibleSelected} disabled={!selectableVisible.length} onChange={(event) => workspace.setAssets(selectableVisible, event.target.checked)} /><span>{isZh ? (category ? "全选当前分类" : "全选当前筛选结果") : (category ? "Select this category" : "Select filtered assets")}</span></label><code>{assets.data.release_artifact_id.slice(0, 8)}</code></div>
        </div>
        {!assets.data.items.length ? <EmptyState /> : (
          <div className="asset-grid asset-registry-grid">
            {assets.data.items.map((item) => (
              <article
                className={`asset-card registry-card ${item.selectable ? "" : "disabled"}`}
                key={item.security_id}
              >
                <div className="asset-symbol">
                  <button onClick={() => setDetail(item)}><strong>{item.symbol}</strong></button>
                  <label title={item.selectable ? copy.select : copy.blocked}>
                    <input
                      checked={workspace.assetSecurityIds.includes(item.security_id)}
                      disabled={!item.selectable}
                      onChange={() => toggle(item)}
                      type="checkbox"
                    />
                    <span>{item.selectable ? copy.select : copy.blocked}</span>
                  </label>
                </div>
                <button className="asset-card-open" onClick={() => setDetail(item)}>
                  <h3>{item.name}</h3>
                  <p>{item.aliases.join(" · ") || item.asset_key}</p>
                </button>
                <div className="asset-tags">
                  <span>{item.category_key.replaceAll("_", " ")}</span>
                  <span>{item.maturity.replaceAll("_", " ")}</span>
                  <span className={item.canonical_data_available ? "ready" : "missing"}>
                    {item.canonical_data_available ? copy.dataReady : copy.noData}
                  </span>
                </div>
                <dl>
                  <div><dt>{copy.type}</dt><dd>{item.instrument_type}</dd></div>
                  <div><dt>{copy.listing}</dt><dd>{item.venue_mic ?? "—"} · {item.currency ?? "—"}</dd></div>
                  <div><dt>{copy.target}</dt><dd>{item.target_maturity.replaceAll("_", " ")}</dd></div>
                </dl>
              </article>
            ))}
          </div>
        )}
      </section>

      {detail ? (
        <AssetDetail
          copy={copy}
          item={detail}
          onClose={() => setDetail(null)}
          series={series.data}
          seriesError={series.error}
          seriesLoading={series.isLoading}
        />
      ) : null}
    </div>
  );
}

function AssetDetail({ copy, item, onClose, series, seriesError, seriesLoading }: {
  copy: typeof enCopy;
  item: AssetItem;
  onClose: () => void;
  series?: AssetSeriesResponse;
  seriesError: Error | null;
  seriesLoading: boolean;
}) {
  const workspace = useWorkspaceSelection();
  const dialogRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.body.style.overflow;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.body.style.overflow = "hidden";
    dialogRef.current?.focus();
    return () => {
      document.body.style.overflow = previous;
      previousFocus?.focus();
    };
  }, []);

  return createPortal(
    <div className="asset-detail-backdrop" role="presentation" onMouseDown={onClose}>
      <aside className="asset-detail-panel" ref={dialogRef} tabIndex={-1} role="dialog" aria-modal="true" aria-label={`${item.symbol} ${copy.dataInputs}`} onKeyDown={(event) => { if (event.key === "Escape") onClose(); }} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <div><p className="eyebrow">ASSET / {item.category_key}</p><h2>{item.symbol}</h2><p>{item.name}</p></div>
          <button aria-label={copy.close} onClick={onClose}>×</button>
        </header>
        <div className="asset-detail-facts">
          <div><span>{copy.identity}</span><code>{item.asset_key}</code></div>
          <div><span>{copy.maturity}</span><strong>{item.maturity.replaceAll("_", " ")}</strong></div>
          <div><span>{copy.tradability}</span><strong>{item.tradability.replaceAll("_", " ")}</strong></div>
          <div><span>{copy.calendar}</span><strong>{item.calendar_key ?? "—"}</strong></div>
        </div>
        {item.missing_requirements.length ? (
          <div className="asset-gap"><strong>{copy.gaps}</strong><p>{item.missing_requirements.join(" · ")}</p></div>
        ) : null}
        <section className="asset-data-inputs">
          <div><p className="eyebrow">DATA INPUTS / DOWNSTREAM FACTORS</p><h3>{copy.dataInputs}</h3><p>{copy.dataInputsNote}</p></div>
          {(item.data_inputs ?? []).map((input) => {
            const selected = (workspace.assetDataInputs[item.security_id] ?? []).includes(input.input_key);
            return <label className={input.available && input.selectable ? "available" : "planned"} key={input.input_key}>
            <input type="checkbox" checked={selected} disabled={!input.available || !input.selectable || (!item.selectable && !selected)} onChange={() => workspace.toggleAssetInput(item.security_id, input.input_key)} />
            <span><strong>{input.name}</strong><small>{input.status_note}</small><code>{input.downstream_factor_keys.join(" · ")}</code></span>
            <b>{selected ? copy.inputSelected : input.available ? copy.published : copy.incubating}</b>
          </label>;
          })}
        </section>
        {!item.canonical_data_available ? (
          <div className="asset-series-empty"><strong>{copy.noSeriesTitle}</strong><p>{copy.noSeriesBody}</p></div>
        ) : seriesLoading ? <LoadingState /> : seriesError ? <ErrorState error={seriesError} /> : series ? (
          <section className="asset-series-section">
            <div className="section-heading">
              <div><p className="eyebrow">CANONICAL / ADJUSTED SERIES</p><h3>{copy.priceChart}</h3></div>
              <a className="download-button" href={`/api/v2/catalog/assets/${item.security_id}/download.csv`}>
                {copy.download}
              </a>
            </div>
            <PriceChart series={series} />
            <div className="asset-series-meta">
              <span>{series.coverage_start} → {series.coverage_end}</span>
              <span>{series.points.length} {copy.observations}</span>
              <code>{series.dataset_artifact_id.slice(0, 8)}</code>
            </div>
          </section>
        ) : null}
      </aside>
    </div>,
    document.body,
  );
}

function PriceChart({ series }: { series: AssetSeriesResponse }) {
  const geometry = useMemo(() => {
    const values = series.points.map((point) => point.adjusted_close);
    const minimum = Math.min(...values);
    const maximum = Math.max(...values);
    const range = maximum - minimum || 1;
    const denominator = Math.max(values.length - 1, 1);
    const points = values.map((value, index) =>
      `${(index / denominator) * 100},${38 - ((value - minimum) / range) * 34}`,
    ).join(" ");
    return { minimum, maximum, points };
  }, [series]);
  return (
    <figure className="asset-price-chart">
      <svg viewBox="0 0 100 42" preserveAspectRatio="none" role="img" aria-label="Adjusted close history">
        <line x1="0" x2="100" y1="38" y2="38" />
        <line x1="0" x2="100" y1="4" y2="4" />
        <polyline points={geometry.points} />
      </svg>
      <figcaption><span>{geometry.minimum.toFixed(2)}</span><span>{geometry.maximum.toFixed(2)}</span></figcaption>
    </figure>
  );
}

function sumCategories(categories: { asset_count: number }[]) {
  return categories.reduce((sum, item) => sum + item.asset_count, 0);
}

const enCopy = {
  dataInputs: "Available research inputs",
  dataInputsNote: "Only published point-in-time inputs may flow downstream. Fundamental PE/ROE stays disabled until filing-time history is available.",
  published: "published",
  inputSelected: "selected",
  incubating: "incubating",
  title: "Assets and research capability", subtitle: "Browse stable security identities by class. Cards distinguish display coverage from canonical-data and research eligibility; no strategy return is shown here.", release: "Registry release", asOf: "As of", catalogSize: "Catalog assets", matches: "Matches", selected: "Selected", filters: "Asset filters", search: "Search", searchHint: "AAPL, Apple, APPL, technology…", maturity: "Current maturity", all: "All", categories: "Asset categories", formal: "Formal", exploratory: "Exploratory", dynamic: "dynamic", members: "members", assets: "Asset directory", select: "Select", blocked: "Unavailable", dataReady: "market data ready", noData: "no canonical market data", type: "Instrument", listing: "Listing", target: "Target maturity", close: "Close", identity: "Stable key", tradability: "Tradability", calendar: "Calendar", gaps: "Capability gaps", noSeriesTitle: "No published cleaned series", noSeriesBody: "This object remains visible in the catalog, but chart, download, and research selection stay disabled until a canonical dataset is published.", priceChart: "Adjusted price history", download: "Download cleaned CSV", observations: "observations",
};
const zhCopy: typeof enCopy = {
  dataInputs: "可进入研究的数据",
  dataInputsNote: "只有已发布且满足时点语义的数据才能进入下游。PE/ROE 在申报时点历史发布前保持禁用。",
  published: "已发布",
  inputSelected: "已选择",
  incubating: "孵化中",
  title: "资产与研究能力", subtitle: "按资产类别浏览稳定身份。卡片明确区分“目录展示”“已有清洗行情”和“可进入研究”，本层不展示任何策略收益。", release: "目录版本", asOf: "目录截至", catalogSize: "目录资产", matches: "当前命中", selected: "已勾选", filters: "资产筛选", search: "搜索", searchHint: "AAPL、Apple、APPL、technology…", maturity: "当前成熟度", all: "全部", categories: "资产分类", formal: "正式", exploratory: "探索", dynamic: "动态", members: "个成员", assets: "资产目录", select: "勾选", blocked: "不可选", dataReady: "行情可用", noData: "无清洗行情", type: "工具类型", listing: "上市信息", target: "目标成熟度", close: "关闭", identity: "稳定标识", tradability: "可交易属性", calendar: "交易日历", gaps: "能力缺口", noSeriesTitle: "尚无已发布清洗序列", noSeriesBody: "该对象仍可在目录中展示，但在规范数据集发布前，图表、下载和研究勾选均保持禁用。", priceChart: "复权价格历史", download: "下载清洗 CSV", observations: "条观测",
};
