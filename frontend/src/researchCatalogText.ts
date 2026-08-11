type CatalogText = { name: string; description: string };

const signalZh: Record<string, CatalogText> = {
  return_continuation: { name: "收益延续", description: "相对收益较强的资产可能因信息与资金调整缓慢而延续领先。" },
  short_return_reversal: { name: "短期收益反转", description: "近期相对弱势可能来自临时价格压力或过度反应，随后出现修复。" },
  lagged_return_continuation: { name: "跳过近期的动量延续", description: "跳过最近一个月，用于区分中期趋势延续与短期反转。" },
  ma_trend_strength: { name: "均线趋势强度", description: "短期均线相对长期均线越强，代表当前趋势越强。" },
  price_above_ma_state: { name: "价格高于均线状态", description: "价格位于广泛关注的移动平均线上方时，视为正向趋势状态。" },
  price_cross_above_ma: { name: "价格向上穿越均线", description: "价格刚刚向上穿越均线，可能代表新趋势启动。" },
  price_cross_below_ma: { name: "价格向下穿越均线", description: "价格刚刚向下穿越均线，可能代表趋势转弱。" },
  golden_cross_event: { name: "黄金交叉事件", description: "50 日均线向上穿越 200 日均线的经典长期趋势事件。" },
  death_cross_event: { name: "死亡交叉事件", description: "50 日均线向下穿越 200 日均线的经典长期转弱事件。" },
  rsi_relative_strength: { name: "RSI 相对强势", description: "持续较高的 RSI 在横截面中可代表相对领先，而不必然意味着立即反转。" },
  rsi_mean_reversion: { name: "RSI 均值回归", description: "较低 RSI 代表更强的超卖压力与潜在反弹空间。" },
  rsi_oversold_state: { name: "RSI 超卖状态", description: "RSI 不高于 30 时标记为传统超卖状态。" },
  rsi_overbought_state: { name: "RSI 超买状态", description: "RSI 不低于 70 时标记为传统超买与反转警示状态。" },
  low_skew_premium: { name: "低偏度溢价", description: "高正偏度资产可能因彩票偏好被过度追逐，低偏度资产可能获得补偿。" },
  high_skew_regime: { name: "高偏度风险偏好状态", description: "正向收益不对称可能识别由上涨主导的风险偏好领先状态。" },
  low_kurtosis_quality: { name: "低峰度质量", description: "较低超额峰度代表极端收益更少、收益分布更稳定。" },
  high_kurtosis_tail_regime: { name: "高峰度尾部状态", description: "极端收益集中可能代表正在发生且可能延续的状态切换。" },
  low_volatility: { name: "低波动", description: "较低已实现波动率可能提供更高效的风险调整后暴露。" },
  low_downside_risk: { name: "低下行风险", description: "较低下行偏差偏好有害波动更少的资产。" },
  drawdown_resilience: { name: "回撤韧性", description: "近期回撤较浅代表更强的资本保护与趋势韧性。" },
  deep_drawdown_reversal: { name: "深回撤反转", description: "较深相对回撤可能在被迫卖出或过度反应后产生修复空间。" },
  dollar_volume_attention: { name: "成交额关注度", description: "成交额上升可能代表关注、参与度与趋势可执行性增强。" },
  low_illiquidity_quality: { name: "低非流动性质量", description: "单位成交额的价格冲击更低，代表流动性与实施质量更好。" },
  illiquidity_premium: { name: "非流动性溢价", description: "承担更高相对非流动性的投资者可能要求风险补偿。" },
  ppo_trend_acceleration: { name: "PPO 趋势加速度", description: "更高的 PPO 柱状值代表归一化趋势正在正向加速。" },
  ppo_cross_above_zero: { name: "PPO 向上穿越零轴", description: "PPO 柱状值向上穿越零轴，标记趋势加速度改善。" },
  ppo_cross_below_zero: { name: "PPO 向下穿越零轴", description: "PPO 柱状值向下穿越零轴，标记趋势加速度恶化。" },
};

const modelZh: Record<string, CatalogText> = {
  single_signal: { name: "单信号直通模型", description: "仅接受一个连续信号，并将其直接转换为可横截面比较的模型分数。" },
  linear_weighted: { name: "确定性线性聚合模型", description: "接收全部已选连续信号；预设只改变公开、固定的权重与归一化契约。" },
  directional_vote: { name: "方向投票模型", description: "聚合离散方向信号，仅用于预测诊断，当前不允许连接 Top-K 策略。" },
  lightgbm_ranker: { name: "LightGBM 横截面排序模型", description: "带显式训练目标的计划中模型；PIT 数据、训练切分与引擎发布完成前不可使用。" },
};

const strategyZh: Record<string, CatalogText> = {
  multi_etf_top_k: { name: "多 ETF 横截面 Top-K 轮动", description: "按单一模型输出对已选 ETF 排序，持有不超过当期可排名 ETF 数量一半的前 K 个标的。" },
  us_large_cap_top_k: { name: "美股大盘股横截面 Top-K 选股", description: "在冻结的 PIT 大盘股资产池内排序；50–99 只仅用于探索，正式结果至少需要 100 只。" },
};

export function catalogText(kind: "signal" | "model" | "strategy", key: string, chinese: boolean, fallback: CatalogText): CatalogText {
  if (!chinese) return fallback;
  const source = kind === "signal" ? signalZh : kind === "model" ? modelZh : strategyZh;
  return source[key] ?? fallback;
}

export function contractLabel(value: string, chinese: boolean): string {
  if (!chinese) return value.replaceAll("_", " ");
  const labels: Record<string, string> = {
    higher_is_better: "数值越高越优", lower_is_better: "数值越低越优",
    continuous: "连续分数", directional: "方向分数", event: "事件信号",
    threshold_state: "阈值状态", crossover_event: "穿越事件",
    academic: "学术依据", market_convention: "市场惯例",
    institutional_research: "机构研究", practitioner_hypothesis: "实践假设",
    canonical: "基准", sensitivity: "敏感性", exploratory: "探索性",
    available: "可用", planned: "计划中", cross_sectional: "横截面可比",
    diagnostic_only: "仅诊断", formal: "正式", none: "无",
    fixed_20: "固定 20% 防御资产", internal_timing_v1: "内部择时防御 v1",
    half_k: "半 K 缓冲", pit_30_percent: "PIT 行业上限 30%",
    selected_asset_unavailable: "所选资产尚未达到研究数据就绪状态",
    asset_count_below_launch_minimum: "可用资产少于策略启动门槛",
    rankable_count_below_k: "可排名资产少于 K",
    asset_type_incompatible: "所选资产类型与策略不兼容",
    model_not_selected: "尚未选择模型",
    selected_model_invalidated: "所选模型已被上游输入判为不可用",
    model_output_incompatible: "模型输出类型与策略不兼容",
    model_not_cross_sectionally_comparable: "模型输出不可用于横截面排序",
    pit_sector_data_unavailable: "缺少PIT行业分类数据",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}
