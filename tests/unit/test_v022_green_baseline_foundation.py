from __future__ import annotations

from pathlib import Path

from style_rotation.v022 import green_baseline_foundation as subject
from tests.unit.test_v022_green_baseline_import import _root, _write_csv


def _spec(tmp_path: Path) -> subject.GreenBaselineFoundationSpec:
    root = _root(tmp_path)
    (root / "SHA256SUMS").write_text("transfer checksums\n", encoding="utf-8")
    return subject.GreenBaselineFoundationSpec(
        transfer_root=root,
        plan=subject.build_green_baseline_import_plan(root),
        created_by="unit-test",
    )


def test_external_manifest_is_path_free_and_content_addressed(tmp_path: Path) -> None:
    spec = _spec(tmp_path)

    manifest = subject._external_import_spec(spec)

    assert [item.logical_key for item in manifest.canonical_objects()] == [
        "SHA256SUMS",
        "manifest.jsonl",
        "package.json",
    ]
    assert all(item.source_uri.startswith("content:sha256/") for item in manifest.objects)
    assert all(str(spec.transfer_root) not in item.source_uri for item in manifest.objects)
    assert sum(item.metadata["transitively_addresses_payloads"] for item in manifest.objects) == 1


def test_foundation_scopes_stable_assets_and_security_identifiers(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write_csv(
        spec.transfer_root,
        "catalog.asset_identifier",
        [
            {
                "asset_identifier_id": "asset-identifier-a",
                "master_data_release_id": "old-release",
                "asset_id": "asset-a",
                "identifier_type": "internal_key",
                "identifier_value": "aaa",
                "valid_from": "",
                "valid_to": "",
            },
            {
                "asset_identifier_id": "asset-identifier-spy",
                "master_data_release_id": "old-release",
                "asset_id": "asset-spy",
                "identifier_type": "internal_key",
                "identifier_value": "spy",
                "valid_from": "",
                "valid_to": "",
            },
        ],
    )
    _write_csv(
        spec.transfer_root,
        "catalog.security_identifier",
        [
            {
                "security_identifier_id": "security-identifier-a",
                "security_id": "security-a",
                "identifier_type": "provider_symbol",
                "identifier_value": "AAA",
                "valid_from": "",
                "valid_to": "",
                "provider_scope": "yahoo_yfinance",
            },
            {
                "security_identifier_id": "security-identifier-spy",
                "security_id": "security-spy",
                "identifier_type": "provider_symbol",
                "identifier_value": "SPY",
                "valid_from": "",
                "valid_to": "",
                "provider_scope": "yahoo_yfinance",
            },
        ],
    )

    assets, asset_ids, securities, security_ids = subject._scoped_source_rows(spec)

    assert {item["asset_id"] for item in assets} == {"asset-a", "asset-spy"}
    assert len(asset_ids) == 2
    assert {item["security_id"] for item in securities} == {
        "security-a",
        "security-spy",
        "security-old",
    }
    assert len(security_ids) == 2


def test_calendar_uses_exact_transferred_sessions(tmp_path: Path) -> None:
    spec = _spec(tmp_path)
    _write_csv(
        spec.transfer_root,
        "catalog.calendar_session",
        [
            {
                "calendar_version_id": "calendar-old",
                "session_date": "2020-01-02",
                "open_at_utc": "2020-01-02T14:30:00+00:00",
                "close_at_utc": "2020-01-02T21:00:00+00:00",
                "is_early_close": "f",
            },
            {
                "calendar_version_id": "calendar-old",
                "session_date": "2020-01-03",
                "open_at_utc": "2020-01-03T14:30:00+00:00",
                "close_at_utc": "2020-01-03T21:00:00+00:00",
                "is_early_close": "f",
            },
        ],
    )

    calendar = subject._calendar(spec)

    assert calendar.calendar_key == "XNYS"
    assert [item.session_date.isoformat() for item in calendar.sessions] == [
        "2020-01-02",
        "2020-01-03",
    ]
