from style_rotation.contracts.spec import ContractLayer, DataContractSpec, FieldSpec

REBALANCE_EVENTS_CONTRACT = DataContractSpec(
    layer=ContractLayer.SIGNAL,
    name="rebalance_events",
    schema_version="0.1.0",
    primary_key=("rebalance_event_id",),
    fields=(
        FieldSpec("rebalance_event_id", "uuid", False, "Deterministic rebalance event ID"),
        FieldSpec("factor_variant_id", "uuid", False, "Registered factor parameter variant"),
        FieldSpec("rebalance_frequency", "string", False, "weekly or monthly"),
        FieldSpec("strategy_template", "string", False, "cross_sectional or trend_filtered"),
        FieldSpec(
            "signal_date", "date", False, "Last actual session in period", time_semantics="close"
        ),
        FieldSpec("execution_date", "date", False, "Next actual session", time_semantics="open"),
        FieldSpec("eligible_count", "integer", False, "Assets ranked after template filter"),
        FieldSpec("tie_flag", "boolean", False, "At least one comparable score was tied"),
        FieldSpec("reserve_target_weight", "decimal(12,10)", False, "Unallocated risk budget"),
    ),
    quality_rules=(
        "signal_date strictly precedes execution_date",
        "weekly and monthly period ends use observed trading dates",
        "the final incomplete period is excluded without a next execution date",
        "cross-sectional reserve weight is zero",
        "trend-filtered reserve weight is zero, 0.5, or 1",
    ),
)

TARGET_POSITIONS_CONTRACT = DataContractSpec(
    layer=ContractLayer.SIGNAL,
    name="target_positions",
    schema_version="0.1.0",
    primary_key=("rebalance_event_id", "asset_id"),
    fields=(
        FieldSpec("rebalance_event_id", "uuid", False, "Parent rebalance event"),
        FieldSpec("asset_id", "uuid", False, "Candidate ETF"),
        FieldSpec("raw_factor_value", "decimal(30,14)", False, "Unchanged factor-layer value"),
        FieldSpec("oriented_factor_value", "decimal(30,14)", False, "Higher-is-better score"),
        FieldSpec("rank", "integer", True, "Rank among assets eligible for this template"),
        FieldSpec("trend_eligible", "boolean", False, "Strict close_adj > SMA200 result"),
        FieldSpec("tie_flag", "boolean", False, "Score tied within ranked assets"),
        FieldSpec("selected", "boolean", False, "Asset belongs to Top 2"),
        FieldSpec("target_weight", "decimal(12,10)", False, "Pre-trade portfolio target"),
    ),
    quality_rules=(
        "exactly four ETF rows per event",
        "fixed tie order is IWF, IWD, IWO, IWN",
        "selected ETF target weight is exactly 0.5",
        "unselected ETF target weight is zero",
        "trend-filtered ineligible assets have no rank",
    ),
)

PHASE4_CONTRACTS = (REBALANCE_EVENTS_CONTRACT, TARGET_POSITIONS_CONTRACT)
