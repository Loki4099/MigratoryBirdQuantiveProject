from __future__ import annotations

import json
import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import style_rotation.v022.market_data_closure as closure_module
from style_rotation.v022.market_data_closure import (
    ClosureAuditInput,
    ClosureBar,
    ClosureLifecycleEvent,
    ClosureMaskInterval,
    ClosureSession,
    MarketDataClosureAuditor,
    audit_market_data_closure,
)


def test_exact_closure_passes_and_report_is_json_serializable() -> None:
    security_id = uuid.uuid4()
    inputs = _input(
        security_id=security_id,
        bars=tuple(
            _bar(security_id, session, str(100 + index))
            for index, session in enumerate(_dates())
        ),
    )

    report = audit_market_data_closure(inputs)

    assert report.passed is True
    assert report.blockers == ()
    assert report.exclude_candidates == ()
    assert report.bar_count == 6
    assert json.loads(json.dumps(report.to_dict()))["passed"] is True


def test_closure_detects_price_geometry_large_return_volume_and_required_gaps() -> None:
    security_id = uuid.uuid4()
    sessions = _dates()
    bars = (
        _bar(security_id, sessions[0], "100", open_raw="0"),
        _bar(security_id, sessions[1], "100"),
        _bar(security_id, sessions[2], "100"),
        _bar(
            security_id,
            sessions[3],
            "200",
            high_raw="150",
            volume_raw=0,
        ),
        # The second decision session is missing, so selectable, decision and held
        # path closure must all fail independently.
        _bar(security_id, sessions[5], "201"),
    )

    report = audit_market_data_closure(_input(security_id=security_id, bars=bars))
    codes = {
        item.rule_code
        for item in report.blockers
        + report.exclude_candidates
        + report.review_findings
    }

    assert report.passed is False
    assert "nonpositive_market_price" in codes
    assert "raw_ohlc_envelope_violation" in codes
    assert "adjusted_return_over_50_percent" in codes
    assert "selectable_zero_volume" in codes
    assert "selectable_path_market_gap" in codes
    assert "decision_session_market_gap" in codes
    assert "potential_held_path_market_gap" in codes


def test_large_observed_return_is_reviewable_without_failing_clean_closure() -> None:
    security_id = uuid.uuid4()
    sessions = _dates()
    bars = tuple(
        _bar(
            security_id,
            session,
            "200" if index >= 3 else "100",
        )
        for index, session in enumerate(sessions)
    )

    report = audit_market_data_closure(_input(security_id=security_id, bars=bars))

    assert report.passed is True
    assert report.blockers == ()
    assert report.exclude_candidates == ()
    assert [item.rule_code for item in report.review_findings] == [
        "adjusted_return_over_50_percent"
    ]


def test_warmup_requires_consecutive_sessions_not_cumulative_observations() -> None:
    security_id = uuid.uuid4()
    sessions = _dates()
    # The selection-session observation is usable, but the immediately
    # preceding session is missing. A cumulative counter would pass this.
    bars = (
        _bar(security_id, sessions[0] - timedelta(days=1), "98"),
        _bar(security_id, sessions[0], "99"),
        _bar(security_id, sessions[2], "100"),
        _bar(security_id, sessions[3], "101"),
        _bar(security_id, sessions[4], "102"),
        _bar(security_id, sessions[5], "103"),
    )

    report = audit_market_data_closure(_input(security_id=security_id, bars=bars))
    issue = next(
        item
        for item in report.exclude_candidates
        if item.rule_code == "strict_consecutive_warmup_incomplete"
    )

    assert issue.details["samples"] == [
        {
            "selection_start": sessions[2].isoformat(),
            "required_consecutive_sessions": 2,
            "actual_consecutive_sessions": 1,
        }
    ]


def test_warmup_includes_the_completed_selection_session() -> None:
    security_id = uuid.uuid4()
    sessions = _dates()
    bars = tuple(
        _bar(security_id, session, "100")
        for session in sessions[1:]
    )

    report = audit_market_data_closure(_input(security_id=security_id, bars=bars))

    assert not any(
        item.rule_code == "strict_consecutive_warmup_incomplete"
        for item in report.exclude_candidates
    )


def test_runtime_contract_requires_confirmed_complete_settlement_instruction() -> None:
    security_id = uuid.uuid4()
    event_id = uuid.uuid4()
    inputs = _input(
        security_id=security_id,
        bars=tuple(_bar(security_id, session, "100") for session in _dates()),
        lifecycle_events=(
            ClosureLifecycleEvent(
                security_id,
                event_id,
                "cash_merger",
                "estimated",
                _dates()[4],
                None,
                1,
                0,
            ),
        ),
    )

    report = audit_market_data_closure(inputs)
    codes = {item.rule_code for item in report.blockers}

    assert "lifecycle_event_not_confirmed" in codes
    assert "lifecycle_settlement_incomplete" in codes


def test_held_path_requires_bars_and_volume_even_after_future_mask_turns_off() -> None:
    security_id = uuid.uuid4()
    sessions = _dates()
    base = _input(
        security_id=security_id,
        bars=(
            _bar(security_id, sessions[0], "100"),
            _bar(security_id, sessions[1], "100"),
            _bar(security_id, sessions[2], "100"),
            _bar(security_id, sessions[3], "100", volume_raw=0),
            # sessions[4] is missing during the potential holding interval.
            _bar(security_id, sessions[5], "100"),
        ),
    )
    inputs = replace(
        base,
        mask_intervals=(
            ClosureMaskInterval(
                security_id,
                sessions[2],
                sessions[2],
                is_selectable=True,
                is_tradable=True,
            ),
        ),
    )

    report = audit_market_data_closure(inputs)
    codes = {item.rule_code for item in report.exclude_candidates}

    assert "potential_held_path_zero_volume" in codes
    assert "potential_held_path_market_gap" in codes


def test_confirmed_complete_terminal_settlement_stops_held_bar_requirement() -> None:
    security_id = uuid.uuid4()
    sessions = _dates()
    event_id = uuid.uuid4()
    base = _input(
        security_id=security_id,
        bars=tuple(
            _bar(security_id, session, "100") for session in sessions[:3]
        ),
        lifecycle_events=(
            ClosureLifecycleEvent(
                security_id,
                event_id,
                "cash_merger",
                "confirmed",
                sessions[3],
                sessions[3],
                1,
                1,
            ),
        ),
    )
    inputs = replace(
        base,
        evaluation_cohort_runtime_contract_id=None,
        mask_intervals=(
            ClosureMaskInterval(
                security_id,
                sessions[2],
                sessions[2],
                is_selectable=True,
                is_tradable=True,
            ),
        ),
    )

    report = audit_market_data_closure(inputs)

    assert report.passed is True
    assert not any(
        item.rule_code.startswith("potential_held_path")
        for item in report.exclude_candidates
    )


def test_candidate_dataset_uses_reference_shape_but_keeps_candidate_report_identity(
    monkeypatch: object,
) -> None:
    candidate_id = uuid.uuid4()
    reference_runtime_id = uuid.uuid4()
    cohort_id = uuid.uuid4()
    connection = object()
    engine = _Engine(connection)
    header = closure_module._AuditHeader(
        candidate_id,
        cohort_id,
        reference_runtime_id,
        2,
        (
            ClosureSession(_dates()[0], "warmup", False),
            ClosureSession(_dates()[1], "evaluation", True),
        ),
    )
    report = _report_with_dataset(candidate_id)
    calls: list[tuple[str, object]] = []

    def require_candidate(_connection: object, dataset_id: uuid.UUID) -> None:
        calls.append(("candidate", dataset_id))

    def load_header(_connection: object, **kwargs: object) -> tuple[object, bool]:
        calls.append(("strict", kwargs["require_dataset_match"]))
        return header, True

    monkeypatch.setattr(  # type: ignore[attr-defined]
        closure_module, "_require_published_candidate_dataset", require_candidate
    )
    monkeypatch.setattr(closure_module, "_load_header", load_header)  # type: ignore[attr-defined]
    auditor = MarketDataClosureAuditor(engine)  # type: ignore[arg-type]
    monkeypatch.setattr(  # type: ignore[attr-defined]
        auditor,
        "_audit_loaded",
        lambda _connection, loaded, runtime: (
            report if loaded is header and runtime is True else None
        ),
    )

    result = auditor.audit_candidate_against_reference_cohort(
        candidate_dataset_publication_id=candidate_id,
        reference_evaluation_cohort_runtime_contract_id=reference_runtime_id,
    )

    assert result.dataset_publication_id == str(candidate_id)
    assert calls == [("candidate", candidate_id), ("strict", False)]


def _report_with_dataset(dataset_id: uuid.UUID) -> closure_module.MarketDataClosureAuditReport:
    return closure_module.MarketDataClosureAuditReport(
        "v0.22.market_data_closure_audit.v1",
        str(dataset_id),
        str(uuid.uuid4()),
        None,
        _dates()[0].isoformat(),
        _dates()[-1].isoformat(),
        1,
        len(_dates()),
        len(_dates()),
        True,
        (),
        (),
        (),
        (),
    )


class _ConnectionContext:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def __enter__(self) -> object:
        return self.connection

    def __exit__(self, *_args: object) -> None:
        return None


class _Engine:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def connect(self) -> _ConnectionContext:
        return _ConnectionContext(self.connection)


def _input(
    *,
    security_id: uuid.UUID,
    bars: tuple[ClosureBar, ...],
    lifecycle_events: tuple[ClosureLifecycleEvent, ...] = (),
) -> ClosureAuditInput:
    sessions = _dates()
    return ClosureAuditInput(
        dataset_publication_id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        evaluation_cohort_version_id=uuid.UUID(
            "00000000-0000-0000-0000-000000000002"
        ),
        evaluation_cohort_runtime_contract_id=uuid.UUID(
            "00000000-0000-0000-0000-000000000003"
        ),
        required_history_sessions=2,
        sessions=(
            ClosureSession(sessions[0], "warmup", False),
            ClosureSession(sessions[1], "warmup", False),
            ClosureSession(sessions[2], "evaluation", True),
            ClosureSession(sessions[3], "evaluation", False),
            ClosureSession(sessions[4], "evaluation", True),
            ClosureSession(sessions[5], "evaluation", False),
        ),
        mask_intervals=(
            ClosureMaskInterval(
                security_id,
                sessions[2],
                sessions[5],
                is_selectable=True,
                is_tradable=True,
            ),
        ),
        bars=bars,
        lifecycle_events=lifecycle_events,
    )


def _dates() -> tuple[date, ...]:
    return tuple(date(2020, 1, 6) + timedelta(days=index) for index in range(6))


def _bar(
    security_id: uuid.UUID,
    session: date,
    close: str,
    *,
    open_raw: str | None = None,
    high_raw: str | None = None,
    volume_raw: int = 100,
) -> ClosureBar:
    close_value = Decimal(close)
    open_value = close_value if open_raw is None else Decimal(open_raw)
    high_value = close_value if high_raw is None else Decimal(high_raw)
    return ClosureBar(
        security_id,
        session,
        open_value,
        high_value,
        min(open_value, close_value),
        close_value,
        close_value,
        close_value,
        close_value,
        close_value,
        volume_raw,
    )
