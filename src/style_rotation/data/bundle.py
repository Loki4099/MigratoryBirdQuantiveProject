from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.data.reserve import (
    AvailableRate,
    ReserveModelCatalog,
    ReserveResult,
    calculate_reserve_intervals,
)
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult

RESERVE_MODEL_KEY = "dgs3mo_cash_accrual_proxy"
BUNDLE_KEY = "us_style_daily_research_bundle"


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)


def publish_reserve_model(
    engine: Engine,
    catalog_path: Path = Path("v0.2/catalogs/reserve_model.v0.2.0.json"),
) -> tuple[PublicationResult, PublicationResult]:
    catalog = ReserveModelCatalog.model_validate_json(catalog_path.read_text(encoding="utf-8"))
    definition_payload = {
        "model_key": catalog.model_key,
        "name": catalog.name,
    }
    version_payload = catalog.model_dump(
        mode="json", exclude={"catalog_type", "catalog_version", "name"}
    )
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        definition = service.publish(
            artifact_type="reserve_return_model_definition",
            artifact_key=catalog.model_key,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            reason="publish DGS3MO reserve model definition",
            draft_writer=lambda tx, artifact_id: _write_reserve_model_definition(
                tx, artifact_id, str(definition_payload["name"])
            ),
        )
        definition_id = connection.execute(
            text(
                "SELECT reserve_return_model_definition_id "
                "FROM experiment.reserve_return_model_definition WHERE artifact_id = :artifact_id"
            ),
            {"artifact_id": definition.artifact_id},
        ).scalar_one()
        version = service.publish(
            artifact_type="reserve_return_model_version",
            artifact_key=catalog.model_key,
            version_number=catalog.version_number,
            semantic_payload=version_payload,
            content_payload=version_payload,
            dependencies=(DependencyInput(definition.artifact_id, "definition", 0),),
            reason=f"publish DGS3MO reserve model v{catalog.version_number}",
            draft_writer=lambda tx, artifact_id: _write_reserve_model_version(
                tx, artifact_id, definition_id, catalog
            ),
        )
    return definition, version


class ReservePublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._artifacts = ArtifactService(engine)

    def publish(
        self,
        rate_dataset_artifact_id: uuid.UUID,
        calendar_artifact_id: uuid.UUID,
        model_artifact_id: uuid.UUID,
        *,
        version_number: int,
    ) -> PublicationResult:
        rate_dataset = self._rate_dataset(rate_dataset_artifact_id)
        calendar = self._calendar(calendar_artifact_id)
        model = self._model(model_artifact_id)
        rates = tuple(
            AvailableRate(
                row["observation_date"], row["available_date"], row["annual_rate_percent"]
            )
            for row in rate_dataset["rates"]
        )
        result = calculate_reserve_intervals(
            rates,
            calendar["sessions"],
            warning_after_days=model["warning_after_days"],
            error_after_days=model["error_after_days"],
        )
        if result.has_errors:
            messages = ", ".join(
                item.rule_code for item in result.issues if item.severity == "error"
            )
            raise ValueError(f"Reserve quality gate rejected publication: {messages}")
        if not result.intervals:
            raise ValueError("Reserve dataset requires at least two calendar sessions")
        semantic = {
            "dataset_key": "dgs3mo_reserve_return",
            "version_number": version_number,
            "rate_dataset_artifact_id": str(rate_dataset_artifact_id),
            "calendar_artifact_id": str(calendar_artifact_id),
            "reserve_model_artifact_id": str(model_artifact_id),
        }
        return self._artifacts.publish(
            artifact_type="dataset_publication",
            artifact_key="dgs3mo_reserve_return",
            version_number=version_number,
            semantic_payload=semantic,
            content_payload={**semantic, "result": asdict(result)},
            dependencies=(
                DependencyInput(rate_dataset_artifact_id, "rate_dataset", 0),
                DependencyInput(calendar_artifact_id, "calendar_version", 1),
                DependencyInput(model_artifact_id, "reserve_model", 2),
            ),
            reason=f"publish DGS3MO reserve return dataset v{version_number}",
            draft_writer=lambda tx, artifact_id: _write_reserve(
                tx,
                artifact_id,
                version_number,
                rate_dataset,
                calendar,
                result,
            ),
        )

    def _rate_dataset(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            dataset = _published_dataset(connection, artifact_id, "dgs3mo_canonical")
            rows = connection.execute(
                text(
                    "SELECT observation_date, available_date, annual_rate_percent "
                    "FROM data.rate_observation WHERE dataset_publication_id = :id "
                    "ORDER BY observation_date"
                ),
                {"id": dataset["dataset_publication_id"]},
            ).mappings()
            return {**dict(dataset), "rates": tuple(rows)}

    def _calendar(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        with self._engine.connect() as connection:
            row = _published_calendar(connection, artifact_id)
            sessions = connection.execute(
                text(
                    "SELECT session_date FROM catalog.calendar_session "
                    "WHERE calendar_version_id = :id ORDER BY session_date"
                ),
                {"id": row["calendar_version_id"]},
            ).scalars()
            return {**dict(row), "sessions": tuple(sessions)}

    def _model(self, artifact_id: uuid.UUID) -> RowMapping:
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text(
                        "SELECT version.* FROM experiment.reserve_return_model_version version "
                        "JOIN lineage.artifact artifact "
                        "ON artifact.artifact_id = version.artifact_id "
                        "WHERE version.artifact_id = :id AND artifact.status = 'published'"
                    ),
                    {"id": artifact_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError("Published reserve return model not found")
        return row


def publish_data_bundle(
    engine: Engine,
    market_dataset_artifact_id: uuid.UUID,
    rate_dataset_artifact_id: uuid.UUID,
    reserve_dataset_artifact_id: uuid.UUID,
    calendar_artifact_id: uuid.UUID,
    *,
    version_number: int,
) -> tuple[PublicationResult, PublicationResult]:
    with engine.begin() as connection:
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        definition_payload = {
            "bundle_key": BUNDLE_KEY,
            "name": "US Style Daily Research Bundle",
            "description": "Canonical ETF bars/actions, DGS3MO, reserve accrual, and XNYS calendar",
        }
        definition = service.publish(
            artifact_type="data_bundle_definition",
            artifact_key=BUNDLE_KEY,
            version_number=1,
            semantic_payload=definition_payload,
            content_payload=definition_payload,
            reason="publish US style daily data bundle definition",
            draft_writer=lambda tx, artifact_id: _write_bundle_definition(
                tx,
                artifact_id,
                str(definition_payload["name"]),
                str(definition_payload["description"]),
            ),
        )
        definition_id = connection.execute(
            text(
                "SELECT data_bundle_definition_id FROM data.data_bundle_definition "
                "WHERE artifact_id = :id"
            ),
            {"id": definition.artifact_id},
        ).scalar_one()
        market = _published_dataset(
            connection, market_dataset_artifact_id, "us_etf_daily_market_canonical"
        )
        rate = _published_dataset(connection, rate_dataset_artifact_id, "dgs3mo_canonical")
        reserve = _published_dataset(
            connection, reserve_dataset_artifact_id, "dgs3mo_reserve_return"
        )
        calendar = _published_calendar(connection, calendar_artifact_id)
        members = (
            ("canonical_market", market, "dataset"),
            ("canonical_rate", rate, "dataset"),
            ("reserve_return", reserve, "dataset"),
            ("trading_calendar", calendar, "calendar"),
        )
        coverage_members = tuple(item for item in members if item[0] != "canonical_rate")
        coverage_start = max(item[1]["coverage_start"] for item in coverage_members)
        coverage_end = min(item[1]["coverage_end"] for item in coverage_members)
        if coverage_start > coverage_end:
            raise ValueError("Data bundle members have no common coverage")
        payload = {
            "bundle_key": BUNDLE_KEY,
            "version_number": version_number,
            "members": [
                {"role": role, "artifact_id": str(row["artifact_id"])}
                for role, row, _kind in members
            ],
            "coverage_start": coverage_start,
            "coverage_end": coverage_end,
        }
        dependencies = (DependencyInput(definition.artifact_id, "definition", 0),) + tuple(
            DependencyInput(row["artifact_id"], role, ordinal + 1)
            for ordinal, (role, row, _kind) in enumerate(members)
        )
        version = service.publish(
            artifact_type="data_bundle_version",
            artifact_key=BUNDLE_KEY,
            version_number=version_number,
            semantic_payload=payload,
            content_payload=payload,
            dependencies=dependencies,
            reason=f"publish US style daily data bundle v{version_number}",
            draft_writer=lambda tx, artifact_id: _write_bundle(
                tx,
                artifact_id,
                definition_id,
                version_number,
                coverage_start,
                coverage_end,
                members,
            ),
        )
    return definition, version


def _published_dataset(connection: Connection, artifact_id: uuid.UUID, key: str) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT dataset.* FROM data.dataset_publication dataset "
                "JOIN lineage.artifact artifact ON artifact.artifact_id = dataset.artifact_id "
                "WHERE dataset.artifact_id = :id AND dataset.dataset_key = :key "
                "AND artifact.status = 'published'"
            ),
            {"id": artifact_id, "key": key},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published dataset not found: {key}")
    return row


def _write_reserve_model_definition(
    connection: Connection, artifact_id: uuid.UUID, name: str
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.reserve_return_model_definition "
            "(reserve_return_model_definition_id, artifact_id, model_key, name) "
            "VALUES (:id, :artifact_id, :key, :name)"
        ),
        {"id": uuid.uuid4(), "artifact_id": artifact_id, "key": RESERVE_MODEL_KEY, "name": name},
    )


def _write_reserve_model_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    catalog: ReserveModelCatalog,
) -> None:
    connection.execute(
        text(
            "INSERT INTO experiment.reserve_return_model_version ("
            "reserve_return_model_version_id, reserve_return_model_definition_id, "
            "artifact_id, version_number, accrual_method, day_count_basis, "
            "warning_after_days, error_after_days) VALUES ("
            ":id, :definition_id, :artifact_id, :version, :method, :basis, :warning, :error)"
        ),
        {
            "id": uuid.uuid4(),
            "definition_id": definition_id,
            "artifact_id": artifact_id,
            "version": catalog.version_number,
            "method": catalog.accrual_method,
            "basis": catalog.day_count_basis,
            "warning": catalog.warning_after_days,
            "error": catalog.error_after_days,
        },
    )


def _write_bundle_definition(
    connection: Connection, artifact_id: uuid.UUID, name: str, description: str
) -> None:
    connection.execute(
        text(
            "INSERT INTO data.data_bundle_definition "
            "(data_bundle_definition_id, artifact_id, bundle_key, name, description) "
            "VALUES (:id, :artifact_id, :key, :name, :description)"
        ),
        {
            "id": uuid.uuid4(),
            "artifact_id": artifact_id,
            "key": BUNDLE_KEY,
            "name": name,
            "description": description,
        },
    )


def _published_calendar(connection: Connection, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                "SELECT version.*, version.coverage_start, version.coverage_end "
                "FROM catalog.calendar_version version JOIN lineage.artifact artifact "
                "ON artifact.artifact_id = version.artifact_id "
                "WHERE version.artifact_id = :id AND artifact.status = 'published'"
            ),
            {"id": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Published calendar version not found")
    return row


def _write_reserve(
    connection: Connection,
    artifact_id: uuid.UUID,
    version_number: int,
    rate_dataset: dict[str, Any],
    calendar: dict[str, Any],
    result: ReserveResult,
) -> None:
    publication_id = uuid.uuid4()
    start = result.intervals[0].interval_start
    end = result.intervals[-1].interval_end
    connection.execute(
        text(
            "INSERT INTO data.dataset_publication (dataset_publication_id, artifact_id, "
            "calendar_version_id, dataset_key, version_number, dataset_kind, value_kind, "
            "coverage_start, coverage_end, row_count) VALUES (:id, :artifact_id, :calendar_id, "
            "'dgs3mo_reserve_return', :version, 'derived', 'reserve_return', :start, :end, :count)"
        ),
        {
            "id": publication_id,
            "artifact_id": artifact_id,
            "calendar_id": calendar["calendar_version_id"],
            "version": version_number,
            "start": start,
            "end": end,
            "count": len(result.intervals),
        },
    )
    connection.execute(
        text(
            "INSERT INTO data.dataset_input (dataset_input_id, dataset_publication_id, "
            "upstream_dataset_publication_id, role, ordinal) "
            "VALUES (:id, :publication_id, :upstream_id, 'rate_dataset', 0)"
        ),
        {
            "id": uuid.uuid4(),
            "publication_id": publication_id,
            "upstream_id": rate_dataset["dataset_publication_id"],
        },
    )
    connection.execute(
        text(
            "INSERT INTO data.reserve_return (dataset_publication_id, interval_start, "
            "interval_end, source_observation_date, source_available_date, annual_rate_percent, "
            "calendar_days, accrual_factor, staleness_days, quality_status) VALUES ("
            ":publication_id, :start, :end, :observation, :available, :rate, :days, :factor, "
            ":staleness, :status)"
        ),
        [
            {
                "publication_id": publication_id,
                "start": item.interval_start,
                "end": item.interval_end,
                "observation": item.source_observation_date,
                "available": item.source_available_date,
                "rate": item.annual_rate_percent,
                "days": item.calendar_days,
                "factor": item.accrual_factor,
                "staleness": item.staleness_days,
                "status": item.quality_status,
            }
            for item in result.intervals
        ],
    )
    connection.execute(
        text(
            "INSERT INTO data.dataset_coverage (dataset_coverage_id, dataset_publication_id, "
            "subject_key, coverage_start, coverage_end, observation_count, missing_count) "
            "VALUES (:id, :publication_id, 'DGS3MO_CASH_PROXY', :start, :end, :count, 0)"
        ),
        {
            "id": uuid.uuid4(),
            "publication_id": publication_id,
            "start": start,
            "end": end,
            "count": len(result.intervals),
        },
    )
    warnings = [item for item in result.issues if item.severity == "warning"]
    if warnings:
        connection.execute(
            text(
                "INSERT INTO data.quality_issue (quality_issue_id, dataset_publication_id, "
                "severity, rule_code, event_date, message, details) VALUES (:id, "
                ":publication_id, 'warning', :rule, :date, :message, CAST(:details AS jsonb))"
            ),
            [
                {
                    "id": uuid.uuid4(),
                    "publication_id": publication_id,
                    "rule": item.rule_code,
                    "date": item.event_date,
                    "message": item.message,
                    "details": json.dumps(item.details),
                }
                for item in warnings
            ],
        )


def _write_bundle(
    connection: Connection,
    artifact_id: uuid.UUID,
    definition_id: uuid.UUID,
    version_number: int,
    coverage_start: date,
    coverage_end: date,
    members: tuple[tuple[str, RowMapping, str], ...],
) -> None:
    version_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO data.data_bundle_version (data_bundle_version_id, "
            "data_bundle_definition_id, artifact_id, version_number, member_count, "
            "coverage_start, coverage_end) VALUES (:id, :definition_id, :artifact_id, "
            ":version, :count, :start, :end)"
        ),
        {
            "id": version_id,
            "definition_id": definition_id,
            "artifact_id": artifact_id,
            "version": version_number,
            "count": len(members),
            "start": coverage_start,
            "end": coverage_end,
        },
    )
    connection.execute(
        text(
            "INSERT INTO data.data_bundle_member (data_bundle_member_id, data_bundle_version_id, "
            "dataset_publication_id, calendar_version_id, role, ordinal) VALUES (:id, "
            ":version_id, :dataset_id, :calendar_id, :role, :ordinal)"
        ),
        [
            {
                "id": uuid.uuid4(),
                "version_id": version_id,
                "dataset_id": row.get("dataset_publication_id") if kind == "dataset" else None,
                "calendar_id": row.get("calendar_version_id") if kind == "calendar" else None,
                "role": role,
                "ordinal": ordinal,
            }
            for ordinal, (role, row, kind) in enumerate(members)
        ],
    )
