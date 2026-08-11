from style_rotation.product.promotion import _exact_selection


def test_exact_selection_freezes_only_the_selected_model_and_strategy() -> None:
    exact = _exact_selection(
        {
            "normalized_selection": {
                "asset_security_ids": ["00000000-0000-0000-0000-000000000001"],
                "asset_data_inputs": {
                    "00000000-0000-0000-0000-000000000001": [
                        "canonical_market_bars"
                    ]
                },
                "factor_variant_keys": ["return_w20"],
                "signal_version_keys": ["momentum_w20"],
                "model_preset_keys": ["selected_model", "sibling_model"],
                "model_target_keys": ["future_return__h5", "future_return__h21"],
                "strategy_preset_keys": ["selected_strategy", "sibling_strategy"],
                "frequency": "weekly",
            },
            "model_instance_key": "selected_model__future_return__h5__weekly",
            "model_preset_key": "selected_model",
            "model_target_key": "future_return__h5",
            "model_slot_assignments": [
                {"slot_key": "inputs", "signal_version_keys": ["momentum_w20"]}
            ],
            "model_parameters": {"weighting": "equal"},
            "branch_key": "selected_branch",
            "strategy_family_key": "multi_etf_top_k",
            "strategy_preset_key": "selected_strategy",
            "schedule_key": "weekly",
            "rule_graph": {"parameters": {"target_k": 2}},
        }
    )

    assert exact["schema_version"] == "product_exact_selection_v1"
    assert exact["model"] == {
        "instance_key": "selected_model__future_return__h5__weekly",
        "preset_key": "selected_model",
        "target_key": "future_return__h5",
        "slot_assignments": [
            {"slot_key": "inputs", "signal_version_keys": ["momentum_w20"]}
        ],
        "parameters": {"weighting": "equal"},
    }
    assert exact["strategy"]["preset_key"] == "selected_strategy"
    assert exact["strategy"]["parameters"] == {"target_k": 2}
    assert "model_preset_keys" not in exact
    assert "strategy_preset_keys" not in exact
    assert "candidate_branches" not in exact
