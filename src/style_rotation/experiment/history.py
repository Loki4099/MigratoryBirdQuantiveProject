from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.product.evidence import load_product_qualification_evidence

_TERMINAL_WORK_STATUSES = frozenset({"completed", "failed", "cancelled", "reused"})


@dataclass(slots=True)
class _ProductEvidence:
    """Exact experiment records required by one or more Product versions."""

    cell_artifact_ids: set[uuid.UUID] = field(default_factory=set)
    qualification_bundle_ids: set[uuid.UUID] = field(default_factory=set)
    complete: bool = True


class ExperimentHistoryService:
    """Prune superseded experiment data without racing leased workers.

    Product versions retain their frozen six-cell Portfolio evidence, the
    Predictive result used by the selected compiled model, and the source
    Suite record that anchors the immutable lineage.  Sibling branches in the
    same Suite are ordinary experiment history and may be reclaimed.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def prune_non_product_suites(self, *, retain_suite_id: uuid.UUID) -> int:
        """Prune terminal superseded history and return deleted Suite count.

        The newly submitted Suite is always retained.  Queued superseded work
        is cancelled atomically.  Running work receives a cancellation request
        but neither its Cell nor Suite is removed until its lease owner reaches
        a terminal state and a later prune pass runs.
        """

        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtextextended('v021-experiment-history-retention', 0))"
                )
            )
            product_evidence = self._product_evidence(connection)
            suite_ids = tuple(
                connection.execute(
                    text("""
                        SELECT research_suite_id
                        FROM experiment.research_suite
                        WHERE research_suite_id <> :retain_suite_id
                        ORDER BY created_at
                        FOR UPDATE
                    """),
                    {"retain_suite_id": retain_suite_id},
                ).scalars()
            )
            deleted_suites = 0
            for suite_id in suite_ids:
                evidence = product_evidence.get(suite_id)
                if evidence is not None and not evidence.complete:
                    # A malformed legacy Product must fail closed: retaining a
                    # whole Suite is preferable to silently losing evidence.
                    continue
                pinned_cells = evidence.cell_artifact_ids if evidence is not None else set()
                all_cells = self._suite_cell_artifact_ids(connection, suite_id)
                removable_cells = all_cells - pinned_cells
                work_rows = self._lock_work_items(connection, suite_id, removable_cells)
                if self._request_cancellation(connection, work_rows):
                    continue
                self._delete_cells(connection, suite_id, removable_cells, work_rows)
                if evidence is None:
                    connection.execute(
                        text(
                            "DELETE FROM experiment.research_suite "
                            "WHERE research_suite_id = :suite_id"
                        ),
                        {"suite_id": suite_id},
                    )
                    deleted_suites += 1
            return deleted_suites

    @staticmethod
    def _product_evidence(connection: Connection) -> dict[uuid.UUID, _ProductEvidence]:
        by_suite: dict[uuid.UUID, _ProductEvidence] = {}
        for qualification in load_product_qualification_evidence(connection):
            suite_id = qualification.source_suite_id
            evidence = by_suite.setdefault(suite_id, _ProductEvidence())
            evidence.qualification_bundle_ids.add(
                qualification.qualification_bundle_id
            )
            # Published Qualification dependency roles are the sole retention
            # authority.  Never expand the pin set by querying sibling Strategy
            # or Model Cells from the source Suite.
            evidence.cell_artifact_ids.update(qualification.cell_artifact_ids)
            if not qualification.complete:
                evidence.complete = False
        return by_suite

    @staticmethod
    def _suite_cell_artifact_ids(
        connection: Connection, suite_id: uuid.UUID
    ) -> set[uuid.UUID]:
        return set(
            connection.execute(
                text("""
                    SELECT artifact_id
                    FROM experiment.predictive_cell_specification
                    WHERE research_suite_id = :suite_id
                    UNION
                    SELECT artifact_id
                    FROM experiment.portfolio_cell_specification
                    WHERE research_suite_id = :suite_id
                """),
                {"suite_id": suite_id},
            ).scalars()
        )

    @staticmethod
    def _lock_work_items(
        connection: Connection,
        suite_id: uuid.UUID,
        removable_cells: set[uuid.UUID],
    ) -> tuple[RowMapping, ...]:
        if not removable_cells:
            return ()
        return tuple(
            connection.execute(
                text("""
                    SELECT work.*
                    FROM experiment.research_suite_work_item link
                    JOIN ops.work_item work ON work.work_item_id = link.work_item_id
                    WHERE link.research_suite_id = :suite_id
                      AND link.cell_artifact_id IN :cell_ids
                    ORDER BY work.created_at, work.work_item_id
                    FOR UPDATE OF work
                """).bindparams(bindparam("cell_ids", expanding=True)),
                {"suite_id": suite_id, "cell_ids": tuple(removable_cells)},
            ).mappings()
        )

    @staticmethod
    def _request_cancellation(
        connection: Connection, work_rows: tuple[RowMapping, ...]
    ) -> bool:
        running = False
        for row in work_rows:
            status = str(row["status"])
            if status == "queued":
                connection.execute(
                    text("""
                        UPDATE ops.work_item
                        SET status = 'cancelled', stage = 'cancelled',
                            cancel_requested_at = COALESCE(cancel_requested_at, now()),
                            updated_at = now()
                        WHERE work_item_id = :work_item_id AND status = 'queued'
                    """),
                    {"work_item_id": row["work_item_id"]},
                )
                ExperimentHistoryService._append_event(
                    connection,
                    row["work_item_id"],
                    event_type="cancelled_by_retention",
                    from_status="queued",
                    to_status="cancelled",
                )
            elif status == "running":
                running = True
                if row["cancel_requested_at"] is None:
                    connection.execute(
                        text("""
                            UPDATE ops.work_item
                            SET cancel_requested_at = now(), updated_at = now()
                            WHERE work_item_id = :work_item_id AND status = 'running'
                        """),
                        {"work_item_id": row["work_item_id"]},
                    )
                    ExperimentHistoryService._append_event(
                        connection,
                        row["work_item_id"],
                        event_type="cancel_requested_by_retention",
                        from_status="running",
                        to_status="running",
                    )
            elif status not in _TERMINAL_WORK_STATUSES:
                # Unknown future state: fail closed rather than deleting work
                # that a newer worker implementation may still own.
                running = True
        return running

    @staticmethod
    def _append_event(
        connection: Connection,
        work_item_id: uuid.UUID,
        *,
        event_type: str,
        from_status: str,
        to_status: str,
    ) -> None:
        connection.execute(
            text("""
                INSERT INTO ops.work_item_event (
                    work_item_event_id, work_item_id, sequence_number, event_type,
                    from_status, to_status, details
                ) SELECT :event_id, :work_item_id,
                         COALESCE(max(sequence_number), 0) + 1,
                         :event_type, :from_status, :to_status,
                         '{"reason":"superseded_experiment_retention"}'::jsonb
                FROM ops.work_item_event
                WHERE work_item_id = :work_item_id
            """),
            {
                "event_id": uuid.uuid4(),
                "work_item_id": work_item_id,
                "event_type": event_type,
                "from_status": from_status,
                "to_status": to_status,
            },
        )

    @staticmethod
    def _delete_cells(
        connection: Connection,
        suite_id: uuid.UUID,
        removable_cells: set[uuid.UUID],
        work_rows: tuple[RowMapping, ...],
    ) -> None:
        if not removable_cells:
            return
        cell_parameter: Any = bindparam("cell_ids", expanding=True)
        work_item_ids = tuple(row["work_item_id"] for row in work_rows)
        connection.execute(
            text("DELETE FROM experiment.cell_result WHERE cell_artifact_id IN :cell_ids")
            .bindparams(cell_parameter),
            {"cell_ids": tuple(removable_cells)},
        )
        connection.execute(
            text("""
                DELETE FROM experiment.research_suite_work_item
                WHERE research_suite_id = :suite_id
                  AND cell_artifact_id IN :cell_ids
            """).bindparams(cell_parameter),
            {"suite_id": suite_id, "cell_ids": tuple(removable_cells)},
        )
        connection.execute(
            text("""
                DELETE FROM experiment.portfolio_cell_specification
                WHERE research_suite_id = :suite_id
                  AND artifact_id IN :cell_ids
            """).bindparams(cell_parameter),
            {"suite_id": suite_id, "cell_ids": tuple(removable_cells)},
        )
        connection.execute(
            text("""
                DELETE FROM experiment.predictive_cell_specification
                WHERE research_suite_id = :suite_id
                  AND artifact_id IN :cell_ids
            """).bindparams(cell_parameter),
            {"suite_id": suite_id, "cell_ids": tuple(removable_cells)},
        )
        ExperimentHistoryService._delete_orphaned_work_items(connection, work_item_ids)

    @staticmethod
    def _delete_orphaned_work_items(
        connection: Connection, work_item_ids: tuple[uuid.UUID, ...]
    ) -> None:
        if not work_item_ids:
            return
        work_parameter: Any = bindparam("work_item_ids", expanding=True)
        orphaned = tuple(
            connection.execute(
                text("""
                    SELECT work.work_item_id
                    FROM ops.work_item work
                    WHERE work.work_item_id IN :work_item_ids
                      AND work.status IN ('completed','failed','cancelled','reused')
                      AND NOT EXISTS (
                          SELECT 1 FROM experiment.research_suite_work_item link
                          WHERE link.work_item_id = work.work_item_id
                      )
                      AND NOT EXISTS (
                          SELECT 1 FROM experiment.cell_result result
                          WHERE result.work_item_id = work.work_item_id
                      )
                """).bindparams(work_parameter),
                {"work_item_ids": work_item_ids},
            ).scalars()
        )
        if not orphaned:
            return
        orphan_parameter: Any = bindparam("orphaned", expanding=True)
        connection.execute(
            text("DELETE FROM ops.work_item_event WHERE work_item_id IN :orphaned")
            .bindparams(orphan_parameter),
            {"orphaned": orphaned},
        )
        connection.execute(
            text("DELETE FROM ops.work_item WHERE work_item_id IN :orphaned")
            .bindparams(orphan_parameter),
            {"orphaned": orphaned},
        )
