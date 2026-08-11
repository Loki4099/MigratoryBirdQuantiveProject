from __future__ import annotations

import uuid

from style_rotation.api.schemas import WorkspaceDraftSelection
from style_rotation.workspace.drafts import _validate_selection


def _selection(security_id: str) -> dict[str, object]:
    return {
        "frequency": "weekly",
        "asset_security_ids": [security_id],
        "factor_variant_keys": [],
        "signal_version_keys": [],
        "model_preset_keys": [],
        "model_target_keys": ["cross_sectional_relative_return__h5"],
        "strategy_preset_keys": [],
    }


def test_legacy_api_draft_defaults_each_selected_asset_to_canonical_market_bars() -> None:
    security_id = str(uuid.uuid4())
    selection = WorkspaceDraftSelection.model_validate(_selection(security_id))
    assert selection.model_dump(mode="json")["asset_data_inputs"] == {
        security_id: ["canonical_market_bars"]
    }


def test_explicit_empty_asset_input_selection_is_not_repaired() -> None:
    security_id = str(uuid.uuid4())
    payload = {**_selection(security_id), "asset_data_inputs": {security_id: []}}
    selection = WorkspaceDraftSelection.model_validate(payload)
    assert selection.model_dump(mode="json")["asset_data_inputs"] == {security_id: []}

    stored_payload = dict(payload)
    _validate_selection(stored_payload)
    assert stored_payload["asset_data_inputs"] == {security_id: []}


def test_legacy_stored_draft_is_migrated_without_a_database_rewrite() -> None:
    security_id = str(uuid.uuid4())
    payload = _selection(security_id)
    _validate_selection(payload)
    assert payload["asset_data_inputs"] == {security_id: ["canonical_market_bars"]}
