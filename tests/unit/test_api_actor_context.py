from __future__ import annotations

import pytest

from style_rotation.api.actor_context import (
    ActorClaimMismatch,
    ActorRoleDenied,
    TrustedLocalActorContext,
    require_actor_claim,
    require_actor_role,
)


def test_trusted_local_actor_has_server_configured_roles() -> None:
    context = TrustedLocalActorContext(actor_key="local_researcher", operator_enabled=True)
    actor = context.current()
    assert actor.actor_key == "local_researcher"
    assert actor.roles == frozenset({"researcher", "operator"})
    assert require_actor_claim(context, "local_researcher") == "local_researcher"
    assert (
        require_actor_claim(context, "local_researcher", required_role="operator")
        == "local_researcher"
    )
    assert require_actor_role(context, required_role="operator") == "local_researcher"


def test_actor_claim_and_role_checks_fail_before_business_operation() -> None:
    context = TrustedLocalActorContext(actor_key="local", operator_enabled=False)
    with pytest.raises(ActorClaimMismatch):
        require_actor_claim(context, "spoofed")
    with pytest.raises(ActorRoleDenied):
        require_actor_claim(context, "local", required_role="operator")
    with pytest.raises(ActorRoleDenied):
        require_actor_role(context, required_role="operator")
