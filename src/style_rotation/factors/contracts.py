from style_rotation.contracts.spec import ContractLayer, DataContractSpec, FieldSpec

FACTOR_VALUES_CONTRACT = DataContractSpec(
    layer=ContractLayer.FACTOR,
    name="factor_values",
    schema_version="0.1.0",
    primary_key=(
        "data_version_id",
        "cleaning_version_id",
        "factor_version_id",
        "factor_variant_id",
        "asset_id",
        "trade_date",
    ),
    fields=(
        FieldSpec("data_version_id", "uuid", False, "Published source data version"),
        FieldSpec("cleaning_version_id", "uuid", False, "Published cleaning version"),
        FieldSpec("factor_version_id", "uuid", False, "Immutable factor registry version"),
        FieldSpec("factor_variant_id", "uuid", False, "Registered parameter variant"),
        FieldSpec("asset_id", "uuid", False, "Candidate ETF identifier"),
        FieldSpec("trade_date", "date", False, "Close information date", time_semantics="XNYS"),
        FieldSpec("raw_value", "decimal(30,14)", False, "Unranked factor value"),
    ),
    quality_rules=(
        "only IWF, IWD, IWO, and IWN are calculated",
        "no missing or non-finite value after the common valid start",
        "factor direction is metadata and is not applied to raw_value",
        "factor values do not contain ranks, selections, or portfolio weights",
    ),
)

PHASE3_CONTRACTS = (FACTOR_VALUES_CONTRACT,)
