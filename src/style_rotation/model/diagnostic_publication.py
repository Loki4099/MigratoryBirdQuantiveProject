# ruff: noqa: E501 -- SQL clauses are kept as contiguous auditable statements.

from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, bindparam, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.model.diagnostics import (
    ModelDiagnostics,
    ModelEvaluationDataset,
    ModelEvaluationValue,
    calculate_model_diagnostics,
)
from style_rotation.signal.diagnostics import EvaluationReturn


@dataclass(frozen=True, slots=True)
class ModelEvaluationPublication:
    model_evaluation_id: uuid.UUID
    artifact_id: uuid.UUID
    frequency: str
    model_count: int
    period_count: int
    pair_count: int
    ablation_count: int
    issue_count: int
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["model_evaluation_id"] = str(self.model_evaluation_id)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _Context:
    model_catalog_artifact_id: uuid.UUID
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    data_bundle_version_id: uuid.UUID
    data_bundle_artifact_id: uuid.UUID
    eligibility_snapshot_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    model_engine_version_id: uuid.UUID
    model_engine_artifact_id: uuid.UUID
    evaluation_engine_version_id: uuid.UUID
    evaluation_engine_artifact_id: uuid.UUID
    forward_return_dataset_id: uuid.UUID
    forward_return_artifact_id: uuid.UUID
    frequency: str
    candidate_asset_ids: frozenset[uuid.UUID]
    datasets: tuple[ModelEvaluationDataset, ...]
    returns: tuple[EvaluationReturn, ...]


class ModelDiagnosticPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        model_catalog_artifact_id: uuid.UUID,
        forward_return_artifact_id: uuid.UUID,
        model_engine_artifact_id: uuid.UUID,
        evaluation_engine_artifact_id: uuid.UUID,
        *,
        high_correlation_threshold: float = 0.85,
    ) -> ModelEvaluationPublication:
        if high_correlation_threshold != 0.85:
            raise ValueError("Model evaluation engine v1 fixes correlation threshold at 0.85")
        context = self._load_context(
            model_catalog_artifact_id,
            forward_return_artifact_id,
            model_engine_artifact_id,
            evaluation_engine_artifact_id,
        )
        diagnostics = calculate_model_diagnostics(
            context.datasets,
            context.returns,
            context.candidate_asset_ids,
            frequency=context.frequency,
            high_correlation_threshold=high_correlation_threshold,
        )
        coverage_start = min(item.decision_date for item in diagnostics.periods)
        coverage_end = max(item.decision_date for item in diagnostics.periods)
        semantic = {
            "model_catalog_artifact_id": context.model_catalog_artifact_id,
            "universe_artifact_id": context.universe_artifact_id,
            "data_bundle_artifact_id": context.data_bundle_artifact_id,
            "eligibility_artifact_id": context.eligibility_artifact_id,
            "model_engine_artifact_id": context.model_engine_artifact_id,
            "evaluation_engine_artifact_id": context.evaluation_engine_artifact_id,
            "forward_return_artifact_id": context.forward_return_artifact_id,
            "model_dataset_artifact_ids": [item.artifact_id for item in context.datasets],
            "frequency": context.frequency,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "high_correlation_threshold": high_correlation_threshold,
        }
        dependencies = (
            DependencyInput(context.model_catalog_artifact_id, "model_catalog", 0),
            DependencyInput(context.universe_artifact_id, "universe_version", 1),
            DependencyInput(context.data_bundle_artifact_id, "data_bundle", 2),
            DependencyInput(context.eligibility_artifact_id, "eligibility", 3),
            DependencyInput(context.model_engine_artifact_id, "model_engine", 4),
            DependencyInput(context.evaluation_engine_artifact_id, "evaluation_engine", 5),
            DependencyInput(context.forward_return_artifact_id, "forward_return_dataset", 6),
            *tuple(
                DependencyInput(item.artifact_id, "model_dataset", index + 7)
                for index, item in enumerate(context.datasets)
            ),
        )
        with self._engine.begin() as connection:
            result = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="model_evaluation",
                artifact_key=f"model_evaluation:{sha256_hexdigest(semantic)[:16]}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={
                    **semantic,
                    "periods": [asdict(item) for item in diagnostics.periods],
                    "metrics": [asdict(item) for item in diagnostics.metrics],
                    "pairs": [asdict(item) for item in diagnostics.pairs],
                    "ablations": [asdict(item) for item in diagnostics.ablations],
                    "issues": [asdict(item) for item in diagnostics.issues],
                },
                dependencies=dependencies,
                reason=f"publish {context.frequency} Model evaluation",
                draft_writer=partial(
                    _write_evaluation,
                    context=context,
                    diagnostics=diagnostics,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    threshold=high_correlation_threshold,
                ),
            )
            evaluation_id = connection.execute(
                text(
                    "SELECT model_evaluation_id FROM model.model_evaluation WHERE artifact_id = :id"
                ),
                {"id": result.artifact_id},
            ).scalar_one()
        if not isinstance(evaluation_id, uuid.UUID):
            raise RuntimeError("Model evaluation id must be a UUID")
        return ModelEvaluationPublication(
            evaluation_id,
            result.artifact_id,
            context.frequency,
            len(context.datasets),
            len(diagnostics.periods),
            len(diagnostics.pairs),
            len(diagnostics.ablations),
            len(diagnostics.issues),
            result.reused,
        )

    def _load_context(
        self,
        model_catalog_artifact_id: uuid.UUID,
        forward_return_artifact_id: uuid.UUID,
        model_engine_artifact_id: uuid.UUID,
        evaluation_engine_artifact_id: uuid.UUID,
    ) -> _Context:
        with self._engine.connect() as connection:
            _published_artifact(
                connection, model_catalog_artifact_id, "model_catalog_materialization"
            )
            target = _target(connection, forward_return_artifact_id)
            model_engine = _engine(connection, model_engine_artifact_id, "model_engine")
            evaluation_engine = _engine(
                connection, evaluation_engine_artifact_id, "model_evaluation_engine"
            )
            specifications = (
                connection.execute(
                    text(
                        "SELECT specification.model_specification_id, specification.specification_key, "
                        "specification.specification_type FROM lineage.artifact_dependency dependency "
                        "JOIN model.model_specification specification ON specification.artifact_id = "
                        "dependency.depends_on_artifact_id JOIN lineage.artifact artifact ON "
                        "artifact.artifact_id = specification.artifact_id WHERE dependency.artifact_id = :catalog "
                        "AND dependency.role = 'materialized_member' AND artifact.status = 'published' "
                        "ORDER BY specification.specification_key"
                    ),
                    {"catalog": model_catalog_artifact_id},
                )
                .mappings()
                .all()
            )
            if not specifications:
                raise ValueError("Model catalog contains no published specifications")
            spec_ids = tuple(item["model_specification_id"] for item in specifications)
            dataset_rows = (
                connection.execute(
                    text(
                        "SELECT dataset.* FROM model.model_dataset dataset JOIN lineage.artifact artifact ON "
                        "artifact.artifact_id = dataset.artifact_id WHERE dataset.model_specification_id IN :specs "
                        "AND dataset.universe_version_id = :universe AND dataset.data_bundle_version_id = :bundle "
                        "AND dataset.engine_version_id = :engine AND artifact.status = 'published'"
                    ).bindparams(bindparam("specs", expanding=True)),
                    {
                        "specs": spec_ids,
                        "universe": target["universe_version_id"],
                        "bundle": target["data_bundle_version_id"],
                        "engine": model_engine["engine_version_id"],
                    },
                )
                .mappings()
                .all()
            )
            by_spec = {item["model_specification_id"]: item for item in dataset_rows}
            if set(by_spec) != set(spec_ids):
                raise ValueError("Formal Model datasets are incomplete for this target context")
            eligibility_ids = {item["eligibility_snapshot_id"] for item in dataset_rows}
            if len(eligibility_ids) != 1:
                raise ValueError("Model datasets do not share one eligibility context")
            eligibility_id = next(iter(eligibility_ids))
            dimensions = (
                connection.execute(
                    text(
                        "SELECT model_specification_id, dimension_key FROM model.model_dimension "
                        "WHERE model_specification_id IN :specs ORDER BY model_specification_id, ordinal"
                    ).bindparams(bindparam("specs", expanding=True)),
                    {"specs": spec_ids},
                )
                .mappings()
                .all()
            )
            dimensions_by_spec: dict[uuid.UUID, list[str]] = {item: [] for item in spec_ids}
            for item in dimensions:
                dimensions_by_spec[item["model_specification_id"]].append(item["dimension_key"])
            candidates = (
                connection.execute(
                    text(
                        "SELECT member.asset_id, asset.asset_key FROM catalog.universe_member member "
                        "JOIN catalog.asset asset ON asset.asset_id = member.asset_id WHERE "
                        "member.universe_version_id = :universe AND member.role = 'candidate' ORDER BY asset.asset_key"
                    ),
                    {"universe": target["universe_version_id"]},
                )
                .mappings()
                .all()
            )
            if len(candidates) != 4:
                raise ValueError("Model evaluation requires exactly four candidate assets")
            candidate_ids = frozenset(item["asset_id"] for item in candidates)
            asset_keys = {item["asset_id"]: item["asset_key"] for item in candidates}
            dataset_ids = tuple(item["model_dataset_id"] for item in dataset_rows)
            value_rows = (
                connection.execute(
                    text(
                        "SELECT model_dataset_id, asset_id, observation_date, score, direction, confidence "
                        "FROM model.model_value WHERE model_dataset_id IN :datasets AND asset_id IN :assets "
                        "ORDER BY model_dataset_id, observation_date, asset_id"
                    ).bindparams(
                        bindparam("datasets", expanding=True), bindparam("assets", expanding=True)
                    ),
                    {"datasets": dataset_ids, "assets": tuple(candidate_ids)},
                )
                .mappings()
                .all()
            )
            return_rows = (
                connection.execute(
                    text(
                        "SELECT asset_id, decision_date, forward_return FROM data.forward_return_value "
                        "WHERE forward_return_dataset_id = :dataset AND asset_id IN :assets "
                        "ORDER BY decision_date, asset_id"
                    ).bindparams(bindparam("assets", expanding=True)),
                    {
                        "dataset": target["forward_return_dataset_id"],
                        "assets": tuple(candidate_ids),
                    },
                )
                .mappings()
                .all()
            )
            universe_artifact = connection.execute(
                text(
                    "SELECT artifact_id FROM catalog.universe_version WHERE universe_version_id = :id"
                ),
                {"id": target["universe_version_id"]},
            ).scalar_one()
            bundle_artifact = connection.execute(
                text(
                    "SELECT artifact_id FROM data.data_bundle_version WHERE data_bundle_version_id = :id"
                ),
                {"id": target["data_bundle_version_id"]},
            ).scalar_one()
            eligibility_artifact = connection.execute(
                text(
                    "SELECT artifact_id FROM catalog.eligibility_snapshot WHERE eligibility_snapshot_id = :id"
                ),
                {"id": eligibility_id},
            ).scalar_one()
        values: dict[uuid.UUID, list[ModelEvaluationValue]] = {item: [] for item in dataset_ids}
        for row in value_rows:
            direction = {"negative": -1, "neutral": 0, "positive": 1}[row["direction"]]
            values[row["model_dataset_id"]].append(
                ModelEvaluationValue(
                    row["asset_id"],
                    asset_keys[row["asset_id"]],
                    row["observation_date"],
                    float(row["score"]),
                    direction,
                    float(row["confidence"]),
                )
            )
        datasets = tuple(
            ModelEvaluationDataset(
                by_spec[spec["model_specification_id"]]["model_dataset_id"],
                by_spec[spec["model_specification_id"]]["artifact_id"],
                spec["specification_key"],
                spec["specification_type"],
                tuple(dimensions_by_spec[spec["model_specification_id"]]),
                tuple(values[by_spec[spec["model_specification_id"]]["model_dataset_id"]]),
            )
            for spec in specifications
        )
        returns = tuple(
            EvaluationReturn(
                row["asset_id"],
                asset_keys[row["asset_id"]],
                row["decision_date"],
                float(row["forward_return"]),
            )
            for row in return_rows
        )
        return _Context(
            model_catalog_artifact_id,
            target["universe_version_id"],
            _uuid(universe_artifact),
            target["data_bundle_version_id"],
            _uuid(bundle_artifact),
            eligibility_id,
            _uuid(eligibility_artifact),
            model_engine["engine_version_id"],
            model_engine_artifact_id,
            evaluation_engine["engine_version_id"],
            evaluation_engine_artifact_id,
            target["forward_return_dataset_id"],
            forward_return_artifact_id,
            target["frequency"],
            candidate_ids,
            datasets,
            returns,
        )


def _write_evaluation(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _Context,
    diagnostics: ModelDiagnostics,
    coverage_start: Any,
    coverage_end: Any,
    threshold: float,
) -> None:
    evaluation_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO model.model_evaluation (model_evaluation_id, artifact_id, model_catalog_artifact_id, universe_version_id, data_bundle_version_id, eligibility_snapshot_id, model_engine_version_id, evaluation_engine_version_id, forward_return_dataset_id, frequency, coverage_start, coverage_end, model_count, period_count, pair_count, ablation_count, high_correlation_threshold) VALUES (:id,:artifact,:catalog,:universe,:bundle,:eligibility,:model_engine,:evaluation_engine,:target,:frequency,:start,:end,:models,:periods,:pairs,:ablations,:threshold)"
        ),
        {
            "id": evaluation_id,
            "artifact": artifact_id,
            "catalog": context.model_catalog_artifact_id,
            "universe": context.universe_version_id,
            "bundle": context.data_bundle_version_id,
            "eligibility": context.eligibility_snapshot_id,
            "model_engine": context.model_engine_version_id,
            "evaluation_engine": context.evaluation_engine_version_id,
            "target": context.forward_return_dataset_id,
            "frequency": context.frequency,
            "start": coverage_start,
            "end": coverage_end,
            "models": len(context.datasets),
            "periods": len(diagnostics.periods),
            "pairs": len(diagnostics.pairs),
            "ablations": len(diagnostics.ablations),
            "threshold": threshold,
        },
    )
    connection.execute(
        text(
            "INSERT INTO model.model_evaluation_period (model_evaluation_id, model_dataset_id, decision_date, rank_ic, rank_ic_reason, top_bottom_spread, active_count, score_dispersion, mean_confidence) VALUES (:evaluation,:dataset,:date,:ic,:reason,:spread,:active,:dispersion,:confidence)"
        ),
        [
            {
                "evaluation": evaluation_id,
                "dataset": item.model_dataset_id,
                "date": item.decision_date,
                "ic": item.rank_ic,
                "reason": item.rank_ic_reason,
                "spread": item.top_bottom_spread,
                "active": item.active_count,
                "dispersion": item.score_dispersion,
                "confidence": item.mean_confidence,
            }
            for item in diagnostics.periods
        ],
    )
    connection.execute(
        text(
            "INSERT INTO model.model_evaluation_metric (model_evaluation_id, model_dataset_id, window_key, window_start, window_end, period_count, valid_ic_count, undefined_ic_count, mean_rank_ic, median_rank_ic, positive_ic_ratio, information_ratio, mean_top_bottom_spread, non_neutral_rate, mean_top2_turnover, mean_score_dispersion, mean_confidence) VALUES (:evaluation,:dataset,:window,:start,:end,:periods,:valid,:undefined,:mean_ic,:median_ic,:positive,:ir,:spread,:non_neutral,:turnover,:dispersion,:confidence)"
        ),
        [
            {
                "evaluation": evaluation_id,
                "dataset": item.model_dataset_id,
                "window": item.window_key,
                "start": item.window_start,
                "end": item.window_end,
                "periods": item.period_count,
                "valid": item.valid_ic_count,
                "undefined": item.undefined_ic_count,
                "mean_ic": item.mean_rank_ic,
                "median_ic": item.median_rank_ic,
                "positive": item.positive_ic_ratio,
                "ir": item.information_ratio,
                "spread": item.mean_top_bottom_spread,
                "non_neutral": item.non_neutral_rate,
                "turnover": item.mean_top2_turnover,
                "dispersion": item.mean_score_dispersion,
                "confidence": item.mean_confidence,
            }
            for item in diagnostics.metrics
        ],
    )
    if diagnostics.pairs:
        connection.execute(
            text(
                "INSERT INTO model.model_pair_diagnostic (model_evaluation_id, left_model_dataset_id, right_model_dataset_id, score_observation_count, score_spearman, spread_period_count, spread_correlation, mean_top2_overlap, high_correlation) VALUES (:evaluation,:left,:right,:score_count,:score_corr,:spread_count,:spread_corr,:overlap,:high)"
            ),
            [
                {
                    "evaluation": evaluation_id,
                    "left": item.left_model_dataset_id,
                    "right": item.right_model_dataset_id,
                    "score_count": item.score_observation_count,
                    "score_corr": item.score_spearman,
                    "spread_count": item.spread_period_count,
                    "spread_corr": item.spread_correlation,
                    "overlap": item.mean_top2_overlap,
                    "high": item.high_correlation,
                }
                for item in diagnostics.pairs
            ],
        )
    if diagnostics.ablations:
        connection.execute(
            text(
                "INSERT INTO model.model_ablation_comparison (model_evaluation_id, full_model_dataset_id, ablated_model_dataset_id, removed_dimension_key, window_key, period_count, delta_mean_rank_ic, delta_information_ratio, delta_mean_top_bottom_spread) VALUES (:evaluation,:full,:ablated,:removed,:window,:periods,:delta_ic,:delta_ir,:delta_spread)"
            ),
            [
                {
                    "evaluation": evaluation_id,
                    "full": item.full_model_dataset_id,
                    "ablated": item.ablated_model_dataset_id,
                    "removed": item.removed_dimension_key,
                    "window": item.window_key,
                    "periods": item.period_count,
                    "delta_ic": item.delta_mean_rank_ic,
                    "delta_ir": item.delta_information_ratio,
                    "delta_spread": item.delta_mean_top_bottom_spread,
                }
                for item in diagnostics.ablations
            ],
        )
    if diagnostics.issues:
        connection.execute(
            text(
                "INSERT INTO model.model_diagnostic_issue (model_diagnostic_issue_id, model_evaluation_id, model_dataset_id, severity, issue_code, message, details) VALUES (:id,:evaluation,:dataset,:severity,:code,:message,CAST(:details AS jsonb))"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "evaluation": evaluation_id,
                    "dataset": item.model_dataset_id,
                    "severity": item.severity,
                    "code": item.issue_code,
                    "message": item.message,
                    "details": json.dumps(item.details, sort_keys=True),
                }
                for item in diagnostics.issues
            ],
        )


def _target(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT dataset.*, version.frequency FROM data.forward_return_dataset dataset JOIN data.forward_return_version version ON version.forward_return_version_id = dataset.forward_return_version_id JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id WHERE dataset.artifact_id = :id AND artifact.artifact_type = 'forward_return_dataset' AND artifact.status = 'published'"
            ),
            {"id": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published forward-return dataset not found")
    return row


def _engine(connection: Connection, artifact_id: uuid.UUID, engine_key: str) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id WHERE version.artifact_id = :id AND definition.engine_key = :key AND artifact.status = 'published'"
            ),
            {"id": artifact_id, "key": engine_key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published {engine_key} not found")
    return row


def _published_artifact(connection: Connection, artifact_id: uuid.UUID, artifact_type: str) -> None:
    if (
        connection.execute(
            text(
                "SELECT 1 FROM lineage.artifact WHERE artifact_id = :id AND artifact_type = :type AND status = 'published'"
            ),
            {"id": artifact_id, "type": artifact_type},
        ).scalar_one_or_none()
        is None
    ):
        raise ValueError(f"Published {artifact_type} not found")


def _uuid(value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise RuntimeError("Expected UUID business identity")
    return value


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
