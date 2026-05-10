from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.notification import notify


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    result = notify(
        title="test notify archive",
        body="This is a local archive-only notification test.",
        level="info",
        channels=["archive"],
        metadata={"source": "scripts.test_notify"},
    )
    print(result)
    archive = result.get("results", {}).get("archive", {})
    return 0 if archive.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
