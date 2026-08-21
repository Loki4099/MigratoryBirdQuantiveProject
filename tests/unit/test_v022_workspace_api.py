from __future__ import annotations

from fastapi.testclient import TestClient

from style_rotation.api.app import create_app


class _Reader:
    def database_revision(self) -> str:
        return "20260810_55_v022_projection"


def test_graph_preview_endpoint_returns_backend_derived_selection_state() -> None:
    client = TestClient(create_app(_Reader()))  # type: ignore[arg-type]
    response = client.post(
        "/api/v2/workspace/graph-preview",
        json={
            "frequency": "weekly",
            "explicit_features": [
                {"feature_key": "return_continuation__w120", "stage_no": 3}
            ],
            "aggregation_family_keys": ["flat_equal_weight_mean"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["context"]["api_version"] == "v2"
    assert payload["quality"] == {
        "state": "warning",
        "codes": ["asset_context_required"],
    }
    assert payload["aggregation_inputs"] == ["return_continuation__w120"]
    assert payload["summary"]["explicit_count"] == 1
    assert payload["summary"]["required_count"] == 3


def test_graph_preview_reports_incomplete_review_as_warning() -> None:
    client = TestClient(create_app(_Reader()))  # type: ignore[arg-type]
    response = client.post("/api/v2/workspace/graph-preview", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["quality"]["state"] == "warning"
    assert set(payload["quality"]["codes"]) == {
        "asset_context_required",
        "input_count_rejected",
        "stage3_input_required",
    }
