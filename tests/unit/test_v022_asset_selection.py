from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from style_rotation.v022.asset_selection import _selection_group
from style_rotation.v022.graph import AssetContextSnapshot


def _explicit_document() -> dict[str, object]:
    selection_id = uuid.UUID("10000000-0000-4000-8000-000000000001")
    selection_artifact_id = uuid.UUID("10000000-0000-4000-8000-000000000002")
    return {
        "contract_version": "v0.22.0",
        "selection_kind": "explicit_security_selection",
        "asset_context_key": "explicit_0123456789abcdef",
        "asset_registry_release_id": "10000000-0000-4000-8000-000000000003",
        "asset_registry_artifact_id": "10000000-0000-4000-8000-000000000004",
        "asset_registry_catalog_version": "0.21.1",
        "explicit_asset_selection_id": str(selection_id),
        "explicit_asset_selection_artifact_id": str(selection_artifact_id),
        "selection_group": "stock",
        "members": [
            {
                "ordinal": 0,
                "security_id": "10000000-0000-4000-8000-000000000005",
                "security_key": "aapl",
                "instrument_type": "Common Stock",
            },
            {
                "ordinal": 1,
                "security_id": "10000000-0000-4000-8000-000000000006",
                "security_key": "msft",
                "instrument_type": "Common Stock",
            },
        ],
    }


def test_explicit_asset_context_requires_exact_selection_identity() -> None:
    snapshot = AssetContextSnapshot.model_validate(_explicit_document())
    assert snapshot.asset_set_definition_id is None
    assert snapshot.selection_group == "stock"
    assert tuple(item.security_key for item in snapshot.members) == ("aapl", "msft")

    invalid = _explicit_document()
    invalid["asset_set_definition_id"] = "10000000-0000-4000-8000-000000000007"
    with pytest.raises(ValidationError, match="only its complete Selection identity"):
        AssetContextSnapshot.model_validate(invalid)


def test_first_explicit_selection_contract_separates_stocks_from_funds() -> None:
    assert _selection_group("Common Stock") == "stock"
    assert _selection_group("ADR") == "stock"
    assert _selection_group("Equity ETF") == "fund"
    assert _selection_group("Futures-Linked ETP") == "fund"
    assert _selection_group("Equity Index") is None
