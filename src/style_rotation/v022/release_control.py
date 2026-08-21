from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

ReleaseState = Literal[
    "hidden", "shadow", "explicit_eligible", "default", "maintenance_read_only"
]
TransitionTarget = Literal["shadow", "explicit_eligible", "default", "maintenance_read_only"]

_ALLOWED: dict[ReleaseState, frozenset[TransitionTarget]] = {
    "hidden": frozenset({"shadow"}),
    "shadow": frozenset({"explicit_eligible", "maintenance_read_only"}),
    "explicit_eligible": frozenset({"default", "shadow", "maintenance_read_only"}),
    "default": frozenset({"explicit_eligible", "shadow", "maintenance_read_only"}),
    "maintenance_read_only": frozenset({"shadow", "explicit_eligible", "default"}),
}
_TARGET_EVIDENCE: dict[TransitionTarget, frozenset[str]] = {
    "shadow": frozenset({"shadow_plan_artifact_id"}),
    "explicit_eligible": frozenset({"parity_gate_artifact_id"}),
    "default": frozenset(
        {
            "parity_gate_artifact_id",
            "shadow_coverage_artifact_id",
            "operations_readiness_artifact_id",
            "restore_drill_artifact_id",
            "rollback_drill_artifact_id",
        }
    ),
    "maintenance_read_only": frozenset(),
}
_RECOVERY_EVIDENCE = frozenset(
    {
        "incident_impact_analysis_artifact_id",
        "parity_gate_artifact_id",
        "restore_drill_artifact_id",
        "rollback_drill_artifact_id",
    }
)
_EXPECTED_EVIDENCE_TYPES = {
    "shadow_plan_artifact_id": "v022_shadow_plan",
    "shadow_coverage_artifact_id": "v022_shadow_coverage_evidence",
    "operations_readiness_artifact_id": "v022_operations_readiness_evidence",
    "restore_drill_artifact_id": "v022_restore_drill_evidence",
    "rollback_drill_artifact_id": "v022_rollback_drill_evidence",
}


@dataclass(frozen=True, slots=True)
class ReleaseControlStatus:
    state: ReleaseState
    transition_sequence: int
    transition_artifact_id: uuid.UUID | None

    @property
    def default_contract(self) -> Literal["v0.21", "v0.22"]:
        return "v0.22" if self.state == "default" else "v0.21"

    @property
    def maintenance_read_only(self) -> bool:
        return self.state == "maintenance_read_only"

    @property
    def shadow_runtime_allowed(self) -> bool:
        return self.state in {"shadow", "explicit_eligible", "default"}

    @property
    def v021_research_creation_allowed(self) -> bool:
        return self.state in {"hidden", "shadow", "explicit_eligible"}

    @property
    def v022_explicit_creation_allowed(self) -> bool:
        return self.state in {"explicit_eligible", "default"}

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "transition_sequence": self.transition_sequence,
            "transition_artifact_id": (
                str(self.transition_artifact_id) if self.transition_artifact_id else None
            ),
            "default_contract": self.default_contract,
            "maintenance_read_only": self.maintenance_read_only,
            "shadow_runtime_allowed": self.shadow_runtime_allowed,
            "v021_research_creation_allowed": self.v021_research_creation_allowed,
            "v022_explicit_creation_allowed": self.v022_explicit_creation_allowed,
        }


@dataclass(frozen=True, slots=True)
class ReleaseTransitionPublication:
    release_transition_id: uuid.UUID
    artifact_id: uuid.UUID
    sequence_number: int
    from_state: ReleaseState
    to_state: TransitionTarget
    reused: bool


@dataclass(frozen=True, slots=True)
class ReleasePreflightReport:
    current_state: ReleaseState
    target_state: TransitionTarget
    required_evidence_keys: tuple[str, ...]
    provided_evidence: dict[str, uuid.UUID]
    ready: bool
    blocker_codes: tuple[str, ...]
    blocker_details: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "current_state": self.current_state,
            "target_state": self.target_state,
            "required_evidence_keys": list(self.required_evidence_keys),
            "provided_evidence": {
                key: str(value) for key, value in sorted(self.provided_evidence.items())
            },
            "ready": self.ready,
            "blocker_codes": list(self.blocker_codes),
            "blocker_details": list(self.blocker_details),
        }


class ReleaseControlService:
    """Database-authoritative v0.22 release, cutover, and rollback state."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def current(self) -> ReleaseControlStatus:
        with self._engine.connect() as connection:
            row = _current_transition(connection)
        if row is None:
            return ReleaseControlStatus("hidden", 0, None)
        return ReleaseControlStatus(
            row["to_state"], row["sequence_number"], row["artifact_id"]
        )
    def preflight(
        self,
        *,
        target: TransitionTarget,
        gate_evidence: dict[str, uuid.UUID] | None = None,
        incident: dict[str, object] | None = None,
    ) -> ReleasePreflightReport:
        """Validate a proposed transition without publishing or mutating release state."""

        current = self.current()
        evidence = dict(gate_evidence or {})
        required = set(_TARGET_EVIDENCE[target])
        if current.state == "maintenance_read_only":
            required.update(_RECOVERY_EVIDENCE)
        blockers: list[str] = []
        details: list[str] = []
        if target not in _ALLOWED[current.state]:
            blockers.append("illegal_release_transition")
            details.append(f"{current.state} -> {target} is not allowed")
        missing = sorted(required - set(evidence))
        if missing:
            blockers.append("release_evidence_incomplete")
            details.append(f"missing evidence: {', '.join(missing)}")
        if target == "maintenance_read_only" and not incident:
            blockers.append("rollback_incident_document_missing")
            details.append("maintenance rollback requires a nonempty incident document")
        if not missing:
            try:
                self._published_evidence(evidence)
            except ValueError as error:
                blockers.append("release_evidence_invalid")
                details.append(str(error))
        return ReleasePreflightReport(
            current.state,
            target,
            tuple(sorted(required)),
            evidence,
            not blockers,
            tuple(blockers),
            tuple(details),
        )

    def transition(
        self,
        *,
        target: TransitionTarget,
        reason_code: str,
        reason: str,
        requested_by: str,
        gate_evidence: dict[str, uuid.UUID] | None = None,
        incident: dict[str, object] | None = None,
        requested_at: datetime | None = None,
    ) -> ReleaseTransitionPublication:
        reason_code = reason_code.strip()
        reason = reason.strip()
        requested_by = requested_by.strip()
        if not reason_code or not reason or not requested_by:
            raise ValueError("Release transition reason code, reason, and actor are required")
        current = self.current()
        if target not in _ALLOWED[current.state]:
            raise ValueError(f"Illegal v0.22 release transition: {current.state} -> {target}")
        evidence = dict(gate_evidence or {})
        incident_document = dict(incident or {})
        required = set(_TARGET_EVIDENCE[target])
        if current.state == "maintenance_read_only":
            required.update(_RECOVERY_EVIDENCE)
        missing = sorted(required - set(evidence))
        if missing:
            raise ValueError(f"Release transition evidence is incomplete: {missing}")
        if target == "maintenance_read_only" and not incident_document:
            raise ValueError("Rollback requires a nonempty incident document")
        evidence_rows = self._published_evidence(evidence)
        occurred_at = requested_at or datetime.now(UTC)
        sequence = current.transition_sequence + 1
        transition_id = uuid.uuid4()
        semantic = {
            "contract_version": "v0.22.0",
            "sequence_number": sequence,
            "from_state": current.state,
            "to_state": target,
            "reason_code": reason_code,
            "reason": reason,
            "requested_by": requested_by,
            "requested_at": occurred_at,
            "gate_evidence": {key: str(value) for key, value in sorted(evidence.items())},
            "incident": incident_document,
        }
        fingerprint = sha256_hexdigest(semantic)
        dependencies = tuple(
            DependencyInput(row["artifact_id"], f"gate_evidence:{key}", ordinal)
            for ordinal, (key, row) in enumerate(sorted(evidence_rows.items()))
        )
        publication = self._artifacts.publish(
            artifact_type="v022_release_transition",
            artifact_key="bird_v022_release_control",
            version_number=sequence,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=dependencies,
            reason=f"transition v0.22 release from {current.state} to {target}",
            draft_writer=partial(
                self._write_transition,
                transition_id=transition_id,
                sequence=sequence,
                from_state=current.state,
                target=target,
                reason_code=reason_code,
                reason=reason,
                requested_by=requested_by,
                requested_at=occurred_at,
                evidence=evidence,
                incident=incident_document,
                fingerprint=fingerprint,
            ),
        )
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM workspace.v022_release_transition "
                    "WHERE artifact_id=:artifact"
                ),
                {"artifact": publication.artifact_id},
            ).mappings().one()
        return ReleaseTransitionPublication(
            row["release_transition_id"],
            row["artifact_id"],
            row["sequence_number"],
            row["from_state"],
            row["to_state"],
            publication.reused,
        )

    def _published_evidence(
        self, evidence: dict[str, uuid.UUID]
    ) -> dict[str, RowMapping]:
        if len(evidence.values()) != len(set(evidence.values())):
            raise ValueError("Release transition evidence Artifact IDs must be unique")
        rows: dict[str, RowMapping] = {}
        with self._engine.connect() as connection:
            for key, artifact_id in evidence.items():
                row = connection.execute(
                    text(
                        "SELECT artifact_id,artifact_type,semantic_fingerprint "
                        "FROM lineage.artifact "
                        "WHERE artifact_id=:artifact AND status='published'"
                    ),
                    {"artifact": artifact_id},
                ).mappings().one_or_none()
                if row is None:
                    raise ValueError(f"Release transition evidence is not published: {key}")
                expected_type = _EXPECTED_EVIDENCE_TYPES.get(key)
                if expected_type is not None and row["artifact_type"] != expected_type:
                    raise ValueError(
                        f"Release transition evidence has the wrong Artifact type: {key}"
                    )
                if key == "shadow_coverage_artifact_id":
                    ready = connection.scalar(
                        text(
                            "SELECT ready_for_default "
                            "FROM workspace.v022_shadow_coverage_snapshot "
                            "WHERE artifact_id=:artifact"
                        ),
                        {"artifact": artifact_id},
                    )
                    if ready is not True:
                        raise ValueError("Shadow Coverage evidence is not ready for default")
                if key == "operations_readiness_artifact_id":
                    ready = connection.scalar(
                        text(
                            "SELECT ready_for_default "
                            "FROM ops.v022_operations_readiness_snapshot "
                            "WHERE artifact_id=:artifact"
                        ),
                        {"artifact": artifact_id},
                    )
                    if ready is not True:
                        raise ValueError("Operations Readiness evidence is not ready for default")
                if key == "restore_drill_artifact_id":
                    restore_row = connection.execute(
                        text(
                            "SELECT restore_drill_snapshot_id,ready_for_gate,"
                            "expected_object_count FROM ops.v022_restore_drill_snapshot "
                            "WHERE artifact_id=:artifact"
                        ),
                        {"artifact": artifact_id},
                    ).mappings().one_or_none()
                    if restore_row is None or restore_row["ready_for_gate"] is not True:
                        raise ValueError("Restore Drill evidence is not ready for release")
                    uncovered = connection.scalar(
                        text(
                            """
                            SELECT count(*) FROM (
                              SELECT DISTINCT manifest.payload_manifest_id,
                                     object.payload_object_id
                              FROM data.payload_manifest manifest
                              JOIN data.v022_strong_payload_manifest strong_root
                                ON strong_root.payload_manifest_id=
                                   manifest.payload_manifest_id
                              JOIN lineage.artifact artifact
                                ON artifact.artifact_id=manifest.artifact_id
                              JOIN data.payload_manifest_partition link
                                ON link.payload_manifest_id=manifest.payload_manifest_id
                              JOIN data.payload_partition partition
                                ON partition.payload_partition_id=link.payload_partition_id
                              JOIN data.payload_object object
                                ON object.payload_object_id=partition.payload_object_id
                              WHERE manifest.materialization_state='materialized'
                                AND artifact.status='published'
                            ) current_object
                            WHERE NOT EXISTS (
                              SELECT 1 FROM ops.v022_restore_drill_object evidence
                              WHERE evidence.restore_drill_snapshot_id=:snapshot
                                AND evidence.payload_manifest_id=current_object.payload_manifest_id
                                AND evidence.payload_object_id=current_object.payload_object_id
                                AND evidence.passed
                            )
                            """
                        ),
                        {"snapshot": restore_row["restore_drill_snapshot_id"]},
                    )
                    current_count = connection.scalar(
                        text(
                            """
                            SELECT count(*) FROM (
                              SELECT DISTINCT manifest.payload_manifest_id,
                                     object.payload_object_id
                              FROM data.payload_manifest manifest
                              JOIN data.v022_strong_payload_manifest strong_root
                                ON strong_root.payload_manifest_id=
                                   manifest.payload_manifest_id
                              JOIN lineage.artifact artifact
                                ON artifact.artifact_id=manifest.artifact_id
                              JOIN data.payload_manifest_partition link
                                ON link.payload_manifest_id=manifest.payload_manifest_id
                              JOIN data.payload_partition partition
                                ON partition.payload_partition_id=link.payload_partition_id
                              JOIN data.payload_object object
                                ON object.payload_object_id=partition.payload_object_id
                              WHERE manifest.materialization_state='materialized'
                                AND artifact.status='published'
                            ) current_object
                            """
                        )
                    )
                    if uncovered or current_count != restore_row["expected_object_count"]:
                        raise ValueError(
                            "Restore Drill evidence does not cover the current strong roots"
                        )
                if key == "rollback_drill_artifact_id":
                    ready = connection.scalar(
                        text(
                            "SELECT ready_for_gate FROM ops.v022_rollback_drill_snapshot "
                            "WHERE artifact_id=:artifact"
                        ),
                        {"artifact": artifact_id},
                    )
                    if ready is not True:
                        raise ValueError("Rollback Drill evidence is not ready for release")
                rows[key] = row
        return rows

    @staticmethod
    def _write_transition(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        transition_id: uuid.UUID,
        sequence: int,
        from_state: ReleaseState,
        target: TransitionTarget,
        reason_code: str,
        reason: str,
        requested_by: str,
        requested_at: datetime,
        evidence: dict[str, uuid.UUID],
        incident: dict[str, object],
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO workspace.v022_release_transition (
                  release_transition_id,artifact_id,sequence_number,from_state,to_state,
                  reason_code,reason,requested_by,requested_at,gate_evidence_document,
                  incident_document,transition_fingerprint
                ) VALUES (
                  :id,:artifact,:sequence,:from_state,:to_state,:reason_code,:reason,:actor,
                  :requested_at,CAST(:evidence AS jsonb),CAST(:incident AS jsonb),:fingerprint
                )
                """
            ),
            {
                "id": transition_id,
                "artifact": artifact_id,
                "sequence": sequence,
                "from_state": from_state,
                "to_state": target,
                "reason_code": reason_code,
                "reason": reason,
                "actor": requested_by,
                "requested_at": requested_at,
                "evidence": json.dumps(
                    {key: str(value) for key, value in sorted(evidence.items())}
                ),
                "incident": json.dumps(incident, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )


class LocalDevelopmentReleaseControlService(ReleaseControlService):
    """Expose v0.22 editing locally without publishing fake release evidence."""

    def current(self) -> ReleaseControlStatus:
        authoritative = super().current()
        if authoritative.state in {"hidden", "shadow"}:
            return ReleaseControlStatus(
                "explicit_eligible",
                authoritative.transition_sequence,
                authoritative.transition_artifact_id,
            )
        return authoritative


def _current_transition(connection: Connection) -> RowMapping | None:
    return (
        connection.execute(
            text(
                "SELECT transition.* FROM workspace.v022_release_transition transition "
                "JOIN lineage.artifact artifact ON artifact.artifact_id=transition.artifact_id "
                "WHERE artifact.status='published' ORDER BY sequence_number DESC LIMIT 1"
            )
        )
        .mappings()
        .one_or_none()
    )
