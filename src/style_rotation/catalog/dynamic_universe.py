from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Literal

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class UniverseMemberSnapshotSeed:
    security_id: uuid.UUID
    issuer_id: uuid.UUID | None = None
    primary_selection_security: bool = True


@dataclass(frozen=True, slots=True)
class UniverseSnapshotSeed:
    rank_date: date
    data_cutoff_at: datetime
    published_at: datetime
    effective_session: date
    members: tuple[UniverseMemberSnapshotSeed, ...]


@dataclass(frozen=True, slots=True)
class DynamicUniversePublication:
    methodology_artifact_id: uuid.UUID
    history_artifact_id: uuid.UUID


class DynamicUniversePublicationService:
    """Publish immutable PIT histories; extensions reuse only the frozen methodology."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        methodology_key: str,
        methodology_version: int,
        history_version: int,
        research_mode: Literal["formal", "exploratory"],
        parameters: dict[str, Any],
        snapshots: tuple[UniverseSnapshotSeed, ...],
    ) -> DynamicUniversePublication:
        self._validate(snapshots)
        methodology_payload = {
            "methodology_key": methodology_key,
            "version_number": methodology_version,
            "research_mode": research_mode,
            "parameters": parameters,
        }
        methodology_id = uuid.uuid4()

        def write_methodology(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.universe_methodology (
                        universe_methodology_id, artifact_id, methodology_key,
                        version_number, research_mode, parameters
                    ) VALUES (:id, :artifact_id, :key, :version, :mode,
                              CAST(:parameters AS jsonb))
                    """
                ),
                {
                    "id": methodology_id,
                    "artifact_id": artifact_id,
                    "key": methodology_key,
                    "version": methodology_version,
                    "mode": research_mode,
                    "parameters": _json(parameters),
                },
            )

        methodology = self._artifacts.publish(
            artifact_type="universe_methodology",
            artifact_key=f"{methodology_key}__{sha256_hexdigest(methodology_payload)}",
            version_number=methodology_version,
            semantic_payload=methodology_payload,
            content_payload=methodology_payload,
            draft_writer=write_methodology,
        )
        with self._engine.connect() as connection:
            persisted_methodology_id = connection.execute(
                text(
                    "SELECT universe_methodology_id FROM catalog.universe_methodology "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": methodology.artifact_id},
            ).scalar_one()
            security_count = connection.execute(
                text("SELECT count(*) FROM catalog.security WHERE security_id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {
                    "ids": tuple(
                        member.security_id for snapshot in snapshots for member in snapshot.members
                    )
                },
            ).scalar_one()
        distinct_security_ids = {
            member.security_id for snapshot in snapshots for member in snapshot.members
        }
        if security_count != len(distinct_security_ids):
            raise ValueError("Dynamic Universe references an unknown Security")
        history_payload = {
            "methodology_artifact_id": str(methodology.artifact_id),
            "history_version": history_version,
            "snapshots": [asdict(snapshot) for snapshot in snapshots],
        }
        history_id = uuid.uuid4()

        def write_history(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO catalog.universe_history (
                        universe_history_id, artifact_id, universe_methodology_id,
                        as_of_date, snapshot_count
                    ) VALUES (:id, :artifact_id, :methodology_id, :as_of, :count)
                    """
                ),
                {
                    "id": history_id,
                    "artifact_id": artifact_id,
                    "methodology_id": persisted_methodology_id,
                    "as_of": max(snapshot.effective_session for snapshot in snapshots),
                    "count": len(snapshots),
                },
            )
            for snapshot in snapshots:
                snapshot_id = uuid.uuid4()
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.universe_snapshot (
                            universe_snapshot_id, universe_history_id, rank_date,
                            data_cutoff_at, published_at, effective_session, member_count
                        ) VALUES (:id, :history_id, :rank_date, :cutoff, :published,
                                  :effective, :count)
                        """
                    ),
                    {
                        "id": snapshot_id,
                        "history_id": history_id,
                        "rank_date": snapshot.rank_date,
                        "cutoff": snapshot.data_cutoff_at,
                        "published": snapshot.published_at,
                        "effective": snapshot.effective_session,
                        "count": len(snapshot.members),
                    },
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO catalog.universe_snapshot_member (
                            universe_snapshot_id, security_id, issuer_id, ordinal,
                            primary_selection_security
                        ) VALUES (:snapshot_id, :security_id, :issuer_id, :ordinal, :primary)
                        """
                    ),
                    [
                        {
                            "snapshot_id": snapshot_id,
                            "security_id": member.security_id,
                            "issuer_id": member.issuer_id,
                            "ordinal": ordinal,
                            "primary": member.primary_selection_security,
                        }
                        for ordinal, member in enumerate(snapshot.members)
                    ],
                )

        history = self._artifacts.publish(
            artifact_type="universe_history",
            artifact_key=f"{methodology_key}__history__{sha256_hexdigest(history_payload)}",
            version_number=history_version,
            semantic_payload=history_payload,
            content_payload=history_payload,
            dependencies=(DependencyInput(methodology.artifact_id, "universe_methodology"),),
            draft_writer=write_history,
        )
        return DynamicUniversePublication(methodology.artifact_id, history.artifact_id)

    @staticmethod
    def _validate(snapshots: tuple[UniverseSnapshotSeed, ...]) -> None:
        if not snapshots:
            raise ValueError("Dynamic Universe requires at least one Snapshot")
        effective = tuple(snapshot.effective_session for snapshot in snapshots)
        if effective != tuple(sorted(effective)) or len(effective) != len(set(effective)):
            raise ValueError("Dynamic Universe Snapshot sessions must be unique and ordered")
        for snapshot in snapshots:
            if not snapshot.members:
                raise ValueError("Dynamic Universe Snapshot requires members")
            if snapshot.data_cutoff_at > snapshot.published_at:
                raise ValueError("Dynamic Universe data cutoff cannot follow publication")
            ids = tuple(member.security_id for member in snapshot.members)
            if len(ids) != len(set(ids)):
                raise ValueError("Dynamic Universe Snapshot contains duplicate Securities")


def _json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
