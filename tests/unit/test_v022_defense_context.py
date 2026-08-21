from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import date
from typing import Any, cast

import pytest
from sqlalchemy.engine import Connection

from style_rotation.v022.defense_context import (
    DefenseExecutionContextService,
    DefenseResolvedInputBinding,
    _BaseContext,
    _Identity,
    _Package,
    _ResolvedInput,
)
from style_rotation.v022.graph import AssetContextSnapshot


class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def one_or_none(self) -> Any | None:
        if len(self._rows) > 1:
            raise AssertionError("fixture returned more than one row")
        return self._rows[0] if self._rows else None

    def one(self) -> Any:
        if len(self._rows) != 1:
            raise AssertionError("fixture must return exactly one row")
        return self._rows[0]


class _CaptureConnection:
    def __init__(self, results: list[_Rows] | None = None) -> None:
        self.statements: list[str] = []
        self._results = list(results or [])

    def execute(self, statement: Any, parameters: Any = None) -> _Rows:
        del parameters
        self.statements.append(str(statement))
        return self._results.pop(0) if self._results else _Rows([])


class _Engine:
    def __init__(self, connection: _CaptureConnection) -> None:
        self.connection = connection

    def begin(self) -> nullcontext[_CaptureConnection]:
        return nullcontext(self.connection)


def _identity() -> _Identity:
    return _Identity(uuid.uuid4(), "a" * 64)


def _asset_context() -> AssetContextSnapshot:
    return AssetContextSnapshot.model_validate(
        {
            "contract_version": "v0.22.0",
            "selection_kind": "fixed_asset_set",
            "asset_context_key": "us_style_rotation_4_etf_sample_v1",
            "asset_registry_release_id": str(uuid.uuid4()),
            "asset_registry_artifact_id": str(uuid.uuid4()),
            "asset_registry_catalog_version": "0.21.1",
            "asset_set_definition_id": str(uuid.uuid4()),
            "members": [
                {
                    "ordinal": 0,
                    "security_id": str(uuid.uuid4()),
                    "security_key": "iwf",
                    "instrument_type": "Equity ETF",
                }
            ],
        }
    )


def _base() -> _BaseContext:
    return _BaseContext(
        uuid.uuid4(),
        _identity(),
        "b" * 64,
        date(2020, 1, 2),
        date(2020, 12, 31),
        _asset_context(),
    )


def _package() -> _Package:
    return _Package(
        uuid.uuid4(),
        _identity(),
        "c" * 64,
        uuid.uuid4(),
        _identity(),
        {"rule_type": "fixed_budget"},
        uuid.uuid4(),
        _identity(),
        uuid.uuid4(),
        _identity(),
        uuid.uuid4(),
        uuid.uuid4(),
        _identity(),
    )


def _input() -> _ResolvedInput:
    binding = DefenseResolvedInputBinding(
        input_key="defensive_asset__ief",
        input_role="defensive_asset",
        allocation_member_ordinal=1,
        dataset_publication_id=uuid.uuid4(),
        dataset_artifact_id=uuid.uuid4(),
        dataset_fingerprint="d" * 64,
        dataset_key="canonical_market",
        dataset_version_number=1,
        calendar_version_id=uuid.uuid4(),
        calendar_artifact_id=uuid.uuid4(),
        calendar_fingerprint="e" * 64,
        coverage_start=date(2019, 1, 2),
        coverage_end=date(2020, 12, 31),
        security_ids=(uuid.uuid4(),),
    )
    document = binding.model_dump(mode="json")
    return _ResolvedInput(0, binding, document, "f" * 64, _identity(), _identity())


def _top_level_item_count(value: str) -> int:
    depth = 0
    count = 1
    for character in value:
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
        elif character == "," and depth == 0:
            count += 1
    return count


def test_none_defense_creates_no_context_or_artifact() -> None:
    service = DefenseExecutionContextService(cast(Any, object()))

    assert service.publish(uuid.uuid4(), None) is None


def test_existing_context_replay_precedes_dataset_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_context_id = uuid.uuid4()
    defense_version_id = uuid.uuid4()
    context_id = uuid.uuid4()
    artifact_id = uuid.uuid4()
    existing = {
        "compiled_defense_execution_context_id": context_id,
        "artifact_id": artifact_id,
        "context_fingerprint": "6" * 64,
        "resolved_input_binding_fingerprint": "7" * 64,
        "resolved_input_binding_document": {
            "contract_version": "v0.22.0",
            "bindings": [{"frozen": "original_dataset"}],
        },
        "input_count": 1,
        "artifact_type": "v022_compiled_defense_execution_context",
        "version_number": 1,
        "status": "published",
        "semantic_fingerprint": "6" * 64,
        "child_count": 1,
    }
    connection = _CaptureConnection([_Rows([]), _Rows([existing])])
    service = DefenseExecutionContextService(cast(Any, _Engine(connection)))
    monkeypatch.setattr(
        service,
        "_base_context",
        lambda *_: (_ for _ in ()).throw(
            AssertionError("replay must not re-resolve a later Dataset")
        ),
    )

    replayed = service.publish(base_context_id, defense_version_id)

    assert replayed is not None
    assert replayed.context_id == context_id
    assert replayed.artifact_id == artifact_id
    assert replayed.reused is True
    assert len(connection.statements) == 2


def test_parent_projection_insert_has_one_value_per_column() -> None:
    connection = _CaptureConnection()
    item = _input()

    DefenseExecutionContextService._write_projection(
        cast(Connection, connection),
        uuid.uuid4(),
        context_id=uuid.uuid4(),
        base=_base(),
        package=_package(),
        binding_document={"contract_version": "v0.22.0", "bindings": [item.document]},
        binding_fingerprint="1" * 64,
        inputs=(item,),
        context_fingerprint="2" * 64,
    )

    statement = " ".join(connection.statements[0].split())
    columns, values = statement.split(") VALUES (", maxsplit=1)
    column_list = columns.split("(", maxsplit=1)[1]
    value_list = values.rsplit(")", maxsplit=1)[0]
    assert _top_level_item_count(column_list) == 19
    assert _top_level_item_count(value_list) == 19


def test_ma200_context_requires_two_hundred_total_published_observations() -> None:
    base = _base()
    asset_ids = tuple(uuid.uuid4() for _ in range(5))
    candidate = {
        "dataset_publication_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "dataset_key": "defense_market",
        "version_number": 1,
        "coverage_start": date(2018, 1, 2),
        "coverage_end": base.coverage_end,
        "calendar_version_id": uuid.uuid4(),
        "dataset_fingerprint": "3" * 64,
        "calendar_artifact_id": uuid.uuid4(),
        "calendar_fingerprint": "4" * 64,
    }
    actual = [
        {
            "asset_id": asset_id,
            "observation_count": 199 if ordinal == 0 else 250,
            "coverage_start": date(2018, 1, 2),
            "coverage_end": base.coverage_end,
        }
        for ordinal, asset_id in enumerate(asset_ids)
    ]
    connection = _CaptureConnection([_Rows([candidate]), _Rows(actual)])

    with pytest.raises(ValueError, match="market_coverage_missing"):
        DefenseExecutionContextService._market_dataset(
            cast(Connection, connection),
            base,
            asset_ids,
            asset_ids[0],
            200,
        )


def test_reserve_dataset_may_start_after_the_risk_history() -> None:
    base = _base()
    candidate = {
        "dataset_publication_id": uuid.uuid4(),
        "artifact_id": uuid.uuid4(),
        "dataset_key": "reserve_return",
        "version_number": 1,
        "coverage_start": date(2020, 8, 3),
        "coverage_end": base.coverage_end,
        "calendar_version_id": uuid.uuid4(),
        "dataset_fingerprint": "3" * 64,
        "calendar_artifact_id": uuid.uuid4(),
        "calendar_fingerprint": "4" * 64,
    }
    connection = _CaptureConnection(
        [
            _Rows([candidate]),
            _Rows([(date(2020, 8, 3), base.coverage_end, 100)]),
        ]
    )

    resolved = DefenseExecutionContextService._reserve_dataset(
        cast(Connection, connection), base, _package()
    )

    assert resolved["dataset_publication_id"] == candidate["dataset_publication_id"]
