from __future__ import annotations

import json
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection, RowMapping

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.data.forward_return_calculator import (
    ForwardOpen,
    ForwardReturnCalculation,
    calculate_forward_returns,
)
from style_rotation.data.forward_return_contracts import ForwardReturnCatalog, ForwardReturnSeed
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult


@dataclass(frozen=True, slots=True)
class ForwardReturnCatalogPublication:
    release_artifact_id: uuid.UUID
    definition_count: int
    version_count: int
    reused_count: int

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["release_artifact_id"] = str(self.release_artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class ForwardReturnDatasetPublication:
    target_key: str
    artifact_id: uuid.UUID
    row_count: int
    coverage_start: str
    coverage_end: str
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_id"] = str(self.artifact_id)
        return payload


@dataclass(frozen=True, slots=True)
class _TargetVersion:
    seed: ForwardReturnSeed
    version_id: uuid.UUID
    artifact_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _DatasetContext:
    universe_version_id: uuid.UUID
    universe_artifact_id: uuid.UUID
    bundle_version_id: uuid.UUID
    bundle_artifact_id: uuid.UUID
    market_dataset_id: uuid.UUID
    market_artifact_id: uuid.UUID
    calendar_version_id: uuid.UUID
    calendar_artifact_id: uuid.UUID
    engine_version_id: uuid.UUID
    engine_artifact_id: uuid.UUID
    sessions: tuple[date, ...]
    opens_by_role: dict[str, tuple[ForwardOpen, ...]]


def publish_forward_return_catalog(
    engine: Engine, catalog_path: Path
) -> ForwardReturnCatalogPublication:
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog = ForwardReturnCatalog.model_validate(raw)
    with engine.begin() as connection:
        source_id = _catalog_artifact(connection, catalog.catalog_version, raw)
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        results: list[PublicationResult] = []
        for seed in catalog.definitions:
            definition_payload = {"target_key": seed.key}
            definition = service.publish(
                artifact_type="forward_return_definition",
                artifact_key=seed.key,
                version_number=1,
                semantic_payload=definition_payload,
                content_payload=definition_payload,
                reason=f"publish forward return definition {seed.key}",
                draft_writer=partial(_write_definition, seed=seed),
            )
            definition_id = connection.execute(
                text(
                    "SELECT forward_return_definition_id FROM data.forward_return_definition "
                    "WHERE artifact_id = :id"
                ),
                {"id": definition.artifact_id},
            ).scalar_one()
            payload = seed.model_dump(mode="json", exclude={"key"})
            version = service.publish(
                artifact_type="forward_return_version",
                artifact_key=seed.key,
                version_number=seed.version_number,
                semantic_payload=payload,
                content_payload=payload,
                dependencies=(DependencyInput(definition.artifact_id, "definition", 0),),
                reason=f"publish forward return version {seed.key}",
                draft_writer=partial(_write_version, definition_id=definition_id, seed=seed),
            )
            results.extend((definition, version))
        release = service.publish(
            artifact_type="forward_return_catalog_materialization",
            artifact_key="forward_return_catalog",
            version_number=semantic_version_number(catalog.catalog_version),
            semantic_payload={
                "catalog_version": catalog.catalog_version,
                "definition_count": len(catalog.definitions),
                "version_count": len(catalog.definitions),
            },
            content_payload={"member_artifact_ids": [str(item.artifact_id) for item in results]},
            dependencies=(
                DependencyInput(source_id, "source_catalog", 0),
                *tuple(
                    DependencyInput(item.artifact_id, "materialized_member", index)
                    for index, item in enumerate(results)
                ),
            ),
            reason=f"materialize forward return catalog {catalog.catalog_version}",
        )
    return ForwardReturnCatalogPublication(
        release.artifact_id,
        len(catalog.definitions),
        len(catalog.definitions),
        sum(item.reused for item in results) + int(release.reused),
    )


class ForwardReturnDatasetPublicationService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def publish(
        self,
        catalog_artifact_id: uuid.UUID,
        universe_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        engine_artifact_id: uuid.UUID,
        *,
        requested_start: date,
        requested_end: date,
    ) -> tuple[ForwardReturnDatasetPublication, ...]:
        targets, context = self._context(
            catalog_artifact_id,
            universe_artifact_id,
            bundle_artifact_id,
            engine_artifact_id,
        )
        available_roles = set(context.opens_by_role)
        required_roles = {role for target in targets for role in target.seed.included_member_roles}
        missing_roles = required_roles.difference(available_roles)
        if missing_roles:
            raise ValueError(
                f"Universe has no canonical market rows for target roles: {sorted(missing_roles)}"
            )
        calculations = tuple(
            (
                target,
                calculate_forward_returns(
                    target.seed,
                    context.sessions,
                    tuple(
                        point
                        for role in target.seed.included_member_roles
                        for point in context.opens_by_role.get(role, ())
                    ),
                    requested_start=requested_start,
                    requested_end=requested_end,
                ),
            )
            for target in targets
        )
        output: list[ForwardReturnDatasetPublication] = []
        with self._engine.begin() as connection:
            service = ArtifactService(cast(Engine, _BoundConnection(connection)))
            for target, calculation in calculations:
                semantic = {
                    "target_version_artifact_id": target.artifact_id,
                    "universe_artifact_id": context.universe_artifact_id,
                    "bundle_artifact_id": context.bundle_artifact_id,
                    "market_artifact_id": context.market_artifact_id,
                    "calendar_artifact_id": context.calendar_artifact_id,
                    "engine_artifact_id": context.engine_artifact_id,
                    "requested_start": requested_start,
                    "requested_end": requested_end,
                    "coverage_start": calculation.coverage_start,
                    "coverage_end": calculation.coverage_end,
                    "return_encoding": "numeric(28,18)",
                }
                result = service.publish(
                    artifact_type="forward_return_dataset",
                    artifact_key=f"{target.seed.key}:{sha256_hexdigest(semantic)[:16]}",
                    version_number=1,
                    semantic_payload=semantic,
                    content_payload={
                        **semantic,
                        "points": [asdict(point) for point in calculation.points],
                    },
                    dependencies=(
                        DependencyInput(target.artifact_id, "target_version", 0),
                        DependencyInput(context.universe_artifact_id, "universe_version", 1),
                        DependencyInput(context.bundle_artifact_id, "data_bundle", 2),
                        DependencyInput(context.market_artifact_id, "canonical_market", 3),
                        DependencyInput(context.calendar_artifact_id, "trading_calendar", 4),
                        DependencyInput(context.engine_artifact_id, "engine_version", 5),
                    ),
                    reason=f"publish forward return dataset {target.seed.key}",
                    draft_writer=partial(
                        _write_dataset,
                        context=context,
                        target=target,
                        calculation=calculation,
                        requested_start=requested_start,
                        requested_end=requested_end,
                    ),
                )
                output.append(
                    ForwardReturnDatasetPublication(
                        target.seed.key,
                        result.artifact_id,
                        len(calculation.points),
                        calculation.coverage_start.isoformat(),
                        calculation.coverage_end.isoformat(),
                        result.reused,
                    )
                )
        return tuple(output)

    def _context(
        self,
        catalog_artifact_id: uuid.UUID,
        universe_artifact_id: uuid.UUID,
        bundle_artifact_id: uuid.UUID,
        engine_artifact_id: uuid.UUID,
    ) -> tuple[tuple[_TargetVersion, ...], _DatasetContext]:
        with self._engine.connect() as connection:
            targets = _targets(connection, catalog_artifact_id)
            universe = _published_row(connection, "catalog.universe_version", universe_artifact_id)
            bundle = _published_row(connection, "data.data_bundle_version", bundle_artifact_id)
            engine = _published_row(connection, "ops.engine_version", engine_artifact_id)
            engine_key = connection.execute(
                text(
                    "SELECT definition.engine_key FROM ops.engine_version version "
                    "JOIN ops.engine_definition definition ON definition.engine_definition_id = "
                    "version.engine_definition_id WHERE version.engine_version_id = :id"
                ),
                {"id": engine["engine_version_id"]},
            ).scalar_one()
            if engine_key != "forward_return_engine":
                raise ValueError("Forward-return datasets require a forward-return engine")
            members = (
                connection.execute(
                    text(
                        "SELECT member.role, member.dataset_publication_id, "
                        "member.calendar_version_id, "
                        "COALESCE(dataset.artifact_id, calendar.artifact_id) AS artifact_id "
                        "FROM data.data_bundle_member member "
                        "LEFT JOIN data.dataset_publication dataset ON "
                        "dataset.dataset_publication_id = "
                        "member.dataset_publication_id LEFT JOIN "
                        "catalog.calendar_version calendar ON "
                        "calendar.calendar_version_id = member.calendar_version_id "
                        "WHERE member.data_bundle_version_id = :id"
                    ),
                    {"id": bundle["data_bundle_version_id"]},
                )
                .mappings()
                .all()
            )
            by_role = {row["role"]: row for row in members}
            market, calendar = by_role["canonical_market"], by_role["trading_calendar"]
            calendar_key = connection.execute(
                text(
                    "SELECT definition.calendar_key FROM catalog.calendar_version version "
                    "JOIN catalog.calendar_definition definition ON "
                    "definition.calendar_definition_id = version.calendar_definition_id "
                    "WHERE version.calendar_version_id = :id"
                ),
                {"id": calendar["calendar_version_id"]},
            ).scalar_one()
            if any(
                target.seed.calendar_key.casefold() != str(calendar_key).casefold()
                for target in targets
            ):
                raise ValueError("Forward-return target and bundle calendar do not match")
            sessions = tuple(
                connection.execute(
                    text(
                        "SELECT session_date FROM catalog.calendar_session "
                        "WHERE calendar_version_id = :id ORDER BY session_date"
                    ),
                    {"id": calendar["calendar_version_id"]},
                )
                .scalars()
                .all()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT bar.asset_id, asset.asset_key, member.role, bar.session_date, "
                        "bar.open_adj FROM data.daily_bar bar JOIN catalog.asset asset ON "
                        "asset.asset_id = bar.asset_id JOIN catalog.universe_member member ON "
                        "member.asset_id = bar.asset_id AND member.universe_version_id = :universe "
                        "WHERE bar.dataset_publication_id = :market ORDER BY member.role, "
                        "asset.asset_key, bar.session_date"
                    ),
                    {
                        "universe": universe["universe_version_id"],
                        "market": market["dataset_publication_id"],
                    },
                )
                .mappings()
                .all()
            )
        opens: dict[str, list[ForwardOpen]] = {}
        for row in rows:
            opens.setdefault(row["role"], []).append(
                ForwardOpen(row["asset_id"], row["asset_key"], row["session_date"], row["open_adj"])
            )
        context = _DatasetContext(
            universe["universe_version_id"],
            universe_artifact_id,
            bundle["data_bundle_version_id"],
            bundle_artifact_id,
            market["dataset_publication_id"],
            market["artifact_id"],
            calendar["calendar_version_id"],
            calendar["artifact_id"],
            engine["engine_version_id"],
            engine_artifact_id,
            sessions,
            {role: tuple(values) for role, values in opens.items()},
        )
        return targets, context


def _targets(connection: Connection, catalog_id: uuid.UUID) -> tuple[_TargetVersion, ...]:
    rows = (
        connection.execute(
            text(
                "SELECT version.forward_return_version_id, version.artifact_id, "
                "definition.target_key, "
                "version.version_number, version.frequency, version.decision_rule, "
                "version.decision_time, version.execution_policy, version.start_price, "
                "version.end_price, version.execution_lag_sessions, version.overlap_policy, "
                "version.calendar_key, version.included_member_roles "
                "FROM lineage.artifact_dependency dep "
                "JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = dep.depends_on_artifact_id "
                "JOIN data.forward_return_version version ON "
                "version.artifact_id = artifact.artifact_id "
                "JOIN data.forward_return_definition definition ON "
                "definition.forward_return_definition_id = "
                "version.forward_return_definition_id WHERE dep.artifact_id = :id "
                "AND dep.role = 'materialized_member' AND artifact.status = 'published' "
                "ORDER BY definition.target_key"
            ),
            {"id": catalog_id},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ValueError("Published forward-return catalog contains no target versions")
    return tuple(
        _TargetVersion(
            ForwardReturnSeed(
                key=row["target_key"],
                version_number=row["version_number"],
                frequency=row["frequency"],
                decision_rule=row["decision_rule"],
                decision_time=row["decision_time"],
                execution_policy=row["execution_policy"],
                start_price=row["start_price"],
                end_price=row["end_price"],
                execution_lag_sessions=row["execution_lag_sessions"],
                overlap_policy=row["overlap_policy"],
                calendar_key=row["calendar_key"],
                included_member_roles=row["included_member_roles"],
            ),
            row["forward_return_version_id"],
            row["artifact_id"],
        )
        for row in rows
    )


def _catalog_artifact(connection: Connection, version: str, raw: Any) -> uuid.UUID:
    row = (
        connection.execute(
            text(
                "SELECT artifact_id, content_hash FROM lineage.artifact WHERE artifact_type = "
                "'research_catalog' AND artifact_key = 'forward_return_catalog' "
                "AND version_number = :version AND status = 'published'"
            ),
            {"version": semantic_version_number(version)},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError("Publish the matching forward-return research catalog first")
    fingerprint = sha256_hexdigest(
        {
            "artifact_identity": {
                "artifact_type": "research_catalog",
                "artifact_key": "forward_return_catalog",
                "version_number": semantic_version_number(version),
            },
            "semantic_payload": raw,
            "dependencies": [],
        }
    )
    expected = sha256_hexdigest(
        {"semantic_fingerprint": fingerprint, "content_payload": raw, "dependencies": []}
    )
    if row["content_hash"] != expected:
        raise ValueError("Local forward-return catalog does not match its published artifact")
    return cast(uuid.UUID, row["artifact_id"])


def _published_row(connection: Connection, table: str, artifact_id: uuid.UUID) -> RowMapping:
    row = (
        connection.execute(
            text(
                f"SELECT business.* FROM {table} business JOIN lineage.artifact artifact ON "
                "artifact.artifact_id = business.artifact_id WHERE business.artifact_id = :id "
                "AND artifact.status = 'published'"
            ),
            {"id": artifact_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ValueError(f"Published business artifact not found: {table}")
    return row


def _write_definition(
    connection: Connection, artifact_id: uuid.UUID, seed: ForwardReturnSeed
) -> None:
    connection.execute(
        text(
            "INSERT INTO data.forward_return_definition "
            "(forward_return_definition_id, artifact_id, target_key) VALUES (:id, :artifact, :key)"
        ),
        {"id": uuid.uuid4(), "artifact": artifact_id, "key": seed.key},
    )


def _write_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    definition_id: uuid.UUID,
    seed: ForwardReturnSeed,
) -> None:
    payload = seed.model_dump(mode="json", exclude={"key"})
    connection.execute(
        text(
            "INSERT INTO data.forward_return_version (forward_return_version_id, "
            "forward_return_definition_id, artifact_id, version_number, frequency, decision_rule, "
            "decision_time, execution_policy, start_price, end_price, execution_lag_sessions, "
            "overlap_policy, calendar_key, included_member_roles) VALUES (:id, :definition, "
            ":artifact, :version_number, :frequency, :decision_rule, :decision_time, "
            ":execution_policy, :start_price, :end_price, :execution_lag_sessions, "
            ":overlap_policy, :calendar_key, CAST(:included_member_roles AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            **payload,
            "included_member_roles": json.dumps(seed.included_member_roles),
        },
    )


def _write_dataset(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    context: _DatasetContext,
    target: _TargetVersion,
    calculation: ForwardReturnCalculation,
    requested_start: date,
    requested_end: date,
) -> None:
    dataset_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO data.forward_return_dataset (forward_return_dataset_id, artifact_id, "
            "forward_return_version_id, universe_version_id, data_bundle_version_id, "
            "market_dataset_publication_id, calendar_version_id, engine_version_id, "
            "requested_start, requested_end, coverage_start, coverage_end, row_count) VALUES "
            "(:id, :artifact, :target, :universe, :bundle, :market, :calendar, :engine, "
            ":requested_start, :requested_end, :coverage_start, :coverage_end, :rows)"
        ),
        {
            "id": dataset_id,
            "artifact": artifact_id,
            "target": target.version_id,
            "universe": context.universe_version_id,
            "bundle": context.bundle_version_id,
            "market": context.market_dataset_id,
            "calendar": context.calendar_version_id,
            "engine": context.engine_version_id,
            "requested_start": requested_start,
            "requested_end": requested_end,
            "coverage_start": calculation.coverage_start,
            "coverage_end": calculation.coverage_end,
            "rows": len(calculation.points),
        },
    )
    connection.execute(
        text(
            "INSERT INTO data.forward_return_value (forward_return_dataset_id, asset_id, "
            "decision_date, start_date, end_date, forward_return) VALUES "
            "(:dataset, :asset, :decision, :start, :end, :value)"
        ),
        [
            {
                "dataset": dataset_id,
                "asset": point.asset_id,
                "decision": point.decision_date,
                "start": point.start_date,
                "end": point.end_date,
                "value": point.forward_return,
            }
            for point in calculation.points
        ],
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
