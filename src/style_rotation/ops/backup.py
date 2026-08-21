from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import Engine, text
from sqlalchemy.engine import make_url

from style_rotation import __version__
from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.persistence.database import database_status
from style_rotation.persistence.session import create_postgres_engine


@dataclass(frozen=True, slots=True)
class BackupPublication:
    backup_record_id: uuid.UUID
    storage_reference: str
    dump_sha256: str
    byte_count: int
    status: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["backup_record_id"] = str(self.backup_record_id)
        return payload


class BackupService:
    def __init__(self, engine: Engine, database_url: str) -> None:
        self._engine = engine
        self._database_url = database_url
        self._url = make_url(database_url)

    def create(
        self,
        output_path: Path,
        *,
        git_commit: str,
        docker_service: str | None = None,
    ) -> BackupPublication:
        if not re.fullmatch(r"[0-9a-f]{7,64}", git_commit):
            raise ValueError("Backup requires an exact hexadecimal Git commit")
        if output_path.suffix != ".dump":
            raise ValueError("Backup output must use the .dump extension")
        status = database_status(self._database_url)
        if status.current_revision is None:
            raise ValueError("Cannot back up an unmigrated database")
        output_path = output_path.resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            command, environment = self._dump_command(docker_service)
            with temporary.open("wb") as dump_output:
                _run(command, environment=environment, output_stream=dump_output)
            _verify_custom_dump(temporary)
            checksum = _sha256(temporary)
            os.replace(temporary, output_path)
        finally:
            temporary.unlink(missing_ok=True)

        record_id = uuid.uuid4()
        now = datetime.now(UTC)
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ops.backup_record "
                    "(backup_record_id, system_version, schema_revision, git_commit, "
                    "dump_sha256, storage_reference, byte_count, status, verified_at) "
                    "VALUES (:id, :system, :schema, :commit, :checksum, :storage, :bytes, "
                    "'verified', :verified)"
                ),
                {
                    "id": record_id,
                    "system": __version__,
                    "schema": status.current_revision,
                    "commit": git_commit,
                    "checksum": checksum,
                    "storage": str(output_path),
                    "bytes": output_path.stat().st_size,
                    "verified": now,
                },
            )
        return BackupPublication(
            record_id, str(output_path), checksum, output_path.stat().st_size, "verified"
        )

    def restore_test(
        self,
        backup_record_id: uuid.UUID,
        *,
        docker_service: str,
        restored_database_verifier: Callable[[str], None] | None = None,
    ) -> BackupPublication:
        _validate_docker_service(docker_service)
        with self._engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM ops.backup_record WHERE backup_record_id = :id"),
                    {"id": backup_record_id},
                )
                .mappings()
                .one_or_none()
            )
        if row is None:
            raise ValueError(f"Backup record not found: {backup_record_id}")
        dump_path = Path(row["storage_reference"])
        _verify_custom_dump(dump_path, expected_checksum=str(row["dump_sha256"]))
        restore_database = f"style_rotation_restore_{backup_record_id.hex[:12]}"
        user = self._url.username or "style_rotation"
        password = self._url.password or ""
        compose = ["docker", "compose", "exec", "-T", docker_service]
        create = [
            *compose,
            "psql",
            "-U",
            user,
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'CREATE DATABASE "{restore_database}"',
        ]
        drop = [
            *compose,
            "psql",
            "-U",
            user,
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'DROP DATABASE IF EXISTS "{restore_database}" WITH (FORCE)',
        ]
        environment = {**os.environ, "PGPASSWORD": password}
        try:
            _run(create, environment=environment)
            with dump_path.open("rb") as dump_input:
                _run(
                    [
                        *compose,
                        "pg_restore",
                        "-U",
                        user,
                        "-d",
                        restore_database,
                        "--exit-on-error",
                        "--no-owner",
                        "--no-privileges",
                    ],
                    environment=environment,
                    input_stream=dump_input,
                )
            restored_url = self._url.set(database=restore_database)
            restored_database_url = restored_url.render_as_string(hide_password=False)
            _verify_restored_database(
                restored_database_url,
                expected_revision=str(row["schema_revision"]),
            )
            if restored_database_verifier is not None:
                restored_database_verifier(restored_database_url)
            now = datetime.now(UTC)
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ops.backup_record SET status = 'restore_tested', "
                        "restore_tested_at = :tested, failure_message = NULL "
                        "WHERE backup_record_id = :id"
                    ),
                    {"tested": now, "id": backup_record_id},
                )
        except Exception as error:
            with self._engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE ops.backup_record SET status = 'failed', "
                        "failure_message = :message WHERE backup_record_id = :id"
                    ),
                    {"message": str(error)[:2000], "id": backup_record_id},
                )
            raise
        finally:
            subprocess.run(drop, check=False, capture_output=True, env=environment)
        return BackupPublication(
            backup_record_id,
            str(dump_path),
            str(row["dump_sha256"]),
            int(row["byte_count"]),
            "restore_tested",
        )

    def _dump_command(self, docker_service: str | None) -> tuple[list[str], dict[str, str]]:
        user = self._url.username or "style_rotation"
        database = self._url.database
        if database is None:
            raise ValueError("Database URL must include a database name")
        environment = {**os.environ, "PGPASSWORD": self._url.password or ""}
        if docker_service is not None:
            _validate_docker_service(docker_service)
            return (
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    docker_service,
                    "pg_dump",
                    "-U",
                    user,
                    "--format=custom",
                    "--no-owner",
                    "--no-privileges",
                    "-d",
                    database,
                ],
                environment,
            )
        command = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--username",
            user,
            "--dbname",
            database,
        ]
        if self._url.host is not None:
            command.extend(("--host", self._url.host))
        if self._url.port is not None:
            command.extend(("--port", str(self._url.port)))
        return command, environment


def _verify_custom_dump(path: Path, expected_checksum: str | None = None) -> None:
    if not path.is_file() or path.stat().st_size <= 5:
        raise ValueError(f"Backup dump is missing or empty: {path}")
    with path.open("rb") as handle:
        if handle.read(5) != b"PGDMP":
            raise ValueError("Backup is not a PostgreSQL custom-format dump")
    if expected_checksum is not None and _sha256(path) != expected_checksum:
        raise ValueError("Backup checksum verification failed")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_docker_service(docker_service: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", docker_service):
        raise ValueError("Docker service name contains unsupported characters")


def _verify_restored_database(database_url: str, *, expected_revision: str) -> None:
    restored = database_status(database_url)
    if restored.current_revision != expected_revision:
        raise RuntimeError("Restored database schema revision does not match the backup")
    if restored.missing_schemas:
        raise RuntimeError(
            "Restored database is missing required schemas: " + ", ".join(restored.missing_schemas)
        )

    engine = create_postgres_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL search_path = pg_catalog"))
            observed_fingerprint = connection.scalar(
                text("SELECT strategy.v022_strategy_parameter_fingerprint(CAST(:value AS jsonb))"),
                {"value": "{}"},
            )
        if observed_fingerprint != sha256_hexdigest({}):
            raise RuntimeError(
                "Restored database canonical fingerprint probe returned the wrong value"
            )
    finally:
        engine.dispose()


def _run(
    command: list[str],
    *,
    environment: dict[str, str],
    input_stream: BinaryIO | None = None,
    output_stream: BinaryIO | None = None,
) -> None:
    completed = subprocess.run(
        command,
        check=False,
        stdin=input_stream,
        stdout=output_stream if output_stream is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    if completed.returncode != 0:
        message = completed.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(message or f"Command failed with exit code {completed.returncode}")
