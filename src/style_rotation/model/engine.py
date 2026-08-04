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

GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{7,64}$")


@dataclass(frozen=True, slots=True)
class ModelEngineSpec:
    version_number: int
    semantic_version: str
    git_commit: str
    dependency_lock_hash: str
    schema_revision: str
    configuration_hash: str
    numerical_environment: dict[str, Any]

    def __post_init__(self) -> None:
        if self.version_number < 1:
            raise ValueError("Model engine version must be positive")
        if not GIT_COMMIT_PATTERN.fullmatch(self.git_commit):
            raise ValueError("Model engine git commit must be a hexadecimal commit id")
        for label, value in (
            ("dependency lock", self.dependency_lock_hash),
            ("configuration", self.configuration_hash),
        ):
            if not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError(f"Model engine {label} hash must be SHA-256")


@dataclass(frozen=True, slots=True)
class ModelEnginePublication:
    engine_version_id: uuid.UUID
    artifact_id: uuid.UUID
    reused: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "engine_version_id": str(self.engine_version_id),
            "artifact_id": str(self.artifact_id),
            "reused": self.reused,
        }


def build_model_engine_spec(
    git_commit: str,
    dependency_lock_path: Path,
    schema_revision: str,
    *,
    version_number: int = 1,
) -> ModelEngineSpec:
    if not dependency_lock_path.is_file():
        raise ValueError(f"Dependency lock file not found: {dependency_lock_path}")
    configuration = {
        "calculator": "model-calculator-v1",
        "architecture": "two-level-dimension-signal",
        "weighted_mean": "positive-normalized-decimal-weights",
        "vote": "dimension-sign-then-normalized-vote-margin",
        "tie_output": "neutral-zero",
        "missing_policy": "common-warmup-then-require-complete-inputs",
        "score_encoding": "numeric-24-18-half-even",
        "direction": "sign-of-model-score",
        "confidence": "absolute-model-score",
        "reduction_order": "specification-dimension-component-asset-date",
    }
    return ModelEngineSpec(
        version_number,
        __version__,
        git_commit,
        hashlib.sha256(dependency_lock_path.read_bytes()).hexdigest(),
        schema_revision,
        sha256_hexdigest(configuration),
        capture_numerical_environment(),
    )


def publish_model_engine(engine: Engine, spec: ModelEngineSpec) -> ModelEnginePublication:
    semantic = asdict(spec)
    with engine.begin() as connection:
        definition_id = _ensure_definition(connection)
        service = ArtifactService(cast(Engine, _BoundConnection(connection)))
        result = service.publish(
            artifact_type="engine_version",
            artifact_key="model_engine",
            version_number=spec.version_number,
            semantic_payload=semantic,
            content_payload=semantic,
            reason=f"publish model_engine v{spec.version_number}",
            draft_writer=partial(_write_version, definition_id=definition_id, spec=spec),
        )
        engine_version_id = connection.execute(
            text("SELECT engine_version_id FROM ops.engine_version WHERE artifact_id = :id"),
            {"id": result.artifact_id},
        ).scalar_one()
    if not isinstance(engine_version_id, uuid.UUID):
        raise RuntimeError("Model engine version id must be a UUID")
    return ModelEnginePublication(engine_version_id, result.artifact_id, result.reused)


def _ensure_definition(connection: Connection) -> uuid.UUID:
    row = (
        connection.execute(
            text(
                "SELECT engine_definition_id, name, engine_type FROM ops.engine_definition "
                "WHERE engine_key = 'model_engine'"
            )
        )
        .mappings()
        .one_or_none()
    )
    name, engine_type = "Deterministic Model Engine", "model"
    if row is not None:
        if row["name"] != name or row["engine_type"] != engine_type:
            raise ValueError("Existing model_engine definition has incompatible semantics")
        definition_id = row["engine_definition_id"]
    else:
        definition_id = uuid.uuid4()
        connection.execute(
            text(
                "INSERT INTO ops.engine_definition "
                "(engine_definition_id, engine_key, name, engine_type) "
                "VALUES (:id, 'model_engine', :name, :engine_type)"
            ),
            {"id": definition_id, "name": name, "engine_type": engine_type},
        )
    if not isinstance(definition_id, uuid.UUID):
        raise RuntimeError("Model engine definition id must be a UUID")
    return definition_id


def _write_version(
    connection: Connection,
    artifact_id: uuid.UUID,
    *,
    definition_id: uuid.UUID,
    spec: ModelEngineSpec,
) -> None:
    connection.execute(
        text(
            "INSERT INTO ops.engine_version (engine_version_id, engine_definition_id, "
            "artifact_id, version_number, semantic_version, git_commit, dependency_lock_hash, "
            "schema_revision, configuration_hash, numerical_environment) VALUES "
            "(:id, :definition_id, :artifact_id, :version, :semantic_version, :git_commit, "
            ":lock_hash, :schema_revision, :configuration_hash, CAST(:environment AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "definition_id": definition_id,
            "artifact_id": artifact_id,
            "version": spec.version_number,
            "semantic_version": spec.semantic_version,
            "git_commit": spec.git_commit,
            "lock_hash": spec.dependency_lock_hash,
            "schema_revision": spec.schema_revision,
            "configuration_hash": spec.configuration_hash,
            "environment": json.dumps(spec.numerical_environment, sort_keys=True),
        },
    )


class _BoundConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def begin(self) -> nullcontext[Connection]:
        return nullcontext(self._connection)
