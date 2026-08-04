import { useTranslation } from "react-i18next";

type Language = "zh-CN" | "en";

const labels: Record<Language, Record<string, string>> = {
  "zh-CN": {
    amihud_illiquidity: "Amihud 非流动性",
    downside_deviation: "下行波动率",
    lagged_return: "跳过近期的历史收益",
    maximum_drawdown: "区间最大回撤",
    moving_average_ratio: "移动平均线比率",
    ppo_histogram: "PPO 柱状差",
    realized_volatility: "已实现波动率",
    relative_dollar_volume: "相对成交额",
    return_excess_kurtosis: "收益超额峰度",
    return_skewness: "收益偏度",
    rsi: "相对强弱指标（RSI）",
    total_return: "区间总收益",
    liquidity: "成交量与流动性",
    oscillator: "反转与超买超卖",
    return: "动量与收益",
    risk: "波动与风险",
    tail_distribution: "尾部分布",
    trend: "趋势",
    weekly: "周频",
    monthly: "月频",
    canonical: "经典参数",
    horizon_anchor: "期限锚点",
    sensitivity: "敏感性参数",
    exploratory: "探索参数",
    positive: "正向",
    negative: "反向",
    continuous: "连续分数",
    directional: "方向信号",
    selected_by_rank: "按模型排名入选",
    excluded_by_rank: "未进入目标排名",
    blocked_by_trend: "趋势过滤未通过",
    positive_trend: "趋势为正",
    negative_trend: "趋势为负",
    no_trend_filter: "未启用趋势过滤",
    weighted_sum: "加权求和",
    weighted_mean: "加权平均",
    directional_vote: "方向投票",
    equal_weight: "等权聚合",
    spy_buy_and_hold: "SPY 买入并持有",
    full_history: "全历史区间",
    accepted: "已接受",
    failed: "失败",
    pending: "待运行",
    running: "运行中",
    defined: "已定义",
    completed: "已完成",
    passed: "通过",
    selected: "已入选",
    below_cutoff: "低于入选边界",
    very_short_sample_warning: "样本极短，结果仅供流程验证",
    strategy: "策略",
    benchmark: "基准",
    relative: "相对基准",
    cumulative_return: "累计收益",
    cagr: "年化复合收益（CAGR）",
    annualized_volatility: "年化波动率",
    sharpe_ratio: "Sharpe 比率",
    sortino_ratio: "Sortino 比率",
    maximum_drawdown_duration_days: "最大回撤持续天数",
    calmar_ratio: "Calmar 比率",
    positive_daily_return_ratio: "日收益为正比例",
    best_daily_return: "最佳单日收益",
    worst_daily_return: "最差单日收益",
    positive_monthly_return_ratio: "月收益为正比例",
    best_monthly_return: "最佳单月收益",
    worst_monthly_return: "最差单月收益",
    cumulative_relative_return: "累计相对收益",
    annualized_relative_wealth_growth: "相对财富年化增长",
    cagr_spread: "CAGR 差值",
    tracking_error: "跟踪误差",
    information_ratio: "信息比率",
    return_correlation: "收益相关性",
    beta: "Beta",
    annualized_alpha: "年化 Alpha",
    accepted_result_inputs: "正式结果输入已固定",
    all_outputs_published: "全部输出已发布",
    interval_availability: "实验区间可用",
    run_started: "实验运行开始",
    artifact_published: "发布物已生成",
    quality_checks_completed: "质量检查完成",
    run_completed: "实验运行完成",
    momentum_trend: "动量与趋势",
    trailing_10_years: "最近 10 年",
    weekly_next_open_to_next_open: "下周开盘至再下周开盘收益",
    monthly_next_open_to_next_open: "下月开盘至再下月开盘收益",
    higher_is_better: "数值越高，信号越强",
    lower_is_better: "数值越低，信号越强",
    attention_liquidity: "关注度与流动性",
    downside_risk: "下行风险",
    drawdown_risk: "回撤风险",
    liquidity_premium: "流动性溢价",
    liquidity_quality: "流动性质量",
    low_risk: "低风险",
    momentum: "动量",
    reversal: "反转",
    tail_premium: "尾部溢价",
    tail_regime: "尾部状态",
    tail_risk: "尾部风险",
  },
  en: {
    amihud_illiquidity: "Amihud illiquidity",
    downside_deviation: "Downside deviation",
    lagged_return: "Skip-period return",
    maximum_drawdown: "Window maximum drawdown",
    moving_average_ratio: "Moving-average ratio",
    ppo_histogram: "PPO histogram",
    realized_volatility: "Realized volatility",
    relative_dollar_volume: "Relative dollar volume",
    return_excess_kurtosis: "Return excess kurtosis",
    return_skewness: "Return skewness",
    rsi: "Relative Strength Index (RSI)",
    total_return: "Total return",
    liquidity: "Volume and liquidity",
    oscillator: "Reversal and oscillator",
    return: "Momentum and return",
    risk: "Volatility and risk",
    tail_distribution: "Tail distribution",
    trend: "Trend",
    canonical: "Canonical preset",
    horizon_anchor: "Horizon anchor",
    sensitivity: "Sensitivity preset",
    exploratory: "Exploratory preset",
    positive: "Positive",
    negative: "Negative",
    continuous: "Continuous score",
    directional: "Directional signal",
    selected_by_rank: "Selected by model rank",
    excluded_by_rank: "Outside target rank",
    blocked_by_trend: "Blocked by trend filter",
    positive_trend: "Positive trend",
    negative_trend: "Negative trend",
    no_trend_filter: "No trend filter",
    weighted_sum: "Weighted sum",
    weighted_mean: "Weighted mean",
    directional_vote: "Directional vote",
    equal_weight: "Equal-weight aggregation",
    spy_buy_and_hold: "SPY buy and hold",
    full_history: "Full history",
    accepted: "Accepted",
    failed: "Failed",
    pending: "Pending",
    running: "Running",
    defined: "Defined",
    completed: "Completed",
    passed: "Passed",
    selected: "Selected",
    below_cutoff: "Below selection cutoff",
    very_short_sample_warning: "Very short sample; workflow validation only",
    strategy: "Strategy",
    benchmark: "Benchmark",
    relative: "Relative",
    cumulative_return: "Cumulative return",
    cagr: "Compound annual growth rate (CAGR)",
    annualized_volatility: "Annualized volatility",
    sharpe_ratio: "Sharpe ratio",
    sortino_ratio: "Sortino ratio",
    maximum_drawdown_duration_days: "Maximum drawdown duration (days)",
    calmar_ratio: "Calmar ratio",
    positive_daily_return_ratio: "Positive daily return ratio",
    best_daily_return: "Best daily return",
    worst_daily_return: "Worst daily return",
    positive_monthly_return_ratio: "Positive monthly return ratio",
    best_monthly_return: "Best monthly return",
    worst_monthly_return: "Worst monthly return",
    cumulative_relative_return: "Cumulative relative return",
    annualized_relative_wealth_growth: "Annualized relative wealth growth",
    cagr_spread: "CAGR spread",
    tracking_error: "Tracking error",
    information_ratio: "Information ratio",
    return_correlation: "Return correlation",
    beta: "Beta",
    annualized_alpha: "Annualized alpha",
    accepted_result_inputs: "Accepted-result inputs",
    all_outputs_published: "All outputs published",
    interval_availability: "Interval availability",
    run_started: "Experiment run started",
    artifact_published: "Artifact published",
    quality_checks_completed: "Quality checks completed",
    run_completed: "Experiment run completed",
    momentum_trend: "Momentum and trend",
    trailing_10_years: "Trailing 10 years",
    weekly_next_open_to_next_open: "Next weekly open-to-open return",
    monthly_next_open_to_next_open: "Next monthly open-to-open return",
    higher_is_better: "Higher values rank better",
    lower_is_better: "Lower values rank better",
    attention_liquidity: "Attention and liquidity",
    downside_risk: "Downside risk",
    drawdown_risk: "Drawdown risk",
    liquidity_premium: "Liquidity premium",
    liquidity_quality: "Liquidity quality",
    low_risk: "Low risk",
    momentum: "Momentum",
    reversal: "Reversal",
    tail_premium: "Tail premium",
    tail_regime: "Tail regime",
    tail_risk: "Tail risk",
  },
};

const formulaGuides: Record<string, { notation: string; zh: string; en: string }> = {
  amihud_illiquidity: { notation: "mean(|rₜ| ÷ (Pₜ × Vₜ), w)", zh: "窗口内，单位成交额对应的绝对收益变动；数值越高通常表示越不易交易。", en: "Mean absolute return per unit of dollar volume; higher values generally indicate lower liquidity." },
  downside_deviation: { notation: "√(252 × mean(min(rₜ, 0)², w))", zh: "只保留负收益并年化，衡量投资者更关心的下行波动。", en: "Annualized volatility using negative returns only, focusing on downside risk." },
  lagged_return: { notation: "Pₜ₋ₛ ÷ Pₜ₋ₗ − 1", zh: "衡量较早区间的累计收益，并跳过最近一段时间以分离短期反转。", en: "Cumulative return over an older interval, skipping the most recent period to separate short-term reversal." },
  maximum_drawdown: { notation: "|min(Pₜ ÷ running max(P) − 1)|", zh: "窗口内价格相对此前峰值的最大跌幅。", en: "Largest peak-to-trough decline inside the window." },
  moving_average_ratio: { notation: "SMA(P, s) ÷ SMA(P, l) − 1", zh: "比较短期与长期均价；正值表示短期价格水平更强。", en: "Compares short- and long-window average prices; a positive value indicates stronger recent price levels." },
  ppo_histogram: { notation: "PPO − EMA(PPO, signal)", zh: "比较快慢指数均线的百分比差，再减去其信号线，用于观察趋势加速或减速。", en: "Percentage difference between fast and slow EMAs minus its signal line, indicating trend acceleration or deceleration." },
  realized_volatility: { notation: "stdev(rₜ, w) × √252", zh: "窗口内对数收益的样本标准差，并按 252 个交易日年化。", en: "Sample standard deviation of log returns, annualized using 252 trading days." },
  relative_dollar_volume: { notation: "(Pₜ × Vₜ) ÷ mean(P × V, w) − 1", zh: "当前成交额相对窗口平均成交额的变化。", en: "Current dollar volume relative to its window average." },
  return_excess_kurtosis: { notation: "excess kurtosis(rₜ, w)", zh: "衡量收益分布厚尾程度；正态分布基准为 0。", en: "Measures return-distribution tail thickness; a normal distribution has value 0." },
  return_skewness: { notation: "skewness(rₜ, w)", zh: "衡量窗口收益分布的不对称程度；正值表示右尾更长。", en: "Measures return-distribution asymmetry; positive values indicate a longer right tail." },
  rsi: { notation: "100 − 100 ÷ (1 + avg gain ÷ avg loss)", zh: "Wilder 平滑后的上涨与下跌强度比，常用于识别超买、超卖或趋势强弱。", en: "Wilder-smoothed ratio of gains to losses, commonly used for overbought, oversold, or trend-strength signals." },
  total_return: { notation: "Pₜ ÷ Pₜ₋w − 1", zh: "当前复权价格相对窗口起点的累计收益。", en: "Cumulative adjusted-price return from the start of the window." },
};

const chineseRationales: Record<string, string> = {
  "A deeper relative drawdown can create rebound potential after forced selling or overreaction.": "相对回撤越深，越可能反映被迫卖出或过度反应，从而形成反弹空间。",
  "A fresh downward price/average crossing can mark trend deterioration.": "价格刚向下穿越均线，可能表示趋势开始恶化。",
  "A fresh upward price/average crossing can mark trend initiation.": "价格刚向上穿越均线，可能表示新趋势正在形成。",
  "A higher PPO histogram indicates positive normalized trend acceleration.": "PPO 柱状差越高，表示标准化后的上行趋势加速度越强。",
  "A higher short-to-long moving-average ratio represents stronger prevailing trend.": "短期均线相对长期均线越高，通常表示当前趋势越强。",
  "A histogram crossing above zero marks improving normalized trend acceleration.": "PPO 柱状差上穿零轴，表示标准化趋势加速度正在改善。",
  "A histogram crossing below zero marks deteriorating normalized trend acceleration.": "PPO 柱状差下穿零轴，表示标准化趋势加速度正在恶化。",
  "A shallower recent drawdown indicates stronger capital preservation and trend resilience.": "近期回撤越浅，通常表示资本保全能力和趋势韧性越强。",
  "Assets with lottery-like positive skew may be over-demanded, leaving relatively low-skew assets with a premium.": "具有彩票特征的高正偏资产可能被过度追捧，因此低偏度资产可能获得补偿性溢价。",
  "Concentrated extreme returns may identify an active regime shift whose relative direction can persist.": "极端收益集中出现可能意味着市场状态切换，其相对方向有时会延续。",
  "Elevated dollar volume can reflect stronger attention, participation and executable trend confirmation.": "成交额上升可能反映关注度、参与度和交易可执行性增强，并对趋势形成确认。",
  "Investors may require compensation for bearing relatively greater illiquidity.": "投资者承担更高的非流动性时，可能要求额外收益作为补偿。",
  "Lower downside deviation favors assets with less harmful return variability.": "较低的下行波动表示有害收益波动较少，因此在风险维度上更占优。",
  "Lower excess kurtosis represents fewer extreme-return observations and a more stable return distribution.": "较低的超额峰度表示极端收益出现得更少，收益分布相对更稳定。",
  "Lower price impact per dollar traded represents better liquidity and implementation quality.": "单位成交额造成的价格冲击越低，通常表示流动性和策略执行质量越好。",
  "Lower realized volatility can represent more efficient risk-adjusted exposure and the low-volatility effect.": "较低的已实现波动率可能代表更有效的风险调整后暴露，并对应低波动效应。",
  "Lower RSI represents greater relative oversold pressure and a possible rebound.": "RSI 越低，表示相对超卖压力越强，也可能孕育反弹。",
  "Persistently high RSI can represent relative leadership rather than immediate reversal.": "持续较高的 RSI 也可能表示相对强势，而不一定立即反转。",
  "Positive return asymmetry may identify upside-led risk-on leadership in a relative rotation context.": "正收益偏度可能识别出由上行收益主导、风险偏好更强的相对领先资产。",
  "Price above a widely followed moving average marks a positive trend state.": "价格位于广泛关注的移动平均线之上，表示当前处于正向趋势状态。",
  "Relative return strength may persist because information and capital adjust gradually.": "信息和资金的调整往往并非瞬间完成，因此相对收益强势可能延续。",
  "RSI at or above 70 is a conventional overbought state and bearish reversal warning.": "RSI 达到或超过 70 通常被视为超买，并构成看跌反转提醒。",
  "RSI at or below 30 is a conventional oversold state.": "RSI 达到或低于 30 通常被视为超卖状态。",
  "Skipping the most recent month separates medium-term continuation from short-term reversal.": "跳过最近一个月，有助于将中期动量延续与短期反转区分开。",
  "The 50-day average crossing above the 200-day average is a classic long-horizon trend event.": "50 日均线上穿 200 日均线，是经典的长期趋势转强事件。",
  "The 50-day average crossing below the 200-day average is a classic long-horizon deterioration event.": "50 日均线下穿 200 日均线，是经典的长期趋势恶化事件。",
  "Very recent relative underperformance may reverse after temporary price pressure or overreaction.": "近期相对弱势可能来自暂时价格压力或过度反应，随后存在反转可能。",
};

function language(value: string | undefined): Language {
  return value?.startsWith("zh") ? "zh-CN" : "en";
}

export function researchLabel(value: string | null | undefined, resolvedLanguage?: string): string {
  if (!value) return "—";
  const current = language(resolvedLanguage);
  const exact = labels[current][value];
  if (exact) return exact;
  if (value.startsWith("dimension_equal_weight__")) {
    const dimension = researchLabel(value.split("__")[1], resolvedLanguage);
    return current === "zh-CN" ? `${dimension}维度等权模型` : `${dimension} equal-weight model`;
  }
  if (value.startsWith("pre_selection_trend_eligible_top_k__k")) {
    const k = value.split("__k")[1];
    return current === "zh-CN" ? `趋势过滤后选取 Top-${k}` : `Trend-filtered Top-${k}`;
  }
  if (value.startsWith("top_k_equal_weight__k")) {
    const k = value.split("__k")[1];
    return current === "zh-CN" ? `Top-${k} 等权` : `Top-${k} equal weight`;
  }
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

export function researchRationale(value: string, resolvedLanguage?: string): string {
  return language(resolvedLanguage) === "zh-CN" ? chineseRationales[value] ?? value : value;
}

export function ResearchKey({ value, className = "" }: { value: string | null | undefined; className?: string }) {
  const { i18n } = useTranslation();
  if (!value) return <span className={className}>—</span>;
  const label = researchLabel(value, i18n.resolvedLanguage);
  return <span className={`research-key ${className}`.trim()}><span>{label}</span>{label !== value && <code>{value}</code>}</span>;
}

export function FormulaDisplay({ factorKey, formula }: { factorKey: string; formula: string }) {
  const { t, i18n } = useTranslation();
  const guide = formulaGuides[factorKey];
  const current = language(i18n.resolvedLanguage);
  if (!guide) return <code className="formula-raw">{formula}</code>;
  return <div className="formula-display" aria-label={`${researchLabel(factorKey, i18n.resolvedLanguage)}: ${guide.notation}`}>
    <div className="formula-notation"><span>{t("common.formula")}</span><strong>{guide.notation}</strong></div>
    <p>{guide[current === "zh-CN" ? "zh" : "en"]}</p>
    <details><summary>{t("common.rawDefinition")}</summary><code>{formula}</code></details>
  </div>;
}
