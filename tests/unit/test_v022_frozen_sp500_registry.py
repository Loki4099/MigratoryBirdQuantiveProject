from __future__ import annotations

from typing import Any, cast

import pytest

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.v022.frozen_sp500_registry import (
    FROZEN_SP500_REGISTRY_CATALOG_VERSION,
    FrozenSp500RegistryPublicationService,
)


def test_registry_uses_new_immutable_v5_identity() -> None:
    assert FROZEN_SP500_REGISTRY_CATALOG_VERSION == "0.22.3"
    assert semantic_version_number(FROZEN_SP500_REGISTRY_CATALOG_VERSION) == 22_004


class _Rows:
    def __init__(self, row: dict[str, object]) -> None:
        self._row = row

    def mappings(self) -> _Rows:
        return self

    def one_or_none(self) -> dict[str, object]:
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def execute(self, statement: object, parameters: dict[str, object]) -> _Rows:
        self.sql = str(statement)
        self.parameters = parameters
        return _Rows({"base_release_id": "base"})


def test_registry_requires_exact_published_v5_gate4() -> None:
    connection = _Connection()

    FrozenSp500RegistryPublicationService._load_inputs(cast(Any, connection))

    assert connection.parameters["risk_version"] == 5
    assert connection.parameters["gate_version"] == 4
    assert "gate.ranking_eligibility='rankable_research'" in connection.sql
    assert "gate_artifact.status='published'" in connection.sql


def test_registry_rejects_duplicate_symbols() -> None:
    profiles = [
        {"symbol": "BRK.B", "risk_dataset_member": True},
        {"symbol": "brk.b", "risk_dataset_member": True},
    ]

    with pytest.raises(ValueError, match="duplicate symbols"):
        FrozenSp500RegistryPublicationService._validate_profiles(profiles)


def test_registry_requires_executable_risk_members() -> None:
    profiles = [{"symbol": "SPY", "risk_dataset_member": False}]

    with pytest.raises(ValueError, match="no executable risk members"):
        FrozenSp500RegistryPublicationService._validate_profiles(profiles)
