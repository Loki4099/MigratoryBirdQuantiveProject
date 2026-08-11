from __future__ import annotations

import json
from pathlib import Path

from style_rotation.api.app import create_app


def main() -> None:
    output = Path(__file__).parents[1] / "openapi.v2.json"
    output.write_text(
        json.dumps(create_app().openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
