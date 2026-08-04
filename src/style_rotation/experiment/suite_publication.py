# ruff: noqa: E501
from __future__ import annotations

import re
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from typing import Any, Literal, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.experiment.intervals import IntervalTemplateKey
from style_rotation.lineage.service import ArtifactService, DependencyInput


@dataclass(frozen=True, slots=True)
class ExperimentCellRequest:
    cell_key: str
    strategy_target_artifact_id: uuid.UUID
    benchmark_version_artifact_id: uuid.UUID
    cost_scenario_artifact_id: uuid.UUID
    metric_catalog_artifact_id: uuid.UUID
    accounting_engine_artifact_id: uuid.UUID
    benchmark_engine_artifact_id: uuid.UUID
    performance_engine_artifact_id: uuid.UUID
    template_key: IntervalTemplateKey
    as_of_date: date
    initialization_policy: Literal["carry_in"] = "carry_in"
    custom_start: date | None = None
    custom_end: date | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,179}", self.cell_key):
            raise ValueError("Experiment cell key must be a stable lowercase identifier")
        if self.template_key == "custom":
            if self.custom_start is None or self.custom_end is None:
                raise ValueError("Custom experiment interval requires start and end")
            if self.custom_start > self.custom_end or self.custom_end > self.as_of_date:
                raise ValueError("Custom experiment interval must end no later than as-of")
        elif self.custom_start is not None or self.custom_end is not None:
            raise ValueError("Preset experiment intervals cannot include custom dates")

    def semantic_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("cell_key")
        return payload


@dataclass(frozen=True, slots=True)
class ExperimentSpecificationPublication:
    artifact_id: uuid.UUID
    specification_fingerprint: str
    reused: bool


@dataclass(frozen=True, slots=True)
class ExperimentSuitePublication:
    artifact_id: uuid.UUID
    suite_key: str
    version_number: int
    specification_count: int
    specifications: tuple[ExperimentSpecificationPublication, ...]
    reused: bool


@dataclass(frozen=True, slots=True)
class _SpecificationContext:
    target: RowMapping
    benchmark: RowMapping
    cost: RowMapping
    metric: RowMapping
    accounting_engine: RowMapping
    benchmark_engine: RowMapping
    performance_engine: RowMapping


def publish_experiment_suite(
    engine: Engine,
    *,
    suite_key: str,
    name: str,
    description: str,
    cells: tuple[ExperimentCellRequest, ...],
    version_number: int = 1,
) -> ExperimentSuitePublication:
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.:-]{0,139}", suite_key):
        raise ValueError("Experiment suite key must be a stable lowercase identifier")
    if version_number < 1 or not name.strip() or not description.strip() or not cells:
        raise ValueError("Experiment suite requires version, name, description, and cells")
    if len({cell.cell_key for cell in cells}) != len(cells):
        raise ValueError("Experiment suite cell keys must be unique")
    semantic_payloads = tuple(cell.semantic_payload() for cell in cells)
    fingerprints = tuple(sha256_hexdigest(payload) for payload in semantic_payloads)
    if len(set(fingerprints)) != len(fingerprints):
        raise ValueError("Experiment suite cannot contain duplicate atomic specifications")

    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        specifications: list[ExperimentSpecificationPublication] = []
        specification_ids: list[uuid.UUID] = []
        for payload, fingerprint in zip(semantic_payloads, fingerprints, strict=True):
            context = _load_context(connection, payload)
            result = service.publish(
                artifact_type="experiment_specification",
                artifact_key=f"spec:{fingerprint}",
                version_number=1,
                semantic_payload=payload,
                content_payload=payload,
                dependencies=_specification_dependencies(payload),
                reason=f"publish atomic experiment specification {fingerprint[:12]}",
                draft_writer=partial(
                    _write_specification, context=context, payload=payload, fingerprint=fingerprint
                ),
            )
            specification_id = connection.execute(
                text(
                    "SELECT experiment_specification_id FROM experiment.experiment_specification WHERE artifact_id = :artifact"
                ),
                {"artifact": result.artifact_id},
            ).scalar_one()
            specifications.append(
                ExperimentSpecificationPublication(result.artifact_id, fingerprint, result.reused)
            )
            specification_ids.append(specification_id)

        suite_semantic = {
            "suite_key": suite_key,
            "version_number": version_number,
            "cells": tuple(
                {"cell_key": cell.cell_key, "specification_fingerprint": fingerprint}
                for cell, fingerprint in zip(cells, fingerprints, strict=True)
            ),
        }
        suite_content = {**suite_semantic, "name": name.strip(), "description": description.strip()}
        suite_result = service.publish(
            artifact_type="experiment_suite",
            artifact_key=suite_key,
            version_number=version_number,
            semantic_payload=suite_semantic,
            content_payload=suite_content,
            dependencies=tuple(
                DependencyInput(item.artifact_id, "experiment_specification", ordinal)
                for ordinal, item in enumerate(specifications)
            ),
            reason=f"publish experiment suite {suite_key} v{version_number}",
            draft_writer=partial(
                _write_suite,
                suite_key=suite_key,
                version_number=version_number,
                name=name.strip(),
                description=description.strip(),
                cells=cells,
                specification_ids=tuple(specification_ids),
            ),
        )
    return ExperimentSuitePublication(
        suite_result.artifact_id,
        suite_key,
        version_number,
        len(cells),
        tuple(specifications),
        suite_result.reused,
    )


def _specification_dependencies(payload: dict[str, Any]) -> tuple[DependencyInput, ...]:
    roles = (
        ("strategy_target_artifact_id", "strategy_target"),
        ("benchmark_version_artifact_id", "benchmark_version"),
        ("cost_scenario_artifact_id", "cost_scenario"),
        ("metric_catalog_artifact_id", "metric_catalog"),
        ("accounting_engine_artifact_id", "accounting_engine"),
        ("benchmark_engine_artifact_id", "benchmark_engine"),
        ("performance_engine_artifact_id", "performance_engine"),
    )
    return tuple(
        DependencyInput(payload[key], role, ordinal) for ordinal, (key, role) in enumerate(roles)
    )


def _load_context(connection: Connection, payload: dict[str, Any]) -> _SpecificationContext:
    target = (
        connection.execute(
            text(
                "SELECT path.*, dataset.coverage_end AS simulation_end FROM strategy.portfolio_target_path path JOIN strategy.model_strategy_target_path owner ON owner.portfolio_target_path_id = path.portfolio_target_path_id JOIN model.model_dataset dataset ON dataset.model_dataset_id = owner.model_dataset_id JOIN lineage.artifact artifact ON artifact.artifact_id = path.artifact_id AND artifact.status = 'published' WHERE path.artifact_id = :artifact AND path.target_type = 'model_strategy'"
            ),
            {"artifact": payload["strategy_target_artifact_id"]},
        )
        .mappings()
        .one_or_none()
    )
    if target is None:
        raise ValueError("Published Model Strategy Target Path not found")
    if payload["as_of_date"] > target["simulation_end"]:
        raise ValueError("Experiment as-of date cannot exceed Strategy Target simulation end")
    benchmark = _published_row(
        connection, "experiment.benchmark_version", payload["benchmark_version_artifact_id"]
    )
    cost = _published_row(
        connection, "experiment.cost_scenario", payload["cost_scenario_artifact_id"]
    )
    metric = _published_row(
        connection, "experiment.performance_metric_catalog", payload["metric_catalog_artifact_id"]
    )
    accounting = _engine(
        connection, payload["accounting_engine_artifact_id"], "portfolio_accounting_engine"
    )
    benchmark_engine = _engine(
        connection, payload["benchmark_engine_artifact_id"], "benchmark_target_engine"
    )
    performance = _engine(
        connection, payload["performance_engine_artifact_id"], "performance_engine"
    )
    return _SpecificationContext(
        target, benchmark, cost, metric, accounting, benchmark_engine, performance
    )


def _published_row(connection: Connection, table: str, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.* FROM {table} business JOIN lineage.artifact artifact ON artifact.artifact_id = business.artifact_id AND artifact.status = 'published' WHERE business.artifact_id = :artifact"
            ),
            {"artifact": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published experiment input not found in {table}")
    return row


def _engine(connection: Connection, artifact_id: uuid.UUID, key: str) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.* FROM ops.engine_version version JOIN ops.engine_definition definition ON definition.engine_definition_id = version.engine_definition_id JOIN lineage.artifact artifact ON artifact.artifact_id = version.artifact_id AND artifact.status = 'published' WHERE version.artifact_id = :artifact AND definition.engine_key = :key"
            ),
            {"artifact": artifact_id, "key": key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published {key} not found")
    return row


def _write_specification(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _SpecificationContext,
    payload: dict[str, Any],
    fingerprint: str,
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.experiment_specification (experiment_specification_id, artifact_id, strategy_target_path_id, benchmark_version_id, cost_scenario_id, performance_metric_catalog_id, accounting_engine_version_id, benchmark_engine_version_id, performance_engine_version_id, specification_fingerprint, template_key, initialization_policy, as_of_date, custom_start, custom_end, simulation_end) VALUES (:id, :artifact, :target, :benchmark, :cost, :metric, :accounting, :benchmark_engine, :performance, :fingerprint, :template, :policy, :as_of, :custom_start, :custom_end, :simulation_end)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact": artifact_id,
            "target": context.target["portfolio_target_path_id"],
            "benchmark": context.benchmark["benchmark_version_id"],
            "cost": context.cost["cost_scenario_id"],
            "metric": context.metric["performance_metric_catalog_id"],
            "accounting": context.accounting_engine["engine_version_id"],
            "benchmark_engine": context.benchmark_engine["engine_version_id"],
            "performance": context.performance_engine["engine_version_id"],
            "fingerprint": fingerprint,
            "template": payload["template_key"],
            "policy": payload["initialization_policy"],
            "as_of": payload["as_of_date"],
            "custom_start": payload["custom_start"],
            "custom_end": payload["custom_end"],
            "simulation_end": context.target["simulation_end"],
        },
    )


def _write_suite(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    suite_key: str,
    version_number: int,
    name: str,
    description: str,
    cells: tuple[ExperimentCellRequest, ...],
    specification_ids: tuple[uuid.UUID, ...],
) -> None:
    suite_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO experiment.experiment_suite (experiment_suite_id, artifact_id, suite_key, version_number, name, description, specification_count) VALUES (:id, :artifact, :key, :version, :name, :description, :count)"
        ),
        {
            "id": suite_id,
            "artifact": artifact_id,
            "key": suite_key,
            "version": version_number,
            "name": name,
            "description": description,
            "count": len(cells),
        },
    )
    connection.execute(
        text(
            "INSERT INTO experiment.experiment_suite_cell (experiment_suite_id, experiment_specification_id, cell_key, ordinal) VALUES (:suite, :specification, :key, :ordinal)"
        ),
        [
            {
                "suite": suite_id,
                "specification": specification_id,
                "key": cell.cell_key,
                "ordinal": ordinal,
            }
            for ordinal, (cell, specification_id) in enumerate(
                zip(cells, specification_ids, strict=True)
            )
        ],
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
