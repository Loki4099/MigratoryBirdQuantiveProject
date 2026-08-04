from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from style_rotation.api.app import create_app
from style_rotation.api.query import ArtifactQueryService
from style_rotation.catalog.bootstrap import publish_catalogs
from style_rotation.lineage.service import ArtifactService
from style_rotation.persistence.database import reset_database
from style_rotation.persistence.session import create_postgres_engine

DATABASE_URL = os.getenv("STYLE_ROTATION_TEST_DATABASE_URL")
PROJECT_ROOT = Path(__file__).parents[2]


@pytest.mark.integration
@pytest.mark.skipif(not DATABASE_URL, reason="STYLE_ROTATION_TEST_DATABASE_URL is not set")
def test_real_catalogs_flow_through_readonly_api_and_built_frontend() -> None:
    assert DATABASE_URL is not None
    reset_database(DATABASE_URL, "style_rotation_test", "test")
    engine = create_postgres_engine(DATABASE_URL)
    results = publish_catalogs(ArtifactService(engine), PROJECT_ROOT / "v0.2" / "catalogs")
    strategy_id = next(
        item["artifact_id"] for item in results if item["catalog_type"] == "strategy"
    )
    client = TestClient(
        create_app(
            ArtifactQueryService(engine),
            static_directory=PROJECT_ROOT / "frontend" / "dist",
        )
    )

    health = client.get("/api/v2/health")
    assert health.status_code == 200
    assert health.json()["database_revision"] == "20260804_18_v02_strategy_target"

    artifacts = client.get("/api/v2/artifacts?status=published&limit=100")
    assert artifacts.status_code == 200
    assert artifacts.json()["total"] == 5
    assert {item["artifact_key"] for item in artifacts.json()["items"]} == {
        "factor_catalog",
        "signal_catalog",
        "model_catalog",
        "strategy_catalog",
        "forward_return_catalog",
    }

    detail = client.get(f"/api/v2/artifacts/{strategy_id}")
    assert detail.status_code == 200
    assert len(detail.json()["direct_dependencies"]) == 2

    lineage = client.get(f"/api/v2/artifacts/{strategy_id}/lineage")
    assert lineage.status_code == 200
    assert len(lineage.json()["artifacts"]) == 4
    assert len(lineage.json()["dependencies"]) == 4

    index = client.get("/")
    assert index.status_code == 200
    assert '<div id="root"></div>' in index.text
    assert client.get("/factors?lang=en").status_code == 200
    assert client.get("/api/v2/not-a-route").status_code == 404
