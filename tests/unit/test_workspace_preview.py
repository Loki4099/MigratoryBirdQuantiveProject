from pathlib import Path

from style_rotation.workspace.catalog import build_component_document
from style_rotation.workspace.preview import build_compile_preview


def _document() -> dict[str, object]:
    return build_component_document(
        Path("v0.2/catalogs/factors.v0.2.0.json"),
        Path("v0.2/catalogs/signals.v0.2.0.json"),
        Path("v0.21/catalogs/workspace_contracts.v0.21.0.json"),
    )


def test_preview_expands_each_model_strategy_pair_to_six_cells() -> None:
    assets = tuple(
        {
            "security_id": key,
            "instrument_type": "Equity ETF",
            "selectable": True,
            "pit_sector_available": False,
        }
        for key in ("iwf", "iwd", "iwo", "iwn")
    )
    result = build_compile_preview(
        _document(),
        frequency="weekly",
        asset_security_ids=tuple(item["security_id"] for item in assets),
        selected_assets=assets,
        factor_variant_keys=("total_return__w120",),
        signal_version_keys=("return_continuation__total_return__w120",),
        model_preset_keys=("single_signal__identity_v1", "linear_weighted__signal_equal_v1"),
        strategy_preset_keys=("multi_etf_top_k__k2__none__none__none",),
    )
    assert result["compiled"]["runnable"] is True
    assert len(result["compiled"]["strategy_branches"]) == 2
    assert result["compiled"]["portfolio_cell_count"] == 12
    assert result["blockers"] == []


def test_preview_preserves_an_invalid_selected_strategy_and_blocks_run() -> None:
    assets = tuple(
        {
            "security_id": key,
            "instrument_type": "Equity ETF",
            "selectable": True,
            "pit_sector_available": False,
        }
        for key in ("iwf", "iwd")
    )
    result = build_compile_preview(
        _document(),
        frequency="weekly",
        asset_security_ids=("iwf", "iwd"),
        selected_assets=assets,
        factor_variant_keys=("total_return__w120",),
        signal_version_keys=("return_continuation__total_return__w120",),
        model_preset_keys=("single_signal__identity_v1",),
        strategy_preset_keys=("multi_etf_top_k__k2__none__none__none",),
    )
    assert result["compiled"]["runnable"] is False
    assert result["blockers"][0]["reason_codes"] == ["etf_k_exceeds_half_rankable"]


def test_preview_blocks_an_unknown_selected_factor() -> None:
    assets = tuple(
        {"security_id": key, "instrument_type": "Equity ETF", "selectable": True}
        for key in ("iwf", "iwd", "iwo", "iwn")
    )
    result = build_compile_preview(
        _document(),
        frequency="weekly",
        asset_security_ids=tuple(item["security_id"] for item in assets),
        selected_assets=assets,
        factor_variant_keys=("total_return__w120", "unknown_factor__w20"),
        signal_version_keys=("return_continuation__total_return__w120",),
        model_preset_keys=("single_signal__identity_v1",),
        strategy_preset_keys=("multi_etf_top_k__k2__none__none__none",),
    )
    assert result["compiled"]["runnable"] is False
    assert result["blockers"] == [
        {
            "layer": "factor",
            "object_key": "unknown_factor__w20",
            "reason_codes": ["selection_unknown"],
        }
    ]


def test_preview_blocks_a_market_factor_when_market_bars_are_not_selected() -> None:
    assets = tuple(
        {
            "security_id": key,
            "instrument_type": "Equity ETF",
            "selectable": True,
            "canonical_data_available": True,
        }
        for key in ("iwf", "iwd")
    )
    result = build_compile_preview(
        _document(),
        frequency="weekly",
        asset_security_ids=("iwf", "iwd"),
        selected_assets=assets,
        asset_data_inputs={"iwf": (), "iwd": ("canonical_market_bars",)},
        factor_variant_keys=("total_return__w120",),
        signal_version_keys=("return_continuation__total_return__w120",),
        model_preset_keys=("single_signal__identity_v1",),
        strategy_preset_keys=("multi_etf_top_k__k1__none__none__none",),
    )
    assert result["compiled"]["runnable"] is False
    assert {
        (blocker["layer"], blocker["object_key"], tuple(blocker["reason_codes"]))
        for blocker in result["blockers"]
    } >= {
        ("factor", "total_return__w120", ("asset_data_input_missing",)),
    }
