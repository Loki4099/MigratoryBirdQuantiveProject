from __future__ import annotations

import uuid
from collections import defaultdict
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService, DependencyInput
from style_rotation.strategy.calculator import (
    CandidateModelInput,
    PortfolioTargetDecision,
    StrategyVariantInput,
    calculate_target,
)


@dataclass(frozen=True, slots=True)
class TargetPathPublication:
    artifact_id: uuid.UUID
    product_key: str
    frequency: str
    decision_count: int
    position_count: int
    coverage_start: str
    coverage_end: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _Context:
    product: RowMapping
    model_dataset: RowMapping
    engine: RowMapping
    bundle_artifact_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    candidates: dict[uuid.UUID, str]
    model_points: tuple[CandidateModelInput, ...]
    decision_dates: tuple[date, ...]
    auxiliary_dataset: RowMapping | None
    auxiliary_states: dict[tuple[uuid.UUID, date], str]


class StrategyTargetPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        product_artifact_id: uuid.UUID,
        model_dataset_artifact_id: uuid.UUID,
        target_engine_artifact_id: uuid.UUID,
        auxiliary_signal_dataset_artifact_id: uuid.UUID | None = None,
    ) -> TargetPathPublication:
        context = self._load_context(
            product_artifact_id,
            model_dataset_artifact_id,
            target_engine_artifact_id,
            auxiliary_signal_dataset_artifact_id,
        )
        by_date: dict[date, list[CandidateModelInput]] = defaultdict(list)
        for point in context.model_points:
            by_date[point.decision_date].append(point)
        for decision_date in context.decision_dates:
            if {item.asset_id for item in by_date[decision_date]} != set(context.candidates):
                raise ValueError("Model Dataset is incomplete on a scheduled decision")
        variant = StrategyVariantInput(
            str(context.product["variant_key"]),
            int(context.product["target_k"]),
            str(context.product["selection_order"]),
            str(context.product["trend_filter"]),
        )
        decisions = tuple(
            calculate_target(
                variant,
                tuple(by_date[decision_date]),
                (
                    {
                        asset_id: context.auxiliary_states[(asset_id, decision_date)]
                        for asset_id in context.candidates
                    }
                    if context.auxiliary_dataset is not None
                    else None
                ),
            )
            for decision_date in context.decision_dates
        )
        if not decisions:
            raise ValueError("Strategy Product has no complete scheduled decisions")
        semantic = {
            "product_artifact_id": str(product_artifact_id),
            "model_dataset_artifact_id": str(model_dataset_artifact_id),
            "target_engine_artifact_id": str(target_engine_artifact_id),
            "auxiliary_signal_dataset_artifact_id": (
                str(auxiliary_signal_dataset_artifact_id)
                if auxiliary_signal_dataset_artifact_id is not None
                else None
            ),
            "frequency": context.product["frequency"],
            "coverage_start": decisions[0].decision_date,
            "coverage_end": decisions[-1].decision_date,
            "decision_count": len(decisions),
            "position_count": sum(len(item.positions) for item in decisions),
        }
        key = sha256_hexdigest(semantic)[:20]
        dependencies = [
            DependencyInput(product_artifact_id, "strategy_product_version", 0),
            DependencyInput(model_dataset_artifact_id, "model_dataset", 1),
            DependencyInput(context.eligibility_artifact_id, "eligibility", 2),
            DependencyInput(context.bundle_artifact_id, "data_bundle", 3),
            DependencyInput(target_engine_artifact_id, "engine_version", 4),
        ]
        if auxiliary_signal_dataset_artifact_id is not None:
            dependencies.append(
                DependencyInput(auxiliary_signal_dataset_artifact_id, "auxiliary_signal_dataset", 5)
            )
        with self._engine.begin() as connection:
            result = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="strategy_target_path",
                artifact_key=f"{context.product['product_short_key']}:{key}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={
                    **semantic,
                    "decisions": [
                        {
                            **asdict(decision),
                            "positions": [asdict(position) for position in decision.positions],
                        }
                        for decision in decisions
                    ],
                },
                dependencies=tuple(dependencies),
                reason=f"publish Strategy target path {key}",
                draft_writer=partial(_write_target_path, context=context, decisions=decisions),
            )
        return TargetPathPublication(
            result.artifact_id,
            str(context.product["product_key"]),
            str(context.product["frequency"]),
            len(decisions),
            sum(len(item.positions) for item in decisions),
            decisions[0].decision_date.isoformat(),
            decisions[-1].decision_date.isoformat(),
            result.reused,
        )

    def _load_context(
        self,
        product_artifact_id: uuid.UUID,
        model_dataset_artifact_id: uuid.UUID,
        engine_artifact_id: uuid.UUID,
        auxiliary_artifact_id: uuid.UUID | None,
    ) -> _Context:
        with self._engine.connect() as connection:
            product = _product(connection, product_artifact_id)
            model_dataset = _model_dataset(connection, model_dataset_artifact_id)
            engine = _engine(connection, engine_artifact_id)
            if product["model_specification_id"] != model_dataset["model_specification_id"]:
                raise ValueError("Strategy Product and Model Dataset specifications do not match")
            if product["universe_version_id"] != model_dataset["universe_version_id"]:
                raise ValueError("Strategy Product and Model Dataset universes do not match")
            bundle_artifact_id = _artifact_for_business(
                connection,
                "data.data_bundle_version",
                "data_bundle_version_id",
                model_dataset["data_bundle_version_id"],
            )
            eligibility_artifact_id = _artifact_for_business(
                connection,
                "catalog.eligibility_snapshot",
                "eligibility_snapshot_id",
                model_dataset["eligibility_snapshot_id"],
            )
            candidates = _candidates(connection, product["universe_version_id"])
            model_points = _model_points(connection, model_dataset["model_dataset_id"], candidates)
            decision_dates = _decision_dates(
                connection,
                model_dataset["data_bundle_version_id"],
                str(product["frequency"]),
                model_dataset["coverage_start"],
                model_dataset["coverage_end"],
                {item.decision_date for item in model_points},
            )
            auxiliary_dataset, states = _auxiliary(
                connection,
                product,
                model_dataset,
                auxiliary_artifact_id,
                candidates,
                decision_dates,
            )
        return _Context(
            product,
            model_dataset,
            engine,
            bundle_artifact_id,
            eligibility_artifact_id,
            candidates,
            model_points,
            decision_dates,
            auxiliary_dataset,
            states,
        )


def _product(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT product.*, product_artifact.artifact_key AS product_short_key, "
                "definition.product_key, variant.variant_key, variant.target_k, "
                "variant.selection_order, variant.trend_filter, "
                "variant.auxiliary_signal_version_id, schedule.frequency "
                "FROM strategy.strategy_product_version product "
                "JOIN lineage.artifact product_artifact ON product_artifact.artifact_id = "
                "product.artifact_id AND product_artifact.status = 'published' "
                "JOIN strategy.strategy_product_definition definition ON "
                "definition.strategy_product_definition_id = "
                "product.strategy_product_definition_id "
                "JOIN strategy.strategy_variant variant ON variant.strategy_variant_id = "
                "product.strategy_variant_id JOIN ops.rebalance_schedule_version schedule ON "
                "schedule.rebalance_schedule_version_id = product.rebalance_schedule_version_id "
                "WHERE product.artifact_id = :artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published Strategy Product version not found")
    return row


def _model_dataset(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT dataset.* FROM model.model_dataset dataset "
                "JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = dataset.artifact_id AND artifact.status = 'published' "
                "WHERE dataset.artifact_id = :artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published Model Dataset not found")
    return row


def _engine(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition "
                "definition ON definition.engine_definition_id = version.engine_definition_id "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id "
                "AND artifact.status = 'published' WHERE version.artifact_id = :artifact "
                "AND definition.engine_key = 'strategy_target_engine'"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published Strategy Target engine not found")
    return row


def _candidates(connection: Connection, universe_id: uuid.UUID) -> dict[uuid.UUID, str]:
    rows = (
        connection.execute(
            text(
                "SELECT member.asset_id, asset.asset_key FROM catalog.universe_member member "
                "JOIN catalog.asset asset ON asset.asset_id = member.asset_id "
                "WHERE member.universe_version_id = :universe AND member.role = 'candidate' "
                "ORDER BY member.ordinal"
            ),
            {"universe": universe_id},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ValueError("Strategy Product universe has no candidate assets")
    return {row["asset_id"]: str(row["asset_key"]) for row in rows}


def _model_points(
    connection: Connection, dataset_id: uuid.UUID, candidates: dict[uuid.UUID, str]
) -> tuple[CandidateModelInput, ...]:
    rows = (
        connection.execute(
            text(
                "SELECT asset_id, observation_date, score, direction, confidence FROM "
                "model.model_value WHERE model_dataset_id = :dataset AND asset_id = ANY(:assets) "
                "ORDER BY observation_date, asset_id"
            ),
            {"dataset": dataset_id, "assets": list(candidates)},
        )
        .mappings()
        .all()
    )
    return tuple(
        CandidateModelInput(
            row["asset_id"],
            candidates[row["asset_id"]],
            row["observation_date"],
            Decimal(row["score"]),
            str(row["direction"]),
            Decimal(row["confidence"]),
        )
        for row in rows
    )


def _decision_dates(
    connection: Connection,
    bundle_id: uuid.UUID,
    frequency: str,
    coverage_start: date,
    coverage_end: date,
    model_dates: set[date],
) -> tuple[date, ...]:
    sessions = (
        connection.execute(
            text(
                "SELECT session.session_date FROM data.data_bundle_member member "
                "JOIN catalog.calendar_session session ON session.calendar_version_id = "
                "member.calendar_version_id WHERE member.data_bundle_version_id = :bundle "
                "AND member.role = 'trading_calendar' ORDER BY session.session_date"
            ),
            {"bundle": bundle_id},
        )
        .scalars()
        .all()
    )
    period_last: dict[tuple[int, int], date] = {}
    for session in sessions:
        key = (
            (session.isocalendar().year, session.isocalendar().week)
            if frequency == "weekly"
            else (session.year, session.month)
        )
        period_last[key] = session
    decisions = tuple(
        session
        for index, session in enumerate(sessions[:-1])
        if coverage_start <= session <= coverage_end
        and session in model_dates
        and session in period_last.values()
        and sessions[index + 1] <= coverage_end
    )
    return decisions


def _auxiliary(
    connection: Connection,
    product: RowMapping,
    model_dataset: RowMapping,
    artifact_id: uuid.UUID | None,
    candidates: dict[uuid.UUID, str],
    decision_dates: tuple[date, ...],
) -> tuple[RowMapping | None, dict[tuple[uuid.UUID, date], str]]:
    required_version = product["auxiliary_signal_version_id"]
    if required_version is None:
        if artifact_id is not None:
            raise ValueError("Unfiltered Strategy Product must not receive an auxiliary Signal")
        return None, {}
    if artifact_id is None:
        raise ValueError("Trend Strategy Product requires its auxiliary Signal Dataset")
    row = (
        connection.execute(
            text(
                "SELECT dataset.* FROM signal.signal_dataset dataset "
                "JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = dataset.artifact_id AND artifact.status = 'published' "
                "WHERE dataset.artifact_id = :artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published auxiliary Signal Dataset not found")
    for column in (
        "universe_version_id",
        "data_bundle_version_id",
        "eligibility_snapshot_id",
    ):
        if row[column] != model_dataset[column]:
            raise ValueError("Auxiliary Signal Dataset context does not match Model Dataset")
    if row["signal_version_id"] != required_version:
        raise ValueError("Auxiliary Signal Dataset version does not match Strategy Variant")
    states = {
        (item["asset_id"], item["observation_date"]): str(item["state"])
        for item in connection.execute(
            text(
                "SELECT asset_id, observation_date, state FROM signal.signal_value "
                "WHERE signal_dataset_id = :dataset AND asset_id = ANY(:assets) "
                "AND observation_date = ANY(:dates)"
            ),
            {
                "dataset": row["signal_dataset_id"],
                "assets": list(candidates),
                "dates": list(decision_dates),
            },
        ).mappings()
    }
    expected = {(asset, day) for asset in candidates for day in decision_dates}
    if set(states) != expected or any(state == "None" for state in states.values()):
        raise ValueError("Auxiliary Signal Dataset is incomplete on scheduled decisions")
    return row, states


def _artifact_for_business(
    connection: Connection, table: str, id_column: str, business_id: uuid.UUID
) -> uuid.UUID:
    result = connection.execute(
        text(f"SELECT artifact_id FROM {table} WHERE {id_column} = :id"), {"id": business_id}
    ).scalar_one()
    if not isinstance(result, uuid.UUID):
        raise RuntimeError("Business artifact id must be a UUID")
    return result


def _write_target_path(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _Context,
    decisions: tuple[PortfolioTargetDecision, ...],
) -> None:
    path_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO strategy.portfolio_target_path "
            "(portfolio_target_path_id, artifact_id, universe_version_id, "
            "data_bundle_version_id, eligibility_snapshot_id, engine_version_id, target_type, "
            "coverage_start, coverage_end, decision_count, position_count) VALUES "
            "(:id, :artifact, :universe, :bundle, :eligibility, :engine, 'model_strategy', "
            ":start, :end, :decisions, :positions)"
        ),
        {
            "id": path_id,
            "artifact": artifact_id,
            "universe": context.model_dataset["universe_version_id"],
            "bundle": context.model_dataset["data_bundle_version_id"],
            "eligibility": context.model_dataset["eligibility_snapshot_id"],
            "engine": context.engine["engine_version_id"],
            "start": decisions[0].decision_date,
            "end": decisions[-1].decision_date,
            "decisions": len(decisions),
            "positions": sum(len(item.positions) for item in decisions),
        },
    )
    connection.execute(
        text(
            "INSERT INTO strategy.model_strategy_target_path "
            "(portfolio_target_path_id, strategy_product_version_id, model_dataset_id) "
            "VALUES (:path, :product, :model)"
        ),
        {
            "path": path_id,
            "product": context.product["strategy_product_version_id"],
            "model": context.model_dataset["model_dataset_id"],
        },
    )
    if context.auxiliary_dataset is not None:
        connection.execute(
            text(
                "INSERT INTO strategy.target_path_auxiliary_input "
                "(portfolio_target_path_id, signal_dataset_id, role) "
                "VALUES (:path, :signal, 'trend_filter_state')"
            ),
            {"path": path_id, "signal": context.auxiliary_dataset["signal_dataset_id"]},
        )
    decision_rows = tuple((uuid.uuid4(), decision) for decision in decisions)
    connection.execute(
        text(
            "INSERT INTO strategy.portfolio_decision "
            "(portfolio_decision_id, portfolio_target_path_id, decision_date, target_k, "
            "actual_holding_count, boundary_tie_count, reserve_target_weight) VALUES "
            "(:id, :path, :date, :k, :holdings, :ties, :reserve)"
        ),
        [
            {
                "id": decision_id,
                "path": path_id,
                "date": decision.decision_date,
                "k": decision.target_k,
                "holdings": decision.actual_holding_count,
                "ties": decision.boundary_tie_count,
                "reserve": decision.reserve_target_weight,
            }
            for decision_id, decision in decision_rows
        ],
    )
    connection.execute(
        text(
            "INSERT INTO strategy.target_asset_position "
            "(portfolio_decision_id, asset_id, model_score, model_rank, selection_rank, "
            "trend_state, strategy_eligible, selected, target_weight, decision_reason) "
            "VALUES (:decision, :asset, :score, :model_rank, :selection_rank, :trend, "
            ":eligible, :selected, :weight, :reason)"
        ),
        [
            {
                "decision": decision_id,
                "asset": position.asset_id,
                "score": position.model_score,
                "model_rank": position.model_rank,
                "selection_rank": position.selection_rank,
                "trend": position.trend_state,
                "eligible": position.strategy_eligible,
                "selected": position.selected,
                "weight": position.target_weight,
                "reason": position.reason,
            }
            for decision_id, decision in decision_rows
            for position in decision.positions
        ],
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
