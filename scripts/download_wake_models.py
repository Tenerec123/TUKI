"""Download the openWakeWord ONNX models used by the wake-word frontend.

Manual/dev companion to ``backend.wake_models`` (the FastAPI lifespan calls
the same logic automatically at server startup). Use this when you want to
provision the models without starting the server:

    python scripts/download_wake_models.py

Idempotent: files that already exist with the expected size are skipped.
``--output`` overrides the target directory (useful for testing). Exit code
is non-zero if any download fails.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.wake_models import ensure_wake_models, missing_models  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_out = Path(__file__).resolve().parent.parent / "frontend" / "models"
    parser.add_argument(
        "--output",
        type=Path,
        default=default_out,
        help="target directory (default: frontend/models next to the repo)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ensure_wake_models(args.output)
    return 1 if missing_models(args.output) else 0


if __name__ == "__main__":
    sys.exit(main())