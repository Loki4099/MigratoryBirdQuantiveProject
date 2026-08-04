from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
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

    def factor_overview(self) -> dict[str, Any]:
        identifiers = [uuid.uuid4() for _ in range(7)]
        return {
            "diagnostic_artifact_id": identifiers[0],
            "factor_catalog_artifact_id": identifiers[1],
            "universe_artifact_id": identifiers[2],
            "data_bundle_artifact_id": identifiers[3],
            "eligibility_artifact_id": identifiers[4],
            "factor_engine_artifact_id": identifiers[5],
            "diagnostic_engine_artifact_id": identifiers[6],
            "coverage_start": date(2026, 1, 1),
            "coverage_end": date(2026, 1, 31),
            "dataset_count": 1,
            "asset_count": 5,
            "observation_count": 100,
            "pair_count": 0,
            "high_correlation_threshold": 0.85,
            "datasets": [
                {
                    "factor_dataset_artifact_id": uuid.uuid4(),
                    "factor_key": "total_return",
                    "measurement_family": "return",
                    "formula": "close[t] / close[t-window] - 1",
                    "output_unit": "ratio",
                    "variant_key": "total_return__w20",
                    "parameters": {"window": 20},
                    "preset_type": "canonical",
                    "coverage_start": date(2026, 1, 1),
                    "coverage_end": date(2026, 1, 31),
                    "row_count": 100,
                    "observation_count": 100,
                    "asset_count": 5,
                    "missing_count": 0,
                    "mean": 0.01,
                    "standard_deviation": 0.02,
                    "minimum": -0.04,
                    "p05": -0.03,
                    "p25": -0.01,
                    "median": 0.01,
                    "p75": 0.03,
                    "p95": 0.05,
                    "maximum": 0.06,
                    "zero_variance": False,
                }
            ],
            "correlations": [],
            "issues": [],
        }

    def signal_overview(self, frequency: str) -> dict[str, Any]:
        identifiers = [uuid.uuid4() for _ in range(8)]
        metric = {
            "window_key": "full",
            "window_start": date(2025, 1, 3),
            "window_end": date(2026, 1, 30),
            "period_count": 52,
            "valid_ic_count": 51,
            "undefined_ic_count": 1,
            "mean_rank_ic": 0.18,
            "median_rank_ic": 0.2,
            "positive_ic_ratio": 0.62,
            "information_ratio": 1.1,
            "mean_top_bottom_spread": 0.003,
            "event_rate": None,
            "event_asset_concentration": None,
            "non_neutral_rate": 1.0,
            "mean_top2_turnover": 0.22,
        }
        return {
            "evaluation_artifact_id": identifiers[0],
            "signal_catalog_artifact_id": identifiers[1],
            "universe_artifact_id": identifiers[2],
            "data_bundle_artifact_id": identifiers[3],
            "eligibility_artifact_id": identifiers[4],
            "signal_engine_artifact_id": identifiers[5],
            "evaluation_engine_artifact_id": identifiers[6],
            "forward_return_artifact_id": identifiers[7],
            "target_key": f"{frequency}_next_open_to_next_open",
            "frequency": frequency,
            "coverage_start": date(2025, 1, 3),
            "coverage_end": date(2026, 1, 30),
            "signal_count": 1,
            "common_period_count": 52,
            "pair_count": 0,
            "high_correlation_threshold": 0.85,
            "signals": [
                {
                    "signal_dataset_artifact_id": uuid.uuid4(),
                    "signal_key": "return_continuation__total_return__w252",
                    "template_key": "return_continuation",
                    "economic_family": "momentum",
                    "rationale_type": "academic",
                    "rationale": "Persistent relative performance may continue.",
                    "research_tier": "canonical",
                    "product_eligible": True,
                    "direction": "higher_is_better",
                    "normalization": "cross_sectional_centered_rank_-1_1",
                    "output_type": "continuous",
                    "factor_variant_key": "total_return__w252",
                    "full": metric,
                    "stability": [{**metric, "window_key": "year:2025"}],
                }
            ],
            "pairs": [],
            "issues": [
                {
                    "signal_key": "return_continuation__total_return__w252",
                    "severity": "warning",
                    "issue_code": "short_evaluation_sample",
                    "message": "Short sample",
                    "details": {"period_count": 52},
                }
            ],
        }


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


def test_factor_overview_reports_factor_properties_without_strategy_metrics() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/factors/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["quality"]["state"] == "ok"
    assert payload["datasets"][0]["variant_key"] == "total_return__w20"
    assert payload["datasets"][0]["median"] == 0.01
    assert "sharpe" not in response.text.lower()
    factor_domain = next(
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "factor"
    )
    assert factor_domain["availability"] == "available"


def test_signal_overview_is_frequency_explicit_and_keeps_strategy_metrics_out() -> None:
    client, _reader = _client()
    response = client.get("/api/v2/signals/overview?frequency=monthly")
    assert response.status_code == 200
    payload = response.json()
    assert payload["frequency"] == "monthly"
    assert payload["quality"] == {
        "state": "warning",
        "codes": ["signal.diagnostic_warning"],
    }
    assert payload["signals"][0]["full"]["mean_rank_ic"] == 0.18
    assert payload["signals"][0]["stability"][0]["window_key"] == "year:2025"
    assert "sharpe" not in response.text.lower()
    signal_domain = next(
        item
        for item in client.get("/api/v2/capabilities").json()["domains"]
        if item["key"] == "signal"
    )
    assert signal_domain["availability"] == "available"
    invalid = client.get("/api/v2/signals/overview?frequency=daily")
    assert invalid.status_code == 422


def test_committed_openapi_contract_matches_application() -> None:
    contract_path = Path(__file__).parents[2] / "v0.2" / "openapi.v2.json"
    committed = json.loads(contract_path.read_text(encoding="utf-8"))
    generated = create_app(FakeArtifactReader()).openapi()
    assert committed == generated
