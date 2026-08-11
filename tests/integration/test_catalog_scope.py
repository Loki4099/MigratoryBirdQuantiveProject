from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from style_rotation.api.app import create_app
from style_rotation.api.query import ArtifactQueryService
from style_rotation.catalog.asset_registry import publish_asset_registry
from style_rotation.catalog.scope import publish_research_scope
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")


@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_scope_publication_is_atomic_reusable_and_immutable() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    postgres_engine: Engine = create_postgres_engine(DATABASE_URL)
    path = Path("v0.2/catalogs/research_scope.v0.2.0.json")
    first = publish_research_scope(postgres_engine, path)
    second = publish_research_scope(postgres_engine, path)

    assert [item["catalog_type"] for item in first] == [
        "master_data_release",
        "universe_version",
        "data_requirement_version",
    ]
    assert all(item["reused"] is False for item in first)
    assert all(item["reused"] is True for item in second)
    publish_asset_registry(postgres_engine, Path("v0.21/catalogs/assets.v0.21.1.json"))
    client = TestClient(create_app(ArtifactQueryService(postgres_engine)))
    asset_response = client.get("/api/v2/catalog/assets?search=IWF")
    requirement_response = client.get("/api/v2/catalog/data-requirements")
    assert asset_response.status_code == 200
    assert [item["symbol"] for item in asset_response.json()["items"]] == ["IWF"]
    assert requirement_response.status_code == 200
    assert len(requirement_response.json()["items"]) == 5
    with postgres_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM catalog.asset")).scalar_one() == 5
        assert (
            connection.execute(text("SELECT count(*) FROM catalog.universe_member")).scalar_one()
            == 5
        )
        assert (
            connection.execute(
                text("SELECT count(*) FROM catalog.data_requirement_member")
            ).scalar_one()
            == 5
        )
        asset_id = connection.execute(
            text("SELECT asset_id FROM catalog.asset WHERE asset_key = 'iwf'")
        ).scalar_one()
    with (
        pytest.raises(Exception, match="only change while their artifact is draft"),
        postgres_engine.begin() as connection,
    ):
        connection.execute(
            text("UPDATE catalog.asset SET name = 'changed' WHERE asset_id = :asset_id"),
            {"asset_id": asset_id},
        )
