from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.factor.service import publish_factor_catalog
from style_rotation.lineage.service import ArtifactService
from style_rotation.model.service import publish_model_catalog
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine
from style_rotation.signal.service import publish_signal_catalog
from style_rotation.strategy.product_service import publish_strategy_product
from style_rotation.strategy.service import publish_strategy_catalog

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_complete_strategy_product_is_compatible_idempotent_and_immutable() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    root = PROJECT_ROOT / "v0.2" / "catalogs"
    publish_catalogs(ArtifactService(engine), root)
    scope = publish_research_scope(engine, root / "research_scope.v0.2.0.json")
    universe_id = uuid.UUID(
        next(item["artifact_id"] for item in scope if item["catalog_type"] == "universe_version")
    )
    publish_factor_catalog(engine, root / "factors.v0.2.0.json")
    publish_signal_catalog(engine, root / "signals.v0.2.0.json")
    models = publish_model_catalog(engine, root / "models.v0.2.0.json")
    strategies = publish_strategy_catalog(engine, root / "strategies.v0.2.0.json")

    arguments = (
        engine,
        strategies.release_artifact_id,
        models.release_artifact_id,
        universe_id,
        "dimension_equal_weight__momentum_trend",
        "top_k_equal_weight__k2",
        "weekly_last_common_session_close",
    )
    first = publish_strategy_product(*arguments)
    second = publish_strategy_product(*arguments)
    assert first.reused is False
    assert second.reused is True
    assert first.version_artifact_id == second.version_artifact_id

    with engine.connect() as connection:
        product = connection.execute(
            text(
                "SELECT specification.specification_key, variant.variant_key, "
                "schedule.frequency, execution.execution_price, definition.product_key "
                "FROM strategy.strategy_product_version product "
                "JOIN strategy.strategy_product_definition definition ON "
                "definition.strategy_product_definition_id = "
                "product.strategy_product_definition_id "
                "JOIN model.model_specification specification ON "
                "specification.model_specification_id = product.model_specification_id "
                "JOIN strategy.strategy_variant variant ON variant.strategy_variant_id = "
                "product.strategy_variant_id JOIN ops.rebalance_schedule_version schedule ON "
                "schedule.rebalance_schedule_version_id = "
                "product.rebalance_schedule_version_id JOIN ops.execution_policy_version "
                "execution ON execution.execution_policy_version_id = "
                "product.execution_policy_version_id"
            )
        ).one()
    assert product[:4] == (
        "dimension_equal_weight__momentum_trend",
        "top_k_equal_weight__k2",
        "weekly",
        "adjusted_open",
    )
    assert "us_style_rotation_core" in product[4]

    with pytest.raises(ValueError, match="not eligible"):
        publish_strategy_product(
            engine,
            strategies.release_artifact_id,
            models.release_artifact_id,
            universe_id,
            "single_signal__price_cross_above_ma__moving_average_ratio__s1_l50",
            "top_k_equal_weight__k2",
            "weekly_last_common_session_close",
        )

    with (
        pytest.raises(ProgrammingError, match="only change while their artifact is draft"),
        engine.begin() as connection,
    ):
        connection.execute(text("UPDATE strategy.strategy_product_version SET version_number = 2"))
