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

from backend.ai.voice import fast_path as vr


@pytest.fixture(autouse=True)
def _provider_key_is_configured(monkeypatch):
    monkeypatch.setattr(vr, "get_provider_key", lambda _provider: "test-key")


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
    _patch_jev(monkeypatch, _intent_payload("time", 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "time"


def test_classify_weather_key(monkeypatch):
    _patch_jev(monkeypatch, _intent_payload("weather", 0.99))
    assert vr._classify(_JEVPATH_PHRASE) == "weather"


def test_classify_other_key(monkeypatch):
    _patch_jev(monkeypatch, _intent_payload("other", 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "other"


def test_classify_accepts_index_form(monkeypatch):
    # Jev may answer with the criteria index instead of the key string.
    _patch_jev(monkeypatch, _intent_payload(1, 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "weather"


def test_classify_accepts_string_digit_index(monkeypatch):
    _patch_jev(monkeypatch, _intent_payload("0", 0.9))
    assert vr._classify(_JEVPATH_PHRASE) == "time"


def test_classify_low_confidence_falls_back_to_other(monkeypatch):
    _patch_jev(monkeypatch, _intent_payload("time", 0.2))
    assert vr._classify("algo ambiguo") == "other"


def test_classify_missing_confidence_is_accepted(monkeypatch):
    _patch_jev(monkeypatch, _intent_payload("time"))
    assert vr._classify(_JEVPATH_PHRASE) == "time"


def test_classify_network_error_returns_other(monkeypatch):

    def boom(request, timeout=None):
        raise OSError("timed out")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    assert vr._classify(_JEVPATH_PHRASE) == "other"


def test_classify_missing_api_key_returns_other(monkeypatch):
    def missing_key(_provider):
        raise ValueError("API key is not configured")

    monkeypatch.setattr(vr, "get_provider_key", missing_key)
    assert vr._classify(_JEVPATH_PHRASE) == "other"


def test_classify_malformed_response_returns_other(monkeypatch):
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

    def boom(request, timeout=None):
        raise AssertionError("urlopen must NOT be called for a quick intent")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    info: dict = {}
    assert vr._classify("que hora es", info) == "time"
    assert info["intent"] == "time"
    assert info["confidence"] == 1.0


def test_classify_short_circuits_weather_before_network(monkeypatch):

    def boom(request, timeout=None):
        raise AssertionError("urlopen must NOT be called for a quick intent")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    info: dict = {}
    assert vr._classify("que tiempo hace", info) == "weather"
    assert info["intent"] == "weather"
    assert info["confidence"] == 1.0


def test_classify_calls_network_for_non_matching(monkeypatch):
    called: list[bool] = []

    def fake(request, timeout=None):
        called.append(True)
        return _FakeJevResponse(_intent_payload("other", 0.9))

    monkeypatch.setattr(vr.urllib.request, "urlopen", fake)
    assert vr._classify("cuenta me un chiste") == "other"
    assert called, "a non-matching transcript must still reach Jev"


def test_classify_never_raises_on_network_error_non_matching(monkeypatch):

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


# S10: a transcription that returns "" is now a FAILED attempt (the wrapper
# raises so the alternate provider is tried and, ultimately, the wake client
# gets an error instead of an empty turn). The graceful end below still covers
# the wake-word-only turn: STT returns text, but stripping the wake word
# leaves nothing for the LLM.
def test_wake_word_only_transcript_yields_end_and_no_audio(monkeypatch):
    from backend.ai import stt as stt_pkg
    from backend.ai.voice import agent as va

    async def fake_transcribe_stream(stream, **kwargs):
        return "alexa"

    # Fully hermetic: patch provider availability too, or a real environment
    # key would send this test's audio to a live provider.
    monkeypatch.setattr(
        stt_pkg, "get_available_stt_providers", lambda: ["deepgram"]
    )
    monkeypatch.setattr(
        stt_pkg, "get_model_config_for",
        lambda usage: ["stt", "deepgram", "nova-2", None],
    )
    monkeypatch.setattr(
        stt_pkg, "get_stt_provider",
        lambda name: (_ for _ in ()).throw(AssertionError("no second provider")),
    )
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


def test_all_providers_empty_or_failed_reaches_wake_as_error(monkeypatch):
    # S10 wake path: when every provider fails or returns no text, the
    # ValueError propagates out of voice_agent_logic; the voice WebSocket
    # already converts it into {"type": "error", ...} for the client.
    from backend.ai import stt as stt_pkg
    from backend.ai.voice import agent as va

    async def fake_transcribe_stream(stream):
        return ""

    class _P:
        async def transcribe_stream(self, chunks, **kwargs):
            return ""

    def fake_get_stt_provider(name):
        class _Fake:
            async def transcribe(self, audio, **kwargs):
                return ""

            async def transcribe_stream(self, chunks, **kwargs):
                return ""

        return _Fake()

    monkeypatch.setattr(stt_pkg, "get_stt_provider", fake_get_stt_provider)
    monkeypatch.setattr(
        stt_pkg, "get_available_stt_providers",
        lambda: ["deepgram", "openrouter"],
    )
    monkeypatch.setattr(
        stt_pkg, "get_model_config_for",
        lambda usage: ["stt", "deepgram", "nova-2", None],
    )
    monkeypatch.setattr(va, "get_stt_provider", lambda name=None: _P())

    # The TTS socket opens before STT; a failing STT never consumes its audio.
    class _Session:
        async def start(self):
            pass

        async def close(self):
            pass

    class _Provider:
        def session(self):
            return _Session()

    monkeypatch.setattr(va, "get_tts_provider", lambda: _Provider())

    async def collect():
        return [item async for item in va.voice_agent_logic(_FakePCMStream(), streaming_path=True)]

    with pytest.raises(ValueError, match="No STT provider produced a transcript"):
        asyncio.run(asyncio.wait_for(collect(), timeout=5.0))


def test_stt_provider_error_tears_down_tts_connect_and_session_once(monkeypatch):
    """S11: an all-raise STT turn must not leak the TTS session opened before STT.

    The TTS connect task is created before STT is awaited. When every STT
    provider raises, that task must be cancelled and joined and the TTS
    session closed exactly once, then the original provider error must
    propagate unchanged to the voice WebSocket route's error frame.
    """
    import sqlalchemy

    # Import the agent hermetically: backend.models bootstraps its tables at
    # import time, so keep MetaData.create_all local and never touch PostgreSQL
    # from test collection.
    _create_all = sqlalchemy.MetaData.create_all
    sqlalchemy.MetaData.create_all = lambda self, bind=None, **_kwargs: None
    try:
        from backend.ai import stt as stt_pkg
        from backend.ai.voice import agent as va
    finally:
        sqlalchemy.MetaData.create_all = _create_all

    original_error = RuntimeError("deepgram stream down")

    class _FailingSTTProvider:
        async def transcribe_stream(self, chunks, **_kwargs):
            # One scheduling point first so the pre-STT TTS connect task is
            # already running when the provider fails, like a real network
            # error after the handshake began.
            await asyncio.sleep(0)
            raise original_error

        async def transcribe(self, audio, **_kwargs):
            raise original_error

    start_calls: list[bool] = []
    cancelled_calls: list[bool] = []
    close_calls: list[bool] = []

    class _FakeTTSConnection:
        async def start(self):
            start_calls.append(True)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled_calls.append(True)
                raise

        async def close(self):
            close_calls.append(True)

    class _FakeTTSProvider:
        def session(self):
            return _FakeTTSConnection()

    monkeypatch.setattr(stt_pkg, "get_available_stt_providers", lambda: ["deepgram"])
    monkeypatch.setattr(
        stt_pkg,
        "get_model_config_for",
        lambda usage: ["stt", "deepgram", "nova-2", None],
    )
    monkeypatch.setattr(va, "get_stt_provider", lambda name=None: _FailingSTTProvider())
    monkeypatch.setattr(va, "get_tts_provider", lambda: _FakeTTSProvider())

    async def collect():
        return [item async for item in va.voice_agent_logic(_FakePCMStream(), streaming_path=True)]

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(asyncio.wait_for(collect(), timeout=5.0))

    # The WebSocket route serializes whatever exception arrives, so it must be
    # the provider's own error, not an unwrapped substitute.
    assert excinfo.value is original_error
    assert start_calls == [True]
    assert cancelled_calls == [True], "the pending TTS connect task must be cancelled and joined"
    assert close_calls == [True], "session.close() must run exactly once"


def test_strip_wake_word_removes_leading_token():
    from backend.ai.voice.agent import _strip_wake_word

    assert _strip_wake_word("alexa que hora es") == "que hora es"
    assert _strip_wake_word("Alexa, pon una tarea") == "pon una tarea"
    assert _strip_wake_word("rexa que tiempo hace") == "que tiempo hace"


def test_strip_wake_word_handles_punctuation_and_case():
    from backend.ai.voice.agent import _strip_wake_word

    assert _strip_wake_word("¡Alexa! sube el volumen") == "sube el volumen"
    assert _strip_wake_word("¿Rexa? apaga la luz") == "apaga la luz"


def test_strip_wake_word_leaves_other_text_untouched():
    from backend.ai.voice.agent import _strip_wake_word

    assert _strip_wake_word("pon una tarea para mañana") == "pon una tarea para mañana"
    # A wake word NOT at the start is part of the sentence, not a prefix.
    assert _strip_wake_word("llama a alexa por favor") == "llama a alexa por favor"


def test_strip_wake_word_only_token_becomes_empty():
    from backend.ai.voice.agent import _strip_wake_word

    assert _strip_wake_word("Alexa") == ""
    assert _strip_wake_word("  alexa  ") == ""
