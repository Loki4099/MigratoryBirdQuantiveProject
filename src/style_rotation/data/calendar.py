from __future__ import annotations

import importlib.metadata
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import exchange_calendars as xcals
import pandas as pd
from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


@dataclass(frozen=True, slots=True)
class TradingSession:
    session_date: date
    open_at_utc: datetime
    close_at_utc: datetime
    is_early_close: bool


@dataclass(frozen=True, slots=True)
class GeneratedCalendar:
    calendar_key: str
    library_name: str
    library_version: str
    coverage_start: date
    coverage_end: date
    sessions: tuple[TradingSession, ...]


class XNYSCalendarGenerator:
    def generate(self, start: date, end_inclusive: date) -> GeneratedCalendar:
        if start > end_inclusive:
            raise ValueError("Calendar start must not be after end")
        # exchange-calendars otherwise builds a moving default window around
        # ``today``.  A frozen historical research calendar must instead be
        # constructed for the exact requested interval; relying on the moving
        # default made valid pre-2006 sessions disappear as wall-clock time
        # advanced.
        # ``exchange_calendars`` requires the constructor's start to be
        # strictly earlier than its end, even though ``sessions_in_range``
        # accepts a one-session inclusive interval. Widen only the calendar
        # construction window; the returned labels stay clipped to the exact
        # caller-requested range below.
        construction_end = max(end_inclusive, start + timedelta(days=1))
        calendar = xcals.get_calendar("XNYS", start=str(start), end=str(construction_end))
        requested_start = pd.Timestamp(start)
        requested_end = pd.Timestamp(end_inclusive)
        # The constructor's first/last labels are trading sessions rather than
        # the requested civil-date boundaries. ``sessions_in_range`` therefore
        # rejects an otherwise valid interval that begins or ends on a holiday.
        # Filter the generated public session index directly so civil-date
        # coverage remains exact without extending the published result.
        labels = calendar.sessions[
            (calendar.sessions >= requested_start) & (calendar.sessions <= requested_end)
        ]
        sessions: list[TradingSession] = []
        regular_duration = timedelta(hours=6, minutes=30)
        for label in labels:
            open_at = calendar.session_open(label).to_pydatetime().astimezone(UTC)
            close_at = calendar.session_close(label).to_pydatetime().astimezone(UTC)
            sessions.append(
                TradingSession(
                    session_date=label.date(),
                    open_at_utc=open_at,
                    close_at_utc=close_at,
                    is_early_close=close_at - open_at < regular_duration,
                )
            )
        if not sessions:
            raise ValueError("Requested XNYS range contains no trading sessions")
        return GeneratedCalendar(
            calendar_key="XNYS",
            library_name="exchange_calendars",
            library_version=importlib.metadata.version("exchange-calendars"),
            coverage_start=start,
            coverage_end=end_inclusive,
            sessions=tuple(sessions),
        )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


class CalendarPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self, generated: GeneratedCalendar, *, version_number: int = 1
    ) -> PublicationResult:
        if version_number < 1:
            raise ValueError("Calendar version must be positive")
        semantic = {
            "calendar_key": generated.calendar_key,
            "version_number": version_number,
            "library_name": generated.library_name,
            "library_version": generated.library_version,
            "coverage_start": generated.coverage_start,
            "coverage_end": generated.coverage_end,
        }
        content = {**semantic, "sessions": [asdict(item) for item in generated.sessions]}
        with self._engine.begin() as connection:
            ids = self._dependency_ids(connection, generated.calendar_key)
            service = ArtifactService(cast(Engine, _BoundConnection(connection)))
            return service.publish(
                artifact_type="calendar_version",
                artifact_key=generated.calendar_key,
                version_number=version_number,
                semantic_payload=semantic,
                content_payload=content,
                dependencies=(
                    DependencyInput(ids["series_artifact_id"], "data_series_version", 0),
                    DependencyInput(ids["master_artifact_id"], "master_data_release", 1),
                ),
                reason=(
                    f"publish {generated.calendar_key} calendar v{version_number} "
                    f"for {generated.coverage_start}:{generated.coverage_end}"
                ),
                draft_writer=lambda tx, artifact_id: self._write(
                    tx, artifact_id, generated, version_number, ids
                ),
            )

    @staticmethod
    def _dependency_ids(connection: Connection, calendar_key: str) -> dict[str, Any]:
        row = (
            connection.execute(
                text(
                    """
                    SELECT calendar.calendar_definition_id,
                           master.artifact_id AS master_artifact_id,
                           series.data_series_version_id,
                           series_artifact.artifact_id AS series_artifact_id
                    FROM catalog.calendar_definition calendar
                    JOIN catalog.master_data_release master
                      ON master.master_data_release_id = calendar.master_data_release_id
                    JOIN lineage.artifact master_artifact
                      ON master_artifact.artifact_id = master.artifact_id
                     AND master_artifact.status = 'published'
                    JOIN data.data_series_definition definition
                      ON definition.series_key = 'xnys_calendar'
                    JOIN data.data_series_version series
                      ON series.data_series_definition_id = definition.data_series_definition_id
                    JOIN lineage.artifact series_artifact
                      ON series_artifact.artifact_id = series.artifact_id
                     AND series_artifact.status = 'published'
                    WHERE calendar.calendar_key = :calendar_key
                    ORDER BY series.version_number DESC
                    LIMIT 1
                    """
                ),
                {"calendar_key": calendar_key},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("Publish research scope and data contracts before the calendar")
        return dict(row)

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        generated: GeneratedCalendar,
        version_number: int,
        ids: dict[str, Any],
    ) -> None:
        calendar_version_id = uuid.uuid4()
        connection.execute(
            text(
                """
                INSERT INTO catalog.calendar_version (
                    calendar_version_id, calendar_definition_id, data_series_version_id,
                    artifact_id, version_number, library_name, library_version,
                    coverage_start, coverage_end, session_count
                ) VALUES (
                    :id, :definition_id, :series_id, :artifact_id, :version_number,
                    :library_name, :library_version, :coverage_start, :coverage_end, :session_count
                )
                """
            ),
            {
                "id": calendar_version_id,
                "definition_id": ids["calendar_definition_id"],
                "series_id": ids["data_series_version_id"],
                "artifact_id": artifact_id,
                "version_number": version_number,
                "library_name": generated.library_name,
                "library_version": generated.library_version,
                "coverage_start": generated.coverage_start,
                "coverage_end": generated.coverage_end,
                "session_count": len(generated.sessions),
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO catalog.calendar_session (
                    calendar_version_id, session_date, open_at_utc, close_at_utc, is_early_close
                ) VALUES (:version_id, :session_date, :open_at, :close_at, :is_early_close)
                """
            ),
            [
                {
                    "version_id": calendar_version_id,
                    "session_date": item.session_date,
                    "open_at": item.open_at_utc,
                    "close_at": item.close_at_utc,
                    "is_early_close": item.is_early_close,
                }
                for item in generated.sessions
            ],
        )
