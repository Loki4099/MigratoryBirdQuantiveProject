import unittest

from style_rotation.contracts.spec import ContractLayer, DataContractSpec, FieldSpec


class DataContractTests(unittest.TestCase):
    def test_contract_hash_is_deterministic(self) -> None:
        contract = DataContractSpec(
            layer=ContractLayer.CLEAN,
            name="clean_market_prices",
            schema_version="0.1.0",
            primary_key=("asset_id", "trade_date"),
            fields=(
                FieldSpec("asset_id", "uuid", False, "Asset identifier"),
                FieldSpec("trade_date", "date", False, "US trading date", time_semantics="XNYS"),
                FieldSpec("close_adj", "decimal", False, "Adjusted close", unit="USD"),
            ),
            quality_rules=("close_adj > 0",),
        )
        self.assertEqual(contract.contract_hash, contract.contract_hash)
        self.assertEqual(len(contract.contract_hash), 64)

    def test_missing_primary_key_field_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Primary-key fields are not defined"):
            DataContractSpec(
                layer=ContractLayer.RAW,
                name="prices",
                schema_version="0.1.0",
                primary_key=("missing",),
                fields=(FieldSpec("asset_id", "uuid", False, "Asset identifier"),),
            )

    def test_nullable_primary_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be nullable"):
            DataContractSpec(
                layer=ContractLayer.RAW,
                name="prices",
                schema_version="0.1.0",
                primary_key=("asset_id",),
                fields=(FieldSpec("asset_id", "uuid", True, "Asset identifier"),),
            )


if __name__ == "__main__":
    unittest.main()
