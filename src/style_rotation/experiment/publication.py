# ruff: noqa: E501
from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.accounting import calculate_gross_portfolio_path, map_execution_dates
from style_rotation.experiment.contracts import (
    AccountingMarketBar,
    AccountingReserveInterval,
    GrossAccountingResult,
    TargetAssetWeight,
    TargetDecision,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class GrossPathPublication:
    artifact_id: uuid.UUID
    nav_count: int
    execution_count: int
    trade_count: int
    effective_nav_start: str
    effective_nav_end: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _Context:
    target: dict[str, Any]
    engine: RowMapping
    execution: RowMapping
    reserve_model: RowMapping
    bundle_artifact_id: uuid.UUID
    reserve_model_artifact_id: uuid.UUID
    market_dataset_id: uuid.UUID
    reserve_dataset_id: uuid.UUID
    common_sessions: tuple[date, ...]
    decisions: tuple[TargetDecision, ...]
    bars: tuple[AccountingMarketBar, ...]
    reserve_intervals: tuple[AccountingReserveInterval, ...]
    simulation_end: date


class GrossPathPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self, target_path_artifact_id: uuid.UUID, accounting_engine_artifact_id: uuid.UUID
    ) -> GrossPathPublication:
        context = self._load_context(target_path_artifact_id, accounting_engine_artifact_id)
        executable = map_execution_dates(
            context.decisions,
            context.common_sessions,
            delay_common_sessions=int(context.execution["delay_common_sessions"]),
            simulation_end=context.simulation_end,
        )
        result = calculate_gross_portfolio_path(
            bars=context.bars,
            reserve_intervals=context.reserve_intervals,
            targets=executable,
            common_sessions=context.common_sessions,
            simulation_end=context.simulation_end,
            delay_common_sessions=int(context.execution["delay_common_sessions"]),
        )
        semantic = {
            "target_path_artifact_id": str(target_path_artifact_id),
            "data_bundle_artifact_id": str(context.bundle_artifact_id),
            "execution_policy_artifact_id": str(context.execution["artifact_id"]),
            "reserve_return_model_artifact_id": str(context.reserve_model_artifact_id),
            "accounting_engine_artifact_id": str(accounting_engine_artifact_id),
            "simulation_end": context.simulation_end,
        }
        key = sha256_hexdigest(semantic)[:20]
        dependencies = (
            DependencyInput(target_path_artifact_id, "strategy_target_path", 0),
            DependencyInput(context.bundle_artifact_id, "data_bundle", 1),
            DependencyInput(context.execution["artifact_id"], "execution_policy", 2),
            DependencyInput(context.reserve_model_artifact_id, "reserve_return_model", 3),
            DependencyInput(accounting_engine_artifact_id, "accounting_engine", 4),
        )
        with self._engine.begin() as connection:
            publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="gross_portfolio_path",
                artifact_key=f"gross:{key}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={**semantic, "result": asdict(result)},
                dependencies=dependencies,
                reason=f"publish gross portfolio path {key}",
                draft_writer=partial(_write_path, context=context, result=result),
            )
        return GrossPathPublication(
            publication.artifact_id,
            len(result.daily_nav),
            len(result.executions),
            len(result.trades),
            result.effective_nav_start.isoformat(),
            result.effective_nav_end.isoformat(),
            publication.reused,
        )

    def _load_context(
        self, target_artifact_id: uuid.UUID, engine_artifact_id: uuid.UUID
    ) -> _Context:
        with self._engine.connect() as connection:
            target = _target(connection, target_artifact_id)
            engine = _accounting_engine(connection, engine_artifact_id)
            execution = _execution_policy(connection, target["execution_policy_version_id"])
            bundle_artifact_id = _artifact_for_business(
                connection,
                "data.data_bundle_version",
                "data_bundle_version_id",
                target["data_bundle_version_id"],
            )
            members = _bundle_members(connection, target["data_bundle_version_id"])
            market_dataset_id = _required_member(
                members, "canonical_market", "dataset_publication_id"
            )
            reserve_dataset_id = _required_member(
                members, "reserve_return", "dataset_publication_id"
            )
            calendar_id = _required_member(members, "trading_calendar", "calendar_version_id")
            reserve_model_artifact_id = connection.execute(
                text(
                    "SELECT dependency.depends_on_artifact_id FROM data.dataset_publication dataset JOIN lineage.artifact_dependency dependency ON dependency.artifact_id = dataset.artifact_id WHERE dataset.dataset_publication_id = :dataset AND dependency.role = 'reserve_model'"
                ),
                {"dataset": reserve_dataset_id},
            ).scalar_one()
            reserve_model = (
                connection.execute(
                    text(
                        "SELECT version.* FROM experiment.reserve_return_model_version version JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact"
                    ),
                    {"artifact": reserve_model_artifact_id},
                )
                .mappings()
                .one()
            )
            sessions = tuple(
                connection.execute(
                    text(
                        "SELECT session_date FROM catalog.calendar_session WHERE calendar_version_id = :calendar ORDER BY session_date"
                    ),
                    {"calendar": calendar_id},
                ).scalars()
            )
            decisions = _decisions(
                connection,
                target["portfolio_target_path_id"],
                str(target["target_type"]),
            )
            simulation_end = target["simulation_end"]
            active_sessions = tuple(
                day for day in sessions if decisions[0].decision_date <= day <= simulation_end
            )
            asset_ids = tuple(item.asset_id for item in decisions[0].asset_weights)
            bars = _bars(connection, market_dataset_id, asset_ids, active_sessions)
            reserve = _reserve_intervals(connection, reserve_dataset_id, active_sessions)
        return _Context(
            target,
            engine,
            execution,
            reserve_model,
            bundle_artifact_id,
            reserve_model_artifact_id,
            market_dataset_id,
            reserve_dataset_id,
            sessions,
            decisions,
            bars,
            reserve,
            simulation_end,
        )


def _target(connection: Connection, artifact_id: uuid.UUID) -> dict[str, Any]:
    base = (
        connection.execute(
            text(
                "SELECT path.* FROM strategy.portfolio_target_path path JOIN lineage.artifact "
                "artifact ON artifact.artifact_id = path.artifact_id AND artifact.status = "
                "'published' WHERE path.artifact_id = :artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if base is None:
        raise ValueError("Published Portfolio Target Path not found")
    row = dict(base)
    if row["target_type"] == "model_strategy":
        owner = (
            connection.execute(
                text(
                    "SELECT owner.strategy_product_version_id, dataset.coverage_end AS "
                    "simulation_end, product.execution_policy_version_id FROM "
                    "strategy.model_strategy_target_path owner JOIN model.model_dataset dataset ON "
                    "dataset.model_dataset_id = owner.model_dataset_id JOIN "
                    "strategy.strategy_product_version product ON "
                    "product.strategy_product_version_id = owner.strategy_product_version_id WHERE "
                    "owner.portfolio_target_path_id = :path"
                ),
                {"path": row["portfolio_target_path_id"]},
            )
            .mappings()
            .one()
        )
    else:
        owner = (
            connection.execute(
                text(
                    "SELECT simulation_end, execution_policy_version_id FROM "
                    "strategy.benchmark_target_path WHERE portfolio_target_path_id = :path"
                ),
                {"path": row["portfolio_target_path_id"]},
            )
            .mappings()
            .one()
        )
    row.update(dict(owner))
    return row


def _accounting_engine(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact AND definition.engine_key = 'portfolio_accounting_engine'"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published Portfolio Accounting engine not found")
    return row


def _execution_policy(connection: Connection, execution_policy_id: uuid.UUID) -> RowMapping:
    return (
        connection.execute(
            text(
                "SELECT version.* FROM ops.execution_policy_version version JOIN "
                "lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND "
                "artifact.status = 'published' WHERE version.execution_policy_version_id = :id"
            ),
            {"id": execution_policy_id},
        )
        .mappings()
        .one()
    )


def _bundle_members(connection: Connection, bundle_id: uuid.UUID) -> dict[str, RowMapping]:
    rows = connection.execute(
        text(
            "SELECT role, dataset_publication_id, calendar_version_id FROM data.data_bundle_member WHERE data_bundle_version_id = :bundle"
        ),
        {"bundle": bundle_id},
    ).mappings()
    return {str(row["role"]): row for row in rows}


def _required_member(members: dict[str, RowMapping], role: str, column: str) -> uuid.UUID:
    row = members.get(role)
    value = row[column] if row is not None else None
    if not isinstance(value, uuid.UUID):
        raise ValueError(f"Data Bundle requires {role}")
    return value


def _decisions(
    connection: Connection, path_id: uuid.UUID, target_type: str
) -> tuple[TargetDecision, ...]:
    if target_type == "benchmark":
        decision_table = "strategy.benchmark_decision"
        position_table = "strategy.benchmark_asset_position"
        decision_id = "benchmark_decision_id"
    else:
        decision_table = "strategy.portfolio_decision"
        position_table = "strategy.target_asset_position"
        decision_id = "portfolio_decision_id"
    rows = (
        connection.execute(
            text(
                f"SELECT decision.decision_date, decision.reserve_target_weight, "
                f"position.asset_id, asset.asset_key, position.target_weight FROM "
                f"{decision_table} decision JOIN {position_table} position ON "
                f"position.{decision_id} = decision.{decision_id} JOIN catalog.asset asset ON "
                f"asset.asset_id = position.asset_id WHERE decision.portfolio_target_path_id = "
                f":path ORDER BY decision.decision_date, asset.asset_key"
            ),
            {"path": path_id},
        )
        .mappings()
        .all()
    )
    grouped: dict[date, list[TargetAssetWeight]] = {}
    reserve: dict[date, Decimal] = {}
    for row in rows:
        day = row["decision_date"]
        grouped.setdefault(day, []).append(
            TargetAssetWeight(row["asset_id"], str(row["asset_key"]), Decimal(row["target_weight"]))
        )
        reserve[day] = Decimal(row["reserve_target_weight"])
    if not grouped:
        raise ValueError("Portfolio Target Path has no decisions")
    return tuple(TargetDecision(day, tuple(grouped[day]), reserve[day]) for day in sorted(grouped))


def _bars(
    connection: Connection,
    dataset_id: uuid.UUID,
    asset_ids: tuple[uuid.UUID, ...],
    sessions: tuple[date, ...],
) -> tuple[AccountingMarketBar, ...]:
    rows = connection.execute(
        text(
            "SELECT bar.asset_id, asset.asset_key, bar.session_date, bar.open_adj, bar.close_adj "
            "FROM data.daily_bar bar JOIN catalog.asset asset ON asset.asset_id = bar.asset_id "
            "WHERE bar.dataset_publication_id = :dataset AND bar.asset_id = ANY(:assets) AND "
            "bar.session_date = ANY(:sessions) ORDER BY asset.asset_key, bar.session_date"
        ),
        {"dataset": dataset_id, "assets": list(asset_ids), "sessions": list(sessions)},
    ).mappings()
    return tuple(
        AccountingMarketBar(
            row["asset_id"],
            str(row["asset_key"]),
            row["session_date"],
            Decimal(row["open_adj"]),
            Decimal(row["close_adj"]),
        )
        for row in rows
    )


def _reserve_intervals(
    connection: Connection, dataset_id: uuid.UUID, sessions: tuple[date, ...]
) -> tuple[AccountingReserveInterval, ...]:
    rows = connection.execute(
        text(
            "SELECT interval_start, interval_end, accrual_factor, source_observation_date, source_available_date, quality_status FROM data.reserve_return WHERE dataset_publication_id = :dataset AND interval_start = ANY(:sessions) ORDER BY interval_start"
        ),
        {"dataset": dataset_id, "sessions": list(sessions)},
    ).mappings()
    return tuple(
        AccountingReserveInterval(
            row["interval_start"],
            row["interval_end"],
            Decimal(row["accrual_factor"]),
            row["source_observation_date"],
            row["source_available_date"],
            row["quality_status"],
        )
        for row in rows
    )


def _artifact_for_business(
    connection: Connection, table: str, id_column: str, business_id: uuid.UUID
) -> uuid.UUID:
    result = connection.execute(
        text(f"SELECT artifact_id FROM {table} WHERE {id_column} = :id"), {"id": business_id}
    ).scalar_one()
    if not isinstance(result, uuid.UUID):
        raise RuntimeError("Business artifact id must be a UUID")
    return result


def _write_path(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _Context,
    result: GrossAccountingResult,
) -> None:
    path_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO experiment.gross_portfolio_path (gross_portfolio_path_id, artifact_id, portfolio_target_path_id, data_bundle_version_id, execution_policy_version_id, reserve_return_model_version_id, engine_version_id, simulation_end, first_decision_date, first_execution_date, effective_nav_start, effective_nav_end, nav_count, execution_count, trade_count) VALUES (:id, :artifact, :target, :bundle, :execution, :reserve_model, :engine, :simulation_end, :first_decision, :first_execution, :nav_start, :nav_end, :nav_count, :execution_count, :trade_count)"
        ),
        {
            "id": path_id,
            "artifact": artifact_id,
            "target": context.target["portfolio_target_path_id"],
            "bundle": context.target["data_bundle_version_id"],
            "execution": context.execution["execution_policy_version_id"],
            "reserve_model": context.reserve_model["reserve_return_model_version_id"],
            "engine": context.engine["engine_version_id"],
            "simulation_end": context.simulation_end,
            "first_decision": result.first_decision_date,
            "first_execution": result.first_execution_date,
            "nav_start": result.effective_nav_start,
            "nav_end": result.effective_nav_end,
            "nav_count": len(result.daily_nav),
            "execution_count": len(result.executions),
            "trade_count": len(result.trades),
        },
    )
    connection.execute(
        text(
            "INSERT INTO experiment.gross_daily_nav (gross_portfolio_path_id, nav_date, daily_return, gross_nav, overnight_factor, intraday_factor) VALUES (:path, :date, :return, :nav, :overnight, :intraday)"
        ),
        [
            {
                "path": path_id,
                "date": item.nav_date,
                "return": item.daily_return,
                "nav": item.gross_nav,
                "overnight": item.overnight_factor,
                "intraday": item.intraday_factor,
            }
            for item in result.daily_nav
        ],
    )
    connection.execute(
        text(
            "INSERT INTO experiment.daily_asset_position (gross_portfolio_path_id, nav_date, asset_id, close_weight) VALUES (:path, :date, :asset, :weight)"
        ),
        [
            {
                "path": path_id,
                "date": item.nav_date,
                "asset": item.asset_id,
                "weight": item.close_weight,
            }
            for item in result.daily_asset_positions
        ],
    )
    connection.execute(
        text(
            "INSERT INTO experiment.daily_reserve_position (gross_portfolio_path_id, nav_date, close_weight, source_observation_date, source_available_date, quality_status) VALUES (:path, :date, :weight, :observation, :available, :quality)"
        ),
        [
            {
                "path": path_id,
                "date": item.nav_date,
                "weight": item.close_weight,
                "observation": item.interval_source_observation_date,
                "available": item.interval_source_available_date,
                "quality": item.quality_status,
            }
            for item in result.daily_reserve_positions
        ],
    )
    execution_rows = tuple((uuid.uuid4(), item) for item in result.executions)
    execution_ids = {
        (item.decision_date, item.execution_date): execution_id
        for execution_id, item in execution_rows
    }
    connection.execute(
        text(
            "INSERT INTO experiment.portfolio_execution (portfolio_execution_id, gross_portfolio_path_id, decision_date, execution_date, gross_pretrade_nav, one_way_turnover, gross_traded_fraction, pretrade_reserve_weight, posttrade_reserve_weight) VALUES (:id, :path, :decision, :execution, :nav, :turnover, :traded, :pre_reserve, :post_reserve)"
        ),
        [
            {
                "id": execution_id,
                "path": path_id,
                "decision": item.decision_date,
                "execution": item.execution_date,
                "nav": item.gross_pretrade_nav,
                "turnover": item.one_way_turnover,
                "traded": item.gross_traded_fraction,
                "pre_reserve": item.pretrade_reserve_weight,
                "post_reserve": item.posttrade_reserve_weight,
            }
            for execution_id, item in execution_rows
        ],
    )
    connection.execute(
        text(
            "INSERT INTO experiment.portfolio_trade (portfolio_execution_id, asset_id, side, adjusted_execution_price, pretrade_weight, target_weight, signed_weight_change, absolute_weight_change) VALUES (:execution, :asset, :side, :price, :pretrade, :target, :signed, :absolute)"
        ),
        [
            {
                "execution": execution_ids[(item.decision_date, item.execution_date)],
                "asset": item.asset_id,
                "side": item.side,
                "price": item.adjusted_execution_price,
                "pretrade": item.pretrade_weight,
                "target": item.target_weight,
                "signed": item.signed_weight_change,
                "absolute": item.absolute_weight_change,
            }
            for item in result.trades
        ],
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
