from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.domain.enums import FactorDirection
from style_rotation.factors.registry import DEFINITIONS, REGISTRY_HASH, VARIANTS


def test_frozen_registry_has_expected_definitions_and_variants() -> None:
    assert len(DEFINITIONS) == 11
    assert len(VARIANTS) == 24
    assert len({item.key for item in DEFINITIONS}) == 11
    assert len({item.key for item in VARIANTS}) == 24
    assert {item.key for item in VARIANTS} >= {
        "momentum_252",
        "skip_momentum_252_20",
        "atr_ratio_14",
        "volume_trend_20_60",
    }


def test_registry_hash_and_directions_are_deterministic() -> None:
    assert sha256_hexdigest({"definitions": DEFINITIONS, "variants": VARIANTS}) == REGISTRY_HASH
    directions = {item.key: item.direction for item in DEFINITIONS}
    assert directions["momentum"] is FactorDirection.HIGHER_IS_BETTER
    assert directions["historical_volatility"] is FactorDirection.LOWER_IS_BETTER
    assert directions["maximum_drawdown"] is FactorDirection.LOWER_IS_BETTER
