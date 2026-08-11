from __future__ import annotations


def semantic_version_number(version: str) -> int:
    """Map x.y.z onto a positive, sortable integer used by artifact identity."""
    parts = version.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"Catalog version must be semantic x.y.z: {version}")
    major, minor, patch = (int(part) for part in parts)
    return major * 1_000_000 + minor * 1_000 + patch + 1
