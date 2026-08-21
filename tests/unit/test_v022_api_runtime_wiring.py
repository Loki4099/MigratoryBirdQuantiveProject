from __future__ import annotations

from pathlib import Path
from typing import Any

from style_rotation.api import app as app_module


def test_production_graph_suite_commands_only_enqueues_identity(
    monkeypatch: Any, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class FakeService:
        def __init__(self, engine: object, **kwargs: object) -> None:
            captured["engine"] = engine
            captured["kwargs"] = kwargs

    engine = object()
    monkeypatch.setattr(app_module, "SuiteRuntimeCommandService", FakeService)
    monkeypatch.setattr(app_module, "GraphSuiteCommandsAdapter", lambda service: service)

    service = app_module._production_graph_suite_commands(
        engine, payload_directory=tmp_path
    )

    assert isinstance(service, FakeService)
    assert captured == {"engine": engine, "kwargs": {}}
