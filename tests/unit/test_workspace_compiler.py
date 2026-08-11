from style_rotation.workspace.compiler import compile_research_spec
from style_rotation.workspace.contracts import (
    ModelInputSlot,
    ModelPresetDescriptor,
    ResearchDraftSelection,
    SignalDescriptor,
    StrategyPresetDescriptor,
)


def _signals() -> tuple[SignalDescriptor, ...]:
    return (
        SignalDescriptor(
            version_key="momentum_w20",
            factor_variant_key="return_w20",
            dimension_key="momentum",
            output_type="continuous",
            frequency="monthly",
        ),
        SignalDescriptor(
            version_key="volatility_w20",
            factor_variant_key="volatility_w20",
            dimension_key="volatility",
            output_type="continuous",
            frequency="monthly",
        ),
    )


def _linear(key: str = "linear_equal") -> ModelPresetDescriptor:
    return ModelPresetDescriptor(
        preset_key=key,
        family_key="deterministic_linear_aggregation",
        output_type="continuous_score",
        output_comparability="cross_sectional",
        supported_frequencies=frozenset({"weekly", "monthly"}),
        input_slots=(
            ModelInputSlot(
                slot_key="continuous_dimensions",
                allowed_dimension_keys=frozenset({"momentum", "volatility"}),
                allowed_output_types=frozenset({"continuous"}),
                minimum_count=1,
                maximum_count=8,
            ),
        ),
    )


def _stock_strategy() -> StrategyPresetDescriptor:
    return StrategyPresetDescriptor(
        preset_key="large_cap_k20_monthly",
        family_key="us_large_cap_top_k",
        compatible_model_output_types=frozenset({"continuous_score"}),
        supported_frequencies=frozenset({"weekly", "monthly"}),
        target_k=20,
        minimum_eligible_assets=50,
        formal_minimum_eligible_assets=100,
        coverage_ratio=0.9,
    )


def _draft(*, models: tuple[str, ...] = ("linear_equal",)) -> ResearchDraftSelection:
    return ResearchDraftSelection(
        asset_context_key="us_liquid_large_cap_300_pit_v1",
        factor_variant_keys=("return_w20", "volatility_w20"),
        signal_version_keys=("momentum_w20", "volatility_w20"),
        model_preset_keys=models,
        strategy_preset_keys=("large_cap_k20_monthly",),
        frequency="monthly",
    )


def test_multiple_models_create_parallel_one_model_strategy_branches() -> None:
    result = compile_research_spec(
        _draft(models=("linear_equal", "linear_tilt")),
        signals=_signals(),
        models=(_linear(), _linear("linear_tilt")),
        strategies=(_stock_strategy(),),
    )
    assert result.runnable
    assert len(result.model_instances) == 2
    assert len(result.strategy_branches) == 2
    assert result.predictive_cell_count == 2
    assert result.portfolio_cell_count == 12
    assert all(branch.model_instance_key for branch in result.strategy_branches)


def test_each_target_kind_and_horizon_compiles_as_an_independent_model_instance() -> None:
    draft = ResearchDraftSelection(
        **{
            **_draft().model_dump(),
            "model_target_keys": (
                "future_return__h21",
                "cross_sectional_relative_return__h21",
            ),
        }
    )
    result = compile_research_spec(
        draft, signals=_signals(), models=(_linear(),), strategies=(_stock_strategy(),)
    )
    assert result.runnable
    assert len(result.model_instances) == 2
    assert {item.target_key for item in result.model_instances} == {
        "future_return__h21",
        "cross_sectional_relative_return__h21",
    }
    assert len(result.strategy_branches) == 2
    assert result.portfolio_cell_count == 12


def test_model_is_rejected_when_any_selected_signal_is_not_accepted() -> None:
    momentum_only = ModelPresetDescriptor(
        **{
            **_linear().model_dump(),
            "input_slots": (
                ModelInputSlot(
                    slot_key="momentum",
                    allowed_dimension_keys=frozenset({"momentum"}),
                    allowed_output_types=frozenset({"continuous"}),
                    minimum_count=1,
                    maximum_count=1,
                ),
            ),
        }
    )
    result = compile_research_spec(
        _draft(),
        signals=_signals(),
        models=(momentum_only,),
        strategies=(_stock_strategy(),),
    )
    assert not result.runnable
    assert not result.model_instances
    assert {issue.reason_code for issue in result.issues} == {"model_signal_unaccepted"}


def test_diagnostic_directional_model_cannot_connect_to_top_k() -> None:
    vote = ModelPresetDescriptor(
        preset_key="vote",
        family_key="directional_voting",
        output_type="directional_score",
        output_comparability="diagnostic_only",
        supported_frequencies=frozenset({"monthly"}),
        input_slots=_linear().input_slots,
    )
    strategy = StrategyPresetDescriptor(
        **{
            **_stock_strategy().model_dump(),
            "compatible_model_output_types": frozenset({"continuous_score", "directional_score"}),
        }
    )
    draft = ResearchDraftSelection(**{**_draft().model_dump(), "model_preset_keys": ("vote",)})
    result = compile_research_spec(
        draft,
        signals=_signals(),
        models=(vote,),
        strategies=(strategy,),
    )
    assert not result.runnable
    assert [issue.reason_code for issue in result.issues] == [
        "strategy_requires_continuous_comparable_score"
    ]


def test_fingerprint_is_stable_when_selection_order_changes() -> None:
    normal = compile_research_spec(
        _draft(), signals=_signals(), models=(_linear(),), strategies=(_stock_strategy(),)
    )
    reversed_draft = ResearchDraftSelection(
        **{
            **_draft().model_dump(),
            "factor_variant_keys": tuple(reversed(_draft().factor_variant_keys)),
            "signal_version_keys": tuple(reversed(_draft().signal_version_keys)),
        }
    )
    reversed_result = compile_research_spec(
        reversed_draft,
        signals=reversed(_signals()),
        models=(_linear(),),
        strategies=(_stock_strategy(),),
    )
    assert normal.specification_fingerprint == reversed_result.specification_fingerprint
