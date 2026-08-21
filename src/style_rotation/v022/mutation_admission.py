from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Engine

from style_rotation.v022.release_control import ReleaseControlService, ReleaseState

MutationScope = Literal[
    "v021_research",
    "v022_research",
    "product_operations",
    "suite_cancellation",
    "historical_export",
]


@dataclass(frozen=True, slots=True)
class MutationAdmissionDecision:
    scope: MutationScope
    release_state: ReleaseState
    allowed: bool
    reason_code: str | None


class MutationAdmissionDenied(RuntimeError):
    def __init__(self, decision: MutationAdmissionDecision) -> None:
        self.decision = decision
        super().__init__(
            f"Mutation scope {decision.scope} is blocked in v0.22 release state "
            f"{decision.release_state}: {decision.reason_code}"
        )


def decide_mutation_admission(
    release_state: ReleaseState, scope: MutationScope
) -> MutationAdmissionDecision:
    if scope == "historical_export":
        return MutationAdmissionDecision(scope, release_state, True, None)
    if release_state == "maintenance_read_only":
        return MutationAdmissionDecision(
            scope, release_state, False, "release_maintenance_read_only"
        )
    if scope == "v022_research" and release_state not in {
        "explicit_eligible",
        "default",
    }:
        return MutationAdmissionDecision(
            scope, release_state, False, "v022_explicit_creation_not_enabled"
        )
    if scope == "v021_research" and release_state == "default":
        return MutationAdmissionDecision(
            scope, release_state, False, "v021_research_creation_retired"
        )
    return MutationAdmissionDecision(scope, release_state, True, None)


class MutationAdmissionService:
    """Database-authoritative API mutation admission for release cutover states."""

    def __init__(
        self,
        engine: Engine,
        *,
        release_control: ReleaseControlService | None = None,
    ) -> None:
        self._release = release_control or ReleaseControlService(engine)

    def decide(self, scope: MutationScope) -> MutationAdmissionDecision:
        return decide_mutation_admission(self._release.current().state, scope)

    def require(self, scope: MutationScope) -> MutationAdmissionDecision:
        decision = self.decide(scope)
        if not decision.allowed:
            raise MutationAdmissionDenied(decision)
        return decision
