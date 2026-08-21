from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from sqlalchemy import create_engine

from style_rotation.v022.green_baseline_gate import (
    GreenBaselineGateService,
    GreenBaselineGateSpec,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish clean-green Dataset Gate 5")
    parser.add_argument("output", type=Path)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--dataset-id", type=uuid.UUID, required=True)
    parser.add_argument("--quality-report-id", type=uuid.UUID, required=True)
    parser.add_argument("--universe-history-id", type=uuid.UUID, required=True)
    parser.add_argument("--weekly-cohort-id", type=uuid.UUID, required=True)
    parser.add_argument("--monthly-cohort-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    publication = GreenBaselineGateService(create_engine(args.database_url)).publish(
        GreenBaselineGateSpec(
            dataset_publication_id=args.dataset_id,
            quality_report_id=args.quality_report_id,
            universe_history_id=args.universe_history_id,
            weekly_cohort_id=args.weekly_cohort_id,
            monthly_cohort_id=args.monthly_cohort_id,
        )
    )
    document = publication.to_dict()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(document, sort_keys=True))


if __name__ == "__main__":
    main()
