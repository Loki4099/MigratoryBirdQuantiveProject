from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.factor.diagnostics import (
    DiagnosticDataset,
    DiagnosticValue,
    FactorDiagnostics,
    calculate_factor_diagnostics,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class FactorDiagnosticPublication:
    factor_diagnostic_set_id: uuid.UUID
    artifact_id: uuid.UUID
    dataset_count: int
    pair_count: int
    issue_count: int
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["factor_diagnostic_set_id"] = str(self.factor_diagnostic_set_id)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _DiagnosticContext:
    factor_catalog_artifact_id: uuid.UUID
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    data_bundle_version_id: uuid.UUID
    data_bundle_artifact_id: uuid.UUID
    eligibility_snapshot_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    factor_engine_version_id: uuid.UUID
    factor_engine_artifact_id: uuid.UUID
    diagnostic_engine_version_id: uuid.UUID
    diagnostic_engine_artifact_id: uuid.UUID
    coverage_start: date
    coverage_end: date
    asset_count: int
    datasets: tuple[DiagnosticDataset, ...]


class FactorDiagnosticPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        factor_catalog_artifact_id: uuid.UUID,
        data_bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        factor_engine_artifact_id: uuid.UUID,
        diagnostic_engine_artifact_id: uuid.UUID,
        *,
        high_correlation_threshold: float = 0.85,
    ) -> FactorDiagnosticPublication:
        if high_correlation_threshold != 0.85:
            raise ValueError(
                "Factor diagnostic engine v1 fixes the high-correlation threshold at 0.85"
            )
        context = self._load_context(
            factor_catalog_artifact_id,
            data_bundle_artifact_id,
            eligibility_artifact_id,
            factor_engine_artifact_id,
            diagnostic_engine_artifact_id,
        )
        diagnostics = calculate_factor_diagnostics(
            context.datasets,
            high_correlation_threshold=high_correlation_threshold,
        )
        semantic = _semantic(context, high_correlation_threshold)
        dependencies = (
            DependencyInput(context.factor_catalog_artifact_id, "factor_catalog", 0),
            DependencyInput(context.universe_artifact_id, "universe_version", 1),
            DependencyInput(context.data_bundle_artifact_id, "data_bundle", 2),
            DependencyInput(context.eligibility_artifact_id, "eligibility", 3),
            DependencyInput(context.factor_engine_artifact_id, "factor_engine", 4),
            DependencyInput(context.diagnostic_engine_artifact_id, "diagnostic_engine", 5),
            *tuple(
                DependencyInput(dataset.artifact_id, "factor_dataset", ordinal + 6)
                for ordinal, dataset in enumerate(context.datasets)
            ),
        )
        with self._engine.begin() as connection:
            service = ArtifactService(cast(Engine, _BoundConnection(connection)))
            result = service.publish(
                artifact_type="factor_diagnostic_set",
                artifact_key=f"factor_diagnostics:{sha256_hexdigest(semantic)[:16]}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={
                    **semantic,
                    "summaries": [asdict(item) for item in diagnostics.summaries],
                    "correlations": [asdict(item) for item in diagnostics.correlations],
                    "issues": [asdict(item) for item in diagnostics.issues],
                },
                dependencies=dependencies,
                reason="publish factor-layer diagnostics",
                draft_writer=partial(
                    _write_diagnostics,
                    context=context,
                    diagnostics=diagnostics,
                    high_correlation_threshold=high_correlation_threshold,
                ),
            )
            diagnostic_set_id = connection.execute(
                text(
                    "SELECT factor_diagnostic_set_id FROM factor.factor_diagnostic_set "
                    "WHERE artifact_id = :artifact_id"
                ),
                {"artifact_id": result.artifact_id},
            ).scalar_one()
        if not isinstance(diagnostic_set_id, uuid.UUID):
            raise RuntimeError("Factor diagnostic set id must be a UUID")
        return FactorDiagnosticPublication(
            diagnostic_set_id,
            result.artifact_id,
            len(diagnostics.summaries),
            len(diagnostics.correlations),
            len(diagnostics.issues),
            result.reused,
        )

    def _load_context(
        self,
        factor_catalog_artifact_id: uuid.UUID,
        data_bundle_artifact_id: uuid.UUID,
        eligibility_artifact_id: uuid.UUID,
        factor_engine_artifact_id: uuid.UUID,
        diagnostic_engine_artifact_id: uuid.UUID,
    ) -> _DiagnosticContext:
        with self._engine.connect() as connection:
            _published_artifact(
                connection, factor_catalog_artifact_id, "factor_catalog_materialization"
            )
            bundle = _published_business(
                connection,
                "data.data_bundle_version",
                "data_bundle_version_id",
                data_bundle_artifact_id,
            )
            eligibility = _published_business(
                connection,
                "catalog.eligibility_snapshot",
                "eligibility_snapshot_id",
                eligibility_artifact_id,
            )
            factor_engine = _published_engine(
                connection, factor_engine_artifact_id, "factor_engine"
            )
            diagnostic_engine = _published_engine(
                connection, diagnostic_engine_artifact_id, "factor_diagnostic_engine"
            )
            if eligibility["data_bundle_version_id"] != bundle["data_bundle_version_id"]:
                raise ValueError("Eligibility snapshot does not bind the supplied data bundle")
            variant_rows = (
                connection.execute(
                    text(
                        "SELECT variant.factor_variant_id, variant.variant_key, "
                        "variant.factor_definition_version_id FROM lineage.artifact_dependency "
                        "dependency JOIN lineage.artifact member ON member.artifact_id = "
                        "dependency.depends_on_artifact_id JOIN factor.factor_variant variant ON "
                        "variant.artifact_id = member.artifact_id WHERE dependency.artifact_id = "
                        ":catalog_id AND dependency.role = 'materialized_member' AND "
                        "member.artifact_type = 'factor_variant' AND member.status = 'published' "
                        "ORDER BY variant.variant_key"
                    ),
                    {"catalog_id": factor_catalog_artifact_id},
                )
                .mappings()
                .all()
            )
            if not variant_rows:
                raise ValueError("Factor catalog contains no published variants")
            variant_ids = tuple(row["factor_variant_id"] for row in variant_rows)
            dataset_rows = (
                connection.execute(
                    text(
                        "SELECT dataset.factor_dataset_id, dataset.artifact_id, "
                        "dataset.factor_variant_id, dataset.universe_version_id, "
                        "dataset.coverage_start, dataset.coverage_end, dataset.row_count "
                        "FROM factor.factor_dataset dataset JOIN lineage.artifact artifact ON "
                        "artifact.artifact_id = dataset.artifact_id WHERE "
                        "dataset.factor_variant_id IN :variant_ids AND "
                        "dataset.data_bundle_version_id = :bundle_id AND "
                        "dataset.eligibility_snapshot_id = :eligibility_id AND "
                        "dataset.engine_version_id = :engine_id AND artifact.status = 'published'"
                    ).bindparams(bindparam("variant_ids", expanding=True)),
                    {
                        "variant_ids": variant_ids,
                        "bundle_id": bundle["data_bundle_version_id"],
                        "eligibility_id": eligibility["eligibility_snapshot_id"],
                        "engine_id": factor_engine["engine_version_id"],
                    },
                )
                .mappings()
                .all()
            )
            by_variant = {row["factor_variant_id"]: row for row in dataset_rows}
            if set(by_variant) != set(variant_ids):
                missing = len(set(variant_ids).difference(by_variant))
                raise ValueError(
                    f"Formal factor datasets are incomplete for the catalog: {missing}"
                )
            coverage = {(row["coverage_start"], row["coverage_end"]) for row in dataset_rows}
            universes = {row["universe_version_id"] for row in dataset_rows}
            if len(coverage) != 1 or len(universes) != 1:
                raise ValueError("Factor datasets do not share one diagnostic context")
            universe_version_id = next(iter(universes))
            universe_artifact_id = connection.execute(
                text(
                    "SELECT artifact_id FROM catalog.universe_version "
                    "WHERE universe_version_id = :id"
                ),
                {"id": universe_version_id},
            ).scalar_one()
            dataset_ids = tuple(row["factor_dataset_id"] for row in dataset_rows)
            value_rows = (
                connection.execute(
                    text(
                        "SELECT factor_dataset_id, asset_id, observation_date, value "
                        "FROM factor.factor_value WHERE factor_dataset_id IN :dataset_ids "
                        "ORDER BY factor_dataset_id, asset_id, observation_date"
                    ).bindparams(bindparam("dataset_ids", expanding=True)),
                    {"dataset_ids": dataset_ids},
                )
                .mappings()
                .all()
            )
        values_by_dataset: dict[uuid.UUID, list[DiagnosticValue]] = {
            dataset_id: [] for dataset_id in dataset_ids
        }
        for row in value_rows:
            values_by_dataset[row["factor_dataset_id"]].append(
                DiagnosticValue(row["asset_id"], row["observation_date"], row["value"])
            )
        datasets: list[DiagnosticDataset] = []
        for variant in variant_rows:
            dataset = by_variant[variant["factor_variant_id"]]
            values = tuple(values_by_dataset[dataset["factor_dataset_id"]])
            if len(values) != dataset["row_count"]:
                raise ValueError(f"Factor dataset row count mismatch: {variant['variant_key']}")
            datasets.append(
                DiagnosticDataset(
                    dataset["factor_dataset_id"],
                    dataset["artifact_id"],
                    variant["factor_definition_version_id"],
                    variant["variant_key"],
                    values,
                )
            )
        coverage_start, coverage_end = next(iter(coverage))
        asset_count = len({point.asset_id for point in datasets[0].values})
        return _DiagnosticContext(
            factor_catalog_artifact_id,
            universe_version_id,
            universe_artifact_id,
            bundle["data_bundle_version_id"],
            data_bundle_artifact_id,
            eligibility["eligibility_snapshot_id"],
            eligibility_artifact_id,
            factor_engine["engine_version_id"],
            factor_engine_artifact_id,
            diagnostic_engine["engine_version_id"],
            diagnostic_engine_artifact_id,
            coverage_start,
            coverage_end,
            asset_count,
            tuple(datasets),
        )


def _semantic(context: _DiagnosticContext, threshold: float) -> dict[str, Any]:
    return {
        "factor_catalog_artifact_id": context.factor_catalog_artifact_id,
        "universe_artifact_id": context.universe_artifact_id,
        "data_bundle_artifact_id": context.data_bundle_artifact_id,
        "eligibility_artifact_id": context.eligibility_artifact_id,
        "factor_engine_artifact_id": context.factor_engine_artifact_id,
        "diagnostic_engine_artifact_id": context.diagnostic_engine_artifact_id,
        "factor_dataset_artifact_ids": [item.artifact_id for item in context.datasets],
        "coverage_start": context.coverage_start,
        "coverage_end": context.coverage_end,
        "correlation_method": "pooled_asset_date_spearman_average_ties",
        "high_correlation_threshold": threshold,
    }


def _write_diagnostics(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _DiagnosticContext,
    diagnostics: FactorDiagnostics,
    high_correlation_threshold: float,
) -> None:
    diagnostic_set_id = uuid.uuid4()
    observation_count = sum(item.observation_count for item in diagnostics.summaries)
    connection.execute(
        text(
            "INSERT INTO factor.factor_diagnostic_set (factor_diagnostic_set_id, artifact_id, "
            "factor_catalog_artifact_id, universe_version_id, data_bundle_version_id, "
            "eligibility_snapshot_id, factor_engine_version_id, diagnostic_engine_version_id, "
            "coverage_start, coverage_end, dataset_count, asset_count, observation_count, "
            "pair_count, high_correlation_threshold) VALUES (:id, :artifact_id, :catalog_id, "
            ":universe_id, :bundle_id, :eligibility_id, :factor_engine_id, "
            ":diagnostic_engine_id, :start, :end, :dataset_count, :asset_count, "
            ":observation_count, :pair_count, :threshold)"
        ),
        {
            "id": diagnostic_set_id,
            "artifact_id": artifact_id,
            "catalog_id": context.factor_catalog_artifact_id,
            "universe_id": context.universe_version_id,
            "bundle_id": context.data_bundle_version_id,
            "eligibility_id": context.eligibility_snapshot_id,
            "factor_engine_id": context.factor_engine_version_id,
            "diagnostic_engine_id": context.diagnostic_engine_version_id,
            "start": context.coverage_start,
            "end": context.coverage_end,
            "dataset_count": len(diagnostics.summaries),
            "asset_count": context.asset_count,
            "observation_count": observation_count,
            "pair_count": len(diagnostics.correlations),
            "threshold": high_correlation_threshold,
        },
    )
    connection.execute(
        text(
            "INSERT INTO factor.factor_dataset_summary (factor_dataset_summary_id, "
            "factor_diagnostic_set_id, factor_dataset_id, observation_count, asset_count, "
            "missing_count, mean, standard_deviation, minimum, p05, p25, median, p75, p95, "
            "maximum, zero_variance) VALUES (:id, :set_id, :dataset_id, :count, :asset_count, "
            ":missing, :mean, :standard_deviation, :minimum, :p05, :p25, :median, :p75, :p95, "
            ":maximum, :zero_variance)"
        ),
        [
            {
                "id": uuid.uuid4(),
                "set_id": diagnostic_set_id,
                "dataset_id": item.factor_dataset_id,
                "count": item.observation_count,
                "asset_count": item.asset_count,
                "missing": item.missing_count,
                "mean": item.mean,
                "standard_deviation": item.standard_deviation,
                "minimum": item.minimum,
                "p05": item.p05,
                "p25": item.p25,
                "median": item.median,
                "p75": item.p75,
                "p95": item.p95,
                "maximum": item.maximum,
                "zero_variance": item.zero_variance,
            }
            for item in diagnostics.summaries
        ],
    )
    if diagnostics.correlations:
        connection.execute(
            text(
                "INSERT INTO factor.factor_pair_correlation (factor_pair_correlation_id, "
                "factor_diagnostic_set_id, left_factor_dataset_id, right_factor_dataset_id, "
                "observation_count, spearman_correlation, same_definition, high_correlation) "
                "VALUES (:id, :set_id, :left_id, :right_id, :count, :correlation, "
                ":same_definition, :high_correlation)"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "set_id": diagnostic_set_id,
                    "left_id": item.left_factor_dataset_id,
                    "right_id": item.right_factor_dataset_id,
                    "count": item.observation_count,
                    "correlation": item.spearman_correlation,
                    "same_definition": item.same_definition,
                    "high_correlation": item.high_correlation,
                }
                for item in diagnostics.correlations
            ],
        )
    if diagnostics.issues:
        connection.execute(
            text(
                "INSERT INTO factor.factor_diagnostic_issue (factor_diagnostic_issue_id, "
                "factor_diagnostic_set_id, factor_dataset_id, severity, issue_code, message, "
                "details) VALUES (:id, :set_id, :dataset_id, :severity, :issue_code, :message, "
                "CAST(:details AS jsonb))"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "set_id": diagnostic_set_id,
                    "dataset_id": item.factor_dataset_id,
                    "severity": item.severity,
                    "issue_code": item.issue_code,
                    "message": item.message,
                    "details": json.dumps(item.details, sort_keys=True),
                }
                for item in diagnostics.issues
            ],
        )


def _published_artifact(
    connection: Connection, artifact_id: uuid.UUID, artifact_type: str
) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT * FROM lineage.artifact WHERE artifact_id = :id "
                "AND artifact_type = :type AND status = 'published'"
            ),
            {"id": artifact_id, "type": artifact_type},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published {artifact_type} artifact not found: {artifact_id}")
    return row


def _published_business(
    connection: Connection, table: str, id_column: str, artifact_id: uuid.UUID
) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.* FROM {table} business JOIN lineage.artifact artifact "
                "ON artifact.artifact_id = business.artifact_id WHERE business.artifact_id = :id "
                "AND artifact.status = 'published'"
            ),
            {"id": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row[id_column] is None:
        raise ValueError(f"Published dependency not found: {table}")
    return row


def _published_engine(
    connection: Connection, artifact_id: uuid.UUID, expected_engine_key: str
) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition "
                "definition ON definition.engine_definition_id = version.engine_definition_id "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id "
                "WHERE version.artifact_id = :id AND definition.engine_key = :engine_key "
                "AND artifact.status = 'published'"
            ),
            {"id": artifact_id, "engine_key": expected_engine_key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published {expected_engine_key} not found: {artifact_id}")
    return row


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
