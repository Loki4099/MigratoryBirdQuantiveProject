from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from style_rotation.catalog.contracts import ResearchScopeCatalog

CATALOG = Path("v0.2/catalogs/research_scope.v0.2.0.json")


def test_research_scope_catalog_has_expected_roles_and_nonasset_rate() -> None:
    catalog = ResearchScopeCatalog.model_validate_json(CATALOG.read_text(encoding="utf-8"))

    assert [member.asset for member in catalog.universe.members if member.role == "candidate"] == [
        "iwf",
        "iwd",
        "iwo",
        "iwn",
    ]
    assert [member.asset for member in catalog.universe.members if member.role == "benchmark"] == [
        "spy"
    ]
    assert "DGS3MO" not in {asset.key for asset in catalog.assets}
    assert any(
        requirement.series_key == "DGS3MO" and requirement.subject == "reference_series"
        for requirement in catalog.data_requirement_set.requirements
    )


def test_research_scope_rejects_noncontiguous_universe_ordinals() -> None:
    payload = ResearchScopeCatalog.model_validate_json(
        CATALOG.read_text(encoding="utf-8")
    ).model_dump(mode="json")
    payload["universe"]["members"][1]["ordinal"] = 7

    with pytest.raises(ValidationError, match="contiguous"):
        ResearchScopeCatalog.model_validate(payload)
