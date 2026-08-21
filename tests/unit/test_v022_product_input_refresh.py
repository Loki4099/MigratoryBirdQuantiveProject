from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

import style_rotation.v022.product_decision_worker as decision_worker_module
from style_rotation.cli.v022_product_input import _aware_datetime
from style_rotation.cli.v022_product_worker import _run_cycle
from style_rotation.v022.product_decision_worker import (
    ProductDecisionWorker,
    _DueDecision,
)
from style_rotation.v022.product_input_refresh import ProductInputRefreshService
from style_rotation.v022.product_input_snapshot import (
    ProductInputSnapshotPublication,
    ProductInputSnapshotSpec,
)


class _MappingRows:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self._rows = rows

    def mappings(self) -> _MappingRows:
        return self

    def __iter__(self) -> Any:
        return iter(self._rows)


class _Connection:
    def __init__(self, rows: tuple[dict[str, Any], ...] = ()) -> None:
        self._rows = rows

    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, *_args: object, **_kwargs: object) -> _MappingRows:
        return _MappingRows(self._rows)


class _Engine:
    def __init__(self, rows: tuple[dict[str, Any], ...] = ()) -> None:
        self._rows = rows

    def connect(self) -> _Connection:
        return _Connection(self._rows)


class _RecordingConnection(_Connection):
    def __init__(self, engine: _RecordingEngine) -> None:
        super().__init__()
        self._engine = engine

    def execute(self, statement: object, *_args: object, **_kwargs: object) -> _MappingRows:
        self._engine.statement = str(statement)
        return super().execute(statement)


class _RecordingEngine(_Engine):
    def __init__(self) -> None:
        super().__init__()
        self.statement = ""

    def connect(self) -> _RecordingConnection:
        return _RecordingConnection(self)


def test_pending_product_inputs_distinguish_prepared_and_waiting() -> None:
    enrollment = uuid.uuid4()
    execution = uuid.uuid4()
    gate = uuid.uuid4()
    prepared_snapshot = uuid.uuid4()
    rows = tuple(
        {
            "product_enrollment_id": enrollment,
            "execution_version_id": execution,
            "decision_session_id": uuid.uuid4(),
            "session_date": date(2026, 8, 14 + ordinal),
            "decision_cutoff_at": datetime(2026, 8, 14 + ordinal, 20, tzinfo=UTC),
            "dataset_gate_assessment_id": gate,
            "candidate_dataset_gate_assessment_id": gate if ordinal == 1 else None,
            "product_input_snapshot_id": prepared_snapshot if ordinal == 0 else None,
        }
        for ordinal in range(2)
    )

    pending = ProductInputRefreshService(_Engine(rows)).pending(
        observed_at=datetime(2026, 8, 17, tzinfo=UTC)
    )

    assert [item.input_state for item in pending] == [
        "prepared",
        "ready_to_prepare",
    ]
    assert pending[0].product_input_snapshot_id == prepared_snapshot
    assert pending[1].baseline_dataset_gate_assessment_id == gate
    assert pending[1].candidate_dataset_gate_assessment_id == gate


def test_pending_product_inputs_wait_when_no_exact_gate_is_available() -> None:
    rows = (
        {
            "product_enrollment_id": uuid.uuid4(),
            "execution_version_id": uuid.uuid4(),
            "decision_session_id": uuid.uuid4(),
            "session_date": date(2026, 8, 15),
            "decision_cutoff_at": datetime(2026, 8, 15, 20, tzinfo=UTC),
            "dataset_gate_assessment_id": uuid.uuid4(),
            "candidate_dataset_gate_assessment_id": None,
            "product_input_snapshot_id": None,
        },
    )

    pending = ProductInputRefreshService(_Engine(rows)).pending(
        observed_at=datetime(2026, 8, 17, tzinfo=UTC)
    )

    assert pending[0].input_state == "awaiting_published_input"


def test_pending_gate_selection_is_deterministic_and_cutoff_safe() -> None:
    engine = _RecordingEngine()

    ProductInputRefreshService(engine).pending(
        observed_at=datetime(2026, 8, 17, tzinfo=UTC)
    )

    assert ")>=session.decision_cutoff_at" in engine.statement
    assert ")<=:observed_at" in engine.statement
    assert "candidate.blocker_count=0" in engine.statement
    assert "candidate.product_eligibility<>'ineligible'" in engine.statement
    assert "ORDER BY inputs_available_at" in engine.statement


def test_product_worker_waits_without_publishing_when_snapshot_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    due = _DueDecision(
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        uuid.uuid4(),
        date(2026, 8, 14),
        datetime(2026, 8, 14, 20, tzinfo=UTC),
        None,
    )
    monkeypatch.setattr(
        decision_worker_module, "_next_due_decision", lambda *_args: due
    )
    worker = object.__new__(ProductDecisionWorker)
    worker._engine = _Engine()  # type: ignore[assignment]

    outcome = worker.run_once(observed_at=datetime(2026, 8, 14, 21, tzinfo=UTC))

    assert outcome.status == "waiting_for_input"
    assert outcome.product_enrollment_id == due.product_enrollment_id
    assert outcome.reason == "product_input_snapshot_not_prepared"


def test_product_input_refresh_prepares_exact_explicit_gate() -> None:
    publication = ProductInputSnapshotPublication(
        uuid.uuid4(),
        uuid.uuid4(),
        "a" * 64,
        uuid.uuid4(),
        date(2021, 1, 4),
        date(2026, 8, 14),
        datetime(2026, 8, 14, 20, 1, tzinfo=UTC),
        500,
        False,
    )
    captured: list[ProductInputSnapshotSpec] = []

    class _Snapshots:
        def publish(
            self, spec: ProductInputSnapshotSpec
        ) -> ProductInputSnapshotPublication:
            captured.append(spec)
            return publication

    service = ProductInputRefreshService(_Engine())
    service._snapshots = _Snapshots()  # type: ignore[assignment]
    enrollment = uuid.uuid4()
    session = uuid.uuid4()
    gate = uuid.uuid4()

    assert service.prepare(
        product_enrollment_id=enrollment,
        decision_session_id=session,
        dataset_gate_assessment_id=gate,
        actor_key="operator",
    ) == publication
    spec = captured[0]
    assert spec.product_enrollment_id == enrollment
    assert spec.decision_session_id == session
    assert spec.dataset_gate_assessment_id == gate


def test_product_input_refresh_prepares_only_exact_ready_candidates() -> None:
    enrollment = uuid.uuid4()
    session = uuid.uuid4()
    gate = uuid.uuid4()
    rows = (
        {
            "product_enrollment_id": enrollment,
            "execution_version_id": uuid.uuid4(),
            "decision_session_id": session,
            "session_date": date(2026, 8, 15),
            "decision_cutoff_at": datetime(2026, 8, 15, 20, tzinfo=UTC),
            "dataset_gate_assessment_id": uuid.uuid4(),
            "candidate_dataset_gate_assessment_id": gate,
            "product_input_snapshot_id": None,
        },
    )
    publication = ProductInputSnapshotPublication(
        uuid.uuid4(),
        uuid.uuid4(),
        "a" * 64,
        uuid.uuid4(),
        date(2021, 1, 4),
        date(2026, 8, 15),
        datetime(2026, 8, 15, 20, 1, tzinfo=UTC),
        500,
        False,
    )
    captured: list[ProductInputSnapshotSpec] = []

    class _Snapshots:
        def publish(
            self, spec: ProductInputSnapshotSpec
        ) -> ProductInputSnapshotPublication:
            captured.append(spec)
            return publication

    service = ProductInputRefreshService(_Engine(rows))
    service._snapshots = _Snapshots()  # type: ignore[assignment]

    result = service.prepare_pending(
        observed_at=datetime(2026, 8, 17, tzinfo=UTC),
        actor_key="automatic-input-refresh",
    )

    assert result == (publication,)
    assert captured[0].product_enrollment_id == enrollment
    assert captured[0].decision_session_id == session
    assert captured[0].dataset_gate_assessment_id == gate


def test_product_worker_cycle_refreshes_inputs_before_running_decision() -> None:
    events: list[str] = []

    class _Inputs:
        def prepare_pending(self, **_kwargs: object) -> tuple[object, ...]:
            events.append("prepare")
            return (object(),)

    class _Worker:
        def run_once(self, **_kwargs: object) -> object:
            events.append("decision")
            return object()

    prepared_count, _outcome = _run_cycle(
        _Worker(),  # type: ignore[arg-type]
        _Inputs(),  # type: ignore[arg-type]
        observed_at=datetime(2026, 8, 17, tzinfo=UTC),
        actor_key="automatic-input-refresh",
        refresh_limit=50,
    )

    assert prepared_count == 1
    assert events == ["prepare", "decision"]


def test_product_input_cli_requires_aware_observed_at() -> None:
    with pytest.raises(ValueError, match="timezone"):
        _aware_datetime("2026-08-14T20:00:00")
    assert _aware_datetime("2026-08-14T20:00:00+00:00").tzinfo is not None
