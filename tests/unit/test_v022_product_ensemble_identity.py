from __future__ import annotations

from typing import Any, cast

import pytest
from sqlalchemy.engine import RowMapping

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.product_identity import _configuration_ensemble_fingerprint


def _configuration(specification: dict[str, object], fingerprint: str) -> RowMapping:
    return cast(
        RowMapping,
        cast(
            Any,
            {
                "semantic_identity_document": {
                    "aggregation": {
                        "trainable_ensemble": {
                            "ensemble_fingerprint": fingerprint,
                            "specification": specification,
                        }
                    }
                }
            },
        ),
    )


def test_product_execution_freezes_exact_ensemble_fingerprint() -> None:
    specification = {
        "contract_version": "v0.22.0",
        "combination_policy": "equal_within_target_equal_across_targets_v1",
    }
    fingerprint = sha256_hexdigest(specification)

    assert _configuration_ensemble_fingerprint(
        _configuration(specification, fingerprint)
    ) == fingerprint

    assert _configuration_ensemble_fingerprint(
        cast(
            RowMapping,
            cast(Any, {"semantic_identity_document": {"aggregation": {}}}),
        )
    ) is None


def test_product_execution_rejects_drifted_ensemble_identity() -> None:
    with pytest.raises(ValueError, match="identity drifted"):
        _configuration_ensemble_fingerprint(
            _configuration({"contract_version": "v0.22.0"}, "0" * 64)
        )
