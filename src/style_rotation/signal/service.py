from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult
from style_rotation.signal.contracts import (
    SignalCatalog,
    SignalDefaults,
    SignalTemplateSeed,
    generated_signal_key,
)


@dataclass(frozen=True, slots=True)
class SignalCatalogPublication:
    release_artifact_id: uuid.UUID
    template_count: int
    definition_count: int
    version_count: int
    product_eligible_count: int
    reused_count: int
    artifact_ids: tuple[uuid.UUID, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["release_artifact_id"] = str(self.release_artifact_id)
        payload["artifact_ids"] = [str(item) for item in self.artifact_ids]
        return payload


def publish_signal_catalog(engine: Engine, catalog_path: Path) -> SignalCatalogPublication:
    raw_catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog = SignalCatalog.model_validate(raw_catalog)
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        catalog_artifact_id = _catalog_artifact(connection, catalog.catalog_version, raw_catalog)
        factor_variants = _factor_variants(connection)
        referenced = {key for template in catalog.templates for key in template.factor_variants}
        unknown = sorted(referenced.difference(factor_variants))
        if unknown:
            raise ValueError(f"Signal catalog references unpublished factor variants: {unknown}")

        results: list[PublicationResult] = []
        for template in catalog.templates:
            for factor_variant_key in template.factor_variants:
                results.extend(
                    _publish_signal(
                        service,
                        connection,
                        template,
                        factor_variant_key,
                        factor_variants[factor_variant_key],
                        catalog.defaults,
                    )
                )
        release = service.publish(
            artifact_type="signal_catalog_materialization",
            artifact_key="signal_catalog",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload={
                "catalog_version": catalog.catalog_version,
                "template_count": len(catalog.templates),
                "definition_count": len(results) // 2,
                "version_count": len(results) // 2,
                "product_eligible_count": sum(
                    len(item.factor_variants) for item in catalog.templates if item.product_eligible
                ),
            },
            content_payload={"member_artifact_ids": [str(item.artifact_id) for item in results]},
            dependencies=(
                DependencyInput(catalog_artifact_id, "source_catalog", 0),
                *tuple(
                    DependencyInput(item.artifact_id, "materialized_member", ordinal)
                    for ordinal, item in enumerate(results)
                ),
            ),
            reason=f"materialize signal catalog {catalog.catalog_version}",
        )
    return SignalCatalogPublication(
        release_artifact_id=release.artifact_id,
        template_count=len(catalog.templates),
        definition_count=len(results) // 2,
        version_count=len(results) // 2,
        product_eligible_count=sum(
            len(item.factor_variants) for item in catalog.templates if item.product_eligible
        ),
        reused_count=sum(item.reused for item in results) + int(release.reused),
        artifact_ids=tuple(item.artifact_id for item in results),
    )


def _publish_signal(
    service: ArtifactService,
    connection: Connection,
    template: SignalTemplateSeed,
    factor_variant_key: str,
    factor_variant: tuple[uuid.UUID, uuid.UUID],
    defaults: SignalDefaults,
) -> list[PublicationResult]:
    signal_key = generated_signal_key(template.key, factor_variant_key)
    definition_payload = {
        "signal_key": signal_key,
        "template_key": template.key,
        "economic_family": template.economic_family,
        "rationale_type": template.rationale_type,
        "rationale": template.rationale,
        "research_tier": template.research_tier,
        "product_eligible": template.product_eligible,
    }
    definition = service.publish(
        artifact_type="signal_definition",
        artifact_key=signal_key,
        version_number=1,
        semantic_payload=definition_payload,
        content_payload=definition_payload,
        reason=f"publish signal definition {signal_key}",
        draft_writer=lambda conn, artifact_id: _write_definition(
            conn, artifact_id, definition_payload
        ),
    )
    definition_id = connection.execute(
        text(
            "SELECT signal_definition_id FROM signal.signal_definition "
            "WHERE artifact_id = :artifact_id"
        ),
        {"artifact_id": definition.artifact_id},
    ).scalar_one()
    factor_variant_id, factor_variant_artifact_id = factor_variant
    version_payload = {
        "factor_variant_key": factor_variant_key,
        "direction": template.direction,
        "normalization": (
            defaults.continuous_normalization if template.form == "continuous" else "none"
        ),
        "extreme_policy": "none",
        "missing_policy": defaults.missing_policy,
        "tie_policy": defaults.tie_policy if template.form == "continuous" else "not_applicable",
        "output_type": template.form,
        "rule": template.rule,
        "calculation_frequency": "daily",
        "time_semantics": "known_at_session_close",
        "evaluation_horizon_policy": "explicit_evaluation_target_required",
    }
    version = service.publish(
        artifact_type="signal_version",
        artifact_key=signal_key,
        version_number=1,
        semantic_payload=version_payload,
        content_payload=version_payload,
        dependencies=(
            DependencyInput(definition.artifact_id, "signal_definition", 0),
            DependencyInput(factor_variant_artifact_id, "factor_variant", 1),
        ),
        reason=f"publish signal version {signal_key} v1",
        draft_writer=lambda conn, artifact_id: _write_version(
            conn,
            artifact_id,
            definition_id,
            factor_variant_id,
            version_payload,
        ),
    )
    return [definition, version]


def _factor_variants(connection: Connection) -> dict[str, tuple[uuid.UUID, uuid.UUID]]:
    rows = connection.execute(
        text("SELECT variant_key, factor_variant_id, artifact_id FROM factor.factor_variant")
    ).mappings()
    return {row["variant_key"]: (row["factor_variant_id"], row["artifact_id"]) for row in rows}


def _catalog_artifact(connection: Connection, catalog_version: str, raw_catalog: Any) -> uuid.UUID:
    row = (
        connection.execute(
            text(
                "SELECT artifact_id, content_hash FROM lineage.artifact "
                "WHERE artifact_type = 'research_catalog' AND artifact_key = 'signal_catalog' "
                "AND version_number = :version AND status = 'published'"
            ),
            {"version": semantic_version_number(catalog_version)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Publish the matching M0 signal research catalog before materialization")
    version_number = semantic_version_number(catalog_version)
    dependency_rows = (
        connection.execute(
            text(
                "SELECT dependency.role, dependency.ordinal, "
                "upstream.semantic_fingerprint, upstream.content_hash "
                "FROM lineage.artifact_dependency dependency "
                "JOIN lineage.artifact upstream ON "
                "upstream.artifact_id = dependency.depends_on_artifact_id "
                "WHERE dependency.artifact_id = :artifact_id "
                "ORDER BY dependency.ordinal NULLS FIRST, dependency.role"
            ),
            {"artifact_id": row["artifact_id"]},
        )
        .mappings()
        .all()
    )
    semantic_fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": "research_catalog",
                "artifact_key": "signal_catalog",
                "version_number": version_number,
            },
            "semantic_payload": raw_catalog,
            "dependencies": [
                {
                    "role": item["role"],
                    "ordinal": item["ordinal"],
                    "semantic_fingerprint": item["semantic_fingerprint"],
                }
                for item in dependency_rows
            ],
        }
    )
    expected_content_hash = sha256_hexdigest(
        {
            "semantic_fingerprint": semantic_fingerprint,
            "content_payload": raw_catalog,
            "dependencies": [
                {
                    "role": item["role"],
                    "ordinal": item["ordinal"],
                    "content_hash": item["content_hash"],
                }
                for item in dependency_rows
            ],
        }
    )
    if row["content_hash"] != expected_content_hash:
        raise ValueError("Local signal catalog does not match the published M0 artifact")
    artifact_id = row["artifact_id"]
    if not isinstance(artifact_id, uuid.UUID):
        raise RuntimeError("Signal catalog artifact id must be a UUID")
    return artifact_id


def _write_definition(
    connection: Connection, artifact_id: uuid.UUID, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            "INSERT INTO signal.signal_definition "
            "(signal_definition_id, artifact_id, signal_key, template_key, economic_family, "
            "rationale_type, rationale, research_tier, product_eligible) VALUES "
            "(:id, :artifact, :signal_key, :template_key, :economic_family, :rationale_type, "
            ":rationale, :research_tier, :product_eligible)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, **payload},
    )


def _write_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    factor_variant_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO signal.signal_version "
            "(signal_version_id, signal_definition_id, factor_variant_id, artifact_id, "
            "version_number, direction, normalization, extreme_policy, missing_policy, "
            "tie_policy, output_type, rule, calculation_frequency, time_semantics, "
            "evaluation_horizon_policy) VALUES "
            "(:id, :definition, :factor_variant, :artifact, 1, :direction, :normalization, "
            ":extreme_policy, :missing_policy, :tie_policy, :output_type, CAST(:rule AS jsonb), "
            ":calculation_frequency, :time_semantics, :evaluation_horizon_policy)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "factor_variant": factor_variant_id,
            "artifact": artifact_id,
            **payload,
            "rule": (
                json.dumps(payload["rule"], sort_keys=True) if payload["rule"] is not None else None
            ),
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
