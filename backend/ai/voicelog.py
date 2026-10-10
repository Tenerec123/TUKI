"""Consistent, low-noise logging for the real-time voice pipeline.

One ``[voice]`` tag, one block per turn, every duration in SECONDS so the
timeline reads top-down without unit conversion. Raw transport chatter from the
Deepgram sockets (frame dumps, Metadata, forwarded counts) is hidden unless
``VOICE_DEBUG=1`` is set, so the default output stays readable.
"""

import os

_TAG = "[voice]"
_OFF = {"", "0", "false", "no", "off"}


def debug_enabled() -> bool:
    """True when ``VOICE_DEBUG`` is set to a truthy value."""
    return os.environ.get("VOICE_DEBUG", "").strip().lower() not in _OFF


def log(message: str) -> None:
    """Always-on voice line (turn block, warnings, errors)."""
    print(f"{_TAG} {message}")


def debug(message: str, source: str = "") -> None:
    """Transport-level detail. No-op unless ``VOICE_DEBUG=1``."""
    if not debug_enabled():
        return
    prefix = f"dbg [{source}] " if source else "dbg "
    print(f"{_TAG} {prefix}{message}")
