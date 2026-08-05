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
from style_rotation.experiment.contracts import GrossDailyNav, NetCostResult, PortfolioExecution
from style_rotation.experiment.cost_accounting import calculate_net_cost_path
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult

COST_MODEL_KEY = "linear_tradable_asset_notional"
FORMAL_COST_BPS = (Decimal("2"), Decimal("5"), Decimal("10"))
DATABASE_NUMERIC_QUANTUM = Decimal("0.000000000000000000000001")


@dataclass(frozen=True, slots=True)
class CostScenarioPublication:
    scenario_key: str
    cost_bps_per_side: Decimal
    artifact_id: uuid.UUID
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cost_bps_per_side"] = str(self.cost_bps_per_side)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class CostCatalogPublication:
    definition_artifact_id: uuid.UUID
    version_artifact_id: uuid.UUID
    scenarios: tuple[CostScenarioPublication, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition_artifact_id": str(self.definition_artifact_id),
            "version_artifact_id": str(self.version_artifact_id),
            "scenarios": [item.to_dict() for item in self.scenarios],
        }


@dataclass(frozen=True, slots=True)
class NetPathPublication:
    artifact_id: uuid.UUID
    cost_bps_per_side: Decimal
    nav_count: int
    execution_cost_count: int
    cumulative_cost_amount: Decimal
    effective_nav_start: str
    effective_nav_end: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_id"] = str(self.artifact_id)
        payload["cost_bps_per_side"] = str(self.cost_bps_per_side)
        payload["cumulative_cost_amount"] = str(self.cumulative_cost_amount)
        return payload


@dataclass(frozen=True, slots=True)
class _NetContext:
    gross_path: RowMapping
    scenario: RowMapping
    gross_artifact_id: uuid.UUID
    scenario_artifact_id: uuid.UUID
    daily_nav: tuple[GrossDailyNav, ...]
    executions: tuple[PortfolioExecution, ...]
    execution_ids: dict[date, uuid.UUID]


def publish_cost_catalog(engine: Engine, *, version_number: int = 1) -> CostCatalogPublication:
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        definition_payload = {
            "model_key": COST_MODEL_KEY,
            "name": "Linear Tradable Asset Notional Cost Model",
            "description": "Per-side bps charged on every tradable-asset buy and sell notional; synthetic reserve is not charged.",
        }
        definition = service.publish(
            artifact_type="cost_model_definition",
            artifact_key=COST_MODEL_KEY,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            reason="publish linear transaction cost model definition",
            draft_writer=partial(_write_definition, payload=definition_payload),
        )
        definition_id = connection.execute(
            text(
                "SELECT cost_model_definition_id FROM experiment.cost_model_definition WHERE artifact_id = :artifact"
            ),
            {"artifact": definition.artifact_id},
        ).scalar_one()
        version_payload = {
            "model_key": COST_MODEL_KEY,
            "version_number": version_number,
            "calculation_method": "gross_traded_fraction_times_bps",
            "charge_basis": "tradable_asset_gross_notional",
            "deduction_timing": "execution_open_before_intraday_return",
            "reserve_charged": False,
            "bps_divisor": 10_000,
        }
        version = service.publish(
            artifact_type="cost_model_version",
            artifact_key=COST_MODEL_KEY,
            version_number=version_number,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=(DependencyInput(definition.artifact_id, "cost_model_definition", 0),),
            reason=f"publish linear transaction cost model v{version_number}",
            draft_writer=partial(
                _write_version, definition_id=definition_id, payload=version_payload
            ),
        )
        version_id = connection.execute(
            text(
                "SELECT cost_model_version_id FROM experiment.cost_model_version WHERE artifact_id = :artifact"
            ),
            {"artifact": version.artifact_id},
        ).scalar_one()
        scenarios = tuple(
            _publish_scenario(service, connection, version, version_id, bps)
            for bps in FORMAL_COST_BPS
        )
    return CostCatalogPublication(definition.artifact_id, version.artifact_id, scenarios)


def _publish_scenario(
    service: ArtifactService,
    connection: Connection,
    version: PublicationResult,
    version_id: uuid.UUID,
    bps: Decimal,
) -> CostScenarioPublication:
    key = f"{COST_MODEL_KEY}__{bps}bps"
    payload = {
        "scenario_key": key,
        "cost_model_version_artifact_id": str(version.artifact_id),
        "cost_bps_per_side": bps,
    }
    result = service.publish(
        artifact_type="cost_scenario",
        artifact_key=key,
        version_number=1,
        semantic_payload=payload,
        content_payload=payload,
        dependencies=(DependencyInput(version.artifact_id, "cost_model_version", 0),),
        reason=f"publish formal {bps} bps cost scenario",
        draft_writer=partial(_write_scenario, version_id=version_id, key=key, bps=bps),
    )
    return CostScenarioPublication(key, bps, result.artifact_id, result.reused)


class NetCostPathPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self, gross_path_artifact_id: uuid.UUID, cost_scenario_artifact_id: uuid.UUID
    ) -> NetPathPublication:
        context = self._load_context(gross_path_artifact_id, cost_scenario_artifact_id)
        result = calculate_net_cost_path(
            gross_daily_nav=context.daily_nav,
            executions=context.executions,
            cost_bps_per_side=Decimal(context.scenario["cost_bps_per_side"]),
        )
        semantic = {
            "gross_path_artifact_id": str(gross_path_artifact_id),
            "cost_scenario_artifact_id": str(cost_scenario_artifact_id),
        }
        key = sha256_hexdigest(semantic)[:20]
        with self._engine.begin() as connection:
            publication = ArtifactService(cast(Engine, _BoundConnection(connection))).publish(
                artifact_type="net_cost_path",
                artifact_key=f"net:{key}",
                version_number=1,
                semantic_payload=semantic,
                content_payload={**semantic, "result": asdict(result)},
                dependencies=(
                    DependencyInput(gross_path_artifact_id, "gross_portfolio_path", 0),
                    DependencyInput(cost_scenario_artifact_id, "cost_scenario", 1),
                ),
                reason=f"publish net cost path {key}",
                draft_writer=partial(_write_net_path, context=context, result=result),
            )
        return NetPathPublication(
            publication.artifact_id,
            result.cost_bps_per_side,
            len(result.daily_nav),
            len(result.execution_costs),
            result.cumulative_cost_amount,
            result.effective_nav_start.isoformat(),
            result.effective_nav_end.isoformat(),
            publication.reused,
        )

    def _load_context(
        self, gross_artifact_id: uuid.UUID, scenario_artifact_id: uuid.UUID
    ) -> _NetContext:
        with self._engine.connect() as connection:
            gross = (
                connection.execute(
                    text(
                        "SELECT path.* FROM experiment.gross_portfolio_path path JOIN lineage.artifact artifact ON artifact.artifact_id = path.artifact_id AND artifact.status = 'published' WHERE path.artifact_id = :artifact"
                    ),
                    {"artifact": gross_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if gross is None:
                raise ValueError("Published Gross Portfolio Path not found")
            scenario = (
                connection.execute(
                    text(
                        "SELECT scenario.*, version.calculation_method, version.charge_basis, version.deduction_timing, version.reserve_charged, version.bps_divisor, definition.model_key FROM experiment.cost_scenario scenario JOIN experiment.cost_model_version version ON version.cost_model_version_id = scenario.cost_model_version_id JOIN experiment.cost_model_definition definition ON definition.cost_model_definition_id = version.cost_model_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = scenario.artifact_id AND artifact.status = 'published' WHERE scenario.artifact_id = :artifact"
                    ),
                    {"artifact": scenario_artifact_id},
                )
                .mappings()
                .one_or_none()
            )
            if scenario is None or scenario["model_key"] != COST_MODEL_KEY:
                raise ValueError("Published formal Cost Scenario not found")
            expected = (
                "gross_traded_fraction_times_bps",
                "tradable_asset_gross_notional",
                "execution_open_before_intraday_return",
                False,
                10_000,
            )
            actual = tuple(
                scenario[key]
                for key in (
                    "calculation_method",
                    "charge_basis",
                    "deduction_timing",
                    "reserve_charged",
                    "bps_divisor",
                )
            )
            if actual != expected:
                raise ValueError(
                    "Cost Scenario semantics are incompatible with Net Cost calculator"
                )
            daily_rows = connection.execute(
                text(
                    "SELECT nav_date, daily_return, gross_nav, overnight_factor, intraday_factor FROM experiment.gross_daily_nav WHERE gross_portfolio_path_id = :path ORDER BY nav_date"
                ),
                {"path": gross["gross_portfolio_path_id"]},
            ).mappings()
            daily = tuple(
                GrossDailyNav(
                    row["nav_date"],
                    Decimal(row["daily_return"]),
                    Decimal(row["gross_nav"]),
                    Decimal(row["overnight_factor"]),
                    Decimal(row["intraday_factor"]),
                )
                for row in daily_rows
            )
            execution_rows = (
                connection.execute(
                    text(
                        "SELECT portfolio_execution_id, decision_date, execution_date, gross_pretrade_nav, one_way_turnover, gross_traded_fraction, pretrade_reserve_weight, posttrade_reserve_weight FROM experiment.portfolio_execution WHERE gross_portfolio_path_id = :path ORDER BY execution_date"
                    ),
                    {"path": gross["gross_portfolio_path_id"]},
                )
                .mappings()
                .all()
            )
            executions = tuple(
                PortfolioExecution(
                    row["decision_date"],
                    row["execution_date"],
                    Decimal(row["gross_pretrade_nav"]),
                    Decimal(row["one_way_turnover"]),
                    Decimal(row["gross_traded_fraction"]),
                    Decimal(row["pretrade_reserve_weight"]),
                    Decimal(row["posttrade_reserve_weight"]),
                )
                for row in execution_rows
            )
            execution_ids = {
                row["execution_date"]: row["portfolio_execution_id"] for row in execution_rows
            }
        return _NetContext(
            gross,
            scenario,
            gross_artifact_id,
            scenario_artifact_id,
            daily,
            executions,
            execution_ids,
        )


def _write_definition(
    connection: Connection, artifact_id: uuid.UUID, *, payload: dict[str, Any]
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.cost_model_definition (cost_model_definition_id, artifact_id, model_key, name, description) VALUES (:id, :artifact, :key, :name, :description)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "key": payload["model_key"],
            "name": payload["name"],
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
            "INSERT INTO experiment.cost_model_version (cost_model_version_id, cost_model_definition_id, artifact_id, version_number, calculation_method, charge_basis, deduction_timing, reserve_charged, bps_divisor) VALUES (:id, :definition, :artifact, :version, :method, :basis, :timing, :reserve, :divisor)"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "version": payload["version_number"],
            "method": payload["calculation_method"],
            "basis": payload["charge_basis"],
            "timing": payload["deduction_timing"],
            "reserve": payload["reserve_charged"],
            "divisor": payload["bps_divisor"],
        },
    )


def _write_scenario(
    connection: Connection, artifact_id: uuid.UUID, *, version_id: uuid.UUID, key: str, bps: Decimal
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.cost_scenario (cost_scenario_id, artifact_id, cost_model_version_id, scenario_key, cost_bps_per_side) VALUES (:id, :artifact, :version, :key, :bps)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "version": version_id,
            "key": key,
            "bps": bps,
        },
    )


def _write_net_path(
    connection: Connection, artifact_id: uuid.UUID, *, context: _NetContext, result: NetCostResult
) -> None:
    path_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO experiment.net_cost_path (net_cost_path_id, artifact_id, gross_portfolio_path_id, cost_scenario_id, effective_nav_start, effective_nav_end, nav_count, execution_cost_count, cumulative_cost_amount) VALUES (:id, :artifact, :gross, :scenario, :start, :end, :nav_count, :cost_count, :cumulative)"
        ),
        {
            "id": path_id,
            "artifact": artifact_id,
            "gross": context.gross_path["gross_portfolio_path_id"],
            "scenario": context.scenario["cost_scenario_id"],
            "start": result.effective_nav_start,
            "end": result.effective_nav_end,
            "nav_count": len(result.daily_nav),
            "cost_count": len(result.execution_costs),
            "cumulative": result.cumulative_cost_amount,
        },
    )
    connection.execute(
        text(
            "INSERT INTO experiment.net_daily_nav (net_cost_path_id, nav_date, net_daily_return, net_nav, gross_nav, daily_cost_amount) VALUES (:path, :date, :return, :net_nav, :gross_nav, :cost)"
        ),
        [
            {
                "path": path_id,
                "date": row.nav_date,
                "return": row.net_daily_return,
                "net_nav": row.net_nav,
                "gross_nav": row.gross_nav,
                "cost": row.daily_cost_amount,
            }
            for row in result.daily_nav
        ],
    )
    execution_cost_rows = []
    for row in result.execution_costs:
        pretrade = row.net_pretrade_nav.quantize(DATABASE_NUMERIC_QUANTUM)
        fraction = row.cost_fraction.quantize(DATABASE_NUMERIC_QUANTUM)
        # Derive the stored amount from the stored operands. PostgreSQL NUMERIC(38, 24)
        # otherwise rounds all three independently and can violate exact reconciliation at
        # the final decimal place even though the unrounded accounting result is correct.
        stored_amount = (pretrade * fraction).quantize(DATABASE_NUMERIC_QUANTUM)
        execution_cost_rows.append(
            {
                "path": path_id,
                "execution": context.execution_ids[row.execution_date],
                "pretrade": pretrade,
                "notional": row.gross_traded_notional.quantize(DATABASE_NUMERIC_QUANTUM),
                "fraction": fraction,
                "amount": stored_amount,
            }
        )
    connection.execute(
        text(
            "INSERT INTO experiment.execution_cost (net_cost_path_id, portfolio_execution_id, net_pretrade_nav, gross_traded_notional, cost_fraction, cost_amount) VALUES (:path, :execution, :pretrade, :notional, :fraction, :amount)"
        ),
        execution_cost_rows,
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
