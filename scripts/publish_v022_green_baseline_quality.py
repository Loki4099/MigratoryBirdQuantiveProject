from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

from sqlalchemy import create_engine

from style_rotation.v022.green_baseline_quality import (
    GreenBaselineQualitySpec,
    publish_green_baseline_quality,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish the source-exact clean-green market quality report"
    )
    parser.add_argument("dataset_publication_id", type=uuid.UUID)
    parser.add_argument("calendar_version_id", type=uuid.UUID)
    parser.add_argument("exclusion_policy", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--report-key", default="us_sp500_free_research_frozen_v5_baseline__quality"
    )
    args = parser.parse_args()
    publication = publish_green_baseline_quality(
        create_engine(args.database_url),
        GreenBaselineQualitySpec(
            dataset_publication_id=args.dataset_publication_id,
            calendar_version_id=args.calendar_version_id,
            exclusion_policy_path=args.exclusion_policy,
            report_key=args.report_key,
        ),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(publication.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(publication.to_dict(), sort_keys=True))


if __name__ == "__main__":
    main()
