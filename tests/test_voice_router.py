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


def test_classify_time_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("time", 0.9))
    assert vr._classify("que hora es") == "time"


def test_classify_weather_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("weather", 0.99))
    assert vr._classify("que tiempo hace") == "weather"


def test_classify_other_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("other", 0.9))
    assert vr._classify("cuenta me un chiste") == "other"


def test_classify_accepts_index_form(monkeypatch):
    # Jev may answer with the criteria index instead of the key string.
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload(1, 0.9))
    assert vr._classify("que tiempo hace") == "weather"


def test_classify_accepts_string_digit_index(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("0", 0.9))
    assert vr._classify("que hora es") == "time"


def test_classify_low_confidence_falls_back_to_other(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("time", 0.2))
    assert vr._classify("algo ambiguo") == "other"


def test_classify_missing_confidence_is_accepted(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, _intent_payload("time"))
    assert vr._classify("que hora es") == "time"


def test_classify_network_error_returns_other(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def boom(request, timeout=None):
        raise OSError("timed out")

    monkeypatch.setattr(vr.urllib.request, "urlopen", boom)
    assert vr._classify("que hora es") == "other"


def test_classify_missing_api_key_returns_other(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert vr._classify("que hora es") == "other"


def test_classify_malformed_response_returns_other(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    _patch_jev(monkeypatch, {"unexpected": "shape"})
    assert vr._classify("que hora es") == "other"


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
