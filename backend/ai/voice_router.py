"""Fast-path router: classify the transcript with Jev before waking the LLM.

Weather and time questions get a prefabricated sentence answered straight to
TTS; everything else falls through to the normal orchestrator path. Jev is a
"System One" decision model (typed questions, not chat), called via OpenRouter.
"""

import asyncio
import json
import os
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from . import voicelog

JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
INTENT_CONFIDENCE_THRESHOLD = 0.5
# Order matters: a response may carry the choice as an index into this list.
_INTENT_KEYS = ["time", "weather", "other"]


def _build_questions() -> dict:
    return {
        "intent": {
            "type": "choice",
            "instructions": "What does the user want?",
            "criteria": {
                "time": "asks for the current time",
                "weather": "asks about the weather, temperature or forecast",
                "other": "anything else",
            },
        }
    }


def _classify(transcript: str, info: dict | None = None) -> str:
    """Return "time", "weather" or "other". Never raises: a router failure must
    degrade to the normal LLM path, not kill the turn.

    ``info``, when given, is filled with the RAW choice and confidence for the
    turn log (the return value is the post-threshold decision).
    """
    if info is not None:
        info.setdefault("intent", "other")
        info.setdefault("confidence", None)
    try:
        payload = {"model": JEV_MODEL, "state": transcript, "questions": _build_questions()}
        request = urllib.request.Request(
            JEV_ENDPOINT,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
        answer = data["answers"]["intent"]
        choice = answer.get("choice")
        confidence = answer.get("confidence")
        # Jev may answer with the criteria key OR its index; normalize to the key.
        if isinstance(choice, int) or (isinstance(choice, str) and choice.isdigit()):
            index = int(choice)
            choice = _INTENT_KEYS[index] if 0 <= index < len(_INTENT_KEYS) else "other"
        if info is not None:
            info["intent"] = choice
            info["confidence"] = confidence
        voicelog.debug(f"choice={choice!r} confidence={confidence}", source="jev")
        if choice in ("time", "weather") and (
            confidence is None or confidence >= INTENT_CONFIDENCE_THRESHOLD
        ):
            return choice
        return "other"
    except Exception as exc:
        voicelog.debug(f"classify failed: {exc}", source="jev")
        return "other"


def _prefab_time() -> str:
    tz = ZoneInfo(os.environ.get("TIMEZONE", "Europe/Madrid"))
    now = datetime.now(tz)
    return f"Son las {now.strftime('%H:%M')}."


def _prefab_weather() -> str:
    # Lazy import: tools.read pulls in DB/yfinance at import time.
    try:
        from .tools.read import Weather

        data = Weather()
        if not isinstance(data, dict) or "error" in data:
            return "Lo siento, no pude consultar el tiempo ahora mismo."
        return (
            f"En {data['city']} hay {data['temp_c']} grados y {data['condition'].lower()}, "
            f"con sensación de {data['feels_like_c']} grados."
        )
    except Exception:
        # A prefab failure must never silence the turn: degrade to the LLM path.
        return None


async def route_fast_path(transcript: str, info: dict | None = None) -> str | None:
    """Return a prefabricated reply for time/weather utterances, else None.

    ``info``, when given, receives ``{"intent", "confidence"}`` for the turn log.
    """
    intent = await asyncio.to_thread(_classify, transcript, info)
    if intent == "time":
        return _prefab_time()
    if intent == "weather":
        return await asyncio.to_thread(_prefab_weather)
    return None
