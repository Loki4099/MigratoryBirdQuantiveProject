from __future__ import annotations

import json
import uuid
from typing import Any, cast

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.product_identity import ProductIdentityService

_COMMAND_KIND = "promote_v022_product_candidate"


class ProductCandidateService:
    """Publish candidate-only Product identities from exact v0.22 Result Evidence."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._products = ProductIdentityService(engine)

    def promote(
        self,
        *,
        result_evidence_snapshot_id: uuid.UUID,
        actor_key: str,
        idempotency_key: uuid.UUID,
        product_key: str,
        name: str,
        description: str,
        version_number: int,
    ) -> dict[str, Any]:
        product_key = product_key.strip()
        name = name.strip()
        description = description.strip()
        if not actor_key.strip() or not product_key or not name:
            raise ValueError("Actor, Product key, and Product name are required")
        if version_number < 1:
            raise ValueError("Product candidate version_number must be positive")
        request = {
            "contract_version": "v0.22.0",
            "result_evidence_snapshot_id": str(result_evidence_snapshot_id),
            "product_key": product_key,
            "name": name,
            "description": description,
            "version_number": version_number,
        }
        request_fingerprint = sha256_hexdigest(request)
        lock_key = f"{actor_key}:{_COMMAND_KIND}:{idempotency_key}"
        with self._engine.connect() as lock_connection:
            lock_connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key,0))"),
                {"key": lock_key},
            )
            lock_connection.commit()
            try:
                replay = self._replay(
                    lock_connection,
                    actor_key=actor_key,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return {**replay, "reused": True}
                lock_connection.commit()
                evidence = self._evidence(result_evidence_snapshot_id)
                quality = cast(dict[str, Any], evidence["quality_document"])
                if quality.get("state") != "passed" or quality.get("outcome") != "accepted":
                    raise ValueError(
                        "Only an accepted, passed v0.22 Result Evidence may become "
                        "a Product candidate"
                    )
                definition = self._products.publish_definition(
                    product_key=product_key,
                    name=name,
                    description=description,
                )
                execution = self._products.publish_execution_version(
                    product_definition_id=definition.product_definition_id,
                    version_number=version_number,
                    configuration_snapshot_id=evidence["configuration_snapshot_id"],
                    promotion_result_evidence_snapshot_id=result_evidence_snapshot_id,
                    runtime_policy_document={
                        "candidate_stage": "candidate",
                        "runtime_contract": "v022_compiled_graph_candidate_v1",
                        "price_basis": "back_adjusted",
                        "market_price_mapping_required": True,
                        "automatic_enrollment": False,
                    },
                )
                qualification = self._products.publish_qualification_version(
                    product_definition_id=definition.product_definition_id,
                    version_number=version_number,
                    execution_version_id=execution.version_id,
                    result_evidence_snapshot_id=result_evidence_snapshot_id,
                    qualification_document={
                        "status": "research_candidate",
                        "evidence_class": evidence["evidence_class"],
                        "quality": quality,
                        "back_adjusted_research_input": True,
                        "market_price_mapping_required": True,
                        "warning_codes": [
                            "back_adjusted_research_requires_market_price_mapping"
                        ],
                    },
                )
                monitoring = self._products.publish_monitoring_policy_version(
                    product_definition_id=definition.product_definition_id,
                    version_number=version_number,
                    monitoring_policy_document={
                        "policy_key": "candidate_observation_v1",
                        "lifecycle": "not_enrolled",
                        "automatic_activation": False,
                        "required_next_step": "explicit_product_enrollment",
                        "minimum_completed_decisions": 1,
                        "maximum_missing_fraction": "0.50",
                        "coverage_warning_floor": "0.80",
                        "coverage_watch_floor": "0.90",
                    },
                )
                response = {
                    "result_evidence_snapshot_id": str(result_evidence_snapshot_id),
                    "product_definition_id": str(definition.product_definition_id),
                    "product_definition_artifact_id": str(definition.artifact_id),
                    "execution_version_id": str(execution.version_id),
                    "execution_version_artifact_id": str(execution.artifact_id),
                    "qualification_version_id": str(qualification.version_id),
                    "qualification_version_artifact_id": str(qualification.artifact_id),
                    "monitoring_policy_version_id": str(monitoring.version_id),
                    "monitoring_policy_version_artifact_id": str(monitoring.artifact_id),
                    "version_number": version_number,
                    "lifecycle": "candidate",
                    "reused": all(
                        (
                            definition.reused,
                            execution.reused,
                            qualification.reused,
                            monitoring.reused,
                        )
                    ),
                }
                with lock_connection.begin():
                    lock_connection.execute(
                        text(
                            """
                            INSERT INTO workspace.v022_command_result (
                              command_result_id,actor_key,command_kind,idempotency_key,
                              request_fingerprint,response_document
                            ) VALUES (
                              :id,:actor,:kind,:key,:fingerprint,CAST(:response AS jsonb)
                            )
                            """
                        ),
                        {
                            "id": uuid.uuid4(),
                            "actor": actor_key,
                            "kind": _COMMAND_KIND,
                            "key": idempotency_key,
                            "fingerprint": request_fingerprint,
                            "response": json.dumps(response, sort_keys=True),
                        },
                    )
                return response
            finally:
                if lock_connection.in_transaction():
                    lock_connection.rollback()
                lock_connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key,0))"),
                    {"key": lock_key},
                )

    def _evidence(self, evidence_id: uuid.UUID) -> Any:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT evidence.*,artifact.status
                          FROM experiment.v022_result_evidence_snapshot evidence
                          JOIN lineage.artifact artifact
                            ON artifact.artifact_id=evidence.artifact_id
                         WHERE evidence.result_evidence_snapshot_id=:evidence
                        """
                    ),
                    {"evidence": evidence_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise LookupError(f"v0.22 Result Evidence not found: {evidence_id}")
        if row["status"] != "published":
            raise ValueError("v0.22 Result Evidence must be published")
        return row

    @staticmethod
    def _replay(
        connection: Any,
        *,
        actor_key: str,
        idempotency_key: uuid.UUID,
        request_fingerprint: str,
    ) -> dict[str, Any] | None:
        row = (
            connection.execute(
                text(
                    """
                    SELECT request_fingerprint,response_document
                      FROM workspace.v022_command_result
                     WHERE actor_key=:actor AND command_kind=:kind
                       AND idempotency_key=:key
                    """
                ),
                {"actor": actor_key, "kind": _COMMAND_KIND, "key": idempotency_key},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        if row["request_fingerprint"] != request_fingerprint:
            raise ValueError("Product candidate idempotency key has different semantics")
        return cast(dict[str, Any], row["response_document"])
