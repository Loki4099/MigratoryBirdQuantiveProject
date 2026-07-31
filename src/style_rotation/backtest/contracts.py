from style_rotation.contracts.spec import ContractLayer, DataContractSpec, FieldSpec

DAILY_NAV_CONTRACT = DataContractSpec(
    layer=ContractLayer.BACKTEST,
    name="daily_nav",
    schema_version="0.1.0",
    primary_key=("run_id", "nav_date"),
    fields=(
        FieldSpec("run_id", "uuid", False, "Immutable backtest run identifier"),
        FieldSpec("nav_date", "date", False, "XNYS valuation date", time_semantics="close"),
        FieldSpec("gross_daily_return", "decimal(30,14)", False, "Return before costs"),
        FieldSpec("net_daily_return", "decimal(30,14)", False, "Return after costs"),
        FieldSpec("gross_nav", "decimal(30,14)", False, "Gross wealth index"),
        FieldSpec("net_nav", "decimal(30,14)", False, "Net wealth index"),
        FieldSpec("turnover", "decimal(20,14)", False, "Single-sided turnover"),
        FieldSpec("transaction_cost_fraction", "decimal(20,14)", False, "One-way cost fraction"),
        FieldSpec("transaction_cost_amount", "decimal(30,14)", False, "NAV cost amount"),
    ),
    quality_rules=(
        "one row per run and XNYS valuation date",
        "gross and net NAV are strictly positive",
        "cost fraction equals turnover times bps divided by 10000",
        "initial portfolio build is charged and terminal liquidation is absent",
    ),
)

DAILY_POSITIONS_CONTRACT = DataContractSpec(
    layer=ContractLayer.BACKTEST,
    name="daily_positions",
    schema_version="0.1.0",
    primary_key=("run_id", "nav_date", "sleeve"),
    fields=(
        FieldSpec("run_id", "uuid", False, "Immutable backtest run identifier"),
        FieldSpec("nav_date", "date", False, "XNYS valuation date", time_semantics="close"),
        FieldSpec("sleeve", "string", False, "ETF symbol or RESERVE"),
        FieldSpec("asset_id", "uuid", True, "Null only for the RESERVE sleeve"),
        FieldSpec("close_weight", "decimal(20,14)", False, "End-of-day portfolio weight"),
    ),
    quality_rules=(
        "five sleeves per strategy date: four candidate ETFs plus RESERVE",
        "weights sum to one within storage precision",
        "weights are post-close marks and lie between zero and one",
    ),
)

REBALANCE_EXECUTIONS_CONTRACT = DataContractSpec(
    layer=ContractLayer.BACKTEST,
    name="rebalance_executions",
    schema_version="0.1.0",
    primary_key=("run_id", "execution_date"),
    fields=(
        FieldSpec("run_id", "uuid", False, "Immutable backtest run identifier"),
        FieldSpec("execution_date", "date", False, "Execution session", time_semantics="open"),
        FieldSpec("signal_date", "date", False, "Prior signal session", time_semantics="close"),
        FieldSpec("turnover", "decimal(20,14)", False, "Single-sided ETF plus reserve turnover"),
        FieldSpec("transaction_cost_fraction", "decimal(20,14)", False, "One-way cost fraction"),
        FieldSpec("transaction_cost_amount", "decimal(30,14)", False, "Net NAV cost amount"),
        FieldSpec("gross_pretrade_nav", "decimal(30,14)", False, "Gross NAV before execution"),
        FieldSpec("net_pretrade_nav", "decimal(30,14)", False, "Net NAV before execution"),
    ),
    quality_rules=(
        "signal date strictly precedes execution date",
        "positions drift to execution open before turnover is measured",
        "execution uses adjusted open",
    ),
)

TRADES_CONTRACT = DataContractSpec(
    layer=ContractLayer.BACKTEST,
    name="trades",
    schema_version="0.1.0",
    primary_key=("trade_id",),
    fields=(
        FieldSpec("trade_id", "uuid", False, "Deterministic run-date-asset trade ID"),
        FieldSpec("run_id", "uuid", False, "Immutable backtest run identifier"),
        FieldSpec("execution_date", "date", False, "Execution session", time_semantics="open"),
        FieldSpec("asset_id", "uuid", False, "Traded ETF identifier"),
        FieldSpec("side", "string", False, "buy or sell"),
        FieldSpec("execution_price", "decimal(20,8)", False, "Adjusted opening price", unit="USD"),
        FieldSpec("pretrade_weight", "decimal(20,14)", False, "Weight after overnight drift"),
        FieldSpec("target_weight", "decimal(20,14)", False, "Post-trade target weight"),
        FieldSpec(
            "weight_change",
            "decimal(20,14)",
            False,
            "Signed target weight minus pre-trade weight",
        ),
    ),
    quality_rules=(
        "at most one trade per run, execution date, and ETF",
        "reserve changes contribute to turnover but are not represented as ETF trades",
        "zero weight changes are omitted",
    ),
)

BENCHMARK_DAILY_NAV_CONTRACT = DataContractSpec(
    layer=ContractLayer.BACKTEST,
    name="benchmark_daily_nav",
    schema_version="0.1.0",
    primary_key=("run_id", "nav_date", "benchmark_type"),
    fields=(
        FieldSpec("run_id", "uuid", False, "Compared strategy run identifier"),
        FieldSpec("nav_date", "date", False, "XNYS valuation date", time_semantics="close"),
        FieldSpec("benchmark_type", "string", False, "Equal-weight or SPY benchmark"),
        FieldSpec("gross_daily_return", "decimal(30,14)", False, "Return before costs"),
        FieldSpec("net_daily_return", "decimal(30,14)", False, "Return after costs"),
        FieldSpec("gross_nav", "decimal(30,14)", False, "Gross wealth index"),
        FieldSpec("net_nav", "decimal(30,14)", False, "Net wealth index"),
        FieldSpec("turnover", "decimal(20,14)", False, "Single-sided turnover"),
        FieldSpec("transaction_cost_fraction", "decimal(20,14)", False, "One-way cost fraction"),
    ),
    quality_rules=(
        "both benchmark types cover every strategy NAV date",
        "four-ETF benchmark rebalances at the strategy frequency",
        "SPY benchmark buys once at first execution and has no later turnover",
    ),
)

PHASE5_CONTRACTS = (
    DAILY_NAV_CONTRACT,
    DAILY_POSITIONS_CONTRACT,
    REBALANCE_EXECUTIONS_CONTRACT,
    TRADES_CONTRACT,
    BENCHMARK_DAILY_NAV_CONTRACT,
)
