from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

SLODomain = Literal["compile", "queue", "cache", "storage", "export", "product_freshness"]
SLOComparator = Literal["lte", "gte"]
AlertSeverity = Literal["warning", "critical"]
REQUIRED_SLO_DOMAINS = frozenset(
    {"compile", "queue", "cache", "storage", "export", "product_freshness"}
)


@dataclass(frozen=True, slots=True)
class SLORule:
    metric_key: str
    domain_key: SLODomain
    comparator: SLOComparator
    threshold: Decimal
    minimum_sample_count: int
    severity: AlertSeverity

    def validated(self) -> SLORule:
        if not self.metric_key.strip() or self.domain_key not in REQUIRED_SLO_DOMAINS:
            raise ValueError("SLO rule requires a metric key and supported domain")
        if self.comparator not in {"lte", "gte"}:
            raise ValueError(f"Unsupported SLO comparator: {self.comparator}")
        if not self.threshold.is_finite() or self.minimum_sample_count < 1:
            raise ValueError("SLO threshold must be finite and sample count positive")
        if self.severity not in {"warning", "critical"}:
            raise ValueError(f"Unsupported SLO alert severity: {self.severity}")
        return self


@dataclass(frozen=True, slots=True)
class SLOMeasurementInput:
    metric_key: str
    domain_key: SLODomain
    observed_value: Decimal
    sample_count: int
    window_start_at: datetime
    window_end_at: datetime
    measured_at: datetime
    probe_document: dict[str, object]


@dataclass(frozen=True, slots=True)
class SLOMeasurement:
    slo_measurement_id: uuid.UUID
    artifact_id: uuid.UUID
    metric_key: str
    domain_key: SLODomain
    observed_value: Decimal
    sample_count: int
    window_start_at: datetime
    window_end_at: datetime
    measurement_fingerprint: str


@dataclass(frozen=True, slots=True)
class SLORuleResult:
    ordinal: int
    metric_key: str
    domain_key: SLODomain
    slo_measurement_id: uuid.UUID | None
    comparator: SLOComparator
    threshold: Decimal
    observed_value: Decimal | None
    minimum_sample_count: int
    actual_sample_count: int
    severity: AlertSeverity
    passed: bool
    blocker_code: str | None


@dataclass(frozen=True, slots=True)
class SLOPolicyPublication:
    slo_policy_version_id: uuid.UUID
    artifact_id: uuid.UUID
    policy_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class SLOMeasurementPublication:
    slo_measurement_id: uuid.UUID
    artifact_id: uuid.UUID
    measurement_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class OperationsReadinessPublication:
    operations_readiness_snapshot_id: uuid.UUID
    artifact_id: uuid.UUID
    readiness_fingerprint: str
    ready_for_default: bool
    blocker_codes: tuple[str, ...]
    alert_count: int
    results: tuple[SLORuleResult, ...]
    reused: bool


def evaluate_slo_rules(
    rules: tuple[SLORule, ...], measurements: tuple[SLOMeasurement, ...]
) -> tuple[tuple[SLORuleResult, ...], tuple[str, ...]]:
    validated = tuple(rule.validated() for rule in rules)
    if not validated:
        raise ValueError("Operations SLO policy requires rules")
    metric_keys = [rule.metric_key for rule in validated]
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError("Operations SLO rule metric keys must be unique")
    domains = {rule.domain_key for rule in validated}
    if domains != REQUIRED_SLO_DOMAINS:
        missing = sorted(REQUIRED_SLO_DOMAINS - domains)
        raise ValueError(f"Operations SLO policy is missing required domains: {missing}")
    measurement_by_key: dict[str, SLOMeasurement] = {}
    for candidate in measurements:
        if candidate.metric_key in measurement_by_key:
            raise ValueError("Operations SLO measurements contain duplicate metric keys")
        measurement_by_key[candidate.metric_key] = candidate
    results: list[SLORuleResult] = []
    blockers: list[str] = []
    for ordinal, rule in enumerate(validated, start=1):
        measurement = measurement_by_key.get(rule.metric_key)
        blocker: str | None = None
        passed = False
        if measurement is None or measurement.domain_key != rule.domain_key:
            blocker = f"missing_measurement:{rule.metric_key}"
        elif measurement.sample_count < rule.minimum_sample_count:
            blocker = f"insufficient_samples:{rule.metric_key}"
        else:
            passed = (
                measurement.observed_value <= rule.threshold
                if rule.comparator == "lte"
                else measurement.observed_value >= rule.threshold
            )
            if not passed:
                blocker = f"slo_breach:{rule.metric_key}"
        if blocker is not None:
            blockers.append(blocker)
        results.append(
            SLORuleResult(
                ordinal,
                rule.metric_key,
                rule.domain_key,
                measurement.slo_measurement_id if measurement else None,
                rule.comparator,
                rule.threshold,
                measurement.observed_value if measurement else None,
                rule.minimum_sample_count,
                measurement.sample_count if measurement else 0,
                rule.severity,
                passed,
                blocker,
            )
        )
    return tuple(results), tuple(blockers)


class SLOPolicyService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self, *, policy_key: str, version_number: int, rules: tuple[SLORule, ...]
    ) -> SLOPolicyPublication:
        policy_key = policy_key.strip()
        if not policy_key or version_number < 1:
            raise ValueError("SLO policy key and positive version are required")
        evaluate_slo_rules(rules, ())
        rule_document = [_rule_document(rule) for rule in rules]
        semantic = {
            "contract_version": "v0.22.0",
            "policy_key": policy_key,
            "version_number": version_number,
            "rules": rule_document,
        }
        fingerprint = sha256_hexdigest(semantic)
        existing = self._existing(policy_key, version_number)
        if existing is not None:
            if existing.policy_fingerprint != fingerprint:
                raise ValueError("SLO Policy version is already bound to different semantics")
            return existing
        policy_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:slo-policy:{fingerprint}")
        publication = self._artifacts.publish(
            artifact_type="v022_slo_policy_version",
            artifact_key=policy_key,
            version_number=version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            reason="publish v0.22 operational SLO Policy Version",
            draft_writer=partial(
                self._write,
                policy_id=policy_id,
                policy_key=policy_key,
                version_number=version_number,
                rules=rule_document,
                fingerprint=fingerprint,
            ),
        )
        return SLOPolicyPublication(
            policy_id, publication.artifact_id, fingerprint, publication.reused
        )

    def _existing(self, key: str, version: int) -> SLOPolicyPublication | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT policy.*,artifact.status FROM ops.v022_slo_policy_version policy "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=policy.artifact_id "
                    "WHERE policy.policy_key=:key AND policy.version_number=:version"
                ),
                {"key": key, "version": version},
            ).mappings().one_or_none()
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError("SLO Policy Artifact is not published")
        return SLOPolicyPublication(
            row["slo_policy_version_id"],
            row["artifact_id"],
            row["policy_fingerprint"],
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        policy_id: uuid.UUID,
        policy_key: str,
        version_number: int,
        rules: list[dict[str, object]],
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO ops.v022_slo_policy_version (
                  slo_policy_version_id,artifact_id,policy_key,version_number,rule_document,
                  rule_count,policy_fingerprint
                ) VALUES (:id,:artifact,:key,:version,CAST(:rules AS jsonb),:count,:fingerprint)
                """
            ),
            {
                "id": policy_id,
                "artifact": artifact_id,
                "key": policy_key,
                "version": version_number,
                "rules": json.dumps(rules, sort_keys=True),
                "count": len(rules),
                "fingerprint": fingerprint,
            },
        )


class SLOMeasurementService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(self, measurement: SLOMeasurementInput) -> SLOMeasurementPublication:
        if measurement.domain_key not in REQUIRED_SLO_DOMAINS:
            raise ValueError(f"Unsupported SLO measurement domain: {measurement.domain_key}")
        if not measurement.metric_key.strip() or not measurement.observed_value.is_finite():
            raise ValueError("SLO measurement key and finite value are required")
        timestamps = (
            measurement.window_start_at,
            measurement.window_end_at,
            measurement.measured_at,
        )
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("SLO measurement timestamps must be timezone-aware")
        if (
            measurement.sample_count < 1
            or measurement.window_start_at >= measurement.window_end_at
            or measurement.measured_at < measurement.window_end_at
            or not measurement.probe_document
        ):
            raise ValueError("SLO measurement window, samples, and probe document are invalid")
        semantic = {
            "contract_version": "v0.22.0",
            "metric_key": measurement.metric_key,
            "domain_key": measurement.domain_key,
            "observed_value": measurement.observed_value,
            "sample_count": measurement.sample_count,
            "window_start_at": measurement.window_start_at,
            "window_end_at": measurement.window_end_at,
            "measured_at": measurement.measured_at,
            "probe_document": measurement.probe_document,
        }
        fingerprint = sha256_hexdigest(semantic)
        measurement_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:slo-measurement:{fingerprint}"
        )
        publication = self._artifacts.publish(
            artifact_type="v022_operational_slo_measurement",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            reason="publish immutable v0.22 operational SLO Measurement",
            draft_writer=partial(
                self._write,
                measurement_id=measurement_id,
                measurement=measurement,
                fingerprint=fingerprint,
            ),
        )
        return SLOMeasurementPublication(
            measurement_id, publication.artifact_id, fingerprint, publication.reused
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        measurement_id: uuid.UUID,
        measurement: SLOMeasurementInput,
        fingerprint: str,
    ) -> None:
        connection.execute(
            text(
                """
                INSERT INTO ops.v022_slo_measurement (
                  slo_measurement_id,artifact_id,metric_key,domain_key,observed_value,
                  sample_count,window_start_at,window_end_at,measured_at,probe_document,
                  measurement_fingerprint
                ) VALUES (:id,:artifact,:metric,:domain,:value,:samples,:window_start,
                          :window_end,:measured,CAST(:probe AS jsonb),:fingerprint)
                """
            ),
            {
                "id": measurement_id,
                "artifact": artifact_id,
                "metric": measurement.metric_key,
                "domain": measurement.domain_key,
                "value": measurement.observed_value,
                "samples": measurement.sample_count,
                "window_start": measurement.window_start_at,
                "window_end": measurement.window_end_at,
                "measured": measurement.measured_at,
                "probe": json.dumps(measurement.probe_document, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )


class OperationsReadinessService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        slo_policy_version_id: uuid.UUID,
        window_start_at: datetime,
        window_end_at: datetime,
        measurement_ids: tuple[uuid.UUID, ...],
        evaluated_at: datetime | None = None,
    ) -> OperationsReadinessPublication:
        occurred_at = evaluated_at or datetime.now(UTC)
        if any(
            value.tzinfo is None for value in (window_start_at, window_end_at, occurred_at)
        ):
            raise ValueError("Operations Readiness timestamps must be timezone-aware")
        if window_start_at >= window_end_at or occurred_at < window_end_at:
            raise ValueError("Operations Readiness window or evaluation time is invalid")
        if len(measurement_ids) != len(set(measurement_ids)):
            raise ValueError("Operations Readiness Measurement IDs must be unique")
        policy, rules = self._policy(slo_policy_version_id)
        measurements = self._measurements(measurement_ids)
        for measurement in measurements:
            if (
                measurement.window_start_at != window_start_at
                or measurement.window_end_at != window_end_at
            ):
                raise ValueError("Operations Readiness Measurements must share the exact window")
        results, blockers = evaluate_slo_rules(rules, measurements)
        ready = not blockers
        document = {
            "contract_version": "v0.22.0",
            "policy_fingerprint": policy["policy_fingerprint"],
            "window_start_at": window_start_at,
            "window_end_at": window_end_at,
            "evaluated_at": occurred_at,
            "ready_for_default": ready,
            "blocker_codes": list(blockers),
            "results": [asdict(item) for item in results],
        }
        fingerprint = sha256_hexdigest(document)
        existing = self._existing(slo_policy_version_id, window_start_at, window_end_at)
        if existing is not None:
            if existing.readiness_fingerprint != fingerprint:
                raise ValueError("Operations Readiness window already has different evidence")
            return existing
        snapshot_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:operations-readiness:{fingerprint}"
        )
        measurement_by_id = {item.slo_measurement_id: item for item in measurements}
        dependencies = [DependencyInput(policy["artifact_id"], "slo_policy", 0)]
        dependencies.extend(
            DependencyInput(item.artifact_id, "slo_measurement", ordinal)
            for ordinal, item in enumerate(measurements, start=1)
        )
        publication = self._artifacts.publish(
            artifact_type="v022_operations_readiness_evidence",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=document,
            content_payload=document,
            dependencies=tuple(dependencies),
            reason="publish v0.22 Operations Readiness SLO evidence",
            draft_writer=partial(
                self._write,
                snapshot_id=snapshot_id,
                policy_id=slo_policy_version_id,
                window_start=window_start_at,
                window_end=window_end_at,
                evaluated_at=occurred_at,
                results=results,
                blockers=blockers,
                document=document,
                fingerprint=fingerprint,
                measurement_by_id=measurement_by_id,
            ),
        )
        return OperationsReadinessPublication(
            snapshot_id,
            publication.artifact_id,
            fingerprint,
            ready,
            blockers,
            len(blockers),
            results,
            publication.reused,
        )

    def _policy(
        self, policy_id: uuid.UUID
    ) -> tuple[RowMapping, tuple[SLORule, ...]]:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT policy.* FROM ops.v022_slo_policy_version policy "
                    "JOIN lineage.artifact artifact ON artifact.artifact_id=policy.artifact_id "
                    "WHERE policy.slo_policy_version_id=:policy AND artifact.status='published'"
                ),
                {"policy": policy_id},
            ).mappings().one_or_none()
        if row is None:
            raise ValueError("Operations Readiness requires a published SLO Policy")
        return row, tuple(_rule_from_document(item) for item in row["rule_document"])

    def _measurements(self, ids: tuple[uuid.UUID, ...]) -> tuple[SLOMeasurement, ...]:
        if not ids:
            return ()
        with self._engine.connect() as connection:
            rows = tuple(
                connection.execute(
                    text(
                        "SELECT measurement.* FROM ops.v022_slo_measurement measurement "
                        "JOIN lineage.artifact artifact "
                        "ON artifact.artifact_id=measurement.artifact_id "
                        "WHERE measurement.slo_measurement_id=ANY(:ids) "
                        "AND artifact.status='published' ORDER BY measurement.metric_key"
                    ),
                    {"ids": list(ids)},
                ).mappings()
            )
        if len(rows) != len(ids):
            raise ValueError("Operations Readiness has unknown or unpublished Measurements")
        return tuple(
            SLOMeasurement(
                row["slo_measurement_id"],
                row["artifact_id"],
                row["metric_key"],
                row["domain_key"],
                row["observed_value"],
                row["sample_count"],
                row["window_start_at"],
                row["window_end_at"],
                row["measurement_fingerprint"],
            )
            for row in rows
        )

    def _existing(
        self, policy_id: uuid.UUID, window_start: datetime, window_end: datetime
    ) -> OperationsReadinessPublication | None:
        with self._engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT snapshot.*,artifact.status "
                    "FROM ops.v022_operations_readiness_snapshot snapshot "
                    "JOIN lineage.artifact artifact "
                    "ON artifact.artifact_id=snapshot.artifact_id "
                    "WHERE snapshot.slo_policy_version_id=:policy "
                    "AND snapshot.window_start_at=:window_start "
                    "AND snapshot.window_end_at=:window_end"
                ),
                {
                    "policy": policy_id,
                    "window_start": window_start,
                    "window_end": window_end,
                },
            ).mappings().one_or_none()
            if row is None:
                return None
            results = tuple(
                _result_from_row(item)
                for item in connection.execute(
                    text(
                        "SELECT * FROM ops.v022_operations_readiness_member "
                        "WHERE operations_readiness_snapshot_id=:snapshot ORDER BY ordinal"
                    ),
                    {"snapshot": row["operations_readiness_snapshot_id"]},
                ).mappings()
            )
            alert_count = connection.scalar(
                text(
                    "SELECT count(*) FROM ops.v022_operational_alert "
                    "WHERE operations_readiness_snapshot_id=:snapshot"
                ),
                {"snapshot": row["operations_readiness_snapshot_id"]},
            )
        if row["status"] != "published":
            raise ValueError("Operations Readiness Artifact is not published")
        return OperationsReadinessPublication(
            row["operations_readiness_snapshot_id"],
            row["artifact_id"],
            row["readiness_fingerprint"],
            row["ready_for_default"],
            tuple(row["blocker_codes"]),
            int(alert_count or 0),
            results,
            True,
        )

    @staticmethod
    def _write(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        snapshot_id: uuid.UUID,
        policy_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
        evaluated_at: datetime,
        results: tuple[SLORuleResult, ...],
        blockers: tuple[str, ...],
        document: dict[str, object],
        fingerprint: str,
        measurement_by_id: dict[uuid.UUID, SLOMeasurement],
    ) -> None:
        passed_count = sum(item.passed for item in results)
        connection.execute(
            text(
                """
                INSERT INTO ops.v022_operations_readiness_snapshot (
                  operations_readiness_snapshot_id,artifact_id,slo_policy_version_id,
                  window_start_at,window_end_at,evaluated_at,ready_for_default,rule_count,
                  passed_rule_count,blocker_codes,readiness_document,readiness_fingerprint
                ) VALUES (:id,:artifact,:policy,:window_start,:window_end,:evaluated,:ready,
                          :rule_count,:passed_count,CAST(:blockers AS jsonb),
                          CAST(:document AS jsonb),:fingerprint)
                """
            ),
            {
                "id": snapshot_id,
                "artifact": artifact_id,
                "policy": policy_id,
                "window_start": window_start,
                "window_end": window_end,
                "evaluated": evaluated_at,
                "ready": not blockers,
                "rule_count": len(results),
                "passed_count": passed_count,
                "blockers": json.dumps(blockers),
                "document": json.dumps(document, sort_keys=True, default=str),
                "fingerprint": fingerprint,
            },
        )
        for result in results:
            connection.execute(
                text(
                    """
                    INSERT INTO ops.v022_operations_readiness_member (
                      operations_readiness_snapshot_id,ordinal,metric_key,domain_key,
                      slo_measurement_id,comparator,threshold,observed_value,
                      minimum_sample_count,actual_sample_count,severity,passed,blocker_code
                    ) VALUES (:snapshot,:ordinal,:metric,:domain,:measurement,:comparator,
                              :threshold,:observed,:minimum_samples,:actual_samples,:severity,
                              :passed,:blocker)
                    """
                ),
                {
                    "snapshot": snapshot_id,
                    "ordinal": result.ordinal,
                    "metric": result.metric_key,
                    "domain": result.domain_key,
                    "measurement": result.slo_measurement_id,
                    "comparator": result.comparator,
                    "threshold": result.threshold,
                    "observed": result.observed_value,
                    "minimum_samples": result.minimum_sample_count,
                    "actual_samples": result.actual_sample_count,
                    "severity": result.severity,
                    "passed": result.passed,
                    "blocker": result.blocker_code,
                },
            )
            if result.passed:
                continue
            measurement = (
                measurement_by_id.get(result.slo_measurement_id)
                if result.slo_measurement_id
                else None
            )
            alert_document = {
                "metric_key": result.metric_key,
                "domain_key": result.domain_key,
                "threshold": result.threshold,
                "observed_value": result.observed_value,
                "minimum_sample_count": result.minimum_sample_count,
                "actual_sample_count": result.actual_sample_count,
                "measurement_artifact_id": (
                    str(measurement.artifact_id) if measurement else None
                ),
                "alert_code": result.blocker_code,
            }
            connection.execute(
                text(
                    """
                    INSERT INTO ops.v022_operational_alert (
                      operational_alert_id,operations_readiness_snapshot_id,member_ordinal,
                      metric_key,domain_key,slo_measurement_id,severity,alert_code,
                      alert_document,opened_at
                    ) VALUES (:id,:snapshot,:ordinal,:metric,:domain,:measurement,:severity,
                              :code,CAST(:document AS jsonb),:opened_at)
                    """
                ),
                {
                    "id": uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"bird:v0.22:operational-alert:{fingerprint}:{result.metric_key}",
                    ),
                    "snapshot": snapshot_id,
                    "ordinal": result.ordinal,
                    "metric": result.metric_key,
                    "domain": result.domain_key,
                    "measurement": result.slo_measurement_id,
                    "severity": result.severity,
                    "code": result.blocker_code,
                    "document": json.dumps(alert_document, sort_keys=True, default=str),
                    "opened_at": evaluated_at,
                },
            )


def _rule_document(rule: SLORule) -> dict[str, object]:
    return {
        "metric_key": rule.metric_key,
        "domain_key": rule.domain_key,
        "comparator": rule.comparator,
        "threshold": str(rule.threshold),
        "minimum_sample_count": rule.minimum_sample_count,
        "severity": rule.severity,
    }


def _rule_from_document(document: object) -> SLORule:
    if not isinstance(document, dict):
        raise ValueError("SLO Policy rule document is malformed")
    return SLORule(
        str(document["metric_key"]),
        str(document["domain_key"]),  # type: ignore[arg-type]
        str(document["comparator"]),  # type: ignore[arg-type]
        Decimal(str(document["threshold"])),
        int(document["minimum_sample_count"]),
        str(document["severity"]),  # type: ignore[arg-type]
    ).validated()


def _result_from_row(row: RowMapping) -> SLORuleResult:
    return SLORuleResult(
        row["ordinal"],
        row["metric_key"],
        row["domain_key"],
        row["slo_measurement_id"],
        row["comparator"],
        row["threshold"],
        row["observed_value"],
        row["minimum_sample_count"],
        row["actual_sample_count"],
        row["severity"],
        row["passed"],
        row["blocker_code"],
    )
