from style_rotation.contracts.spec import ContractLayer, DataContractSpec, FieldSpec

RAW_MARKET_PRICES_CONTRACT = DataContractSpec(
    layer=ContractLayer.RAW,
    name="raw_market_prices",
    schema_version="0.1.0",
    primary_key=("data_version_id", "asset_id", "trade_date"),
    fields=(
        FieldSpec("data_version_id", "uuid", False, "Immutable acquisition version"),
        FieldSpec("asset_id", "uuid", False, "Asset identifier"),
        FieldSpec("trade_date", "date", False, "US market session label", time_semantics="XNYS"),
        FieldSpec("open_raw", "decimal(20,8)", True, "Yahoo unadjusted open", unit="USD"),
        FieldSpec("high_raw", "decimal(20,8)", True, "Yahoo unadjusted high", unit="USD"),
        FieldSpec("low_raw", "decimal(20,8)", True, "Yahoo unadjusted low", unit="USD"),
        FieldSpec("close_raw", "decimal(20,8)", True, "Yahoo unadjusted close", unit="USD"),
        FieldSpec("adj_close", "decimal(20,8)", True, "Yahoo adjusted close", unit="USD"),
        FieldSpec("volume_raw", "bigint", True, "Yahoo raw volume", unit="shares"),
        FieldSpec("dividends", "decimal(20,8)", False, "Cash distribution", unit="USD/share"),
        FieldSpec("stock_splits", "decimal(20,8)", False, "Split ratio"),
        FieldSpec("source_row_hash", "sha256", False, "Canonical source-row digest"),
    ),
    quality_rules=("unique primary key", "nullable raw values are rejected by publication gate"),
)


CLEAN_MARKET_PRICES_CONTRACT = DataContractSpec(
    layer=ContractLayer.CLEAN,
    name="clean_market_prices",
    schema_version="0.1.0",
    primary_key=("data_version_id", "cleaning_version_id", "asset_id", "trade_date"),
    fields=(
        FieldSpec("data_version_id", "uuid", False, "Immutable acquisition version"),
        FieldSpec("cleaning_version_id", "uuid", False, "Immutable cleaning rules version"),
        FieldSpec("asset_id", "uuid", False, "Asset identifier"),
        FieldSpec("trade_date", "date", False, "US market session label", time_semantics="XNYS"),
        FieldSpec("open_adj", "decimal(20,8)", False, "Adjusted open", unit="USD"),
        FieldSpec("high_adj", "decimal(20,8)", False, "Adjusted high", unit="USD"),
        FieldSpec("low_adj", "decimal(20,8)", False, "Adjusted low", unit="USD"),
        FieldSpec("close_adj", "decimal(20,8)", False, "Adjusted close", unit="USD"),
        FieldSpec("adj_factor", "decimal(24,12)", False, "adj_close / close_raw"),
        FieldSpec("volume_raw", "bigint", False, "Unadjusted Yahoo volume", unit="shares"),
        FieldSpec("dividends", "decimal(20,8)", False, "Cash distribution", unit="USD/share"),
        FieldSpec("stock_splits", "decimal(20,8)", False, "Split ratio"),
    ),
    quality_rules=(
        "all five ETF date sets equal SPY after common inception",
        "positive OHLC and adjustment factor",
        "high >= max(open, close)",
        "low <= min(open, close)",
        "nonnegative volume, dividends, and split ratio",
        "no adjusted close-to-close move greater than 50 percent",
    ),
)


RESERVE_RETURNS_CONTRACT = DataContractSpec(
    layer=ContractLayer.CLEAN,
    name="reserve_daily_returns",
    schema_version="0.1.0",
    primary_key=("data_version_id", "cleaning_version_id", "nav_date"),
    fields=(
        FieldSpec("data_version_id", "uuid", False, "Immutable acquisition version"),
        FieldSpec("cleaning_version_id", "uuid", False, "Immutable cleaning rules version"),
        FieldSpec(
            "nav_date", "date", False, "XNYS portfolio valuation date", time_semantics="XNYS"
        ),
        FieldSpec("series_id", "string", False, "FRED series identifier"),
        FieldSpec("source_observation_date", "date", False, "Underlying rate observation date"),
        FieldSpec("source_available_date", "date", False, "First allowed usage date"),
        FieldSpec("annual_rate_percent", "decimal(12,8)", False, "Annual rate", unit="percent"),
        FieldSpec("calendar_daily_factor", "decimal(24,16)", False, "ACT/365 daily growth factor"),
    ),
    quality_rules=(
        "only observations with available_date <= nav_date",
        "forward fill only",
        "maximum source staleness 10 calendar days",
        "ACT/365 conversion",
    ),
)


PHASE2_CONTRACTS = (
    RAW_MARKET_PRICES_CONTRACT,
    CLEAN_MARKET_PRICES_CONTRACT,
    RESERVE_RETURNS_CONTRACT,
)
