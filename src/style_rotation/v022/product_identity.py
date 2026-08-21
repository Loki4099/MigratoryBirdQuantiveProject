# ruff: noqa: E501
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput

VersionKind = Literal["execution", "qualification", "monitoring_policy"]


@dataclass(frozen=True, slots=True)
class ProductDefinitionPublication:
    product_definition_id: uuid.UUID
    artifact_id: uuid.UUID
    product_key: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ProductVersionPublication:
    version_id: uuid.UUID
    artifact_id: uuid.UUID
    version_kind: VersionKind
    version_number: int
    fingerprint: str
    reused: bool


class ProductIdentityService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish_definition(
        self,
        *,
        product_key: str,
        name: str,
        description: str = "",
    ) -> ProductDefinitionPublication:
        product_key = product_key.strip()
        name = name.strip()
        if not product_key or not name:
            raise ValueError("Product key and name must be nonblank")
        semantic = {
            "contract_version": "v0.22.0",
            "product_key": product_key,
            "name": name,
            "description": description,
        }
        existing = self._definition(product_key)
        if existing is not None:
            ArtifactService(self._engine).publish(
                artifact_type="v022_product_definition",
                artifact_key=product_key,
                version_number=1,
                semantic_payload=semantic,
                content_payload=semantic,
                reason="replay stable v0.22 Product Definition",
            )
            return ProductDefinitionPublication(
                existing["product_definition_id"], existing["artifact_id"], product_key, True
            )
        definition_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:product:{product_key}")
        publication = ArtifactService(self._engine).publish(
            artifact_type="v022_product_definition",
            artifact_key=product_key,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            reason="publish stable v0.22 Product Definition",
            draft_writer=partial(
                self._write_definition,
                definition_id=definition_id,
                product_key=product_key,
                name=name,
                description=description,
            ),
        )
        return ProductDefinitionPublication(
            definition_id, publication.artifact_id, product_key, publication.reused
        )

    def publish_execution_version(
        self,
        *,
        product_definition_id: uuid.UUID,
        version_number: int,
        configuration_snapshot_id: uuid.UUID,
        promotion_result_evidence_snapshot_id: uuid.UUID,
        runtime_policy_document: dict[str, Any],
    ) -> ProductVersionPublication:
        self._validate_version(version_number, runtime_policy_document, "runtime policy")
        with self._engine.connect() as connection:
            definition = _definition_identity(connection, product_definition_id)
            configuration = _configuration_identity(connection, configuration_snapshot_id)
            evidence = _evidence_identity(connection, promotion_result_evidence_snapshot_id)
        if evidence["configuration_snapshot_id"] != configuration_snapshot_id:
            raise ValueError("Execution Version Evidence must bind the exact Configuration")
        semantic: dict[str, Any] = {
            "contract_version": "v0.22.0",
            "product_key": definition["product_key"],
            "version_number": version_number,
            "configuration_fingerprint": configuration["configuration_fingerprint"],
            "promotion_evidence_fingerprint": evidence["evidence_fingerprint"],
            "runtime_policy": runtime_policy_document,
        }
        ensemble_fingerprint = _configuration_ensemble_fingerprint(configuration)
        if ensemble_fingerprint is not None:
            semantic["trainable_ensemble_fingerprint"] = ensemble_fingerprint
        return self._publish_version(
            kind="execution",
            product_definition_id=product_definition_id,
            version_number=version_number,
            semantic=semantic,
            dependencies=(
                DependencyInput(definition["artifact_id"], "product_definition", 0),
                DependencyInput(configuration["artifact_id"], "configuration", 1),
                DependencyInput(evidence["artifact_id"], "promotion_evidence", 2),
            ),
            writer=partial(
                self._write_execution,
                product_definition_id=product_definition_id,
                version_number=version_number,
                configuration_snapshot_id=configuration_snapshot_id,
                evidence_snapshot_id=promotion_result_evidence_snapshot_id,
                runtime_policy_document=runtime_policy_document,
            ),
        )

    def publish_qualification_version(
        self,
        *,
        product_definition_id: uuid.UUID,
        version_number: int,
        execution_version_id: uuid.UUID,
        result_evidence_snapshot_id: uuid.UUID,
        qualification_document: dict[str, Any],
        evidence_artifact_ids: tuple[uuid.UUID, ...] = (),
    ) -> ProductVersionPublication:
        self._validate_version(version_number, qualification_document, "qualification")
        if len(evidence_artifact_ids) != len(set(evidence_artifact_ids)):
            raise ValueError("Qualification evidence Artifact IDs must be unique")
        with self._engine.connect() as connection:
            definition = _definition_identity(connection, product_definition_id)
            execution = _execution_identity(connection, execution_version_id)
            evidence = _evidence_identity(connection, result_evidence_snapshot_id)
            extras = tuple(_published_artifact(connection, item) for item in evidence_artifact_ids)
        if execution["product_definition_id"] != product_definition_id:
            raise ValueError("Qualification Version must belong to the same Product Definition")
        if evidence["configuration_snapshot_id"] != execution["configuration_snapshot_id"]:
            raise ValueError("Qualification Evidence must bind the Execution Configuration")
        semantic = {
            "contract_version": "v0.22.0",
            "product_key": definition["product_key"],
            "version_number": version_number,
            "execution_fingerprint": execution["execution_fingerprint"],
            "result_evidence_fingerprint": evidence["evidence_fingerprint"],
            "qualification": qualification_document,
            "evidence_artifact_fingerprints": [item["semantic_fingerprint"] for item in extras],
        }
        dependencies = [
            DependencyInput(definition["artifact_id"], "product_definition", 0),
            DependencyInput(execution["artifact_id"], "execution_version", 1),
            DependencyInput(evidence["artifact_id"], "result_evidence", 2),
        ]
        dependencies.extend(
            DependencyInput(item["artifact_id"], "qualification_evidence", ordinal + 3)
            for ordinal, item in enumerate(extras)
        )
        return self._publish_version(
            kind="qualification",
            product_definition_id=product_definition_id,
            version_number=version_number,
            semantic=semantic,
            dependencies=tuple(dependencies),
            writer=partial(
                self._write_qualification,
                product_definition_id=product_definition_id,
                version_number=version_number,
                execution_version_id=execution_version_id,
                evidence_snapshot_id=result_evidence_snapshot_id,
                qualification_document=qualification_document,
            ),
        )

    def publish_monitoring_policy_version(
        self,
        *,
        product_definition_id: uuid.UUID,
        version_number: int,
        monitoring_policy_document: dict[str, Any],
    ) -> ProductVersionPublication:
        self._validate_version(version_number, monitoring_policy_document, "monitoring policy")
        with self._engine.connect() as connection:
            definition = _definition_identity(connection, product_definition_id)
        semantic = {
            "contract_version": "v0.22.0",
            "product_key": definition["product_key"],
            "version_number": version_number,
            "monitoring_policy": monitoring_policy_document,
        }
        return self._publish_version(
            kind="monitoring_policy",
            product_definition_id=product_definition_id,
            version_number=version_number,
            semantic=semantic,
            dependencies=(DependencyInput(definition["artifact_id"], "product_definition", 0),),
            writer=partial(
                self._write_monitoring,
                product_definition_id=product_definition_id,
                version_number=version_number,
                monitoring_policy_document=monitoring_policy_document,
            ),
        )

    def _publish_version(
        self,
        *,
        kind: VersionKind,
        product_definition_id: uuid.UUID,
        version_number: int,
        semantic: dict[str, Any],
        dependencies: tuple[DependencyInput, ...],
        writer: Any,
    ) -> ProductVersionPublication:
        fingerprint = sha256_hexdigest(semantic)
        existing = self._version(kind, product_definition_id, version_number)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise ValueError(f"Product {kind} Version is already bound to different semantics")
            return existing
        version_id = uuid.uuid5(uuid.NAMESPACE_URL, f"bird:v0.22:product:{kind}:{fingerprint}")
        publication = ArtifactService(self._engine).publish(
            artifact_type=f"v022_product_{kind}_version",
            artifact_key=fingerprint,
            version_number=1,
            semantic_payload=semantic,
            content_payload=semantic,
            dependencies=dependencies,
            reason=f"publish immutable v0.22 Product {kind} Version",
            draft_writer=partial(writer, version_id=version_id, fingerprint=fingerprint),
        )
        if publication.reused:
            reused = self._version(kind, product_definition_id, version_number)
            if reused is None:
                raise ValueError(f"Reused Product {kind} Artifact has no version row")
            return reused
        return ProductVersionPublication(
            version_id, publication.artifact_id, kind, version_number, fingerprint, False
        )

    def _definition(self, product_key: str) -> RowMapping | None:
        with self._engine.connect() as connection:
            return (
                connection.execute(
                    text("SELECT * FROM product.v022_product_definition WHERE product_key=:key"),
                    {"key": product_key},
                )
                .mappings()
                .one_or_none()
            )

    def _version(
        self, kind: VersionKind, product_definition_id: uuid.UUID, version_number: int
    ) -> ProductVersionPublication | None:
        table, id_column, fingerprint_column = _version_columns(kind)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        f"SELECT version.*,artifact.status FROM product.{table} version "
                        "JOIN lineage.artifact artifact ON artifact.artifact_id=version.artifact_id "
                        "WHERE version.product_definition_id=:product AND version.version_number=:version"
                    ),
                    {"product": product_definition_id, "version": version_number},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            return None
        if row["status"] != "published":
            raise ValueError(f"Product {kind} Version Artifact is not published")
        return ProductVersionPublication(
            row[id_column],
            row["artifact_id"],
            kind,
            version_number,
            row[fingerprint_column],
            True,
        )

    @staticmethod
    def _validate_version(version_number: int, document: dict[str, Any], label: str) -> None:
        if version_number < 1:
            raise ValueError("Product version_number must be positive")
        if not document:
            raise ValueError(f"Product {label} document must be nonempty")

    @staticmethod
    def _write_definition(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        definition_id: uuid.UUID,
        product_key: str,
        name: str,
        description: str,
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_product_definition "
                "(product_definition_id,artifact_id,product_key,name,description) "
                "VALUES (:id,:artifact,:key,:name,:description)"
            ),
            {
                "id": definition_id,
                "artifact": artifact_id,
                "key": product_key,
                "name": name,
                "description": description,
            },
        )

    @staticmethod
    def _write_execution(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        fingerprint: str,
        product_definition_id: uuid.UUID,
        version_number: int,
        configuration_snapshot_id: uuid.UUID,
        evidence_snapshot_id: uuid.UUID,
        runtime_policy_document: dict[str, Any],
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_execution_version "
                "(execution_version_id,product_definition_id,artifact_id,version_number,"
                "configuration_snapshot_id,promotion_result_evidence_snapshot_id,"
                "runtime_policy_document,execution_fingerprint) VALUES "
                "(:id,:product,:artifact,:version,:configuration,:evidence,CAST(:document AS jsonb),:fingerprint)"
            ),
            {
                "id": version_id,
                "product": product_definition_id,
                "artifact": artifact_id,
                "version": version_number,
                "configuration": configuration_snapshot_id,
                "evidence": evidence_snapshot_id,
                "document": json.dumps(runtime_policy_document, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )

    @staticmethod
    def _write_qualification(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        fingerprint: str,
        product_definition_id: uuid.UUID,
        version_number: int,
        execution_version_id: uuid.UUID,
        evidence_snapshot_id: uuid.UUID,
        qualification_document: dict[str, Any],
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_qualification_version "
                "(qualification_version_id,product_definition_id,artifact_id,version_number,"
                "execution_version_id,result_evidence_snapshot_id,qualification_document,"
                "qualification_fingerprint) VALUES "
                "(:id,:product,:artifact,:version,:execution,:evidence,CAST(:document AS jsonb),:fingerprint)"
            ),
            {
                "id": version_id,
                "product": product_definition_id,
                "artifact": artifact_id,
                "version": version_number,
                "execution": execution_version_id,
                "evidence": evidence_snapshot_id,
                "document": json.dumps(qualification_document, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )

    @staticmethod
    def _write_monitoring(
        connection: Connection,
        artifact_id: uuid.UUID,
        *,
        version_id: uuid.UUID,
        fingerprint: str,
        product_definition_id: uuid.UUID,
        version_number: int,
        monitoring_policy_document: dict[str, Any],
    ) -> None:
        connection.execute(
            text(
                "INSERT INTO product.v022_monitoring_policy_version "
                "(monitoring_policy_version_id,product_definition_id,artifact_id,version_number,"
                "monitoring_policy_document,monitoring_policy_fingerprint) VALUES "
                "(:id,:product,:artifact,:version,CAST(:document AS jsonb),:fingerprint)"
            ),
            {
                "id": version_id,
                "product": product_definition_id,
                "artifact": artifact_id,
                "version": version_number,
                "document": json.dumps(monitoring_policy_document, sort_keys=True),
                "fingerprint": fingerprint,
            },
        )


def _version_columns(kind: VersionKind) -> tuple[str, str, str]:
    if kind == "execution":
        return "v022_execution_version", "execution_version_id", "execution_fingerprint"
    if kind == "qualification":
        return "v022_qualification_version", "qualification_version_id", "qualification_fingerprint"
    return (
        "v022_monitoring_policy_version",
        "monitoring_policy_version_id",
        "monitoring_policy_fingerprint",
    )


def _published_artifact(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text("SELECT * FROM lineage.artifact WHERE artifact_id=:artifact"),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["status"] != "published":
        raise ValueError(f"Expected published Artifact: {artifact_id}")
    return row


def _definition_identity(connection: Connection, definition_id: uuid.UUID) -> RowMapping:
    return _published_business_identity(
        connection,
        "product.v022_product_definition",
        "product_definition_id",
        definition_id,
        "Product Definition",
    )


def _configuration_identity(connection: Connection, configuration_id: uuid.UUID) -> RowMapping:
    return _published_business_identity(
        connection,
        "experiment.v022_research_configuration_snapshot",
        "configuration_snapshot_id",
        configuration_id,
        "Research Configuration Snapshot",
    )


def _configuration_ensemble_fingerprint(configuration: RowMapping) -> str | None:
    semantic = cast(dict[str, Any], configuration["semantic_identity_document"])
    aggregation = cast(dict[str, Any], semantic.get("aggregation", {}))
    ensemble = aggregation.get("trainable_ensemble")
    if ensemble is None:
        return None
    if not isinstance(ensemble, dict):
        raise ValueError("Configuration Trainable Ensemble identity must be an object")
    fingerprint = ensemble.get("ensemble_fingerprint")
    specification = ensemble.get("specification")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or not isinstance(specification, dict)
        or fingerprint != sha256_hexdigest(specification)
    ):
        raise ValueError("Configuration Trainable Ensemble identity drifted")
    return fingerprint


def _evidence_identity(connection: Connection, evidence_id: uuid.UUID) -> RowMapping:
    return _published_business_identity(
        connection,
        "experiment.v022_result_evidence_snapshot",
        "result_evidence_snapshot_id",
        evidence_id,
        "Result Evidence Snapshot",
    )


def _execution_identity(connection: Connection, execution_id: uuid.UUID) -> RowMapping:
    return _published_business_identity(
        connection,
        "product.v022_execution_version",
        "execution_version_id",
        execution_id,
        "Product Execution Version",
    )


def _published_business_identity(
    connection: Connection,
    table: str,
    id_column: str,
    identity: uuid.UUID,
    label: str,
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.*,artifact.status FROM {table} business "
                f"JOIN lineage.artifact artifact ON artifact.artifact_id=business.artifact_id "
                f"WHERE business.{id_column}=:identity"
            ),
            {"identity": identity},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"{label} not found: {identity}")
    if row["status"] != "published":
        raise ValueError(f"{label} Artifact is not published")
    return row
