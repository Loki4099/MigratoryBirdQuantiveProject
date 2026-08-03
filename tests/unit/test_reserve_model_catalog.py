from pathlib import Path

from style_rotation.data.reserve import ReserveModelCatalog


def test_reserve_model_catalog_freezes_act365_and_staleness_thresholds() -> None:
    catalog = ReserveModelCatalog.model_validate_json(
        Path("v0.2/catalogs/reserve_model.v0.2.0.json").read_text(encoding="utf-8")
    )
    assert catalog.model_key == "dgs3mo_cash_accrual_proxy"
    assert catalog.accrual_method == "simple"
    assert catalog.day_count_basis == "ACT/365"
    assert (catalog.warning_after_days, catalog.error_after_days) == (5, 10)
