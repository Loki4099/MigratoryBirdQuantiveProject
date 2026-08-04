# ruff: noqa: E501
from __future__ import annotations

import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.benchmark import BenchmarkKey, calculate_benchmark_targets
from style_rotation.experiment.contracts import TargetDecision
from style_rotation.lineage.service import ArtifactService, DependencyInput

BENCHMARKS: tuple[dict[str, Any], ...] = (
    {
        "benchmark_key": "spy_buy_and_hold",
        "name": "SPY Buy-and-Hold",
        "category": "product_primary",
        "description": "Investable US large-cap market product benchmark.",
        "target_rule": "single_asset_full_weight",
        "member_role": "benchmark",
        "rebalance_policy": "buy_once_at_common_start",
    },
    {
        "benchmark_key": "four_etf_equal_weight_buy_and_hold",
        "name": "Four-ETF Equal-Weight Buy-and-Hold",
        "category": "research",
        "description": "Passive hold of the same four style candidate ETFs.",
        "target_rule": "four_candidate_equal_weight",
        "member_role": "candidate",
        "rebalance_policy": "buy_once_at_common_start",
    },
    {
        "benchmark_key": "four_etf_equal_weight_same_schedule_rebalanced",
        "name": "Four-ETF Equal-Weight Same-Schedule Rebalanced",
        "category": "research",
        "description": "Mechanical four-style equal weight on the reference strategy schedule.",
        "target_rule": "four_candidate_equal_weight",
        "member_role": "candidate",
        "rebalance_policy": "reference_strategy_schedule",
    },
)


@dataclass(frozen=True, slots=True)
class BenchmarkVersionPublication:
    benchmark_key: str
    definition_artifact_id: uuid.UUID
    version_artifact_id: uuid.UUID
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "benchmark_key": self.benchmark_key,
            "definition_artifact_id": str(self.definition_artifact_id),
            "version_artifact_id": str(self.version_artifact_id),
            "reused": self.reused,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkCatalogPublication:
    benchmarks: tuple[BenchmarkVersionPublication, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"benchmarks": [item.to_dict() for item in self.benchmarks]}


@dataclass(frozen=True, slots=True)
class BenchmarkTargetPublication:
    artifact_id: uuid.UUID
    benchmark_key: str
    category: str
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
    reference: RowMapping
    benchmark: RowMapping
    engine: RowMapping
    candidate_assets: dict[uuid.UUID, str]
    benchmark_asset: tuple[uuid.UUID, str]
    reference_dates: tuple[date, ...]
    bundle_artifact_id: uuid.UUID
    eligibility_artifact_id: uuid.UUID
    execution_artifact_id: uuid.UUID
    schedule_artifact_id: uuid.UUID


def publish_benchmark_catalog(
    engine: Engine, *, version_number: int = 1
) -> BenchmarkCatalogPublication:
    publications: list[BenchmarkVersionPublication] = []
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        for seed in BENCHMARKS:
            key = str(seed["benchmark_key"])
            definition_payload = {
                name: seed[name] for name in ("benchmark_key", "name", "category", "description")
            }
            definition = service.publish(
                artifact_type="benchmark_definition",
                artifact_key=key,
                version_number=1,
                semantic_payload=definition_payload,
                content_payload=definition_payload,
                reason=f"publish benchmark definition {key}",
                draft_writer=partial(_write_definition, payload=definition_payload),
            )
            definition_id = connection.execute(
                text(
                    "SELECT benchmark_definition_id FROM experiment.benchmark_definition WHERE artifact_id = :artifact"
                ),
                {"artifact": definition.artifact_id},
            ).scalar_one()
            version_payload = {
                "benchmark_key": key,
                "version_number": version_number,
                "target_rule": seed["target_rule"],
                "member_role": seed["member_role"],
                "rebalance_policy": seed["rebalance_policy"],
                "initial_reserve_weight": 1,
            }
            version = service.publish(
                artifact_type="benchmark_version",
                artifact_key=key,
                version_number=version_number,
                semantic_payload=version_payload,
                content_payload=version_payload,
                dependencies=(DependencyInput(definition.artifact_id, "benchmark_definition", 0),),
                reason=f"publish benchmark {key} v{version_number}",
                draft_writer=partial(
                    _write_version, definition_id=definition_id, payload=version_payload
                ),
            )
            publications.append(
                BenchmarkVersionPublication(
                    key, definition.artifact_id, version.artifact_id, version.reused
                )
            )
    return BenchmarkCatalogPublication(tuple(publications))


class BenchmarkTargetPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        reference_target_artifact_id: uuid.UUID,
        benchmark_version_artifact_id: uuid.UUID,
        benchmark_engine_artifact_id: uuid.UUID,
    ) -> BenchmarkTargetPublication:
        context = self._load_context(
            reference_target_artifact_id,
            benchmark_version_artifact_id,
            benchmark_engine_artifact_id,
        )
        key = str(context.benchmark["benchmark_key"])
        decisions = calculate_benchmark_targets(
            benchmark_key=cast(BenchmarkKey, key),
            reference_decision_dates=context.reference_dates,
            candidate_assets=context.candidate_assets,
            product_benchmark_asset=context.benchmark_asset,
        )
        semantic = {
            "reference_target_artifact_id": str(reference_target_artifact_id),
            "benchmark_version_artifact_id": str(benchmark_version_artifact_id),
            "benchmark_engine_artifact_id": str(benchmark_engine_artifact_id),
            "data_bundle_artifact_id": str(context.bundle_artifact_id),
            "eligibility_artifact_id": str(context.eligibility_artifact_id),
            "execution_policy_artifact_id": str(context.execution_artifact_id),
            "schedule_artifact_id": str(context.schedule_artifact_id),
            "simulation_end": context.reference["simulation_end"],
        }
        short_hash = sha256_hexdigest(semantic)[:20]
        dependencies = (
            DependencyInput(reference_target_artifact_id, "reference_strategy_target", 0),
            DependencyInput(benchmark_version_artifact_id, "benchmark_version", 1),
            DependencyInput(context.bundle_artifact_id, "data_bundle", 2),
            DependencyInput(context.eligibility_artifact_id, "eligibility", 3),
            DependencyInput(context.execution_artifact_id, "execution_policy", 4),
            DependencyInput(context.schedule_artifact_id, "rebalance_schedule", 5),
            DependencyInput(benchmark_engine_artifact_id, "benchmark_target_engine", 6),
        )
        with self._engine.begin() as connection:
            publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="benchmark_target_path",
                artifact_key=f"{key}:{short_hash}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={**semantic, "decisions": [asdict(item) for item in decisions]},
                dependencies=dependencies,
                reason=f"publish benchmark target path {key}",
                draft_writer=partial(_write_target, context=context, decisions=decisions),
            )
        return BenchmarkTargetPublication(
            publication.artifact_id,
            key,
            str(context.benchmark["category"]),
            len(decisions),
            sum(len(item.asset_weights) for item in decisions),
            decisions[0].decision_date.isoformat(),
            decisions[-1].decision_date.isoformat(),
            publication.reused,
        )

    def _load_context(
        self,
        reference_artifact_id: uuid.UUID,
        benchmark_artifact_id: uuid.UUID,
        engine_artifact_id: uuid.UUID,
    ) -> _Context:
        with self._engine.connect() as connection:
            reference = (
                connection.execute(
                    text(
                        "SELECT path.*, owner.strategy_product_version_id, dataset.coverage_end AS simulation_end, product.execution_policy_version_id, product.rebalance_schedule_version_id FROM strategy.portfolio_target_path path JOIN lineage.artifact artifact ON artifact.artifact_id = path.artifact_id AND artifact.status = 'published' JOIN strategy.model_strategy_target_path owner ON owner.portfolio_target_path_id = path.portfolio_target_path_id JOIN model.model_dataset dataset ON dataset.model_dataset_id = owner.model_dataset_id JOIN strategy.strategy_product_version product ON product.strategy_product_version_id = owner.strategy_product_version_id WHERE path.artifact_id = :artifact AND path.target_type = 'model_strategy'"
                    ),
                    {"artifact": reference_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if reference is None:
                raise ValueError("Published reference Strategy Target Path not found")
            benchmark = (
                connection.execute(
                    text(
                        "SELECT version.*, definition.benchmark_key, definition.category FROM experiment.benchmark_version version JOIN experiment.benchmark_definition definition ON definition.benchmark_definition_id = version.benchmark_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact"
                    ),
                    {"artifact": benchmark_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if benchmark is None:
                raise ValueError("Published Benchmark Version not found")
            engine = (
                connection.execute(
                    text(
                        "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact AND definition.engine_key = 'benchmark_target_engine'"
                    ),
                    {"artifact": engine_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if engine is None:
                raise ValueError("Published Benchmark Target engine not found")
            members = (
                connection.execute(
                    text(
                        "SELECT member.asset_id, asset.asset_key, member.role FROM catalog.universe_member member JOIN catalog.asset asset ON asset.asset_id = member.asset_id WHERE member.universe_version_id = :universe ORDER BY member.ordinal"
                    ),
                    {"universe": reference["universe_version_id"]},
                )
                .mappings()
                .all()
            )
            candidates = {
                row["asset_id"]: str(row["asset_key"])
                for row in members
                if row["role"] == "candidate"
            }
            benchmark_members = [
                (row["asset_id"], str(row["asset_key"]))
                for row in members
                if row["role"] == "benchmark"
            ]
            if len(benchmark_members) != 1:
                raise ValueError("Universe must contain exactly one product benchmark asset")
            dates = tuple(
                connection.execute(
                    text(
                        "SELECT decision_date FROM strategy.portfolio_decision WHERE portfolio_target_path_id = :path ORDER BY decision_date"
                    ),
                    {"path": reference["portfolio_target_path_id"]},
                ).scalars()
            )
            bundle_artifact = _artifact_for_business(
                connection,
                "data.data_bundle_version",
                "data_bundle_version_id",
                reference["data_bundle_version_id"],
            )
            eligibility_artifact = _artifact_for_business(
                connection,
                "catalog.eligibility_snapshot",
                "eligibility_snapshot_id",
                reference["eligibility_snapshot_id"],
            )
            execution_artifact = _artifact_for_business(
                connection,
                "ops.execution_policy_version",
                "execution_policy_version_id",
                reference["execution_policy_version_id"],
            )
            schedule_artifact = _artifact_for_business(
                connection,
                "ops.rebalance_schedule_version",
                "rebalance_schedule_version_id",
                reference["rebalance_schedule_version_id"],
            )
        return _Context(
            reference,
            benchmark,
            engine,
            candidates,
            benchmark_members[0],
            dates,
            bundle_artifact,
            eligibility_artifact,
            execution_artifact,
            schedule_artifact,
        )


def _artifact_for_business(
    connection: Connection, table: str, column: str, business_id: uuid.UUID
) -> uuid.UUID:
    result = connection.execute(
        text(f"SELECT artifact_id FROM {table} WHERE {column} = :id"), {"id": business_id}
    ).scalar_one()
    if not isinstance(result, uuid.UUID):
        raise RuntimeError("Business artifact id must be a UUID")
    return result


def _write_definition(
    connection: Connection, artifact_id: uuid.UUID, *, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.benchmark_definition (benchmark_definition_id, artifact_id, benchmark_key, name, category, description) VALUES (:id, :artifact, :key, :name, :category, :description)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "key": payload["benchmark_key"],
            "name": payload["name"],
            "category": payload["category"],
            "description": payload["description"],
        },
    )


def _write_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    definition_id: uuid.UUID,
    payload: dict[str, Any],
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.benchmark_version (benchmark_version_id, benchmark_definition_id, artifact_id, version_number, target_rule, member_role, rebalance_policy, initial_reserve_weight) VALUES (:id, :definition, :artifact, :version, :rule, :role, :rebalance, :reserve)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "version": payload["version_number"],
            "rule": payload["target_rule"],
            "role": payload["member_role"],
            "rebalance": payload["rebalance_policy"],
            "reserve": payload["initial_reserve_weight"],
        },
    )


def _write_target(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _Context,
    decisions: tuple[TargetDecision, ...],
) -> None:
    path_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO strategy.portfolio_target_path (portfolio_target_path_id, artifact_id, universe_version_id, data_bundle_version_id, eligibility_snapshot_id, engine_version_id, target_type, coverage_start, coverage_end, decision_count, position_count) VALUES (:id, :artifact, :universe, :bundle, :eligibility, :engine, 'benchmark', :start, :end, :decision_count, :position_count)"
        ),
        {
            "id": path_id,
            "artifact": artifact_id,
            "universe": context.reference["universe_version_id"],
            "bundle": context.reference["data_bundle_version_id"],
            "eligibility": context.reference["eligibility_snapshot_id"],
            "engine": context.engine["engine_version_id"],
            "start": decisions[0].decision_date,
            "end": decisions[-1].decision_date,
            "decision_count": len(decisions),
            "position_count": sum(len(item.asset_weights) for item in decisions),
        },
    )
    benchmark_asset_id = (
        context.benchmark_asset[0] if context.benchmark["member_role"] == "benchmark" else None
    )
    connection.execute(
        text(
            "INSERT INTO strategy.benchmark_target_path (portfolio_target_path_id, benchmark_asset_id, benchmark_version_id, reference_portfolio_target_path_id, execution_policy_version_id, rebalance_schedule_version_id, simulation_end) VALUES (:path, :asset, :version, :reference, :execution, :schedule, :simulation_end)"
        ),
        {
            "path": path_id,
            "asset": benchmark_asset_id,
            "version": context.benchmark["benchmark_version_id"],
            "reference": context.reference["portfolio_target_path_id"],
            "execution": context.reference["execution_policy_version_id"],
            "schedule": context.reference["rebalance_schedule_version_id"],
            "simulation_end": context.reference["simulation_end"],
        },
    )
    for decision in decisions:
        decision_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO strategy.benchmark_decision (benchmark_decision_id, portfolio_target_path_id, decision_date, actual_holding_count, reserve_target_weight) VALUES (:id, :path, :date, :holdings, :reserve)"
            ),
            {
                "id": decision_id,
                "path": path_id,
                "date": decision.decision_date,
                "holdings": len(decision.asset_weights),
                "reserve": decision.reserve_target_weight,
            },
        )
        connection.execute(
            text(
                "INSERT INTO strategy.benchmark_asset_position (benchmark_decision_id, asset_id, target_weight) VALUES (:decision, :asset, :weight)"
            ),
            [
                {"decision": decision_id, "asset": item.asset_id, "weight": item.target_weight}
                for item in decision.asset_weights
            ],
        )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
