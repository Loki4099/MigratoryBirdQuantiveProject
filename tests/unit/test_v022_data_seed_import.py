from __future__ import annotations

import pytest

from style_rotation.core.canonical import sha256_hexdigest
from style_rotation.v022.data_seed_import import (
    ExternalImportManifestSpec,
    ExternalImportObjectSpec,
)


def _object(logical_key: str, *, role: str = "source_evidence") -> ExternalImportObjectSpec:
    return ExternalImportObjectSpec(
        object_role=role,
        logical_key=logical_key,
        media_type="text/csv",
        content_sha256="a" * 64,
        size_bytes=123,
        source_uri=f"content://sha256/{'a' * 64}",
        license_key="MIT",
        provenance_status="verified",
        usage_scope="redistributable",
        metadata={"source_repository": "fja05680/sp500"},
    )


def test_import_manifest_is_canonical_across_input_order() -> None:
    first = ExternalImportManifestSpec(
        manifest_key="sp500_seed_v1",
        version_number=1,
        source_project_key="momentum_reversion_method",
        source_release_key="2013warmup_2018eval_2026",
        objects=(_object("license", role="license"), _object("membership")),
        created_by="local",
    )
    second = ExternalImportManifestSpec(
        manifest_key=first.manifest_key,
        version_number=first.version_number,
        source_project_key=first.source_project_key,
        source_release_key=first.source_release_key,
        objects=tuple(reversed(first.objects)),
        created_by=first.created_by,
    )

    assert first.document() == second.document()
    assert sha256_hexdigest(first.document()) == sha256_hexdigest(second.document())
    assert [item["logical_key"] for item in first.document()["objects"]] == [
        "license",
        "membership",
    ]


@pytest.mark.parametrize(
    "source_uri,metadata",
    (
        (r"C:\\Users\\person\\seed.csv", {}),
        ("file:///C:/seed.csv", {}),
        ("content://sha256/" + "a" * 64, {"old_path": r"D:\\old\\seed.csv"}),
    ),
)
def test_import_object_rejects_workstation_paths(
    source_uri: str, metadata: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="non-filesystem URI|workstation path"):
        ExternalImportObjectSpec(
            object_role="source_evidence",
            logical_key="membership",
            media_type="text/csv",
            content_sha256="a" * 64,
            size_bytes=1,
            source_uri=source_uri,
            license_key="MIT",
            provenance_status="verified",
            usage_scope="redistributable",
            metadata=metadata,
        )


def test_import_manifest_requires_unique_logical_objects() -> None:
    with pytest.raises(ValueError, match="logical_key values must be unique"):
        ExternalImportManifestSpec(
            manifest_key="sp500_seed_v1",
            version_number=1,
            source_project_key="momentum_reversion_method",
            source_release_key="2013warmup_2018eval_2026",
            objects=(_object("membership"), _object("membership")),
            created_by="local",
        )
