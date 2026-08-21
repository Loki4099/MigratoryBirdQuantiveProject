from __future__ import annotations

import uuid
from typing import Any, cast

import pytest
from sqlalchemy.engine import Connection

from style_rotation.v022.compiler_service import (
    _composed_defense_version_ids,
    _load_defenses,
)
from style_rotation.v022.graph import CompilationResult, DefenseSpec, StrategyBranch


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows


class _Connection:
    def __init__(
        self,
        base_rows: list[dict[str, Any]],
        *,
        composed: bool,
        package_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._results = [_Rows(base_rows)]
        if package_rows is not None:
            self._results.append(_Rows(package_rows))
        self._composed = composed

    def execute(self, statement: Any, parameters: Any = None) -> _Rows:
        del statement, parameters
        return self._results.pop(0)

    def scalar(self, statement: Any, parameters: Any = None) -> bool:
        del statement, parameters
        return self._composed


def _base_row() -> dict[str, Any]:
    return {
        "variant_key": "fixed20_defense",
        "defense_version_id": uuid.uuid4(),
        "version_fingerprint": "1" * 64,
    }


def _package_row(base: dict[str, Any]) -> dict[str, Any]:
    registry_release_id = uuid.uuid4()
    registry_artifact_id = uuid.uuid4()
    return {
        **base,
        "supported_asset_set_count": 1,
        "timing_policy_version_id": uuid.uuid4(),
        "timing_version_fingerprint": "2" * 64,
        "supported_frequencies": ["weekly", "monthly"],
        "allocation_policy_version_id": uuid.uuid4(),
        "allocation_version_fingerprint": "3" * 64,
        "supported_ordinal": 0,
        "asset_context_key": "us_style_rotation_4_etf_sample_v1",
        "asset_registry_release_id": registry_release_id,
        "asset_registry_artifact_id": registry_artifact_id,
        "asset_set_definition_id": uuid.uuid4(),
        "timing_family_pinned": True,
        "timing_variant_pinned": True,
        "timing_version_pinned": True,
        "allocation_family_pinned": True,
        "allocation_variant_pinned": True,
        "allocation_version_pinned": True,
    }


def test_legacy_release_retains_plain_defense_version_identity() -> None:
    base = _base_row()
    connection = _Connection([base], composed=False)

    loaded = _load_defenses(cast(Connection, connection), uuid.uuid4())

    assert loaded == {"fixed20_defense": str(base["defense_version_id"])}


def test_composed_release_loads_exact_package_and_policy_identity() -> None:
    base = _base_row()
    package = _package_row(base)
    connection = _Connection([base], composed=True, package_rows=[package])

    loaded = _load_defenses(cast(Connection, connection), uuid.uuid4())

    specification = loaded["fixed20_defense"]
    assert isinstance(specification, DefenseSpec)
    assert specification.version_id == str(base["defense_version_id"])
    assert specification.timing_policy_version_id == str(
        package["timing_policy_version_id"]
    )
    assert specification.allocation_policy_version_id == str(
        package["allocation_policy_version_id"]
    )
    assert specification.supported_frequencies == ("weekly", "monthly")


def test_composed_release_rejects_policy_not_in_pinned_release() -> None:
    base = _base_row()
    package = _package_row(base)
    package["timing_version_pinned"] = False
    connection = _Connection([base], composed=True, package_rows=[package])

    with pytest.raises(ValueError, match="exact Package, Timing and Allocation"):
        _load_defenses(cast(Connection, connection), uuid.uuid4())


def test_composed_context_plan_deduplicates_sorts_and_omits_none() -> None:
    lower = uuid.UUID("00000000-0000-4000-8000-000000000001")
    upper = uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")

    def branch(version_id: uuid.UUID | None, *, composed: bool) -> StrategyBranch:
        return StrategyBranch(
            "branch",
            "aggregation",
            "strategy",
            str(uuid.uuid4()),
            None,
            None,
            None,
            None,
            "defense" if version_id is not None else None,
            str(version_id) if version_id is not None else None,
            "1" * 64 if composed else None,
            str(uuid.uuid4()) if composed else None,
            "2" * 64 if composed else None,
            str(uuid.uuid4()) if composed else None,
            "3" * 64 if composed else None,
        )

    compiled = CompilationResult(
        "4" * 64,
        {},
        (),
        (),
        (),
        (
            branch(upper, composed=True),
            branch(lower, composed=True),
            branch(upper, composed=True),
            branch(None, composed=False),
        ),
    )

    assert _composed_defense_version_ids(compiled) == (lower, upper)
