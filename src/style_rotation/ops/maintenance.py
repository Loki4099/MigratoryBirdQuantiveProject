from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.experiment.history import ExperimentHistoryService

_GENERATION_LOCK_KEY = "v021-suite-generation-maintenance"


@contextmanager
def suite_generation_guard(engine: Engine) -> Iterator[Connection]:
    """Serialize Suite publication with latest-generation retention passes.

    Retention itself uses a different transaction-scoped advisory lock.  This
    session lock closes the otherwise dangerous gap between reading the newest
    Suite id and asking ``ExperimentHistoryService`` to prune older Suites.
    """

    connection = engine.connect()
    locked = False
    try:
        connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": _GENERATION_LOCK_KEY},
        )
        locked = True
        yield connection
    finally:
        try:
            if locked:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:lock_key, 0))"),
                    {"lock_key": _GENERATION_LOCK_KEY},
                )
        finally:
            connection.close()


def prune_latest_non_product_suites(engine: Engine) -> int:
    """Retry safe retention after leases settle, preserving the newest Suite."""

    with suite_generation_guard(engine) as connection:
        retain_suite_id: uuid.UUID | None = connection.execute(
            text("""
                SELECT research_suite_id
                FROM experiment.research_suite
                ORDER BY created_at DESC, research_suite_id DESC
                LIMIT 1
            """)
        ).scalar_one_or_none()
        if retain_suite_id is None:
            return 0
        return ExperimentHistoryService(engine).prune_non_product_suites(
            retain_suite_id=retain_suite_id
        )
