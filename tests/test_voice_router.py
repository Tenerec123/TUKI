"""Tests for the post-STT fast-path router (Jev intent + prefabricated replies).

The router sits between STT and the LLM: a misclassification that reaches the
prefab path answers the WRONG question, and a raised exception kills the whole
voice turn. What must be pinned here is the response parsing (key vs index,
confidence floor) and the never-raise degradation to "other".
"""

import asyncio
import json
import sys
import types
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai import voice_router as vr


class _FakeJevResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_jev(monkeypatch, payload: dict):
    monkeypatch.setattr(
        vr.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeJevResponse(payload),
    )


def _intent_payload(choice, confidence=None) -> dict:
    answer = {"type": "choice", "choice": choice}
    if confidence is not None:
        answer["confidence"] = confidence
    return {"answers": {"intent": answer}}


# --- _classify: parsing ------------------------------------------------------
#
# These exercise the Jev network path, so they must use a transcript the local
# pre-classifier (_quick_intent) leaves alone -- otherwise it short-circuits
# before the request and the parsing assertions never run.

_JEVPATH_PHRASE = "cuenta me un chiste"


def test_classify_time_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("time", 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "time"


def test_classify_weather_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("weather", 0.99))
    assert vr._classify(_JEVPATH_PHRASE) == "weather"


def test_classify_other_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("other", 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "other"


def test_classify_accepts_index_form(monkeypatch):
    # Jev may answer with the criteria index instead of the key string.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload(1, 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "weather"


def test_classify_accepts_string_digit_index(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("0", 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "time"


def test_classify_low_confidence_falls_back_to_other(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("time", 0.2))
    assert vr._classify("algo ambiguo") == "other"


def test_classify_missing_confidence_is_accepted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("time"))
    assert vr._classify(_JEVPATH_PHRASE) == "time"


def test_classify_network_error_returns_other(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def boom(request, timeout=None):
        raise OSError("timed out")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    assert vr._classify(_JEVPATH_PHRASE) == "other"


def test_classify_missing_api_key_returns_other(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert vr._classify(_JEVPATH_PHRASE) == "other"


def test_classify_malformed_response_returns_other(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, {"unexpected": "shape"})
    assert vr._classify(_JEVPATH_PHRASE) == "other"


# --- _quick_intent: local pre-classifier -------------------------------------

_TIME_MATCH_PHRASES = [
    "que hora es",
    "que hora son",
    "me dices la hora",
    "dime la hora",
    "decime la hora",
    "la hora",
    "que hora tenes",
    "what time is it",
    "what's the time",
    "current time",
]

_WEATHER_MATCH_PHRASES = [
    "que tiempo hace",
    "como esta el tiempo",
    "que tiempo va a hacer",
    "el clima",
    "como esta el clima",
    "va a llover",
    "temperatura",
    "cuantos grados hace",
    "how's the weather",
    "the weather",
    "forecast",
]

_NON_MATCH_PHRASES = [
    "cuanto tiempo falta para fin de mes",
    "cuanto tiempo queda",
    "cuanto tiempo tarda",
    "no tengo tiempo",
    "me falta tiempo",
    "a que hora es la reunion",
    "que hora te viene mejor",
    "que hora es la reunion con Ana",
    "como hago una tarea",
    "ponme una tarea",
    "que tareas tengo",
]


@pytest.mark.parametrize("phrase", _TIME_MATCH_PHRASES)
def test_quick_intent_matches_time(phrase):
    assert vr._quick_intent(phrase) == "time"


@pytest.mark.parametrize("phrase", _WEATHER_MATCH_PHRASES)
def test_quick_intent_matches_weather(phrase):
    assert vr._quick_intent(phrase) == "weather"


@pytest.mark.parametrize("phrase", _NON_MATCH_PHRASES)
def test_quick_intent_returns_none_for_ambiguous(phrase):
    assert vr._quick_intent(phrase) is None


def test_quick_intent_accent_variants_both_match_time():
    # "qu\u00e9" (with accent) and "que" must classify identically.
    assert vr._quick_intent("qu\u00e9 hora es") == "time"
    assert vr._quick_intent("que hora es") == "time"


def test_quick_intent_weather_accent_variants_match():
    assert vr._quick_intent("qu\u00e9 tiempo hace") == "weather"
    assert vr._quick_intent("c\u00f3mo est\u00e1 el tiempo") == "weather"


def test_quick_intent_trailing_punctuation_still_matches():
    assert vr._quick_intent("  \u00bfQue hora es?  ") == "time"


def test_quick_intent_empty_returns_none():
    assert vr._quick_intent("") is None


# --- _classify: local short-circuit ------------------------------------------


def test_classify_short_circuits_before_network(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def boom(request, timeout=None):
        raise AssertionError("urlopen must NOT be called for a quick intent")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    info: dict = {}
    assert vr._classify("que hora es", info) == "time"
    assert info["intent"] == "time"
    assert info["confidence"] == 1.0


def test_classify_short_circuits_weather_before_network(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def boom(request, timeout=None):
        raise AssertionError("urlopen must NOT be called for a quick intent")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    info: dict = {}
    assert vr._classify("que tiempo hace", info) == "weather"
    assert info["intent"] == "weather"
    assert info["confidence"] == 1.0


def test_classify_calls_network_for_non_matching(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    called: list[bool] = []

    def fake(request, timeout=None):
        called.append(True)
        return _FakeJevResponse(_intent_payload("other", 0.9))

    monkeypatch.setattr(vr.urllib.request, "urlopen", fake)
    assert vr._classify("cuenta me un chiste") == "other"
    assert called, "a non-matching transcript must still reach Jev"


def test_classify_never_raises_on_network_error_non_matching(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def boom(request, timeout=None):
        raise OSError("timed out")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    info: dict = {}
    assert vr._classify("cuenta me un chiste", info) == "other"
    assert info["intent"] == "other"


# --- prefabs -----------------------------------------------------------------


def test_prefab_time_is_a_spoken_sentence():
    text = vr._prefab_time()
    assert isinstance(text, str) and text.strip()
    assert text.startswith("Son las")


def _stub_weather_module(monkeypatch, weather_fn):
    # tools.read imports DB/yfinance at module level; a stub keeps the test
    # hermetic and still exercises the lazy import inside _prefab_weather.
    stub = types.ModuleType("backend.ai.tools.read")
    stub.Weather = weather_fn
    monkeypatch.setitem(sys.modules, "backend.ai.tools.read", stub)


def test_prefab_weather_formats_the_dict(monkeypatch):
    _stub_weather_module(
        monkeypatch,
        lambda city=None: {
            "city": "Madrid", "temp_c": "21", "condition": "Sunny", "feels_like_c": "20",
        },
    )
    text = vr._prefab_weather()
    assert "Madrid" in text
    assert "21" in text
    assert "sunny" in text  # condition is lowercased for speech


def test_prefab_weather_error_dict_returns_spoken_error(monkeypatch):
    _stub_weather_module(monkeypatch, lambda city=None: {"error": "boom"})
    text = vr._prefab_weather()
    assert isinstance(text, str) and text.strip()
    assert "error" not in text.lower()  # spoken sentence, not the raw dict text


# --- route_fast_path ---------------------------------------------------------


def test_route_fast_path_time_returns_prefab(monkeypatch):
    monkeypatch.setattr(vr, "_classify", lambda transcript, info=None: "time")
    reply = asyncio.run(vr.route_fast_path("que hora es"))
    assert reply is not None
    assert reply.startswith("Son las")


def test_route_fast_path_weather_returns_prefab(monkeypatch):
    monkeypatch.setattr(vr, "_classify", lambda transcript, info=None: "weather")
    monkeypatch.setattr(vr, "_prefab_weather", lambda: "fake weather sentence")
    reply = asyncio.run(vr.route_fast_path("que tiempo hace"))
    assert reply == "fake weather sentence"


def test_route_fast_path_other_returns_none(monkeypatch):
    monkeypatch.setattr(vr, "_classify", lambda transcript, info=None: "other")
    assert asyncio.run(vr.route_fast_path("cuenta me un chiste")) is None


# --- empty transcript short-circuit in voice_agent ---------------------------


class _FakePCMStream:
    async def __anext__(self):
        raise StopAsyncIteration

    def __aiter__(self):
        return self


def test_empty_transcript_yields_end_and_no_audio(monkeypatch):
    from backend.ai import voice_agent as va

    async def fake_transcribe_stream(stream):
        return ""

    monkeypatch.setattr(
        va, "get_stt_provider",
        lambda name=None: type("P", (), {"transcribe_stream": staticmethod(fake_transcribe_stream)})(),
    )

    # The TTS socket now opens BEFORE STT, so the empty turn must close it.
    closed: list[bool] = []

    class _Session:
        async def start(self):
            pass

        async def close(self):
            closed.append(True)

    class _Provider:
        def session(self):
            return _Session()

    monkeypatch.setattr(va, "get_tts_provider", lambda: _Provider())

    async def collect():
        return [item async for item in va.voice_agent_logic(_FakePCMStream(), streaming_path=True)]

    res = asyncio.run(asyncio.wait_for(collect(), timeout=5.0))
    assert res == [{"type": "end"}]
    assert not any(isinstance(item, bytes) for item in res)
    assert closed, "the session opened before STT must be closed on an empty turn"


# --- _strip_wake_word (defense in depth) -------------------------------------


def test_strip_wake_word_removes_leading_token():
    from backend.ai.voice_agent import _strip_wake_word

    assert _strip_wake_word("alexa que hora es") == "que hora es"
    assert _strip_wake_word("Alexa, pon una tarea") == "pon una tarea"
    assert _strip_wake_word("rexa que tiempo hace") == "que tiempo hace"


def test_strip_wake_word_handles_punctuation_and_case():
    from backend.ai.voice_agent import _strip_wake_word

    assert _strip_wake_word("¡Alexa! sube el volumen") == "sube el volumen"
    assert _strip_wake_word("¿Rexa? apaga la luz") == "apaga la luz"


def test_strip_wake_word_leaves_other_text_untouched():
    from backend.ai.voice_agent import _strip_wake_word

    assert _strip_wake_word("pon una tarea para mañana") == "pon una tarea para mañana"
    # A wake word NOT at the start is part of the sentence, not a prefix.
    assert _strip_wake_word("llama a alexa por favor") == "llama a alexa por favor"


def test_strip_wake_word_only_token_becomes_empty():
    from backend.ai.voice_agent import _strip_wake_word

    assert _strip_wake_word("Alexa") == ""
    assert _strip_wake_word("  alexa  ") == ""
