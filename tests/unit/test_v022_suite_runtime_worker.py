from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any, cast

from style_rotation.v022.dag import (
    ClaimedGraphWork,
    GraphDagService,
    finalize_released_graph_run,
)
from style_rotation.v022.suite_runtime_commands import SUITE_RUNTIME_EXECUTOR_VERSION
from style_rotation.v022.suite_runtime_planner import (
    PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT,
    PROCESSING_RUNTIME_EXECUTOR_VERSION,
)
from style_rotation.v022.suite_runtime_worker import (
    _latest_active_run,
    _lock_next_suite,
    _obsolete_active_runs,
)


class _EmptyScalars:
    def all(self) -> list[object]:
        return []

    def __iter__(self) -> Iterator[object]:
        return iter(())


class _CaptureConnection:
    sql = ""
    parameters: dict[str, object] = {}

    def scalars(self, statement: object, parameters: dict[str, object]) -> _EmptyScalars:
        self.sql = str(statement)
        self.parameters = parameters
        return _EmptyScalars()

    def scalar(self, statement: object, parameters: dict[str, object]) -> None:
        self.sql = str(statement)
        self.parameters = parameters
        return None


class _ExecuteCapture:
    sql = ""
    parameters: dict[str, object] = {}

    def execute(self, statement: object, parameters: dict[str, object]) -> None:
        self.sql = str(statement)
        self.parameters = parameters


class _RowCount:
    rowcount = 1


class _RenewConnection:
    sql = ""
    parameters: dict[str, object] = {}

    def execute(self, statement: object, parameters: dict[str, object]) -> _RowCount:
        self.sql = str(statement)
        self.parameters = parameters
        return _RowCount()


class _Begin:
    def __init__(self, connection: _RenewConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _RenewConnection:
        return self.connection

    def __exit__(self, *_args: object) -> None:
        return None


class _RenewEngine:
    def __init__(self) -> None:
        self.connection = _RenewConnection()

    def begin(self) -> _Begin:
        return _Begin(self.connection)


def test_worker_never_automatically_retries_failed_or_cancelled_suites() -> None:
    connection = _CaptureConnection()

    assert _lock_next_suite(cast(Any, connection)) is None

    assert "run.status IN ('failed','cancelled')" not in connection.sql
    assert "retry_plan" not in connection.sql
    assert "active_round.status='active'" in connection.sql
    assert "v022_result_element_diagnostic" in connection.sql
    assert connection.parameters["runtime_executor_version"] == SUITE_RUNTIME_EXECUTOR_VERSION


def test_processing_identity_is_independent_from_the_trainable_runtime_fix() -> None:
    assert PROCESSING_RUNTIME_EXECUTOR_VERSION == "v022-first-slice-runtime-20"
    assert SUITE_RUNTIME_EXECUTOR_VERSION == "v022-first-slice-runtime-37"
    assert (
        PROCESSING_RUNTIME_ENVIRONMENT_FINGERPRINT
        == "12ea0bc3640f5047103a0af474b410109d6feec799e82a7aeff80ac659a88c97"
    )


def test_worker_never_resumes_an_active_run_from_an_obsolete_runtime() -> None:
    connection = _CaptureConnection()
    suite_id = uuid.uuid4()

    assert _latest_active_run(cast(Any, connection), suite_id) is None

    assert "JOIN experiment.v022_suite_runtime_plan plan" in connection.sql
    assert "plan.executor_version=:runtime_executor_version" in connection.sql
    assert connection.parameters == {
        "suite": suite_id,
        "runtime_executor_version": SUITE_RUNTIME_EXECUTOR_VERSION,
    }


def test_worker_identifies_only_obsolete_active_runs_for_cancellation() -> None:
    connection = _CaptureConnection()
    suite_id = uuid.uuid4()

    assert _obsolete_active_runs(cast(Any, connection), suite_id) == ()

    assert "run.status IN ('ready','running')" in connection.sql
    assert "plan.executor_version<>:runtime_executor_version" in connection.sql
    assert connection.parameters == {
        "suite": suite_id,
        "runtime_executor_version": SUITE_RUNTIME_EXECUTOR_VERSION,
    }


def test_long_trainable_work_can_renew_only_its_exact_active_fence() -> None:
    engine = _RenewEngine()
    claim = ClaimedGraphWork(uuid.uuid4(), 7, "aggregation")

    GraphDagService(cast(Any, engine)).renew(
        claim,
        worker_key="suite-worker",
        lease_seconds=3600,
    )

    assert "status='running'" in engine.connection.sql
    assert "lease_owner=:worker" in engine.connection.sql
    assert "fencing_token=:fence" in engine.connection.sql
    assert "cancel_requested_at IS NULL" in engine.connection.sql
    assert engine.connection.parameters == {
        "item": claim.graph_work_item_id,
        "worker": "suite-worker",
        "fence": 7,
        "lease": 3600,
    }


def test_released_running_run_is_closed_when_no_live_consumers_remain() -> None:
    connection = _ExecuteCapture()
    run_id = uuid.uuid4()

    finalize_released_graph_run(cast(Any, connection), run_id)

    assert "SET status='cancelled'" in connection.sql
    assert "consumer.released_at IS NULL" in connection.sql
    assert connection.parameters == {"run": run_id}
