from __future__ import annotations

import csv
import json
from pathlib import Path

from style_rotation.v022 import green_baseline_universe as subject


def _write(path: Path, name: str, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    target = path / "metadata" / f"{name}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_reconstructs_full_snapshots_from_change_events(tmp_path: Path) -> None:
    document = {
        "mappings": [
            {
                "source_symbol": "sec::A",
                "security_id": "00000000-0000-0000-0000-000000000001",
                "valid_from": None,
                "valid_to": None,
            },
            {
                "source_symbol": "sec::B",
                "security_id": "00000000-0000-0000-0000-000000000002",
                "valid_from": None,
                "valid_to": None,
            },
        ],
        "source_rows": [
            {"effective_session": "2020-01-02", "source_row_number": 2},
            {"effective_session": "2020-02-03", "source_row_number": 3},
        ],
    }
    _write(
        tmp_path,
        subject._LEDGER_TABLE,
        [
            "universe_membership_ledger_id",
            "universe_key",
            "version_number",
            "research_tier",
            "snapshot_count",
            "ledger_document",
            "created_at",
        ],
        [
            {
                "universe_membership_ledger_id": "ledger",
                "universe_key": "sp500_historical_free_research_v1",
                "version_number": "2",
                "research_tier": "rankable_research",
                "snapshot_count": "2",
                "ledger_document": json.dumps(document),
                "created_at": "2020-03-01T00:00:00+00:00",
            }
        ],
    )
    _write(
        tmp_path,
        subject._BATCH_TABLE,
        [
            "universe_change_batch_id",
            "universe_membership_ledger_id",
            "ordinal",
            "effective_session",
            "source_member_count",
            "evidence_status",
            "reason_code",
        ],
        [
            {
                "universe_change_batch_id": "batch-1",
                "universe_membership_ledger_id": "ledger",
                "ordinal": "0",
                "effective_session": "2020-01-02",
                "source_member_count": "1",
                "evidence_status": "confirmed",
                "reason_code": "source",
            },
            {
                "universe_change_batch_id": "batch-2",
                "universe_membership_ledger_id": "ledger",
                "ordinal": "1",
                "effective_session": "2020-02-03",
                "source_member_count": "1",
                "evidence_status": "confirmed",
                "reason_code": "source",
            },
        ],
    )
    _write(
        tmp_path,
        subject._EVENT_TABLE,
        [
            "universe_membership_event_id",
            "universe_change_batch_id",
            "ordinal",
            "event_type",
            "security_id",
            "source_symbol",
        ],
        [
            {
                "universe_membership_event_id": "e1",
                "universe_change_batch_id": "batch-1",
                "ordinal": "0",
                "event_type": "seed",
                "security_id": "00000000-0000-0000-0000-000000000001",
                "source_symbol": "sec::A",
            },
            {
                "universe_membership_event_id": "e2",
                "universe_change_batch_id": "batch-2",
                "ordinal": "0",
                "event_type": "add",
                "security_id": "00000000-0000-0000-0000-000000000002",
                "source_symbol": "sec::B",
            },
            {
                "universe_membership_event_id": "e3",
                "universe_change_batch_id": "batch-2",
                "ordinal": "1",
                "event_type": "remove",
                "security_id": "00000000-0000-0000-0000-000000000001",
                "source_symbol": "sec::A",
            },
        ],
    )

    snapshots, mappings, cutoff = subject._source_facts(tmp_path)

    assert snapshots[0].source_symbols == ("sec::A",)
    assert snapshots[1].source_symbols == ("sec::B",)
    assert len(mappings) == 2
    assert cutoff.isoformat() == "2020-03-01T00:00:00+00:00"
