from __future__ import annotations

import pytest

from style_rotation.v022.comparison_identity import _classify_comparison


@pytest.mark.parametrize(
    (
        "left_context",
        "right_context",
        "left_configuration",
        "right_configuration",
        "changed",
        "expected",
    ),
    (
        ("context-a", "context-b", "config-a", "config-b", ("defense_package",), "incompatible"),
        ("context-a", "context-a", "config-a", "config-a", (), "replication"),
        ("context-a", "context-a", "config-a", "config-b", ("defense_package",), "controlled"),
        (
            "context-a",
            "context-a",
            "config-a",
            "config-b",
            ("strategy_selection", "defense_package"),
            "multi_axis",
        ),
        ("context-a", "context-a", "config-a", "config-b", (), "incompatible"),
    ),
)
def test_comparison_classification_is_derived_fail_closed(
    left_context: str,
    right_context: str,
    left_configuration: str,
    right_configuration: str,
    changed: tuple[str, ...],
    expected: str,
) -> None:
    assert (
        _classify_comparison(
            left_context_fingerprint=left_context,
            right_context_fingerprint=right_context,
            left_configuration_fingerprint=left_configuration,
            right_configuration_fingerprint=right_configuration,
            changed_dimensions=changed,
        )
        == expected
    )
