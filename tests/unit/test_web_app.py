from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from fastapi.testclient import TestClient

from style_rotation.web.app import create_app


class FakeRepository:
    def options(self) -> dict[str, Any]:
        return {"frequencies": ["weekly", "monthly"]}

    def status(self) -> dict[str, Any]:
        return {"state": "healthy", "counts": {"publications": 288}}

    def leaderboard(
        self,
        *,
        frequency: str,
        strategy_template: str,
        cost_bps: Decimal,
        sort_metric: str,
        descending: bool,
    ) -> dict[str, Any]:
        return {
            "filters": [frequency, strategy_template, str(cost_bps), sort_metric, descending],
            "items": [],
        }

    def factor_detail(self, factor_variant_id: uuid.UUID) -> dict[str, Any]:
        return {"factor": {"factor_variant_id": factor_variant_id}}

    def compare(self, run_ids: Sequence[uuid.UUID], *, max_points: int) -> dict[str, Any]:
        return {"run_ids": list(run_ids), "max_points": max_points}


def test_read_only_api_and_pages_are_reachable() -> None:
    client = TestClient(create_app(FakeRepository()))
    identifier = uuid.uuid4()

    assert client.get("/api/v1/health").json()["mode"] == "read_only"
    assert client.get("/api/v1/status").json()["state"] == "healthy"
    assert client.get("/api/v1/leaderboard?frequency=monthly").status_code == 200
    assert client.get(f"/api/v1/factors/{identifier}").status_code == 200
    assert client.get(f"/api/v1/compare?run_ids={identifier}").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get(f"/factors/{identifier}").status_code == 200
    assert client.get("/compare").status_code == 200
