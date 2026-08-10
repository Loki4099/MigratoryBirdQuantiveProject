from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, text

from style_rotation.core.canonical import sha256_hexdigest


class IdempotencyConflict(RuntimeError):
    pass


class CommandIdempotencyService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def execute(
        self,
        *,
        command_name: str,
        idempotency_key: uuid.UUID,
        request: dict[str, Any],
        operation: Callable[[], dict[str, Any]],
    ) -> dict[str, Any]:
        fingerprint = sha256_hexdigest(request)
        lock_key = f"command:{command_name}:{idempotency_key}"
        with self._engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"), {"key": lock_key}
            )
            try:
                existing = (
                    connection.execute(
                        text(
                            "SELECT request_fingerprint, response FROM ops.command_result "
                            "WHERE command_name = :name AND idempotency_key = :key"
                        ),
                        {"name": command_name, "key": idempotency_key},
                    )
                    .mappings()
                    .one_or_none()
                )
                if existing is not None:
                    if existing["request_fingerprint"] != fingerprint:
                        raise IdempotencyConflict(
                            "Idempotency key was already used for a different request"
                        )
                    if existing["response"] is None:
                        raise IdempotencyConflict(
                            "The command is already in progress or its outcome requires audit"
                        )
                    return dict(existing["response"])
                connection.commit()
                with connection.begin():
                    connection.execute(
                        text("""
                        INSERT INTO ops.command_result (
                            command_name, idempotency_key, request_fingerprint, response
                        ) VALUES (:name, :key, :fingerprint, NULL)
                    """),
                        {
                            "name": command_name,
                            "key": idempotency_key,
                            "fingerprint": fingerprint,
                        },
                    )
                try:
                    response = operation()
                except Exception:
                    with connection.begin():
                        connection.execute(
                            text(
                                "DELETE FROM ops.command_result WHERE command_name = :name "
                                "AND idempotency_key = :key AND response IS NULL"
                            ),
                            {"name": command_name, "key": idempotency_key},
                        )
                    raise
                with connection.begin():
                    connection.execute(
                        text(
                            "UPDATE ops.command_result SET response = CAST(:response AS jsonb) "
                            "WHERE command_name = :name AND idempotency_key = :key"
                        ),
                        {
                            "name": command_name,
                            "key": idempotency_key,
                            "response": json.dumps(response, sort_keys=True, default=str),
                        },
                    )
                return response
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": lock_key},
                )

    def pending_audit(self, *, limit: int = 100) -> tuple[dict[str, Any], ...]:
        """List commands whose business operation may have committed without a response."""

        if limit < 1 or limit > 1_000:
            raise ValueError("Pending idempotency audit limit must be between 1 and 1000")
        with self._engine.connect() as connection:
            rows = connection.execute(
                text("""
                    SELECT command_name, idempotency_key, request_fingerprint, created_at
                    FROM ops.command_result
                    WHERE response IS NULL
                    ORDER BY created_at, command_name, idempotency_key
                    LIMIT :limit
                """),
                {"limit": limit},
            ).mappings()
            return tuple(
                {
                    "command_name": row["command_name"],
                    "idempotency_key": str(row["idempotency_key"]),
                    "request_fingerprint": row["request_fingerprint"],
                    "created_at": row["created_at"],
                }
                for row in rows
            )

    def repair_response(
        self,
        *,
        command_name: str,
        idempotency_key: uuid.UUID,
        expected_request_fingerprint: str,
        response: dict[str, Any],
    ) -> dict[str, Any]:
        """Attach an operator-reconstructed response after business-state audit.

        This never re-runs the operation.  The exact request fingerprint and
        advisory lock prevent repairing the wrong command or racing a live call.
        """

        if not command_name.strip() or not response:
            raise ValueError("Repair requires a command name and non-empty response")
        lock_key = f"command:{command_name}:{idempotency_key}"
        with self._engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            # The session-level advisory lock survives commit; end SQLAlchemy's
            # implicit transaction before opening the row-locking repair block.
            connection.commit()
            try:
                with connection.begin():
                    row = (
                        connection.execute(
                            text("""
                                SELECT request_fingerprint, response
                                FROM ops.command_result
                                WHERE command_name = :name AND idempotency_key = :key
                                FOR UPDATE
                            """),
                            {"name": command_name, "key": idempotency_key},
                        )
                        .mappings()
                        .one_or_none()
                    )
                    if row is None:
                        raise LookupError("Pending idempotency command was not found")
                    if row["request_fingerprint"] != expected_request_fingerprint:
                        raise IdempotencyConflict("Request fingerprint confirmation does not match")
                    if row["response"] is not None:
                        return dict(row["response"])
                    connection.execute(
                        text("""
                            UPDATE ops.command_result
                            SET response = CAST(:response AS jsonb)
                            WHERE command_name = :name AND idempotency_key = :key
                              AND response IS NULL
                        """),
                        {
                            "name": command_name,
                            "key": idempotency_key,
                            "response": json.dumps(response, sort_keys=True, default=str),
                        },
                    )
                return response
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                    {"key": lock_key},
                )
