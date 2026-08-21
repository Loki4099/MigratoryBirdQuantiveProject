from __future__ import annotations

import pytest

from style_rotation.v022.shadow_comparator import (
    ComparatorField,
    compare_projected_documents,
)

FIELDS = (
    ComparatorField("execution_date", ("legacy_date",), ("recommended_execution_date",)),
    ComparatorField("target_weights", ("targets", "weights"), ("target_weights",)),
)


def test_projection_comparator_matches_explicit_cross_version_paths() -> None:
    result = compare_projected_documents(
        FIELDS,
        {"legacy_date": "2026-08-11", "targets": {"weights": {"IWF": "1.0"}}},
        {"recommended_execution_date": "2026-08-11", "target_weights": {"IWF": "1.0"}},
    )

    assert result.matched is True
    assert result.comparison_document["different_fields"] == []
    assert result.comparison_document["missing_fields"] == []


def test_projection_comparator_reports_difference_without_explaining_it() -> None:
    result = compare_projected_documents(
        FIELDS,
        {"legacy_date": "2026-08-11", "targets": {"weights": {"IWF": "1.0"}}},
        {"recommended_execution_date": "2026-08-11", "target_weights": {"IWD": "1.0"}},
    )

    assert result.matched is False
    assert result.comparison_document["different_fields"] == ["target_weights"]
    assert "explanation_codes" not in result.comparison_document


def test_projection_comparator_fails_closed_on_missing_field() -> None:
    result = compare_projected_documents(
        FIELDS,
        {"legacy_date": "2026-08-11", "targets": {}},
        {"recommended_execution_date": "2026-08-11", "target_weights": {}},
    )

    assert result.matched is False
    assert result.comparison_document["missing_fields"] == ["target_weights"]


def test_projection_comparator_rejects_duplicate_keys() -> None:
    duplicate = (
        ComparatorField("date", ("a",), ("a",)),
        ComparatorField("date", ("b",), ("b",)),
    )
    with pytest.raises(ValueError, match="unique"):
        compare_projected_documents(duplicate, {"a": 1, "b": 2}, {"a": 1, "b": 2})
