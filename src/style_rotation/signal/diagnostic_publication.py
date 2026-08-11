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
from style_rotation.signal.diagnostics import (
    EvaluationReturn,
    EvaluationSignal,
    EvaluationValue,
    SignalDiagnostics,
    calculate_signal_diagnostics,
)


@dataclass(frozen=True, slots=True)
class SignalEvaluationPublication:
    signal_evaluation_id: uuid.UUID
    artifact_id: uuid.UUID
    frequency: str
    signal_count: int
    period_count: int
    pair_count: int
    issue_count: int
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["signal_evaluation_id"] = str(self.signal_evaluation_id)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _Context:
    signal_catalog_artifact_id: uuid.UUID
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    data_bundle_version_id: uuid.UUID
    data_bundle_artifact_id: uuid.UUID
    eligibility_snapshot_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    signal_engine_version_id: uuid.UUID
    signal_engine_artifact_id: uuid.UUID
    evaluation_engine_version_id: uuid.UUID
    evaluation_engine_artifact_id: uuid.UUID
    forward_return_dataset_id: uuid.UUID
    forward_return_artifact_id: uuid.UUID
    frequency: str
    candidate_asset_ids: frozenset[uuid.UUID]
    signals: tuple[EvaluationSignal, ...]
    returns: tuple[EvaluationReturn, ...]


class SignalDiagnosticPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        signal_catalog_artifact_id: uuid.UUID,
        forward_return_artifact_id: uuid.UUID,
        signal_engine_artifact_id: uuid.UUID,
        evaluation_engine_artifact_id: uuid.UUID,
        *,
        high_correlation_threshold: float = 0.85,
    ) -> SignalEvaluationPublication:
        if high_correlation_threshold != 0.85:
            raise ValueError("Signal evaluation engine v1 fixes correlation threshold at 0.85")
        context = self._load_context(
            signal_catalog_artifact_id,
            forward_return_artifact_id,
            signal_engine_artifact_id,
            evaluation_engine_artifact_id,
        )
        diagnostics = calculate_signal_diagnostics(
            context.signals,
            context.returns,
            context.candidate_asset_ids,
            frequency=context.frequency,
            high_correlation_threshold=high_correlation_threshold,
        )
        coverage_start = min(item.decision_date for item in diagnostics.periods)
        coverage_end = max(item.decision_date for item in diagnostics.periods)
        semantic = {
            "signal_catalog_artifact_id": context.signal_catalog_artifact_id,
            "universe_artifact_id": context.universe_artifact_id,
            "data_bundle_artifact_id": context.data_bundle_artifact_id,
            "eligibility_artifact_id": context.eligibility_artifact_id,
            "signal_engine_artifact_id": context.signal_engine_artifact_id,
            "evaluation_engine_artifact_id": context.evaluation_engine_artifact_id,
            "forward_return_artifact_id": context.forward_return_artifact_id,
            "signal_dataset_artifact_ids": [item.artifact_id for item in context.signals],
            "frequency": context.frequency,
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
            "high_correlation_threshold": high_correlation_threshold,
        }
        dependencies = (
            DependencyInput(context.signal_catalog_artifact_id, "signal_catalog", 0),
            DependencyInput(context.universe_artifact_id, "universe_version", 1),
            DependencyInput(context.data_bundle_artifact_id, "data_bundle", 2),
            DependencyInput(context.eligibility_artifact_id, "eligibility", 3),
            DependencyInput(context.signal_engine_artifact_id, "signal_engine", 4),
            DependencyInput(context.evaluation_engine_artifact_id, "evaluation_engine", 5),
            DependencyInput(context.forward_return_artifact_id, "forward_return_dataset", 6),
            *tuple(
                DependencyInput(item.artifact_id, "signal_dataset", index + 7)
                for index, item in enumerate(context.signals)
            ),
        )
        with self._engine.begin() as connection:
            result = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="signal_evaluation",
                artifact_key=f"signal_evaluation:{sha256_hexdigest(semantic)[:16]}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={
                    **semantic,
                    "periods": [asdict(item) for item in diagnostics.periods],
                    "metrics": [asdict(item) for item in diagnostics.metrics],
                    "pairs": [asdict(item) for item in diagnostics.pairs],
                    "issues": [asdict(item) for item in diagnostics.issues],
                },
                dependencies=dependencies,
                reason=f"publish {context.frequency} Signal evaluation",
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
                    "SELECT signal_evaluation_id FROM signal.signal_evaluation "
                    "WHERE artifact_id = :id"
                ),
                {"id": result.artifact_id},
            ).scalar_one()
        if not isinstance(evaluation_id, uuid.UUID):
            raise RuntimeError("Signal evaluation id must be a UUID")
        return SignalEvaluationPublication(
            evaluation_id,
            result.artifact_id,
            context.frequency,
            len(context.signals),
            len(diagnostics.periods),
            len(diagnostics.pairs),
            len(diagnostics.issues),
            result.reused,
        )

    def _load_context(
        self,
        signal_catalog_artifact_id: uuid.UUID,
        forward_return_artifact_id: uuid.UUID,
        signal_engine_artifact_id: uuid.UUID,
        evaluation_engine_artifact_id: uuid.UUID,
    ) -> _Context:
        with self._engine.connect() as connection:
            _published_artifact(
                connection, signal_catalog_artifact_id, "signal_catalog_materialization"
            )
            target = _target(connection, forward_return_artifact_id)
            signal_engine = _engine(connection, signal_engine_artifact_id, "signal_engine")
            evaluation_engine = _engine(
                connection, evaluation_engine_artifact_id, "signal_evaluation_engine"
            )
            version_rows = (
                connection.execute(
                    text(
                        "SELECT version.signal_version_id, version.output_type, "
                        "version.artifact_id, "
                        "definition.signal_key FROM lineage.artifact_dependency dependency "
                        "JOIN signal.signal_version version ON "
                        "version.artifact_id = dependency.depends_on_artifact_id "
                        "JOIN signal.signal_definition definition ON "
                        "definition.signal_definition_id = version.signal_definition_id "
                        "JOIN lineage.artifact artifact ON "
                        "artifact.artifact_id = version.artifact_id "
                        "WHERE dependency.artifact_id = :catalog AND dependency.role = "
                        "'materialized_member' AND artifact.status = 'published' "
                        "ORDER BY definition.signal_key"
                    ),
                    {"catalog": signal_catalog_artifact_id},
                )
                .mappings()
                .all()
            )
            if not version_rows:
                raise ValueError("Signal catalog contains no published Signal versions")
            version_ids = tuple(item["signal_version_id"] for item in version_rows)
            dataset_rows = (
                connection.execute(
                    text(
                        "SELECT dataset.* FROM signal.signal_dataset dataset "
                        "JOIN lineage.artifact artifact ON "
                        "artifact.artifact_id = dataset.artifact_id "
                        "WHERE dataset.signal_version_id IN :versions "
                        "AND dataset.universe_version_id = :universe "
                        "AND dataset.data_bundle_version_id = :bundle "
                        "AND dataset.engine_version_id = :engine AND artifact.status = 'published'"
                    ).bindparams(bindparam("versions", expanding=True)),
                    {
                        "versions": version_ids,
                        "universe": target["universe_version_id"],
                        "bundle": target["data_bundle_version_id"],
                        "engine": signal_engine["engine_version_id"],
                    },
                )
                .mappings()
                .all()
            )
            by_version = {item["signal_version_id"]: item for item in dataset_rows}
            if set(by_version) != set(version_ids):
                raise ValueError("Formal Signal datasets are incomplete for this target context")
            eligibility_ids = {item["eligibility_snapshot_id"] for item in dataset_rows}
            if len(eligibility_ids) != 1:
                raise ValueError("Signal datasets do not share one eligibility context")
            eligibility_id = next(iter(eligibility_ids))
            eligibility_artifact_id = connection.execute(
                text(
                    "SELECT artifact_id FROM catalog.eligibility_snapshot "
                    "WHERE eligibility_snapshot_id = :id"
                ),
                {"id": eligibility_id},
            ).scalar_one()
            universe_artifact_id = connection.execute(
                text(
                    "SELECT artifact_id FROM catalog.universe_version "
                    "WHERE universe_version_id = :id"
                ),
                {"id": target["universe_version_id"]},
            ).scalar_one()
            bundle_artifact_id = connection.execute(
                text(
                    "SELECT artifact_id FROM data.data_bundle_version "
                    "WHERE data_bundle_version_id = :id"
                ),
                {"id": target["data_bundle_version_id"]},
            ).scalar_one()
            candidate_rows = (
                connection.execute(
                    text(
                        "SELECT member.asset_id, asset.asset_key "
                        "FROM catalog.universe_member member "
                        "JOIN catalog.asset asset ON asset.asset_id = member.asset_id "
                        "WHERE member.universe_version_id = :universe "
                        "AND member.role = 'candidate' "
                        "ORDER BY asset.asset_key"
                    ),
                    {"universe": target["universe_version_id"]},
                )
                .mappings()
                .all()
            )
            if len(candidate_rows) != 4:
                raise ValueError("Signal evaluation requires exactly four candidate assets")
            candidate_ids = frozenset(item["asset_id"] for item in candidate_rows)
            asset_keys = {item["asset_id"]: item["asset_key"] for item in candidate_rows}
            dataset_ids = tuple(item["signal_dataset_id"] for item in dataset_rows)
            value_rows = (
                connection.execute(
                    text(
                        "SELECT signal_dataset_id, asset_id, observation_date, score, state, event "
                        "FROM signal.signal_value WHERE signal_dataset_id IN :datasets "
                        "AND asset_id IN :assets ORDER BY signal_dataset_id, "
                        "observation_date, asset_id"
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
                        "SELECT value.asset_id, value.decision_date, value.forward_return "
                        "FROM data.forward_return_value value WHERE "
                        "value.forward_return_dataset_id = :dataset AND value.asset_id IN :assets "
                        "ORDER BY value.decision_date, value.asset_id"
                    ).bindparams(bindparam("assets", expanding=True)),
                    {
                        "dataset": target["forward_return_dataset_id"],
                        "assets": tuple(candidate_ids),
                    },
                )
                .mappings()
                .all()
            )
        values: dict[uuid.UUID, list[EvaluationValue]] = {
            item["signal_dataset_id"]: [] for item in dataset_rows
        }
        for row in value_rows:
            values[row["signal_dataset_id"]].append(
                EvaluationValue(
                    row["asset_id"],
                    asset_keys[row["asset_id"]],
                    row["observation_date"],
                    float(row["score"]),
                    row["state"],
                    row["event"],
                )
            )
        signals = tuple(
            EvaluationSignal(
                by_version[version["signal_version_id"]]["signal_dataset_id"],
                by_version[version["signal_version_id"]]["artifact_id"],
                version["signal_key"],
                version["output_type"],
                tuple(values[by_version[version["signal_version_id"]]["signal_dataset_id"]]),
            )
            for version in version_rows
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
            signal_catalog_artifact_id,
            target["universe_version_id"],
            _uuid(universe_artifact_id),
            target["data_bundle_version_id"],
            _uuid(bundle_artifact_id),
            eligibility_id,
            _uuid(eligibility_artifact_id),
            signal_engine["engine_version_id"],
            signal_engine_artifact_id,
            evaluation_engine["engine_version_id"],
            evaluation_engine_artifact_id,
            target["forward_return_dataset_id"],
            forward_return_artifact_id,
            target["frequency"],
            candidate_ids,
            signals,
            returns,
        )


def _write_evaluation(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _Context,
    diagnostics: SignalDiagnostics,
    coverage_start: Any,
    coverage_end: Any,
    threshold: float,
) -> None:
    evaluation_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO signal.signal_evaluation (signal_evaluation_id, artifact_id, "
            "signal_catalog_artifact_id, universe_version_id, data_bundle_version_id, "
            "eligibility_snapshot_id, signal_engine_version_id, evaluation_engine_version_id, "
            "forward_return_dataset_id, frequency, coverage_start, coverage_end, signal_count, "
            "period_count, pair_count, high_correlation_threshold) VALUES (:id, :artifact, "
            ":catalog, :universe, :bundle, :eligibility, :signal_engine, :evaluation_engine, "
            ":target, :frequency, :start, :end, :signals, :periods, :pairs, :threshold)"
        ),
        {
            "id": evaluation_id,
            "artifact": artifact_id,
            "catalog": context.signal_catalog_artifact_id,
            "universe": context.universe_version_id,
            "bundle": context.data_bundle_version_id,
            "eligibility": context.eligibility_snapshot_id,
            "signal_engine": context.signal_engine_version_id,
            "evaluation_engine": context.evaluation_engine_version_id,
            "target": context.forward_return_dataset_id,
            "frequency": context.frequency,
            "start": coverage_start,
            "end": coverage_end,
            "signals": len(context.signals),
            "periods": len(diagnostics.periods),
            "pairs": len(diagnostics.pairs),
            "threshold": threshold,
        },
    )
    connection.execute(
        text(
            "INSERT INTO signal.signal_evaluation_period (signal_evaluation_id, "
            "signal_dataset_id, decision_date, rank_ic, rank_ic_reason, top_bottom_spread, "
            "active_count, event_count) VALUES (:evaluation, :dataset, :date, :ic, :reason, "
            ":spread, :active, :events)"
        ),
        [
            {
                "evaluation": evaluation_id,
                "dataset": item.signal_dataset_id,
                "date": item.decision_date,
                "ic": item.rank_ic,
                "reason": item.rank_ic_reason,
                "spread": item.top_bottom_spread,
                "active": item.active_count,
                "events": item.event_count,
            }
            for item in diagnostics.periods
        ],
    )
    connection.execute(
        text(
            "INSERT INTO signal.signal_evaluation_metric (signal_evaluation_id, "
            "signal_dataset_id, window_key, window_start, window_end, period_count, "
            "valid_ic_count, undefined_ic_count, mean_rank_ic, median_rank_ic, "
            "positive_ic_ratio, information_ratio, mean_top_bottom_spread, event_rate, "
            "event_asset_concentration, non_neutral_rate, mean_top2_turnover) VALUES "
            "(:evaluation, :dataset, :window, :start, :end, "
            ":periods, :valid, :undefined, :mean_ic, :median_ic, :positive, :ir, :spread, "
            ":event_rate, :event_concentration, :non_neutral, :turnover)"
        ),
        [
            {
                "evaluation": evaluation_id,
                "dataset": item.signal_dataset_id,
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
                "event_rate": item.event_rate,
                "event_concentration": item.event_asset_concentration,
                "non_neutral": item.non_neutral_rate,
                "turnover": item.mean_top2_turnover,
            }
            for item in diagnostics.metrics
        ],
    )
    if diagnostics.pairs:
        connection.execute(
            text(
                "INSERT INTO signal.signal_pair_diagnostic (signal_evaluation_id, "
                "left_signal_dataset_id, right_signal_dataset_id, score_observation_count, "
                "score_spearman, spread_period_count, spread_correlation, mean_top2_overlap, "
                "high_correlation) VALUES (:evaluation, :left, :right, :score_count, "
                ":score_corr, :spread_count, :spread_corr, :overlap, :high)"
            ),
            [
                {
                    "evaluation": evaluation_id,
                    "left": item.left_signal_dataset_id,
                    "right": item.right_signal_dataset_id,
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
    if diagnostics.issues:
        connection.execute(
            text(
                "INSERT INTO signal.signal_diagnostic_issue (signal_diagnostic_issue_id, "
                "signal_evaluation_id, signal_dataset_id, severity, issue_code, message, "
                "details) VALUES (:id, :evaluation, :dataset, :severity, :code, :message, "
                "CAST(:details AS jsonb))"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "evaluation": evaluation_id,
                    "dataset": item.signal_dataset_id,
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
                "SELECT dataset.*, version.frequency FROM data.forward_return_dataset dataset "
                "JOIN data.forward_return_version version ON version.forward_return_version_id = "
                "dataset.forward_return_version_id JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = dataset.artifact_id WHERE dataset.artifact_id = :id "
                "AND artifact.artifact_type = 'forward_return_dataset' "
                "AND artifact.status = 'published'"
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
                "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition "
                "definition ON definition.engine_definition_id = version.engine_definition_id "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id "
                "WHERE version.artifact_id = :id AND definition.engine_key = :key "
                "AND artifact.status = 'published'"
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
    found = connection.execute(
        text(
            "SELECT 1 FROM lineage.artifact WHERE artifact_id = :id "
            "AND artifact_type = :type AND status = 'published'"
        ),
        {"id": artifact_id, "type": artifact_type},
    ).scalar_one_or_none()
    if found is None:
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
