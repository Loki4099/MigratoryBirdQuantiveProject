from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

ActorRole = Literal["researcher", "operator"]


@dataclass(frozen=True, slots=True)
class AuthenticatedActor:
    actor_key: str
    roles: frozenset[ActorRole]
    authentication_source: str

    def __post_init__(self) -> None:
        if not self.actor_key.strip() or not self.authentication_source.strip():
            raise ValueError("Authenticated actor key and source are required")
        if not self.roles:
            raise ValueError("Authenticated actor requires at least one role")


class ActorContext(Protocol):
    def current(self) -> AuthenticatedActor: ...


class TrustedLocalActorContext:
    """Server-configured identity for the loopback-only single-user API."""

    def __init__(self, *, actor_key: str, operator_enabled: bool) -> None:
        roles: frozenset[ActorRole] = frozenset(
            {"researcher", "operator"} if operator_enabled else {"researcher"}
        )
        self._actor = AuthenticatedActor(
            actor_key.strip(), roles, "trusted_local_server_configuration"
        )

    def current(self) -> AuthenticatedActor:
        return self._actor


class ActorClaimMismatch(PermissionError):
    def __init__(self, *, claimed_actor: str, authenticated_actor: str) -> None:
        self.claimed_actor = claimed_actor
        self.authenticated_actor = authenticated_actor
        super().__init__("Request actor claim does not match the authenticated actor context")


class ActorRoleDenied(PermissionError):
    def __init__(self, *, actor_key: str, required_role: ActorRole) -> None:
        self.actor_key = actor_key
        self.required_role = required_role
        super().__init__(f"Authenticated actor lacks required role: {required_role}")


def require_actor_claim(
    context: ActorContext | None,
    claimed_actor: str,
    *,
    required_role: ActorRole = "researcher",
) -> str:
    """Return the server actor, treating legacy request fields only as assertions."""

    if context is None:
        return claimed_actor
    actor = context.current()
    if claimed_actor != actor.actor_key:
        raise ActorClaimMismatch(
            claimed_actor=claimed_actor, authenticated_actor=actor.actor_key
        )
    return require_actor_role(context, required_role=required_role)


def require_actor_role(
    context: ActorContext,
    *,
    required_role: ActorRole,
) -> str:
    """Require a role when the caller has no legacy actor assertion field."""

    actor = context.current()
    if required_role not in actor.roles:
        raise ActorRoleDenied(actor_key=actor.actor_key, required_role=required_role)
    return actor.actor_key
