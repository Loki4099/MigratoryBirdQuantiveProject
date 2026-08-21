from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from style_rotation.cli import v022_research_round_gc
from style_rotation.v022.payload_runtime import LocalPayloadObjectStore
from style_rotation.v022.research_round_gc import (
    TERMINAL_GRAPH_WORK_STATUSES,
    _count_nonterminal_graph_work,
)


class _DisposableEngine:
    disposed = False

    def dispose(self) -> None:
        self.disposed = True


def _configure_gc_cli(monkeypatch: pytest.MonkeyPatch) -> _DisposableEngine:
    engine = _DisposableEngine()

    class _Settings:
        database_url = "postgresql+psycopg://unused"
        v022_payload_directory = "relative-payload-directory"

    monkeypatch.setattr(v022_research_round_gc, "get_settings", _Settings)
    monkeypatch.setattr(
        v022_research_round_gc,
        "create_postgres_engine",
        lambda _url: engine,
    )
    monkeypatch.setattr(
        v022_research_round_gc,
        "LocalPayloadObjectStore",
        lambda _root: object(),
    )
    monkeypatch.setattr(v022_research_round_gc, "ResearchRoundGCService", lambda *_args: object())
    return engine


def test_payload_store_evict_is_exact_and_idempotent(tmp_path: Path) -> None:
    store = LocalPayloadObjectStore(tmp_path)
    published = store.publish(b"ordinary experiment bytes", file_extension="parquet")
    assert store.evict(
        published.storage_uri,
        expected_content_hash=published.content_hash,
        expected_byte_size=published.byte_size,
    ) is True
    assert store.evict(
        published.storage_uri,
        expected_content_hash=published.content_hash,
        expected_byte_size=published.byte_size,
    ) is False


def test_payload_store_evict_rejects_wrong_plan_identity(tmp_path: Path) -> None:
    store = LocalPayloadObjectStore(tmp_path)
    published = store.publish(b"retained bytes", file_extension="parquet")
    with pytest.raises(ValueError, match="does not match the GC plan"):
        store.evict(
            published.storage_uri,
            expected_content_hash="0" * 64,
            expected_byte_size=published.byte_size,
        )
    assert store.read(published.storage_uri) == b"retained bytes"


def test_gc_cli_normalizes_the_configured_payload_directory_to_a_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[Path] = []

    class _Engine:
        def dispose(self) -> None:
            pass

    class _Settings:
        database_url = "postgresql+psycopg://unused"
        v022_payload_directory = "relative-payload-directory"

    monkeypatch.setattr(v022_research_round_gc, "get_settings", _Settings)
    monkeypatch.setattr(v022_research_round_gc, "create_postgres_engine", lambda _url: _Engine())
    monkeypatch.setattr(
        v022_research_round_gc,
        "LocalPayloadObjectStore",
        lambda root: observed.append(root) or object(),
    )
    monkeypatch.setattr(v022_research_round_gc, "ResearchRoundGCService", lambda *_args: object())
    monkeypatch.setattr(
        v022_research_round_gc,
        "_run_once",
        lambda *_args, **_kwargs: {"status": "idle"},
    )

    assert v022_research_round_gc.main([]) == 0
    assert observed == [Path("relative-payload-directory")]


def test_gc_terminal_work_set_includes_reuse_and_both_blocked_states() -> None:
    assert TERMINAL_GRAPH_WORK_STATUSES == (
        "completed",
        "reused",
        "failed",
        "cancelled",
        "blocked_upstream_failed",
        "blocked_upstream_cancelled",
    )


def test_gc_nonterminal_query_passes_the_complete_terminal_status_set() -> None:
    round_id = uuid.uuid4()

    class _Connection:
        observed_statement: Any = None
        observed_parameters: dict[str, Any] | None = None

        def scalar(self, statement: Any, parameters: dict[str, Any]) -> int:
            self.observed_statement = statement
            self.observed_parameters = parameters
            return 0

    connection = _Connection()
    assert _count_nonterminal_graph_work(connection, round_id) == 0  # type: ignore[arg-type]
    assert connection.observed_parameters == {
        "round": round_id,
        "terminal_statuses": TERMINAL_GRAPH_WORK_STATUSES,
    }
    assert "work.status NOT IN" in str(connection.observed_statement)


def test_gc_forever_retries_the_known_terminal_work_wait(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    engine = _configure_gc_cli(monkeypatch)
    calls = 0
    sleeps: list[float] = []

    def _run_once(*_args: object, **_kwargs: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ValueError("research_round_gc_waiting_for_terminal_work")
        raise KeyboardInterrupt

    monkeypatch.setattr(v022_research_round_gc, "_run_once", _run_once)
    monkeypatch.setattr(v022_research_round_gc.time, "sleep", sleeps.append)

    assert v022_research_round_gc.main(["--forever", "--poll-seconds", "0.25"]) == 0
    assert calls == 2
    assert sleeps == [0.25]
    assert engine.disposed is True
    assert '"status": "waiting"' in capsys.readouterr().err


def test_gc_forever_keeps_unknown_value_errors_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _configure_gc_cli(monkeypatch)
    monkeypatch.setattr(
        v022_research_round_gc,
        "_run_once",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unexpected_gc_failure")),
    )

    with pytest.raises(ValueError, match="unexpected_gc_failure"):
        v022_research_round_gc.main(["--forever", "--poll-seconds", "0.25"])
    assert engine.disposed is True


def test_gc_cli_reports_unknown_value_errors_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise_unknown_error() -> int:
        raise ValueError("unexpected_gc_failure")

    monkeypatch.setattr(v022_research_round_gc, "main", _raise_unknown_error)

    with pytest.raises(SystemExit) as exit_info:
        v022_research_round_gc.run()

    assert exit_info.value.code == 2
    assert "error: unexpected_gc_failure" in capsys.readouterr().err
