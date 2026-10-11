"""Fast-path router: classify the transcript with Jev before waking the LLM.

Weather and time questions get a prefabricated sentence answered straight to
TTS; everything else falls through to the normal orchestrator path. Jev is a
"System One" decision model (typed questions, not chat), called via OpenRouter.
"""

import asyncio
import json
import os
import re
import unicodedata
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from . import voicelog

JEV_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"
INTENT_CONFIDENCE_THRESHOLD = 0.5
# Order matters: a response may carry the choice as an index into this list.
_INTENT_KEYS = ["time", "weather", "other"]

# Local pre-classifier patterns. They run on the FOLDED transcript (lowercase,
# accent-stripped, whitespace-collapsed) so "que" and "que" with an accent both
# match. Deliberately narrow: only clear CURRENT-time / CURRENT-weather phrasings
# qualify, because a wrong fast-path answers the WRONG question and kills the
# turn. Everything uncertain returns None and is left to Jev / the LLM.

# Time: prefer near-terminal phrasings so scheduling questions that merely start
# with "que hora ..." (e.g. "que hora es la reunion con Ana") do not match. The
# negative lookbehind drops "a que hora ..." (at what time), which is scheduling.
_TIME_RE = re.compile(
    r"(?:"
    r"(?:me\s+dices|dime|decime)\s+la\s+hora"
    r"|(?<!a\s)que\s+hora\s+(?:es|son)"
    r"|que\s+hora\s+tenes"
    r"|la\s+hora"
    r"|what'?s\s+the\s+time"
    r"|what\s+time\s+is\s+it"
    r"|current\s+time"
    r")\s*[.!?]*\s*$"
)
# Weather: "tiempo" only counts inside an explicit weather phrase ("que tiempo",
# "el/del tiempo"), never bare, so duration phrases ("cuanto tiempo falta") stay
# out. No terminal anchor: "que tiempo hace hoy" is still a weather question.
_WEATHER_RE = re.compile(
    r"(?:"
    r"que\s+tiempo\s+(?:hace|va\s+a\s+hacer)"
    r"|(?:el|del)\s+tiempo"
    r"|el\s+clima"
    r"|va\s+a\s+llover"
    r"|temperatura"
    r"|cuantos\s+grados\s+hace"
    r"|the\s+weather"
    r"|forecast"
    r")"
)


def _fold(text: str) -> str:
    """Lowercase, strip accents and collapse whitespace for pattern matching.

    NFKD decomposes accented letters into a base letter plus a combining mark;
    dropping the combining marks leaves a plain form, so a transcript typed with
    or without accents classifies identically. Curly apostrophes are normalized
    to straight ones so English contractions ("what's", "how's") match.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    normalized = stripped.lower()
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")
    return " ".join(normalized.split())


def _quick_intent(transcript: str) -> str | None:
    """Return "time", "weather" or None from a local, network-free match.

    Conservative by design: only unambiguous current-time / current-weather
    phrasings match. Anything else (durations, scheduling, tasks) returns None so
    Jev, and then the LLM, still get a chance to decide.
    """
    if not transcript:
        return None
    folded = _fold(transcript)
    if _TIME_RE.search(folded):
        return "time"
    if _WEATHER_RE.search(folded):
        return "weather"
    return None


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
    # Local short-circuit: an obvious time/weather question never needs the
    # ~0.7s Jev round-trip. The regex match is effectively certain, so report
    # full confidence for the turn log.
    quick = _quick_intent(transcript)
    if quick is not None:
        if info is not None:
            info["intent"] = quick
            info["confidence"] = 1.0
        voicelog.debug(f"quick={quick!r}", source="jev")
        return quick
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
