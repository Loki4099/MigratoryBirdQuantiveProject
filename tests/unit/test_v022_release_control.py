from unittest.mock import patch

from style_rotation.v022.release_control import (
    LocalDevelopmentReleaseControlService,
    ReleaseControlService,
    ReleaseControlStatus,
)


def test_release_control_status_exposes_non_ambiguous_routing_flags() -> None:
    hidden = ReleaseControlStatus("hidden", 0, None)
    assert hidden.to_dict() == {
        "state": "hidden",
        "transition_sequence": 0,
        "transition_artifact_id": None,
        "default_contract": "v0.21",
        "maintenance_read_only": False,
        "shadow_runtime_allowed": False,
        "v021_research_creation_allowed": True,
        "v022_explicit_creation_allowed": False,
    }
    maintenance = ReleaseControlStatus("maintenance_read_only", 5, None)
    assert maintenance.maintenance_read_only is True
    assert maintenance.v021_research_creation_allowed is False
    assert maintenance.v022_explicit_creation_allowed is False
    default = ReleaseControlStatus("default", 3, None)
    assert default.default_contract == "v0.22"
    assert default.v021_research_creation_allowed is False
    assert default.v022_explicit_creation_allowed is True


def test_local_development_access_does_not_mutate_authoritative_release_state() -> None:
    service = object.__new__(LocalDevelopmentReleaseControlService)
    with patch.object(
        ReleaseControlService,
        "current",
        return_value=ReleaseControlStatus("hidden", 0, None),
    ):
        effective = service.current()
    assert effective.state == "explicit_eligible"
    assert effective.transition_sequence == 0
    assert effective.transition_artifact_id is None

    with patch.object(
        ReleaseControlService,
        "current",
        return_value=ReleaseControlStatus("maintenance_read_only", 4, None),
    ):
        maintenance = service.current()
    assert maintenance.state == "maintenance_read_only"
