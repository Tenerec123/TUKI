"""Provision the openWakeWord ONNX models used by the wake-word frontend.

The model binaries are runtime artifacts (they change as wake words evolve),
so they are NOT committed to the repo. ``ensure_wake_models`` is called from
the FastAPI lifespan at server startup; if a file is missing or its size does
not match the pinned manifest, it is downloaded. Existing files are skipped
(the check is a cheap ``stat()`` per file).

Sources: official openWakeWord GitHub release assets (v0.5.1), the same
upstream URLs the Python ``openwakeword`` package uses.
"""

from __future__ import annotations

import logging
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

RELEASE_BASE = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1"

# filename -> expected size in bytes. Sizes pin the exact upstream artifact so
# a partial/corrupt file is detected and re-downloaded instead of served.
MODELS: dict[str, int] = {
    # Shared feature frontends (required by every wake word)
    "melspectrogram.onnx": 1_087_958,
    "embedding_model.onnx": 1_326_578,
    # Voice activity detection for utterance capture
    "silero_vad.onnx": 1_807_522,
    # Wake word model(s)
    "alexa_v0.1.onnx": 854_246,
    # Optional additional wake words (uncomment to also fetch):
    # "hey_jarvis_v0.1.onnx": 1_271_370,
}


def models_dir() -> Path:
    """Default target directory: <repo>/frontend/models."""
    return Path(__file__).resolve().parent.parent / "frontend" / "models"


def missing_models(output_dir: Path | None = None) -> list[str]:
    """Return the manifest files that are absent or corrupt (size mismatch)."""
    target = output_dir or models_dir()
    missing = []
    for filename, expected in MODELS.items():
        path = target / filename
        if not path.exists() or path.stat().st_size != expected:
            missing.append(filename)
    return missing


def ensure_wake_models(output_dir: Path | None = None) -> None:
    """Download missing/corrupt wake word models into ``output_dir``.

    Skips files that already exist with the expected size. Logs, does not
    raise: a missing model must not take down the whole server (the wake UI
    is the only feature that needs it).
    """
    target = (output_dir or models_dir()).resolve()
    target.mkdir(parents=True, exist_ok=True)

    for filename in missing_models(target):
        url = f"{RELEASE_BASE}/{filename}"
        dest = target / filename
        temp = dest.with_suffix(dest.suffix + ".part")
        try:
            with urllib.request.urlopen(url) as resp, open(temp, "wb") as out:
                while chunk := resp.read(64 * 1024):
                    out.write(chunk)
            temp.replace(dest)
            logger.info("wake models: downloaded %s", filename)
        except Exception:
            logger.exception("wake models: FAILED to download %s", filename)
            temp.unlink(missing_ok=True)