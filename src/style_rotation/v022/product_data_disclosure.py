from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

ProductEligibility = Literal["eligible", "eligible_with_warnings", "ineligible"]


@dataclass(frozen=True, slots=True)
class ProductDataSource:
    evidence: RowMapping
    cohort: RowMapping
    runtime: RowMapping
    gate: RowMapping
    warning_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductDataDisclosurePublication:
    product_data_disclosure_id: uuid.UUID
    artifact_id: uuid.UUID
    disclosure_fingerprint: str
    product_eligibility: str
    warning_codes: tuple[str, ...]
    reused: bool


class ProductDataDisclosureService:
    """Freeze the exact free-data Gate and future-input policy for one Product."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def source(self, result_evidence_snapshot_id: uuid.UUID) -> ProductDataSource:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        """
                        SELECT evidence.*,evidence_artifact.status AS evidence_status,
                               evidence_artifact.artifact_id AS evidence_artifact_id,
                               cohort.cohort_key,cohort.version_number AS cohort_version_number,
                               cohort.frequency,cohort.warmup_start,cohort.evaluation_start,
                               cohort.evaluation_end,cohort.dataset_publication_id,
                               cohort.universe_history_id,cohort.calendar_version_id,
                               cohort.artifact_id AS cohort_artifact_id,
                               cohort_artifact.status AS cohort_status,
                               runtime.evaluation_cohort_runtime_contract_id,
                               runtime.artifact_id AS runtime_artifact_id,
                               runtime.runtime_fingerprint,runtime.ranking_eligibility,
                               runtime.product_eligibility,runtime.runtime_document,
                               runtime_artifact.status AS runtime_status,
                               gate.dataset_gate_assessment_id,
                               gate.artifact_id AS dataset_gate_artifact_id,
                               gate.assessment_fingerprint,gate.assessment_document,
                               gate.price_semantics,gate.warning_count,gate.blocker_count,
                               gate.uniform_exclusion_count,gate.gap_resolution_count,
                               gate.alternate_observation_count,
                               gate_artifact.status AS gate_status
                          FROM experiment.v022_result_evidence_snapshot evidence
                          JOIN lineage.artifact evidence_artifact
                            ON evidence_artifact.artifact_id=evidence.artifact_id
                          JOIN experiment.v022_evaluation_cohort_version cohort
                            ON cohort.evaluation_cohort_version_id=
                               evidence.evaluation_cohort_version_id
                          JOIN lineage.artifact cohort_artifact
                            ON cohort_artifact.artifact_id=cohort.artifact_id
                          JOIN experiment.v022_evaluation_cohort_runtime_contract runtime
                            ON runtime.evaluation_cohort_version_id=
                               cohort.evaluation_cohort_version_id
                          JOIN lineage.artifact runtime_artifact
                            ON runtime_artifact.artifact_id=runtime.artifact_id
                          JOIN data.v022_dataset_gate_assessment gate
                            ON gate.dataset_gate_assessment_id=
                               runtime.dataset_gate_assessment_id
                          JOIN lineage.artifact gate_artifact
                            ON gate_artifact.artifact_id=gate.artifact_id
                         WHERE evidence.result_evidence_snapshot_id=:evidence
                        """
                    ),
                    {"evidence": result_evidence_snapshot_id},
                )
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise LookupError(
                    f"Published Result Evidence with Cohort runtime Gate not found: "
                    f"{result_evidence_snapshot_id}"
                )
            if any(
                row[key] != "published"
                for key in ("evidence_status", "cohort_status", "runtime_status", "gate_status")
            ):
                raise ValueError("Product data inputs must all be published")
            quality = cast(dict[str, Any], row["quality_document"])
            if quality.get("state") != "passed" or quality.get("outcome") != "accepted":
                raise ValueError("Product promotion requires accepted, passed Result Evidence")
            product_eligibility = cast(str, row["product_eligibility"])
            if product_eligibility == "ineligible":
                raise ValueError("Dataset Gate makes this Result ineligible for Product")
            findings = tuple(
                connection.execute(
                    text(
                        """
                        SELECT finding_code FROM data.v022_dataset_gate_finding
                         WHERE dataset_gate_assessment_id=:gate
                           AND product_effect='warning'
                         ORDER BY ordinal
                        """
                    ),
                    {"gate": row["dataset_gate_assessment_id"]},
                ).scalars()
            )
        warnings = _warning_codes(row, findings)
        return ProductDataSource(row, row, row, row, warnings)

    def publish(
        self,
        *,
        source: ProductDataSource,
        execution_version_id: uuid.UUID,
        qualification_version_id: uuid.UUID,
        created_by: str,
    ) -> ProductDataDisclosurePublication:
        with self._engine.connect() as connection:
            identities = (
                connection.execute(
                    text(
                        """
                        SELECT execution.artifact_id AS execution_artifact_id,
                               execution.version_number,
                               execution.promotion_result_evidence_snapshot_id,
                               qualification.artifact_id AS qualification_artifact_id,
                               qualification.result_evidence_snapshot_id
                          FROM product.v022_execution_version execution
                          JOIN lineage.artifact execution_artifact
                            ON execution_artifact.artifact_id=execution.artifact_id
                           AND execution_artifact.status='published'
                          JOIN product.v022_qualification_version qualification
                            ON qualification.qualification_version_id=:qualification
                           AND qualification.execution_version_id=execution.execution_version_id
                          JOIN lineage.artifact qualification_artifact
                            ON qualification_artifact.artifact_id=qualification.artifact_id
                           AND qualification_artifact.status='published'
                         WHERE execution.execution_version_id=:execution
                        """
                    ),
                    {"execution": execution_version_id, "qualification": qualification_version_id},
                )
                .mappings()
                .one_or_none()
            )
        if identities is None:
            raise ValueError("Product disclosure requires exact published Execution/Qualification")
        evidence_id = cast(uuid.UUID, source.evidence["result_evidence_snapshot_id"])
        if (
            identities["promotion_result_evidence_snapshot_id"] != evidence_id
            or identities["result_evidence_snapshot_id"] != evidence_id
        ):
            raise ValueError("Product disclosure Result Evidence drift")
        document: dict[str, object] = {
            "contract_version": "v0.22.product_data_disclosure.v1",
            "execution_version_id": str(execution_version_id),
            "qualification_version_id": str(qualification_version_id),
            "result_evidence_snapshot_id": str(evidence_id),
            "evaluation_cohort_version_id": str(
                source.cohort["evaluation_cohort_version_id"]
            ),
            "evaluation_cohort_runtime_contract_id": str(
                source.runtime["evaluation_cohort_runtime_contract_id"]
            ),
            "dataset_gate_assessment_id": str(source.gate["dataset_gate_assessment_id"]),
            "dataset_gate_fingerprint": str(source.gate["assessment_fingerprint"]),
            "ranking_eligibility": str(source.runtime["ranking_eligibility"]),
            "product_eligibility": str(source.runtime["product_eligibility"]),
            "product_class": "research_product",
            "historical_membership_semantics": "historical_constituent_pit",
            "price_semantics": str(source.gate["price_semantics"]),
            "provider_native_pit_claimed": False,
            "warning_codes": list(source.warning_codes),
            "counts": {
                "gate_warnings": int(source.gate["warning_count"]),
                "uniform_exclusions": int(source.gate["uniform_exclusion_count"]),
                "gap_resolutions": int(source.gate["gap_resolution_count"]),
                "alternate_observations": int(source.gate["alternate_observation_count"]),
            },
            "frozen_inputs": {
                "dataset_publication_id": str(source.cohort["dataset_publication_id"]),
                "universe_history_id": str(source.cohort["universe_history_id"]),
                "calendar_version_id": str(source.cohort["calendar_version_id"]),
                "cohort_runtime_fingerprint": str(source.runtime["runtime_fingerprint"]),
            },
            "future_input_policy": {
                "policy_key": "published_exact_product_inputs_v1",
                "require_published_dataset_universe_manifest": True,
                "require_gate_assessment": True,
                "stop_on_new_product_ineligible_blocker": True,
                "preserve_prior_decisions_and_evidence": True,
                "runtime_network_access": False,
            },
        }
        fingerprint = sha256_hexdigest(document)
        disclosure_id = uuid.uuid5(
            uuid.NAMESPACE_URL, f"bird:v0.22:product-data-disclosure:{fingerprint}"
        )
        dependencies = (
            DependencyInput(identities["execution_artifact_id"], "execution_version", 0),
            DependencyInput(identities["qualification_artifact_id"], "qualification", 1),
            DependencyInput(source.evidence["evidence_artifact_id"], "result_evidence", 2),
            DependencyInput(source.runtime["runtime_artifact_id"], "cohort_runtime", 3),
            DependencyInput(source.gate["dataset_gate_artifact_id"], "dataset_gate", 4),
        )

        def writer(connection: Connection, artifact_id: uuid.UUID) -> None:
            connection.execute(
                text(
                    """
                    INSERT INTO product.v022_product_data_disclosure (
                      product_data_disclosure_id,artifact_id,execution_version_id,
                      qualification_version_id,result_evidence_snapshot_id,
                      evaluation_cohort_version_id,evaluation_cohort_runtime_contract_id,
                      dataset_gate_assessment_id,dataset_gate_artifact_id,
                      dataset_gate_fingerprint,ranking_eligibility,product_eligibility,
                      warning_codes,disclosure_document,disclosure_fingerprint,created_by
                    ) VALUES (
                      :id,:artifact,:execution,:qualification,:evidence,:cohort,:runtime,
                      :gate,:gate_artifact,:gate_fingerprint,:ranking,:product,
                      CAST(:warnings AS jsonb),CAST(:document AS jsonb),:fingerprint,:created_by
                    )
                    """
                ),
                {
                    "id": disclosure_id,
                    "artifact": artifact_id,
                    "execution": execution_version_id,
                    "qualification": qualification_version_id,
                    "evidence": evidence_id,
                    "cohort": source.cohort["evaluation_cohort_version_id"],
                    "runtime": source.runtime["evaluation_cohort_runtime_contract_id"],
                    "gate": source.gate["dataset_gate_assessment_id"],
                    "gate_artifact": source.gate["dataset_gate_artifact_id"],
                    "gate_fingerprint": source.gate["assessment_fingerprint"],
                    "ranking": source.runtime["ranking_eligibility"],
                    "product": source.runtime["product_eligibility"],
                    "warnings": json.dumps(source.warning_codes),
                    "document": json.dumps(document, sort_keys=True),
                    "fingerprint": fingerprint,
                    "created_by": created_by,
                },
            )

        publication = self._artifacts.publish(
            artifact_type="v022_product_data_disclosure",
            artifact_key=f"v022_product_data_disclosure__{execution_version_id}",
            version_number=int(identities["version_number"]),
            semantic_payload=document,
            content_payload=document,
            dependencies=dependencies,
            reason=f"publish Product data disclosure {execution_version_id}",
            draft_writer=writer,
        )
        return ProductDataDisclosurePublication(
            disclosure_id,
            publication.artifact_id,
            fingerprint,
            cast(str, source.runtime["product_eligibility"]),
            source.warning_codes,
            publication.reused,
        )


def _warning_codes(row: RowMapping, findings: tuple[str, ...]) -> tuple[str, ...]:
    values = ["free_data_research_product", "historical_membership_retrospective"]
    if "retrospective" in str(row["price_semantics"]):
        values.append("retrospective_price_snapshot")
    if int(row["uniform_exclusion_count"]):
        values.append("uniform_provider_exclusions_present")
    if int(row["gap_resolution_count"]):
        values.append("manual_gap_resolutions_present")
    if int(row["alternate_observation_count"]):
        values.append("alternate_source_observations_present")
    values.extend(findings)
    return tuple(dict.fromkeys(values))
