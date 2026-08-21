from __future__ import annotations

import uuid
from contextlib import nullcontext
from typing import Any, cast

import pytest
from sqlalchemy.engine import Connection, Engine, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.experiment_identity import (
    ConfigurationSnapshotService,
    _configuration_execution_context_binding,
)
from style_rotation.v022.suite_identity import _suite_execution_contexts


class _Rows:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, Any]]:
        return self._rows

    def one_or_none(self) -> dict[str, Any] | None:
        if len(self._rows) > 1:
            raise AssertionError("fixture returned more than one row")
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, results: list[_Rows], *, composed: bool = True) -> None:
        self._results = list(results)
        self._composed = composed

    def execute(self, statement: Any, parameters: Any = None) -> _Rows:
        del statement, parameters
        return self._results.pop(0)

    def scalar(self, statement: Any, parameters: Any = None) -> bool:
        del statement, parameters
        return self._composed


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self._connection = connection

    def connect(self) -> nullcontext[_Connection]:
        return nullcontext(self._connection)


def _branch(*, defense_version_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "compiled_research_graph_id": uuid.uuid4(),
        "compiled_strategy_branch_id": uuid.uuid4(),
        "defense_version_id": defense_version_id,
    }


def _risk(branch: dict[str, Any]) -> dict[str, Any]:
    return {
        "compiled_research_graph_id": branch["compiled_research_graph_id"],
        "artifact_id": uuid.uuid4(),
        "context_fingerprint": "1" * 64,
        "artifact_type": "v022_compiled_execution_data_context",
        "artifact_status": "published",
    }


def _defense(
    *, risk_context_id: uuid.UUID, defense_version_id: uuid.UUID
) -> dict[str, Any]:
    return {
        "compiled_execution_data_context_id": risk_context_id,
        "defense_version_id": defense_version_id,
        "defense_package_artifact_id": uuid.uuid4(),
        "timing_policy_version_id": uuid.uuid4(),
        "timing_policy_artifact_id": uuid.uuid4(),
        "allocation_policy_version_id": uuid.uuid4(),
        "allocation_policy_artifact_id": uuid.uuid4(),
        "defense_context_artifact_id": uuid.uuid4(),
        "context_fingerprint": "2" * 64,
        "context_artifact_type": "v022_compiled_defense_execution_context",
        "context_artifact_status": "published",
        "package_artifact_status": "published",
        "timing_artifact_status": "published",
        "allocation_artifact_status": "published",
    }


def test_legacy_snapshot_remains_context_binding_free() -> None:
    branch = _branch(defense_version_id=None)
    connection = _Connection([], composed=False)

    binding = _configuration_execution_context_binding(
        cast(Connection, connection),
        branch=cast(RowMapping, branch),
        composed=False,
        compiled_execution_data_context_id=None,
        compiled_defense_execution_context_id=None,
    )

    assert binding is None
    with pytest.raises(ValueError, match="Legacy Configuration Snapshots"):
        _configuration_execution_context_binding(
            cast(Connection, connection),
            branch=cast(RowMapping, branch),
            composed=False,
            compiled_execution_data_context_id=uuid.uuid4(),
            compiled_defense_execution_context_id=None,
        )


def test_composed_none_snapshot_binds_only_exact_risk_context() -> None:
    branch = _branch(defense_version_id=None)
    risk_context_id = uuid.uuid4()
    risk = _risk(branch)
    connection = _Connection([_Rows([risk])])

    binding = _configuration_execution_context_binding(
        cast(Connection, connection),
        branch=cast(RowMapping, branch),
        composed=True,
        compiled_execution_data_context_id=risk_context_id,
        compiled_defense_execution_context_id=None,
    )

    assert binding is not None
    assert binding.document["defense"] is None
    assert binding.document["risk_execution_context"] == {
        "compiled_execution_data_context_id": str(risk_context_id),
        "artifact_id": str(risk["artifact_id"]),
        "context_fingerprint": risk["context_fingerprint"],
    }
    assert binding.fingerprint == sha256_hexdigest(binding.document)
    assert [(item.role, item.ordinal) for item in binding.dependencies()] == [
        ("compiled_execution_data_context", 0)
    ]


def test_composed_defended_snapshot_binds_exact_package_policies_and_context() -> None:
    defense_version_id = uuid.uuid4()
    branch = _branch(defense_version_id=defense_version_id)
    risk_context_id = uuid.uuid4()
    defense_context_id = uuid.uuid4()
    risk = _risk(branch)
    defense = _defense(
        risk_context_id=risk_context_id, defense_version_id=defense_version_id
    )
    connection = _Connection([_Rows([risk]), _Rows([defense])])

    binding = _configuration_execution_context_binding(
        cast(Connection, connection),
        branch=cast(RowMapping, branch),
        composed=True,
        compiled_execution_data_context_id=risk_context_id,
        compiled_defense_execution_context_id=defense_context_id,
    )

    assert binding is not None
    assert binding.document["defense"] == {
        "defense_version_id": str(defense_version_id),
        "package_artifact_id": str(defense["defense_package_artifact_id"]),
        "timing_policy_version_id": str(defense["timing_policy_version_id"]),
        "timing_policy_artifact_id": str(defense["timing_policy_artifact_id"]),
        "allocation_policy_version_id": str(
            defense["allocation_policy_version_id"]
        ),
        "allocation_policy_artifact_id": str(
            defense["allocation_policy_artifact_id"]
        ),
        "execution_context": {
            "compiled_defense_execution_context_id": str(defense_context_id),
            "artifact_id": str(defense["defense_context_artifact_id"]),
            "context_fingerprint": defense["context_fingerprint"],
        },
    }
    assert [item.role for item in binding.dependencies()] == [
        "compiled_execution_data_context",
        "defense_package",
        "defense_timing_policy_version",
        "defense_allocation_policy_version",
        "compiled_defense_execution_context",
    ]


def test_suite_resolves_one_graph_risk_context_and_exact_branch_defense_context() -> None:
    risk_context_id = uuid.uuid4()
    none_branch = _branch(defense_version_id=None)
    defended_branch = _branch(defense_version_id=uuid.uuid4())
    defense_context_id = uuid.uuid4()
    connection = _Connection(
        [
            _Rows(
                [
                    {
                        "compiled_execution_data_context_id": risk_context_id,
                        "artifact_type": "v022_compiled_execution_data_context",
                        "artifact_status": "published",
                    }
                ]
            ),
            _Rows(
                [
                    {
                        "compiled_defense_execution_context_id": defense_context_id,
                        "artifact_type": "v022_compiled_defense_execution_context",
                        "artifact_status": "published",
                    }
                ]
            ),
        ]
    )

    risk_id, defense_ids = _suite_execution_contexts(
        cast(Connection, connection),
        graph_id=uuid.uuid4(),
        branches=cast(
            tuple[RowMapping, ...],
            (cast(RowMapping, none_branch), cast(RowMapping, defended_branch)),
        ),
    )

    assert risk_id == risk_context_id
    assert defense_ids[none_branch["compiled_strategy_branch_id"]] is None
    assert (
        defense_ids[defended_branch["compiled_strategy_branch_id"]]
        == defense_context_id
    )


def test_suite_fails_closed_when_graph_risk_context_is_ambiguous() -> None:
    branch = _branch(defense_version_id=None)
    risk_row = {
        "compiled_execution_data_context_id": uuid.uuid4(),
        "artifact_type": "v022_compiled_execution_data_context",
        "artifact_status": "published",
    }
    connection = _Connection([_Rows([risk_row, {**risk_row}])])

    with pytest.raises(ValueError, match="exactly one Graph Risk"):
        _suite_execution_contexts(
            cast(Connection, connection),
            graph_id=uuid.uuid4(),
            branches=(cast(RowMapping, branch),),
        )


def test_existing_snapshot_replay_loads_and_validates_context_child() -> None:
    document = {
        "contract_version": "v0.22.0",
        "compiled_research_graph_id": str(uuid.uuid4()),
        "compiled_strategy_branch_id": str(uuid.uuid4()),
        "risk_execution_context": {
            "compiled_execution_data_context_id": str(uuid.uuid4()),
            "artifact_id": str(uuid.uuid4()),
            "context_fingerprint": "1" * 64,
        },
        "defense": None,
    }
    semantic = {"contract_version": "v0.22.0", "execution_contexts": document}
    fingerprint = sha256_hexdigest(semantic)
    connection = _Connection(
        [
            _Rows(
                [
                    {
                        "configuration_snapshot_id": uuid.uuid4(),
                        "artifact_id": uuid.uuid4(),
                        "configuration_fingerprint": fingerprint,
                        "semantic_identity_document": semantic,
                        "provenance_document": {"source": "fixture"},
                        "display_document": {"name": "fixture"},
                        "status": "published",
                        "binding_document": document,
                        "binding_fingerprint": sha256_hexdigest(document),
                    }
                ]
            )
        ]
    )
    service = ConfigurationSnapshotService(
        cast(Engine, _Engine(connection))
    )

    replay = service._existing(fingerprint)

    assert replay is not None
    assert replay.reused is True
    assert replay.execution_context_binding == document


def test_existing_snapshot_replay_rejects_semantic_fingerprint_drift() -> None:
    semantic = {"contract_version": "v0.22.0", "defense_budget": 0.2}
    connection = _Connection(
        [
            _Rows(
                [
                    {
                        "configuration_snapshot_id": uuid.uuid4(),
                        "artifact_id": uuid.uuid4(),
                        "configuration_fingerprint": "f" * 64,
                        "semantic_identity_document": semantic,
                        "provenance_document": {"source": "fixture"},
                        "display_document": {"name": "fixture"},
                        "status": "published",
                        "binding_document": None,
                        "binding_fingerprint": None,
                    }
                ]
            )
        ]
    )
    service = ConfigurationSnapshotService(cast(Engine, _Engine(connection)))

    with pytest.raises(ValueError, match="semantic identity drifted"):
        service._existing("f" * 64)
