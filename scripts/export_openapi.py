from __future__ import annotations

import json
from pathlib import Path

from style_rotation.api.app import create_app


class _SchemaReader:
    def database_revision(self) -> None:
        return None


def main() -> None:
    root = Path(__file__).parents[1]
    contract = create_app(_SchemaReader()).openapi()  # type: ignore[arg-type]
    target = root / "v0.2" / "openapi.v2.json"
    target.write_text(
        json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
