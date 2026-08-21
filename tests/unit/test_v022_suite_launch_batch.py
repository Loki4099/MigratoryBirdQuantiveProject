from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path
from typing import Any, cast

import pytest

from style_rotation.v022.suite_launch_batch import (
    _batch_status,
    _normalize_frequencies,
    _require_rankable_frequency_graph,
)


def test_launch_batch_normalizes_frequency_order_and_rejects_empty() -> None:
    assert _normalize_frequencies(("monthly", "weekly")) == ("weekly", "monthly")
    assert _normalize_frequencies(("weekly", "weekly")) == ("weekly",)
    with pytest.raises(ValueError):
        _normalize_frequencies(())


def test_launch_batch_status_preserves_partial_and_terminal_states() -> None:
    assert _batch_status([]) == "planning"
    assert _batch_status([{"status": "planning"}]) == "planning"
    assert _batch_status([{"status": "not_started"}, {"status": "not_started"}]) == ("submitted")
    assert _batch_status([{"status": "completed"}, {"status": "running"}]) == "running"
    assert _batch_status([{"status": "completed"}, {"status": "completed"}]) == ("completed")
    assert _batch_status([{"status": "completed"}, {"status": "failed"}]) == "failed"


class _Rows:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _Rows:
        return self

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _AdmissionConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.sql = ""
        self.parameters: dict[str, object] = {}

    def execute(self, statement: object, parameters: dict[str, object]) -> _Rows:
        self.sql = str(statement)
        self.parameters = parameters
        return _Rows(self.rows)


def test_launch_batch_requires_exact_published_runtime_and_gate() -> None:
    graph_id = uuid.uuid4()
    connection = _AdmissionConnection(
        [
            {
                "frequency": "weekly",
                "graph_status": "published",
                "evaluation_cohort_version_id": uuid.uuid4(),
                "cohort_status": "published",
                "runtime_status": "published",
                "gate_status": "published",
            }
        ]
    )

    _require_rankable_frequency_graph(
        cast(Any, connection),
        compiled_research_graph_id=graph_id,
        frequency="weekly",
    )

    assert connection.parameters == {"graph": graph_id, "cohort_version": 11}
    assert "v022_evaluation_cohort_runtime_contract" in connection.sql
    assert "v022_dataset_gate_assessment" in connection.sql
    assert "gate.dataset_publication_id=cohort.dataset_publication_id" in connection.sql


def test_launch_batch_rejects_half_published_cohort() -> None:
    connection = _AdmissionConnection(
        [
            {
                "frequency": "monthly",
                "graph_status": "published",
                "evaluation_cohort_version_id": uuid.uuid4(),
                "cohort_status": "published",
                "runtime_status": "draft",
                "gate_status": "published",
            }
        ]
    )

    with pytest.raises(ValueError, match="runtime/Gate"):
        _require_rankable_frequency_graph(
            cast(Any, connection),
            compiled_research_graph_id=uuid.uuid4(),
            frequency="monthly",
        )


def test_launch_batch_migration_is_append_only_and_downgrade_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = Path("migrations/versions/20260818_120_v022_launch_batch.py")
    spec = importlib.util.spec_from_file_location("v022_launch_batch_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    statements: list[str] = []
    monkeypatch.setattr(module.op, "execute", statements.append)

    module.upgrade()
    upgrade_sql = "\n".join(statements)
    assert module.revision == "20260818_120_v022_launch_batch"
    assert module.down_revision == "20260818_119_v022_restore_root"
    assert "CREATE TABLE experiment.v022_suite_launch_batch" in upgrade_sql
    assert "PRIMARY KEY (suite_launch_batch_id,frequency)" in upgrade_sql
    assert "Suite Launch Batch child identity is immutable" in upgrade_sql
    assert "Suite Launch Batch identity is append-only" in upgrade_sql

    statements.clear()
    module.downgrade()
    downgrade_sql = "\n".join(statements)
    assert "Cannot downgrade nonempty v0.22 Suite Launch Batch identities" in downgrade_sql
