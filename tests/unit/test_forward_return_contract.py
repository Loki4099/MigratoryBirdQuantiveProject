from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.data.forward_return_contracts import ForwardReturnCatalog

PROJECT_ROOT = Path(__file__).parents[2]
CATALOG_PATH = PROJECT_ROOT / "v0.2" / "catalogs" / "forward_returns.v0.2.0.json"


def _payload() -> dict[str, object]:
    value = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_catalog_defines_parallel_weekly_and_monthly_targets() -> None:
    catalog = ForwardReturnCatalog.model_validate(_payload())
    assert len(catalog.definitions) == 2
    assert {item.frequency for item in catalog.definitions} == {"weekly", "monthly"}
    assert all(
        item.included_member_roles == ["candidate", "benchmark"] for item in catalog.definitions
    )


def test_contract_rejects_frequency_rule_mismatch() -> None:
    payload = _payload()
    definitions = payload["definitions"]
    assert isinstance(definitions, list)
    definitions[0]["decision_rule"] = "last_common_session_of_calendar_month"
    with pytest.raises(ValidationError, match="frequency and decision rule"):
        ForwardReturnCatalog.model_validate(payload)
