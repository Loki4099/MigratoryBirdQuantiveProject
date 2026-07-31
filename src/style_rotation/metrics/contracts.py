from style_rotation.contracts.spec import ContractLayer, DataContractSpec, FieldSpec

FACTOR_DIAGNOSTIC_SETS_CONTRACT = DataContractSpec(
    layer=ContractLayer.METRICS,
    name="factor_diagnostic_sets",
    schema_version="0.1.0",
    primary_key=("diagnostic_set_id",),
    fields=(
        FieldSpec("diagnostic_set_id", "uuid", False, "Shared factor-frequency diagnosis"),
        FieldSpec("metric_version_id", "uuid", False, "Frozen metric methodology"),
        FieldSpec("factor_variant_id", "uuid", False, "Registered factor parameter variant"),
        FieldSpec("rebalance_frequency", "string", False, "weekly or monthly"),
        FieldSpec("period_count", "integer", False, "Complete prediction periods"),
        FieldSpec("valid_ic_count", "integer", False, "Defined Rank IC observations"),
        FieldSpec("undefined_ic_count", "integer", False, "Undefined Rank IC observations"),
        FieldSpec("mean_rank_ic", "decimal(30,18)", True, "Arithmetic mean of valid ICs"),
        FieldSpec("positive_ic_ratio", "decimal(30,18)", True, "Share of valid ICs above zero"),
        FieldSpec(
            "mean_top_bottom_return_spread",
            "decimal(30,18)",
            False,
            "Arithmetic mean holding-period Top 2 minus Bottom 2 return",
        ),
        FieldSpec("ic_summary_reason_code", "string", True, "Stable undefined reason"),
    ),
    quality_rules=(
        "one set per upstream versions, factor variant, frequency, and metric version",
        "diagnostics use all four ETFs and ignore the SMA200 template filter",
        "valid plus undefined IC count equals complete prediction period count",
        "the last event without a next execution date is excluded",
        "period rows can only be inserted while the parent set is publishing",
    ),
)


FACTOR_DIAGNOSTIC_PERIODS_CONTRACT = DataContractSpec(
    layer=ContractLayer.METRICS,
    name="factor_diagnostic_periods",
    schema_version="0.1.0",
    primary_key=("diagnostic_set_id", "signal_date"),
    fields=(
        FieldSpec("diagnostic_set_id", "uuid", False, "Parent diagnostic set"),
        FieldSpec("signal_date", "date", False, "Factor information date", time_semantics="close"),
        FieldSpec("execution_date", "date", False, "Current holding-period open"),
        FieldSpec("next_execution_date", "date", False, "Next holding-period open"),
        FieldSpec("rank_ic", "decimal(30,18)", True, "Four-ETF Spearman correlation"),
        FieldSpec("rank_ic_reason_code", "string", True, "Stable undefined reason"),
        FieldSpec(
            "top_bottom_return_spread",
            "decimal(30,18)",
            False,
            "Cost-free equal-weight Top 2 minus Bottom 2 open-to-open return",
        ),
    ),
    quality_rules=(
        "Spearman ties use average statistical ranks",
        "Top-Bottom boundary ties use the frozen ticker order",
        "rank_ic is in [-1,1] or null with a reason code",
        "execution date strictly precedes next execution date",
    ),
)


METRIC_PUBLICATIONS_CONTRACT = DataContractSpec(
    layer=ContractLayer.METRICS,
    name="metric_publications",
    schema_version="0.1.0",
    primary_key=("metric_publication_id",),
    fields=(
        FieldSpec("metric_publication_id", "uuid", False, "Immutable run metric publication"),
        FieldSpec("run_id", "uuid", False, "Completed source backtest run"),
        FieldSpec("metric_version_id", "uuid", False, "Frozen metric methodology"),
        FieldSpec("diagnostic_set_id", "uuid", False, "Shared factor diagnosis"),
        FieldSpec("metric_fingerprint", "sha256", False, "Run and methodology fingerprint"),
        FieldSpec("input_manifest_hash", "sha256", False, "Ordered source result digest"),
        FieldSpec("content_hash", "sha256", False, "Published metric result digest"),
        FieldSpec("metric_count", "integer", False, "Tall performance metric row count"),
        FieldSpec("status", "string", False, "Published only"),
    ),
    quality_rules=(
        "only completed backtest runs may be published",
        "same run and metric version is immutable and reusable",
        "all performance rows publish atomically with this record",
        "published parent and child rows reject direct insert, update, and delete mutations",
    ),
)


PERFORMANCE_METRICS_CONTRACT = DataContractSpec(
    layer=ContractLayer.METRICS,
    name="performance_metrics",
    schema_version="0.1.0",
    primary_key=(
        "metric_publication_id",
        "series_type",
        "return_basis",
        "metric_key",
    ),
    fields=(
        FieldSpec("metric_publication_id", "uuid", False, "Parent publication"),
        FieldSpec("series_type", "string", False, "Strategy, benchmark, or relative series"),
        FieldSpec("return_basis", "string", False, "gross, net, or cost_independent"),
        FieldSpec("metric_key", "string", False, "Stable metric identifier"),
        FieldSpec("metric_value", "decimal(38,18)", True, "Finite metric value"),
        FieldSpec("value_status", "string", False, "defined, undefined, or not_applicable"),
        FieldSpec("reason_code", "string", True, "Stable null reason code"),
        FieldSpec("observation_count", "integer", False, "Input sample size"),
        FieldSpec("unit", "string", False, "Explicit metric unit"),
    ),
    quality_rules=(
        "defined values are finite and have no reason code",
        "undefined and not-applicable values are null with a reason code",
        "gross compares with gross and net compares with same-cost net benchmark",
        "SPY is reported independently and is not the default TE or IR benchmark",
    ),
)


PHASE6_CONTRACTS = (
    FACTOR_DIAGNOSTIC_SETS_CONTRACT,
    FACTOR_DIAGNOSTIC_PERIODS_CONTRACT,
    METRIC_PUBLICATIONS_CONTRACT,
    PERFORMANCE_METRICS_CONTRACT,
)
