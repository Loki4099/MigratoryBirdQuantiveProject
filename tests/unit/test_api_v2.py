from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from style_rotation.api.app import create_app


class FakeArtifactReader:
    def __init__(self) -> None:
        self.artifact_id = uuid.uuid4()
        self.row: dict[str, Any] = {
            "artifact_id": self.artifact_id,
            "artifact_type": "research_catalog",
            "artifact_key": "factor_catalog",
            "version_number": 2001,
            "status": "published",
            "semantic_fingerprint": "a" * 64,
            "content_hash": "b" * 64,
            "published_at": datetime(2026, 8, 2, tzinfo=UTC),
            "created_at": datetime(2026, 8, 2, tzinfo=UTC),
        }

    def database_revision(self) -> str:
        return "20260802_02_v02_lineage"

    def list_artifacts(
        self,
        *,
        statuses: list[str],
        artifact_type: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if self.row["status"] not in statuses:
            return [], 0
        if artifact_type and self.row["artifact_type"] != artifact_type:
            return [], 0
        return [self.row][offset : offset + limit], 1

    def artifact_detail(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        if artifact_id != self.artifact_id:
            raise LookupError("Artifact not found")
        return {
            "artifact": self.row,
            "direct_dependencies": [],
            "direct_dependents": [],
            "has_manifest": True,
        }

    def lineage_manifest(self, artifact_id: uuid.UUID) -> dict[str, Any]:
        if artifact_id != self.artifact_id:
            raise LookupError("Lineage manifest not found")
        return {
            "artifact": self.row,
            "manifest_hash": "c" * 64,
            "canonical_version": "canonical-json-v2",
            "manifest": {
                "root_artifact_id": str(artifact_id),
                "artifacts": [],
                "dependencies": [],
            },
            "created_at": datetime(2026, 8, 2, tzinfo=UTC),
        }

    def data_overview(self) -> dict[str, Any]:
        return {"sources": [], "datasets": [], "bundle": None, "eligibility": None}


def _client() -> tuple[TestClient, FakeArtifactReader]:
    reader = FakeArtifactReader()
    return TestClient(create_app(reader)), reader


def test_health_capabilities_and_openapi_are_v2_read_only() -> None:
    client, _reader = _client()
    health = client.get("/api/v2/health")
    assert health.status_code == 200
    assert health.json()["context"] == {
        "api_version": "v2",
        "system_version": "0.2.0",
        "read_only": True,
    }
    capabilities = client.get("/api/v2/capabilities").json()
    assert capabilities["languages"] == ["zh-CN", "en"]
    assert "tainted" in capabilities["interface_states"]

    openapi = client.get("/api/v2/openapi.json").json()
    assert openapi["info"]["version"] == "0.2.0"
    assert all(
        set(methods).issubset({"get", "parameters"}) for methods in openapi["paths"].values()
    )


def test_artifact_list_supports_quality_pagination_filters_and_etag() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/artifacts?status=published&limit=10")
    assert response.status_code == 200
    assert response.json()["quality"] == {"state": "ok", "codes": []}
    assert response.json()["items"][0]["quality"]["state"] == "ok"
    assert response.json()["total"] == 1
    etag = response.headers["etag"]

    cached = client.get(
        "/api/v2/artifacts?status=published&limit=10",
        headers={"If-None-Match": etag},
    )
    assert cached.status_code == 304
    assert cached.content == b""

    empty = client.get("/api/v2/artifacts?artifact_type=other")
    assert empty.status_code == 200
    assert empty.json()["items"] == []


def test_artifact_detail_lineage_and_errors_use_stable_contracts() -> None:
    client, reader = _client()
    detail = client.get(f"/api/v2/artifacts/{reader.artifact_id}")
    assert detail.status_code == 200
    assert detail.json()["lineage_url"].endswith("/lineage")

    lineage = client.get(f"/api/v2/artifacts/{reader.artifact_id}/lineage")
    assert lineage.status_code == 200
    assert lineage.json()["canonical_version"] == "canonical-json-v2"

    missing = client.get(f"/api/v2/artifacts/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["code"] == "not_found"

    invalid = client.get("/api/v2/artifacts?status=unknown")
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "invalid_request"

    assert client.post("/api/v2/artifacts").status_code == 405


def test_data_overview_reports_an_incomplete_published_chain() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/data/overview")
    assert response.status_code == 200
    assert response.json()["quality"] == {
        "state": "partial",
        "codes": ["data.incomplete_chain"],
    }
    assert response.json()["datasets"] == []
    assert "etag" in response.headers
    capabilities = client.get("/api/v2/capabilities").json()
    data_domain = next(item for item in capabilities["domains"] if item["key"] == "data")
    assert data_domain["availability"] == "available"


def test_committed_openapi_contract_matches_application() -> None:
    contract_path = Path(__file__).parents[2] / "v0.2" / "openapi.v2.json"
    committed = json.loads(contract_path.read_text(encoding="utf-8"))
    generated = create_app(FakeArtifactReader()).openapi()
    assert committed == generated
