from pathlib import Path

from style_rotation.workspace.catalog import build_component_document
from style_rotation.workspace.options import build_workspace_options


def _document() -> dict[str, object]:
    return build_component_document(
        Path("v0.2/catalogs/factors.v0.2.0.json"),
        Path("v0.2/catalogs/signals.v0.2.0.json"),
        Path("v0.21/catalogs/workspace_contracts.v0.21.0.json"),
    )


def _preset(options: dict[str, object], key: str) -> dict[str, object]:
    return next(
        preset
        for family in options["model_families"]
        for preset in family["presets"]
        if preset["preset_key"] == key
    )


def _strategy(options: dict[str, object], key: str) -> dict[str, object]:
    return next(
        preset
        for family in options["strategy_families"]
        for preset in family["presets"]
        if preset["preset_key"] == key
    )


def test_upstream_factor_selection_controls_signal_legality() -> None:
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=("total_return__w120",),
        selected_signals=(),
    )
    continuation = next(
        family for family in options["signal_families"] if family["key"] == "return_continuation"
    )
    legal = next(
        version
        for version in continuation["versions"]
        if version["factor_variant_key"] == "total_return__w120"
    )
    blocked = next(
        version
        for version in continuation["versions"]
        if version["factor_variant_key"] == "total_return__w60"
    )
    assert legal["selectable"] is True
    assert blocked["reason_codes"] == ["factor_not_selected"]


def test_model_accepts_every_selected_legal_signal_or_is_disabled() -> None:
    momentum = "return_continuation__total_return__w120"
    reversal = "short_return_reversal__total_return__w5"
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=("total_return__w120", "total_return__w5"),
        selected_signals=(momentum, reversal),
    )
    single = _preset(options, "single_signal__identity_v1")
    linear = _preset(options, "linear_weighted__signal_equal_v1")
    assert single["selectable"] is False
    assert "slot_overflow" in single["reason_codes"]
    assert linear["selectable"] is True
    assert linear["accepted_signal_keys"] == sorted([momentum, reversal])


def test_invalid_signal_selection_disables_every_model_instead_of_dropping_it() -> None:
    signal = "return_continuation__total_return__w120"
    options = build_workspace_options(
        _document(), frequency="weekly", selected_factor_variants=(), selected_signals=(signal,)
    )
    for family in options["model_families"]:
        for preset in family["presets"]:
            assert preset["selectable"] is False
            assert "selected_signal_invalidated" in preset["reason_codes"]


def test_vote_and_training_families_remain_explicitly_constrained() -> None:
    directional = "price_above_ma_state__moving_average_ratio__s1_l200"
    options = build_workspace_options(
        _document(),
        frequency="monthly",
        selected_factor_variants=("moving_average_ratio__s1_l200",),
        selected_signals=(directional,),
    )
    vote = _preset(options, "directional_vote__majority_v1")
    linear = _preset(options, "linear_weighted__signal_equal_v1")
    trained = _preset(options, "lightgbm_ranker__conservative_v1")
    assert vote["selectable"] is True
    assert linear["selectable"] is False
    assert "signal_unaccepted" in linear["reason_codes"]
    assert trained["selectable"] is False
    assert "implementation_unavailable" in trained["reason_codes"]


def test_etf_strategy_requires_two_usable_etfs_and_a_legal_continuous_model() -> None:
    signal = "return_continuation__total_return__w120"
    model = "single_signal__identity_v1"
    assets = tuple(
        {
            "security_id": key,
            "instrument_type": "Equity ETF",
            "selectable": True,
            "pit_sector_available": False,
        }
        for key in ("iwf", "iwd", "iwo", "iwn")
    )
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=("total_return__w120",),
        selected_signals=(signal,),
        selected_models=(model,),
        selected_assets=assets,
    )
    strategy = _strategy(options, "multi_etf_top_k__k1__none__none__none")
    assert strategy["selectable"] is True
    assert strategy["research_mode"] == "formal"
    all_hold = _strategy(options, "multi_etf_top_k__k2__none__none__none")
    too_large = _strategy(options, "multi_etf_top_k__k3__none__none__none")
    assert all_hold["selectable"] is True
    assert too_large["selectable"] is False
    assert "etf_k_exceeds_half_rankable" in too_large["reason_codes"]


def test_two_etfs_allow_only_k1() -> None:
    signal = "return_continuation__total_return__w120"
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=("total_return__w120",),
        selected_signals=(signal,),
        selected_models=("single_signal__identity_v1",),
        selected_assets=tuple(
            {
                "security_id": key,
                "instrument_type": "Equity ETF",
                "selectable": True,
                "pit_sector_available": False,
            }
            for key in ("iwf", "iwd")
        ),
    )
    assert _strategy(options, "multi_etf_top_k__k1__none__none__none")["selectable"]
    k2 = _strategy(options, "multi_etf_top_k__k2__none__none__none")
    assert k2["selectable"] is False
    assert "etf_k_exceeds_half_rankable" in k2["reason_codes"]


def test_stock_strategy_exploratory_threshold_and_sector_gate_are_explicit() -> None:
    signal = "return_continuation__total_return__w120"
    assets = tuple(
        {
            "security_id": f"stock-{index}",
            "instrument_type": "Common Stock",
            "selectable": True,
            "pit_sector_available": False,
        }
        for index in range(50)
    )
    options = build_workspace_options(
        _document(),
        frequency="monthly",
        selected_factor_variants=("total_return__w120",),
        selected_signals=(signal,),
        selected_models=("single_signal__identity_v1",),
        selected_assets=assets,
    )
    without_cap = _strategy(options, "us_large_cap_top_k__k20__none__half_k__none")
    with_cap = _strategy(options, "us_large_cap_top_k__k20__none__half_k__pit_30_percent")
    assert without_cap["selectable"] is True
    assert without_cap["research_mode"] == "exploratory"
    assert with_cap["selectable"] is False
    assert "pit_sector_data_unavailable" in with_cap["reason_codes"]


def test_large_cap_stock_strategy_accepts_adrs_in_the_equity_universe() -> None:
    signal = "return_continuation__total_return__w120"
    assets = tuple(
        {
            "security_id": f"stock-{index}",
            "instrument_type": "ADR" if index < 3 else "Common Stock",
            "selectable": True,
            "pit_sector_available": False,
        }
        for index in range(100)
    )
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=("total_return__w120",),
        selected_signals=(signal,),
        selected_models=("single_signal__identity_v1",),
        selected_assets=assets,
    )
    strategy = _strategy(options, "us_large_cap_top_k__k20__none__half_k__none")
    assert strategy["selectable"] is True
    assert strategy["research_mode"] == "formal"


def test_explicitly_unselected_market_input_disables_the_market_factor_chain() -> None:
    asset = {
        "security_id": "iwf",
        "instrument_type": "Equity ETF",
        "selectable": True,
        "canonical_data_available": True,
        "pit_sector_available": False,
    }
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=("total_return__w120",),
        selected_signals=("return_continuation__total_return__w120",),
        selected_assets=(asset,),
        selected_asset_data_inputs={"iwf": ()},
    )
    factor = next(
        variant
        for family in options["factor_families"]
        for variant in family["variants"]
        if variant["key"] == "total_return__w120"
    )
    signal = next(
        version
        for family in options["signal_families"]
        for version in family["versions"]
        if version["version_key"] == "return_continuation__total_return__w120"
    )
    assert factor["selectable"] is False
    assert factor["reason_codes"] == ["asset_data_input_missing"]
    assert signal["selectable"] is False
    assert "factor_data_input_unavailable" in signal["reason_codes"]
    assert options["usable_asset_count"] == 0


def test_unpublished_asset_input_is_reported_instead_of_silently_accepted() -> None:
    options = build_workspace_options(
        _document(),
        frequency="weekly",
        selected_factor_variants=(),
        selected_signals=(),
        selected_assets=(
            {
                "security_id": "aapl",
                "instrument_type": "Common Stock",
                "selectable": True,
                "canonical_data_available": True,
            },
        ),
        selected_asset_data_inputs={
            "aapl": ("canonical_market_bars", "sec_filing_fundamentals")
        },
    )
    assert options["asset_data_input_blockers"] == [
        {
            "security_id": "aapl",
            "input_key": "sec_filing_fundamentals",
            "reason_codes": ["asset_data_input_unavailable"],
        }
    ]
