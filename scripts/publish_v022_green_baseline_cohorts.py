from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine

from style_rotation.v022.evaluation_cohort import (
    EvaluationCohortPublicationService,
    EvaluationCohortSpec,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish clean-green weekly/monthly Cohort 11")
    parser.add_argument("output", type=Path)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--universe-history-id", type=uuid.UUID, required=True)
    parser.add_argument("--risk-dataset-id", type=uuid.UUID, required=True)
    parser.add_argument("--benchmark-dataset-id", type=uuid.UUID, required=True)
    parser.add_argument("--quality-report-id", type=uuid.UUID, required=True)
    parser.add_argument("--calendar-version-id", type=uuid.UUID, required=True)
    args = parser.parse_args()
    service = EvaluationCohortPublicationService(create_engine(args.database_url))
    publications = {}
    for frequency in ("weekly", "monthly"):
        publications[frequency] = service.publish(
            EvaluationCohortSpec(
                cohort_key=f"sp500_free_research_2007_2026_{frequency}_v11",
                version_number=11,
                research_tier="rankable_research",
                frequency=frequency,
                universe_history_id=args.universe_history_id,
                dataset_publication_id=args.risk_dataset_id,
                benchmark_dataset_publication_id=args.benchmark_dataset_id,
                security_market_quality_report_id=args.quality_report_id,
                calendar_version_id=args.calendar_version_id,
                warmup_start=date(2004, 12, 31),
                evaluation_start=date(2007, 1, 3),
                evaluation_end=date(2026, 6, 30),
                cost_bps_per_side=Decimal("5"),
                created_by="codex-green-baseline-cohort11",
            )
        )
    document = {
        frequency: {
            **asdict(publication),
            "evaluation_cohort_version_id": str(
                publication.evaluation_cohort_version_id
            ),
            "artifact_id": str(publication.artifact_id),
        }
        for frequency, publication in publications.items()
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(document, sort_keys=True))


if __name__ == "__main__":
    main()
