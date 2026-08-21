# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

Lifecycle = Literal["active", "suspended", "superseded", "retired", "invalidated"]
Health = Literal["observing", "healthy", "watch", "warning", "data_interrupted"]


class LifecycleSequenceConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LifecyclePublication:
    enrollment_lifecycle_event_id: uuid.UUID
    artifact_id: uuid.UUID
    sequence_number: int
    from_lifecycle: Lifecycle
    to_lifecycle: Lifecycle
    event_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class MonitoringPublication:
    oos_monitoring_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    snapshot_fingerprint: str
    health: Health
    eligible_decision_count: int
    completed_decision_count: int
    missing_decision_count: int
    reused: bool


class EnrollmentLifecycleService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        product_enrollment_id: uuid.UUID,
        expected_sequence: int,
        target: Lifecycle,
        reason_code: str,
        reason: str,
        requested_by: str,
        requested_at: datetime,
        effective_at: datetime,
    ) -> LifecyclePublication:
        if expected_sequence < 1:
            raise ValueError("Lifecycle expected_sequence must be positive")
        if target not in {"active", "suspended", "superseded", "retired", "invalidated"}:
            raise ValueError(f"Unsupported Enrollment lifecycle: {target}")
        if any(not value.strip() for value in (reason_code, reason, requested_by)):
            raise ValueError("Lifecycle Event requires explicit reason and requester")
        if requested_at.tzinfo is None or effective_at.tzinfo is None:
            raise ValueError("Lifecycle Event timestamps must be timezone-aware")
        if requested_at > effective_at:
            raise ValueError("Lifecycle effective time cannot precede request")
        with self._engine.connect() as connection:
            enrollment = _business(
                connection,
                "product.v022_product_enrollment",
                "product_enrollment_id",
                product_enrollment_id,
            )
            prior = (
                connection.execute(
                    text(
                        "SELECT * FROM product.v022_enrollment_lifecycle_event "
                        "WHERE product_enrollment_id=:enrollment ORDER BY sequence_number DESC LIMIT 1"
                    ),
                    {"enrollment": product_enrollment_id},
                )
                .mappings()
                .one_or_none()
            )
        actual_sequence = 1 if prior is None else prior["sequence_number"] + 1
        if expected_sequence != actual_sequence:
            existing = self._existing(product_enrollment_id, expected_sequence)
            if existing is not None:
                return self._replay_or_conflict(
                    existing,
                    target=target,
                    reason_code=reason_code,
                    reason=reason,
                    requested_by=requested_by,
                    requested_at=requested_at,
                    effective_at=effective_at,
                )
            raise LifecycleSequenceConflict(
                f"Lifecycle expected sequence {expected_sequence}, current next is {actual_sequence}"
            )
        source = cast(Lifecycle, "active" if prior is None else prior["to_lifecycle"])
        _validate_transition(source, target)
        if prior is not None and effective_at < prior["effective_at"]:
            raise ValueError("Lifecycle effective times must be nondecreasing")
        semantic = {
            "contract_version": "v0.22.0",
            "enrollment_fingerprint": enrollment["enrollment_fingerprint"],
            "sequence_number": expected_sequence,
            "from_lifecycle": source,
            "to_lifecycle": target,
            "reason_code": reason_code,
            "reason": reason,
            "requested_by": requested_by,
            "requested_at": requested_at.isoformat(),
            "effective_at": effective_at.isoformat(),
        }
        fingerprint = sha256_hexdigest(semantic)
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:lifecycle:{fingerprint}")
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_enrollment_lifecycle_event",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=(DependencyInput(enrollment["artifact_id"], "enrollment", 0),),
            reason="publish append-only v0.22 Enrollment Lifecycle Event",
            draft_writer=partial(
                self._write,
                event_id=event_id,
                enrollment_id=product_enrollment_id,
                sequence=expected_sequence,
                source=source,
                target=target,
                reason_code=reason_code,
                reason=reason,
                requested_by=requested_by,
                requested_at=requested_at,
                effective_at=effective_at,
                fingerprint=fingerprint,
            ),
        )
        return LifecyclePublication(
            event_id,
            publication.artifact_id,
            expected_sequence,
            source,
            target,
            fingerprint,
            publication.reused,
        )

    def current(self, product_enrollment_id: uuid.UUID, *, as_of: datetime) -> Lifecycle:
        if as_of.tzinfo is None:
            raise ValueError("Lifecycle as_of must be timezone-aware")
        with self._engine.connect() as connection:
            _business(
                connection,
                "product.v022_product_enrollment",
                "product_enrollment_id",
                product_enrollment_id,
            )
            state = connection.scalar(
                text(
                    "SELECT to_lifecycle FROM product.v022_enrollment_lifecycle_event "
                    "WHERE product_enrollment_id=:enrollment AND effective_at<=:as_of "
                    "ORDER BY effective_at DESC,sequence_number DESC LIMIT 1"
                ),
                {"enrollment": product_enrollment_id, "as_of": as_of},
            )
        return cast(Lifecycle, state or "active")

    def _existing(self, enrollment_id: uuid.UUID, sequence: int) -> RowMapping | None:
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    text(
                        "SELECT event.*,artifact.status FROM product.v022_enrollment_lifecycle_event event "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=event.artifact_id "
                        "WHERE event.product_enrollment_id=:enrollment AND event.sequence_number=:sequence"
                    ),
                    {"enrollment": enrollment_id, "sequence": sequence},
                )
                .mappings()
                .one_or_none()
            )

    @staticmethod
    def _replay_or_conflict(
        row: RowMapping,
        *,
        target: Lifecycle,
        reason_code: str,
        reason: str,
        requested_by: str,
        requested_at: datetime,
        effective_at: datetime,
    ) -> LifecyclePublication:
        if row["status"] != "published" or any(
            (
                row["to_lifecycle"] != target,
                row["reason_code"] != reason_code,
                row["reason"] != reason,
                row["requested_by"] != requested_by,
                row["requested_at"] != requested_at,
                row["effective_at"] != effective_at,
            )
        ):
            raise LifecycleSequenceConflict("Lifecycle sequence is already bound to another event")
        return LifecyclePublication(
            row["enrollment_lifecycle_event_id"],
            row["artifact_id"],
            row["sequence_number"],
            cast(Lifecycle, row["from_lifecycle"]),
            cast(Lifecycle, row["to_lifecycle"]),
            row["event_fingerprint"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        event_id: uuid.UUID,
        enrollment_id: uuid.UUID,
        sequence: int,
        source: Lifecycle,
        target: Lifecycle,
        reason_code: str,
        reason: str,
        requested_by: str,
        requested_at: datetime,
        effective_at: datetime,
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_enrollment_lifecycle_event "
                "(enrollment_lifecycle_event_id,artifact_id,product_enrollment_id,sequence_number,"
                "from_lifecycle,to_lifecycle,reason_code,reason,requested_by,requested_at,effective_at,"
                "event_fingerprint) VALUES "
                "(:id,:artifact,:enrollment,:sequence,:source,:target,:reason_code,:reason,:requester,"
                ":requested_at,:effective_at,:fingerprint)"
            ),
            {
                "id": event_id,
                "artifact": artifact_id,
                "enrollment": enrollment_id,
                "sequence": sequence,
                "source": source,
                "target": target,
                "reason_code": reason_code,
                "reason": reason,
                "requester": requested_by,
                "requested_at": requested_at,
                "effective_at": effective_at,
                "fingerprint": fingerprint,
            },
        )


class OOSMonitoringService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        *,
        product_enrollment_id: uuid.UUID,
        monitoring_policy_version_id: uuid.UUID,
        monitoring_engine_artifact_id: uuid.UUID,
        as_of_decision_session_id: uuid.UUID,
        known_at: datetime,
        metrics_document: dict[str, Any],
    ) -> MonitoringPublication:
        if known_at.tzinfo is None:
            raise ValueError("Monitoring known_at must be timezone-aware")
        with self._engine.connect() as connection:
            enrollment = _business(
                connection,
                "product.v022_product_enrollment",
                "product_enrollment_id",
                product_enrollment_id,
            )
            execution = _business(
                connection,
                "product.v022_execution_version",
                "execution_version_id",
                enrollment["execution_version_id"],
            )
            policy = _business(
                connection,
                "product.v022_monitoring_policy_version",
                "monitoring_policy_version_id",
                monitoring_policy_version_id,
            )
            monitoring_engine = _artifact(connection, monitoring_engine_artifact_id)
            session = _session(connection, as_of_decision_session_id)
            decisions = tuple(
                connection.execute(
                    text(
                        "SELECT decision.*,artifact.semantic_fingerprint,artifact.artifact_id,"
                        "session.ordinal AS session_ordinal FROM product.v022_product_decision decision "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=decision.artifact_id "
                        "JOIN product.v022_decision_schedule_session session "
                        "ON session.decision_session_id=decision.decision_session_id "
                        "WHERE decision.product_enrollment_id=:enrollment AND decision.oos_eligible "
                        "AND artifact.status='published' AND session.ordinal<=:as_of "
                        "ORDER BY session.ordinal"
                    ),
                    {"enrollment": product_enrollment_id, "as_of": session["ordinal"]},
                ).mappings()
            )
        if policy["product_definition_id"] != execution["product_definition_id"]:
            raise ValueError("Monitoring Policy belongs to another Product")
        if session["decision_schedule_version_id"] != enrollment["decision_schedule_version_id"]:
            raise ValueError("Monitoring as-of Session belongs to another Enrollment Schedule")
        if known_at < session["decision_cutoff_at"]:
            raise ValueError("Monitoring known_at cannot precede the as-of decision cutoff")
        health, health_document = _evaluate_health(
            policy["monitoring_policy_document"], metrics_document, decisions
        )
        completed = sum(item["decision_status"] == "completed" for item in decisions)
        missing = len(decisions) - completed
        semantic = {
            "contract_version": "v0.22.0",
            "enrollment_fingerprint": enrollment["enrollment_fingerprint"],
            "monitoring_policy_fingerprint": policy["monitoring_policy_fingerprint"],
            "monitoring_engine_fingerprint": monitoring_engine["semantic_fingerprint"],
            "as_of_decision_session_id": str(as_of_decision_session_id),
            "known_at": known_at.isoformat(),
            "health": health,
            "eligible_decisions": [item["semantic_fingerprint"] for item in decisions],
            "metrics": metrics_document,
            "health_document": health_document,
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(fingerprint)
        if existing is not None:
            return existing
        snapshot_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:monitoring:{fingerprint}")
        dependencies = [
            DependencyInput(enrollment["artifact_id"], "enrollment", 0),
            DependencyInput(policy["artifact_id"], "monitoring_policy_version", 1),
            DependencyInput(monitoring_engine["artifact_id"], "monitoring_engine_version", 2),
        ]
        dependencies.extend(
            DependencyInput(item["artifact_id"], "oos_decision", ordinal + 3)
            for ordinal, item in enumerate(decisions)
        )
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_oos_monitoring_snapshot",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=tuple(dependencies),
            reason="publish immutable v0.22 OOS Monitoring Snapshot",
            draft_writer=partial(
                self._write,
                snapshot_id=snapshot_id,
                enrollment_id=product_enrollment_id,
                policy_id=monitoring_policy_version_id,
                monitoring_engine_artifact_id=monitoring_engine_artifact_id,
                session_id=as_of_decision_session_id,
                known_at=known_at,
                health=health,
                metrics=metrics_document,
                health_document=health_document,
                decisions=decisions,
                completed=completed,
                missing=missing,
                fingerprint=fingerprint,
            ),
        )
        return MonitoringPublication(
            snapshot_id,
            publication.artifact_id,
            fingerprint,
            health,
            len(decisions),
            completed,
            missing,
            publication.reused,
        )

    def _existing(self, fingerprint: str) -> MonitoringPublication | None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT snapshot.*,artifact.status FROM product.v022_oos_monitoring_snapshot snapshot "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=snapshot.artifact_id "
                        "WHERE snapshot.snapshot_fingerprint=:fingerprint"
                    ),
                    {"fingerprint": fingerprint},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("OOS Monitoring Snapshot Artifact is not published")
        return MonitoringPublication(
            row["oos_monitoring_snapshot_id"],
            row["artifact_id"],
            row["snapshot_fingerprint"],
            cast(Health, row["health"]),
            row["eligible_decision_count"],
            row["completed_decision_count"],
            row["missing_decision_count"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        snapshot_id: uuid.UUID,
        enrollment_id: uuid.UUID,
        policy_id: uuid.UUID,
        monitoring_engine_artifact_id: uuid.UUID,
        session_id: uuid.UUID,
        known_at: datetime,
        health: Health,
        metrics: dict[str, Any],
        health_document: dict[str, Any],
        decisions: tuple[RowMapping, ...],
        completed: int,
        missing: int,
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_oos_monitoring_snapshot "
                "(oos_monitoring_snapshot_id,artifact_id,product_enrollment_id,"
                "monitoring_policy_version_id,monitoring_engine_artifact_id,"
                "as_of_decision_session_id,known_at,health,"
                "eligible_decision_count,completed_decision_count,missing_decision_count,"
                "metrics_document,health_document,snapshot_fingerprint) VALUES "
                "(:id,:artifact,:enrollment,:policy,:engine,:session,:known_at,:health,:eligible,:completed,"
                ":missing,CAST(:metrics AS jsonb),CAST(:health_document AS jsonb),:fingerprint)"
            ),
            {
                "id": snapshot_id,
                "artifact": artifact_id,
                "enrollment": enrollment_id,
                "policy": policy_id,
                "engine": monitoring_engine_artifact_id,
                "session": session_id,
                "known_at": known_at,
                "health": health,
                "eligible": len(decisions),
                "completed": completed,
                "missing": missing,
                "metrics": json.dumps(metrics, sort_keys=True),
                "health_document": json.dumps(health_document, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )
        for ordinal, decision in enumerate(decisions, 1):
            connection.execute(
                text(
                    "INSERT INTO product.v022_oos_monitoring_snapshot_decision "
                    "(oos_monitoring_snapshot_id,ordinal,product_decision_id) "
                    "VALUES (:snapshot,:ordinal,:decision)"
                ),
                {
                    "snapshot": snapshot_id,
                    "ordinal": ordinal,
                    "decision": decision["product_decision_id"],
                },
            )


def _evaluate_health(
    policy: dict[str, Any], metrics: dict[str, Any], decisions: tuple[RowMapping, ...]
) -> tuple[Health, dict[str, Any]]:
    required = {
        "minimum_completed_decisions",
        "maximum_missing_fraction",
        "coverage_warning_floor",
        "coverage_watch_floor",
    }
    if not required.issubset(policy):
        raise ValueError("Monitoring Policy lacks required v0.22 health thresholds")
    if "signal_coverage" not in metrics:
        raise ValueError("Monitoring metrics require signal_coverage")
    minimum = int(policy["minimum_completed_decisions"])
    maximum_missing = Decimal(str(policy["maximum_missing_fraction"]))
    warning_floor = Decimal(str(policy["coverage_warning_floor"]))
    watch_floor = Decimal(str(policy["coverage_watch_floor"]))
    coverage = Decimal(str(metrics.get("signal_coverage")))
    if minimum < 1 or not (
        Decimal("0") <= maximum_missing <= Decimal("1")
        and Decimal("0") <= warning_floor <= watch_floor <= Decimal("1")
        and Decimal("0") <= coverage <= Decimal("1")
    ):
        raise ValueError("Monitoring thresholds and signal coverage must be canonical fractions")
    completed = sum(item["decision_status"] == "completed" for item in decisions)
    missing = len(decisions) - completed
    missing_fraction = Decimal(missing) / Decimal(len(decisions)) if decisions else Decimal("0")
    reasons: list[str] = []
    if missing_fraction > maximum_missing:
        health: Health = "data_interrupted"
        reasons.append("missing_decision_fraction_exceeded")
    elif completed < minimum:
        health = "observing"
        reasons.append("minimum_completed_decisions_not_met")
    elif coverage < warning_floor:
        health = "warning"
        reasons.append("signal_coverage_below_warning_floor")
    elif coverage < watch_floor:
        health = "watch"
        reasons.append("signal_coverage_below_watch_floor")
    else:
        health = "healthy"
    return health, {
        "reason_codes": reasons,
        "eligible_decision_count": len(decisions),
        "completed_decision_count": completed,
        "missing_decision_count": missing,
        "missing_fraction": str(missing_fraction),
        "signal_coverage": str(coverage),
        "thresholds": {key: policy[key] for key in sorted(required)},
    }


def _validate_transition(source: Lifecycle, target: Lifecycle) -> None:
    allowed: dict[Lifecycle, set[Lifecycle]] = {
        "active": {"suspended", "superseded", "retired", "invalidated"},
        "suspended": {"active", "superseded", "retired", "invalidated"},
        "superseded": {"retired"},
        "retired": set(),
        "invalidated": set(),
    }
    if target not in allowed[source]:
        raise ValueError(f"Illegal Enrollment lifecycle transition: {source} -> {target}")


def _business(
    connection: Connection, table: str, id_column: str, identity: uuid.UUID
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.*,artifact.status FROM {table} business JOIN lineage.artifact artifact "
                f"ON artifact.artifact_id=business.artifact_id WHERE business.{id_column}=:identity"
            ),
            {"identity": identity},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        raise ValueError(f"Expected published {table} identity: {identity}")
    return row


def _session(connection: Connection, session_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM product.v022_decision_schedule_session WHERE decision_session_id=:session"
            ),
            {"session": session_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"Decision Session not found: {session_id}")
    return row


def _artifact(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM lineage.artifact WHERE artifact_id=:artifact AND status='published'"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Expected published Monitoring Engine Artifact: {artifact_id}")
    return row
