from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from style_rotation.catalog.versioning import semantic_version_number
from style_rotation.lineage.service import ArtifactService, DependencyInput, PublicationResult

CATALOG_FILES = (
    "factors.v0.2.0.json",
    "signals.v0.2.0.json",
    "models.v0.2.0.json",
    "strategies.v0.2.0.json",
)
CATALOG_DEPENDENCIES = {
    "factor": (),
    "signal": ("factor",),
    "model": ("signal",),
    "strategy": ("model", "signal"),
}


def publish_catalogs(service: ArtifactService, catalog_directory: Path) -> list[dict[str, Any]]:
    published: dict[str, PublicationResult] = {}
    output: list[dict[str, Any]] = []
    for filename in CATALOG_FILES:
        path = catalog_directory / filename
        if not path.is_file():
            raise ValueError(f"Missing catalog file: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        catalog_type = str(payload["catalog_type"])
        version = str(payload["catalog_version"])
        dependencies = tuple(
            DependencyInput(published[item].artifact_id, f"{item}_catalog", ordinal)
            for ordinal, item in enumerate(CATALOG_DEPENDENCIES[catalog_type])
        )
        result = service.publish(
            artifact_type="research_catalog",
            artifact_key=f"{catalog_type}_catalog",
            version_number=semantic_version_number(version),
            semantic_payload=payload,
            content_payload=payload,
            dependencies=dependencies,
            reason=f"bootstrap {catalog_type} catalog {version}",
        )
        published[catalog_type] = result
        serialized = asdict(result)
        serialized["artifact_id"] = str(result.artifact_id)
        output.append({"catalog_type": catalog_type, **serialized})
    return output
