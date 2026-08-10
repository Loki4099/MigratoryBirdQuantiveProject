from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

GateKey = Literal["pit_universe", "terminal_event", "impact_policy"]
_GATES: tuple[GateKey, ...] = ("pit_universe", "terminal_event", "impact_policy")
_SOURCE_TYPES: dict[GateKey, str] = {
    "pit_universe": "pit_universe_snapshot",
    "terminal_event": "terminal_event_dataset",
    "impact_policy": "impact_policy",
}


@dataclass(frozen=True, slots=True)
class ReleaseGateStatus:
    formal_enabled: bool
    product_enabled: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        result = {
            "formal_enabled": self.formal_enabled,
            "product_enabled": self.product_enabled,
            "reason_codes": list(self.reason_codes),
        }
        return result


def current_release_gates() -> ReleaseGateStatus:
    """Safe default for pure callers that have no evidence repository."""
    reasons = tuple(f"{gate}_gate_open" for gate in _GATES)
    return ReleaseGateStatus(False, False, reasons)


class ReleaseGateEvidenceService:
    """Publishes and resolves immutable, versioned evidence; missing evidence fails closed."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        *,
        gate_key: GateKey,
        version_number: int,
        source_evidence_artifact_id: uuid.UUID,
        document: dict[str, Any],
    ) -> uuid.UUID:
        if version_number < 1:
            raise ValueError("Release Gate evidence version must be positive")
        self._validate_source(gate_key, source_evidence_artifact_id, document)
        payload = {"gate_key": gate_key, "version_number": version_number, **document}
        fingerprint = sha256_hexdigest(payload)

        def write(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": f"release_gate:{gate_key}"},
            )
            connection.execute(
                text(
                    "UPDATE workspace.release_gate_evidence SET active = false "
                    "WHERE gate_key = :gate_key AND active"
                ),
                {"gate_key": gate_key},
            )
            connection.execute(
                text("""
                INSERT INTO workspace.release_gate_evidence (
                    release_gate_evidence_id, artifact_id, gate_key, version_number,
                    source_evidence_artifact_id, document, active
                ) VALUES (:id, :artifact_id, :gate_key, :version_number,
                          :source_id, CAST(:document AS jsonb), true)
            """),
                {
                    "id": uuid.uuid4(),
                    "artifact_id": artifact_id,
                    "gate_key": gate_key,
                    "version_number": version_number,
                    "source_id": source_evidence_artifact_id,
                    "document": json.dumps(document, sort_keys=True, default=str),
                },
            )

        result = self._artifacts.publish(
            artifact_type="release_gate_evidence",
            artifact_key=f"{gate_key}__{fingerprint}",
            version_number=version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=(DependencyInput(source_evidence_artifact_id, "gate_source"),),
            draft_writer=write,
        )
        return result.artifact_id

    def current_status(self) -> ReleaseGateStatus:
        evidence = self.active_evidence()
        reasons = tuple(f"{gate}_gate_open" for gate in _GATES if gate not in evidence)
        return ReleaseGateStatus(not reasons, not reasons, reasons)

    def calibrate_comparison_context(
        self,
        *,
        comparison_context_artifact_id: uuid.UUID,
        version_number: int,
        impact_coefficient: Decimal = Decimal("0.5"),
        impact_maximum_bps: Decimal = Decimal("50"),
        defensive_basket_version: str = "standard_defensive_basket_long_history_v1",
    ) -> dict[str, uuid.UUID]:
        """Build production Gate sources from the exact frozen Comparison Context.

        Calibration fails closed when the context has no published eligibility
        snapshot or contains an unresolved terminal event effective in-range.
        """
        with self._engine.connect() as connection:
            context = (
                connection.execute(
                    text(
                        """
                        SELECT context.*, bundle.data_bundle_version_id,
                               universe.universe_version_id,
                               eligibility.eligibility_snapshot_id,
                               eligibility.artifact_id AS eligibility_artifact_id,
                               eligibility.requested_start, eligibility.requested_end,
                               eligibility.member_count, eligibility.eligible_count
                        FROM experiment.comparison_context context
                        JOIN lineage.artifact context_artifact
                          ON context_artifact.artifact_id = context.artifact_id
                         AND context_artifact.status = 'published'
                        JOIN data.data_bundle_version bundle
                          ON bundle.artifact_id = context.data_bundle_artifact_id
                        JOIN catalog.universe_version universe
                          ON universe.artifact_id = context.universe_history_artifact_id
                        JOIN catalog.eligibility_snapshot eligibility
                          ON eligibility.data_bundle_version_id = bundle.data_bundle_version_id
                         AND eligibility.universe_version_id = universe.universe_version_id
                        JOIN lineage.artifact eligibility_artifact
                          ON eligibility_artifact.artifact_id = eligibility.artifact_id
                         AND eligibility_artifact.status = 'published'
                        WHERE context.artifact_id = :context_id
                        ORDER BY eligibility.created_at DESC LIMIT 1
                        """
                    ),
                    {"context_id": comparison_context_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if context is None:
                raise ValueError("Comparison Context lacks a published Eligibility Snapshot")
            if (
                context["requested_start"] > context["resolved_start"]
                or context["requested_end"] < context["resolved_end"]
                or context["member_count"] < 2
            ):
                raise ValueError("Eligibility Snapshot does not cover the Comparison Context")
            unresolved = connection.execute(
                text(
                    """
                    SELECT count(*) FROM catalog.security_terminal_event event
                    JOIN catalog.security security ON security.security_id = event.security_id
                    JOIN catalog.eligibility_item item
                      ON item.asset_id = security.legacy_asset_id
                    WHERE item.eligibility_snapshot_id = :snapshot_id
                      AND event.effective_session <= :resolved_end
                      AND (event.status = 'unresolved' OR event.terminal_total_return IS NULL)
                    """
                ),
                {
                    "snapshot_id": context["eligibility_snapshot_id"],
                    "resolved_end": context["resolved_end"],
                },
            ).scalar_one()
            terminal_ids = tuple(
                connection.execute(
                    text(
                        """
                        SELECT event.artifact_id FROM catalog.security_terminal_event event
                        JOIN catalog.security security ON security.security_id = event.security_id
                        JOIN catalog.eligibility_item item
                          ON item.asset_id = security.legacy_asset_id
                        WHERE item.eligibility_snapshot_id = :snapshot_id
                          AND event.effective_session <= :resolved_end
                        ORDER BY event.effective_session, event.artifact_id
                        """
                    ),
                    {
                        "snapshot_id": context["eligibility_snapshot_id"],
                        "resolved_end": context["resolved_end"],
                    },
                )
                .scalars()
                .all()
            )
            sector_rows = [
                dict(row)
                for row in connection.execute(
                    text(
                        """
                        SELECT item.asset_id, value.value_key,
                               classification.valid_from, classification.valid_to
                        FROM catalog.eligibility_item item
                        JOIN catalog.asset_classification classification
                          ON classification.asset_id = item.asset_id
                        JOIN catalog.classification_value value
                          ON value.classification_value_id =
                             classification.classification_value_id
                        JOIN catalog.classification_scheme scheme
                          ON scheme.classification_scheme_id = value.classification_scheme_id
                        WHERE item.eligibility_snapshot_id = :snapshot_id
                          AND scheme.scheme_key IN ('sector','gics_sector')
                        ORDER BY item.asset_id, classification.valid_from,
                                 classification.valid_to, value.value_key
                        """
                    ),
                    {"snapshot_id": context["eligibility_snapshot_id"]},
                )
                .mappings()
                .all()
            ]
        if unresolved:
            raise ValueError("Unresolved terminal events keep the Terminal Gate open")
        key = context["context_fingerprint"]
        pit_payload = {
            "comparison_context_artifact_id": str(comparison_context_artifact_id),
            "eligibility_snapshot_artifact_id": str(context["eligibility_artifact_id"]),
            "member_count": context["member_count"],
            "eligible_count": context["eligible_count"],
            "coverage_start": context["requested_start"],
            "coverage_end": context["requested_end"],
            # Freeze PIT classification intervals into the Gate source.  Formal
            # execution must never reread a mutable "latest sector" table.
            "sector_classifications": sector_rows,
        }
        pit = self._artifacts.publish(
            artifact_type="pit_universe_snapshot",
            artifact_key=f"pit__{key}",
            version_number=version_number,
            semantic_payload=pit_payload,
            content_payload=pit_payload,
            dependencies=(
                DependencyInput(comparison_context_artifact_id, "comparison_context"),
                DependencyInput(context["eligibility_artifact_id"], "eligibility_snapshot"),
            ),
        )
        terminal_payload = {
            "comparison_context_artifact_id": str(comparison_context_artifact_id),
            "terminal_event_artifact_ids": [str(value) for value in terminal_ids],
            "unresolved_count": 0,
        }
        terminal = self._artifacts.publish(
            artifact_type="terminal_event_dataset",
            artifact_key=f"terminal__{key}",
            version_number=version_number,
            semantic_payload=terminal_payload,
            content_payload=terminal_payload,
            dependencies=(
                DependencyInput(comparison_context_artifact_id, "comparison_context"),
                *(
                    DependencyInput(value, "terminal_event", index)
                    for index, value in enumerate(terminal_ids)
                ),
            ),
        )
        impact_payload = {
            "comparison_context_artifact_id": str(comparison_context_artifact_id),
            "coefficient": str(impact_coefficient),
            "maximum_bps": str(impact_maximum_bps),
            "defensive_basket_version": defensive_basket_version,
        }
        impact = self._artifacts.publish(
            artifact_type="impact_policy",
            artifact_key=f"impact__{key}",
            version_number=version_number,
            semantic_payload=impact_payload,
            content_payload=impact_payload,
            dependencies=(DependencyInput(comparison_context_artifact_id, "comparison_context"),),
        )
        return {
            "pit_universe": self.publish(
                gate_key="pit_universe",
                version_number=version_number,
                source_evidence_artifact_id=pit.artifact_id,
                document={**pit_payload, "p0_finalized": True},
            ),
            "terminal_event": self.publish(
                gate_key="terminal_event",
                version_number=version_number,
                source_evidence_artifact_id=terminal.artifact_id,
                document={**terminal_payload, "p0_finalized": True},
            ),
            "impact_policy": self.publish(
                gate_key="impact_policy",
                version_number=version_number,
                source_evidence_artifact_id=impact.artifact_id,
                document={
                    **impact_payload,
                    "policy_key": f"v021_square_root_impact_v{version_number}",
                },
            ),
        }

    def active_evidence(self) -> dict[str, dict[str, Any]]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    text("""
                SELECT gate.gate_key, gate.artifact_id,
                       gate.source_evidence_artifact_id, gate.document,
                       source.artifact_type AS source_artifact_type
                FROM workspace.release_gate_evidence gate
                JOIN lineage.artifact gate_artifact ON gate_artifact.artifact_id = gate.artifact_id
                JOIN lineage.artifact source
                  ON source.artifact_id = gate.source_evidence_artifact_id
                WHERE gate.active AND gate_artifact.status = 'published'
                  AND source.status = 'published'
            """)
                )
                .mappings()
                .all()
            )
        result = {
            row["gate_key"]: {
                **dict(row["document"]),
                "gate_artifact_id": row["artifact_id"],
                "source_evidence_artifact_id": row["source_evidence_artifact_id"],
                "source_artifact_type": row["source_artifact_type"],
            }
            for row in rows
            if row["source_artifact_type"] == _SOURCE_TYPES[row["gate_key"]]
        }
        for key in tuple(result):
            if result[key].get("p0_finalized") is not True and key != "impact_policy":
                result.pop(key)
        impact = result.get("impact_policy")
        if impact is not None:
            try:
                valid_impact = (
                    Decimal(str(impact["coefficient"])) > 0
                    and Decimal(str(impact["maximum_bps"])) > 0
                )
                context_id = uuid.UUID(str(impact["comparison_context_artifact_id"]))
            except (KeyError, ValueError, ArithmeticError):
                valid_impact = False
                context_id = uuid.UUID(int=0)
            with self._engine.connect() as connection:
                context_ok = connection.execute(
                    text("""
                    SELECT EXISTS (
                        SELECT 1 FROM experiment.comparison_context context
                        JOIN lineage.artifact artifact
                          ON artifact.artifact_id = context.artifact_id
                        WHERE context.artifact_id = :id AND artifact.status = 'published'
                    )
                """),
                    {"id": context_id},
                ).scalar_one()
            if not valid_impact or not context_ok:
                result.pop("impact_policy", None)
        return result

    def _validate_source(
        self, gate_key: GateKey, source_id: uuid.UUID, document: dict[str, Any]
    ) -> None:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT artifact_type, status FROM lineage.artifact WHERE artifact_id = :id"
                    ),
                    {"id": source_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None or row["status"] != "published":
            raise ValueError("Release Gate source evidence must be published")
        if row["artifact_type"] != _SOURCE_TYPES[gate_key]:
            raise ValueError(f"{gate_key} requires {_SOURCE_TYPES[gate_key]} evidence")
        if gate_key == "impact_policy":
            required = {"coefficient", "maximum_bps", "comparison_context_artifact_id"}
            if not required.issubset(document):
                raise ValueError("Impact policy evidence is incomplete")
