from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import date
from threading import Lock
from typing import Any

import pytest

from style_rotation.product.recommendation import ProductRecommendationService


class _ScalarResult:
    def __init__(self, values: tuple[date, ...]) -> None:
        self._values = values

    def scalars(self) -> tuple[date, ...]:
        return self._values


class _CalendarConnection:
    def __init__(self, values: tuple[date, ...], calls: list[dict[str, Any]]) -> None:
        self._values = values
        self._calls = calls

    def execute(self, _statement: object, parameters: dict[str, Any]) -> _ScalarResult:
        self._calls.append(parameters)
        return _ScalarResult(self._values)


class _CalendarEngine:
    def __init__(self, values: tuple[date, ...]) -> None:
        self._values = values
        self.calls: list[dict[str, Any]] = []

    @contextmanager
    def connect(self):  # type: ignore[no-untyped-def]
        yield _CalendarConnection(self._values, self.calls)


class _MappingResult:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def mappings(self) -> _MappingResult:
        return self

    def one_or_none(self) -> dict[str, Any]:
        return self._row


class _BundleConnection:
    def __init__(self, engine: _BundleEngine) -> None:
        self._engine = engine

    def execute(self, statement: object, parameters: dict[str, Any]) -> _MappingResult:
        self._engine.statement = str(statement)
        self._engine.parameters = parameters
        return _MappingResult(
            {
                "data_bundle_version_id": uuid.uuid4(),
                "artifact_id": uuid.uuid4(),
                "coverage_end": date(2026, 8, 3),
                "created_at": date(2026, 8, 4),
                "calendar_version_id": uuid.uuid4(),
            }
        )


class _BundleEngine:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: dict[str, Any] = {}

    @contextmanager
    def connect(self):  # type: ignore[no-untyped-def]
        yield _BundleConnection(self)


def _service(engine: _CalendarEngine) -> ProductRecommendationService:
    service = object.__new__(ProductRecommendationService)
    service._engine = engine  # type: ignore[assignment]
    return service


def test_latest_weekly_signal_uses_only_the_frozen_bundle_calendar() -> None:
    calendar_version_id = uuid.uuid4()
    engine = _CalendarEngine(
        (
            date(2026, 7, 30),
            date(2026, 7, 31),
            date(2026, 8, 3),
            date(2026, 8, 4),
        )
    )

    decision = _service(engine)._latest_signal_session(
        date(2026, 8, 3), "weekly", calendar_version_id
    )

    assert decision == date(2026, 7, 31)
    assert engine.calls == [
        {
            "coverage_end": date(2026, 8, 3),
            "calendar_version_id": calendar_version_id,
        }
    ]


def test_future_sessions_use_the_same_frozen_calendar_for_execution_and_refresh() -> None:
    calendar_version_id = uuid.uuid4()
    engine = _CalendarEngine(
        (
            date(2026, 8, 3),
            date(2026, 8, 4),
            date(2026, 8, 5),
            date(2026, 8, 6),
            date(2026, 8, 7),
            date(2026, 8, 10),
        )
    )

    execution, next_signal = _service(engine)._future_sessions(
        date(2026, 7, 31), "weekly", calendar_version_id
    )

    assert execution == date(2026, 8, 3)
    assert next_signal == date(2026, 8, 7)
    assert engine.calls == [
        {
            "decision": date(2026, 7, 31),
            "calendar_version_id": calendar_version_id,
        }
    ]


def test_latest_bundle_groups_the_selected_calendar_version() -> None:
    engine = _BundleEngine()
    asset_ids = (uuid.uuid4(), uuid.uuid4())

    bundle = _service(engine)._latest_bundle(asset_ids)  # type: ignore[arg-type]

    group_by = engine.statement.split("GROUP BY", maxsplit=1)[1].split(
        "HAVING", maxsplit=1
    )[0]
    assert "calendar.calendar_version_id" in group_by
    assert engine.parameters == {"asset_ids": asset_ids, "asset_count": 2}
    assert bundle["calendar_version_id"] is not None


class _RecommendationProbe(RuntimeError):
    pass


class _RecordingExecutor:
    def __init__(self) -> None:
        self.parameters: dict[str, Any] = {}

    def latest_product_scores(
        self, **parameters: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        self.parameters = parameters
        raise _RecommendationProbe


def test_recommendation_get_never_calculates_missing_historical_signals() -> None:
    enrollment_id = uuid.uuid4()
    bundle_id = uuid.uuid4()
    executor = _RecordingExecutor()
    service = object.__new__(ProductRecommendationService)
    service._executor = executor  # type: ignore[assignment]
    service._cache = {}
    service._cache_lock = Lock()
    service._context = lambda _enrollment_id: {  # type: ignore[method-assign]
        "asset_ids": (uuid.uuid4(),),
        "current_holdings": set(),
        "frequency": "weekly",
    }
    service._latest_bundle = lambda _asset_ids: {  # type: ignore[method-assign]
        "artifact_id": bundle_id,
        "coverage_end": date(2026, 8, 3),
        "calendar_version_id": uuid.uuid4(),
    }
    service._latest_signal_session = lambda *_args: date(2026, 7, 31)  # type: ignore[method-assign]

    with pytest.raises(_RecommendationProbe):
        service.latest(enrollment_id)

    assert executor.parameters == {
        "enrollment_id": enrollment_id,
        "data_bundle_artifact_id": bundle_id,
        "as_of_session": date(2026, 7, 31),
        "cached_signals_only": True,
    }
