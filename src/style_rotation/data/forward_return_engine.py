from __future__ import annotations

import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, cast

from sqlalchemy import Engine, text
from sqlalchemy.engine import Connection

from style_rotation import __version__
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.lineage.service import ArtifactService
from style_rotation.ops.environment import capture_numerical_environment


@dataclass(frozen=True, slots=True)
class ForwardReturnEngineSpec:
    version_number: int
    semantic_version: str
    git_commit: str
    dependency_lock_hash: str
    schema_revision: str
    configuration_hash: str
    numerical_environment: dict[str, Any]

    def __post_init__(self) -> None:
        if self.version_number < 1 or not re.fullmatch(r"[0-9a-f]{7,64}", self.git_commit):
            raise ValueError("Forward-return engine requires a positive version and hex commit")
        if not re.fullmatch(r"[0-9a-f]{64}", self.dependency_lock_hash):
            raise ValueError("Forward-return dependency lock hash must be SHA-256")
        if not re.fullmatch(r"[0-9a-f]{64}", self.configuration_hash):
            raise ValueError("Forward-return configuration hash must be SHA-256")


@dataclass(frozen=True, slots=True)
class ForwardReturnEnginePublication:
    engine_version_id: uuid.UUID
    artifact_id: uuid.UUID
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version_id": str(self.engine_version_id),
            "artifact_id": str(self.artifact_id),
            "reused": self.reused,
        }


def build_forward_return_engine_spec(
    git_commit: str,
    dependency_lock_path: Path,
    schema_revision: str,
    *,
    version_number: int = 1,
) -> ForwardReturnEngineSpec:
    if not dependency_lock_path.is_file():
        raise ValueError(f"Dependency lock file not found: {dependency_lock_path}")
    configuration = {
        "calculator": "forward-return-calculator-v1",
        "schedule": "last-common-session-week-or-month",
        "execution": "next-common-session-adjusted-open",
        "return": "next-execution-open-to-following-execution-open",
        "overlap": "non-overlapping-schedule-intervals",
        "incomplete_final_interval": "excluded",
        "encoding": "numeric-28-18-half-even",
    }
    return ForwardReturnEngineSpec(
        version_number,
        __version__,
        git_commit,
        hashlib.sha256(dependency_lock_path.read_bytes()).hexdigest(),
        schema_revision,
        sha256_hexdigest(configuration),
        capture_numerical_environment(),
    )


def publish_forward_return_engine(
    engine: Engine, spec: ForwardReturnEngineSpec
) -> ForwardReturnEnginePublication:
    with engine.begin() as connection:
        definition_id = _definition(connection)
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        result = service.publish(
            artifact_type="engine_version",
            artifact_key="forward_return_engine",
            version_number=spec.version_number,
            semantic_payload=asdict(spec),
            content_payload=asdict(spec),
            reason=f"publish forward_return_engine v{spec.version_number}",
            draft_writer=partial(_write_version, definition_id=definition_id, spec=spec),
        )
        engine_version_id = connection.execute(
            text("SELECT engine_version_id FROM ops.engine_version WHERE artifact_id = :id"),
            {"id": result.artifact_id},
        ).scalar_one()
    return ForwardReturnEnginePublication(
        cast(uuid.UUID, engine_version_id), result.artifact_id, result.reused
    )


def _definition(connection: Connection) -> uuid.UUID:
    row = (
        connection.execute(
            text(
                "SELECT engine_definition_id, name, engine_type FROM ops.engine_definition "
                "WHERE engine_key = 'forward_return_engine'"
            )
        )
        .mappings()
        .one_or_none()
    )
    name, engine_type = "Deterministic Forward Return Engine", "forward_return"
    if row is not None:
        if row["name"] != name or row["engine_type"] != engine_type:
            raise ValueError("Existing forward_return_engine has incompatible semantics")
        return cast(uuid.UUID, row["engine_definition_id"])
    definition_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO ops.engine_definition "
            "(engine_definition_id, engine_key, name, engine_type) "
            "VALUES (:id, 'forward_return_engine', :name, :type)"
        ),
        {"id": definition_id, "name": name, "type": engine_type},
    )
    return definition_id


def _write_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    definition_id: uuid.UUID,
    spec: ForwardReturnEngineSpec,
) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.engine_version (engine_version_id, engine_definition_id, "
            "artifact_id, version_number, semantic_version, git_commit, dependency_lock_hash, "
            "schema_revision, configuration_hash, numerical_environment) VALUES "
            "(:id, :definition, :artifact, :version, :semantic, :commit, :lock, :schema, "
            ":configuration, CAST(:environment AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "definition": definition_id,
            "artifact": artifact_id,
            "version": spec.version_number,
            "semantic": spec.semantic_version,
            "commit": spec.git_commit,
            "lock": spec.dependency_lock_hash,
            "schema": spec.schema_revision,
            "configuration": spec.configuration_hash,
            "environment": json.dumps(spec.numerical_environment, sort_keys=True),
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
