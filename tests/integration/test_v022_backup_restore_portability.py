from __future__ import annotations

import os
from pathlib import Path

import pytest

from style_rotation.ops.backup import BackupService
from style_rotation.persistence.database import database_status
from style_rotation.persistence.session import create_postgres_engine

pytestmark = pytest.mark.integration
DATABASE_URL = os.getenv("STYLE_ROTATION_BACKUP_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not DATABASE_URL, reason="STYLE_ROTATION_BACKUP_TEST_DATABASE_URL is not set"
)
def test_streamed_backup_restores_with_restricted_search_path(tmp_path: Path) -> None:
    assert DATABASE_URL is not None
    engine = create_postgres_engine(DATABASE_URL)
    service = BackupService(engine, DATABASE_URL)

    backup = service.create(
        tmp_path / "portable.dump",
        git_commit="abcdef0",
        docker_service="postgres",
    )
    observed_revisions: list[str | None] = []
    restored = service.restore_test(
        backup.backup_record_id,
        docker_service="postgres",
        restored_database_verifier=lambda restored_url: observed_revisions.append(
            database_status(restored_url).current_revision
        ),
    )

    assert backup.byte_count > 5
    assert restored.status == "restore_tested"
    assert observed_revisions == ["20260821_142_asset_export"]
    assert database_status(DATABASE_URL).current_revision == (
        "20260821_142_asset_export"
    )
