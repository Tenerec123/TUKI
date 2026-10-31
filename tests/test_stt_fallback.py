import asyncio
import io
import json
import wave

import pytest
import sqlalchemy
from fastapi import HTTPException

# Application model import bootstraps tables. Keep this focused module hermetic.
_create_all = sqlalchemy.MetaData.create_all
sqlalchemy.MetaData.create_all = lambda self, bind=None, **kwargs: None
from backend.ai import stt
from backend.ai.stt import deepgram as deepgram_stt
from backend.routers import ai as ai_router
sqlalchemy.MetaData.create_all = _create_all


class _Provider:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = []

    async def transcribe(self, audio_data, **kwargs):
        self.calls.append((audio_data, kwargs))
        if self.error:
            raise self.error
        return self.result

    async def transcribe_stream(self, chunks, **kwargs):
        self.calls.append((chunks, kwargs))
        if self.error:
            raise self.error
        return self.result


class _FailBatchProvider(_Provider):
    async def transcribe(self, audio_data, **kwargs):
        self.calls.append((audio_data, kwargs))
        raise RuntimeError("down")


class _CollectStreamProvider(_Provider):
    async def transcribe_stream(self, chunks, **kwargs):
        audio = b"".join([chunk async for chunk in chunks])
        self.calls.append((audio, kwargs))
        return self.result


class _FailAfterFirstChunk(_Provider):
    def __init__(self, name, source_exhausted):
        super().__init__(name)
        self.source_exhausted = source_exhausted

    async def transcribe_stream(self, chunks, **kwargs):
        self.calls.append((chunks, kwargs))
        first = await chunks.__anext__()
        assert first == b"one"
        assert not self.source_exhausted[0]
        raise RuntimeError("down")


def test_default_prefers_deepgram_when_both_keys_exist(monkeypatch):
    monkeypatch.setattr(stt, "get_provider_key", lambda name: "key")
    monkeypatch.setattr(stt, "get_model_config_for", lambda usage: ["stt", "deepgram", "model", None])
    assert isinstance(stt.get_stt_provider(), stt.DeepgramSTTProvider)


def test_explicit_unconfigured_provider_is_not_selectable(monkeypatch):
    monkeypatch.setattr(stt, "get_provider_key", lambda name: (_ for _ in ()).throw(ValueError("missing")))
    with pytest.raises(ValueError, match="not configured"):
        stt.get_stt_provider("deepgram")


def test_batch_transcription_falls_back_to_other_configured_provider(monkeypatch):
    first = _Provider("deepgram", error=RuntimeError("down"))
    second = _Provider("openrouter", result="hello")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])
    assert asyncio.run(stt.transcribe_with_fallback(b"audio", provider_name="deepgram")) == "hello"
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_openrouter_first_failure_reuses_buffer_for_deepgram(monkeypatch):
    first = _FailBatchProvider("openrouter")
    second = _CollectStreamProvider("deepgram", result="hello")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": second, "openrouter": first}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])

    async def chunks():
        yield b"one"
        yield b"two"

    assert asyncio.run(stt.transcribe_stream_with_fallback(chunks(), provider_name="openrouter")) == "hello"
    assert second.calls[0][0] == b"onetwo"


# --- Red: Deepgram Error events and empty transcripts are failures (S10) -----


class _FakeWS:
    """Minimal Deepgram WebSocket stand-in: scripted events, then settle."""

    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        raise asyncio.TimeoutError

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _pcm_chunks():
    async def chunks():
        yield b"\x01\x02"
    return chunks()


def test_deepgram_error_event_is_a_failed_attempt_not_empty_success(monkeypatch):
    # Deepgram's streaming API answers malformed/unsupported audio with a JSON
    # error event. The provider must raise so the configured fallback is tried,
    # instead of logging and returning "" (which produced empty prompts).
    monkeypatch.setattr(deepgram_stt, "get_provider_key", lambda name: "key")
    monkeypatch.setattr(
        deepgram_stt, "get_model_config_for",
        lambda usage: ["stt", "deepgram", "nova-2", None],
    )
    ws = _FakeWS([
        json.dumps({
            "type": "Error",
            "variant": "InvalidRequestMessage",
            "description": "audio format not supported",
        }),
    ])
    monkeypatch.setattr(
        deepgram_stt.websockets, "connect",
        lambda url, additional_headers=None: ws,
    )
    provider = deepgram_stt.DeepgramSTTProvider()
    with pytest.raises(RuntimeError, match="audio format not supported"):
        asyncio.run(provider.transcribe_stream(_pcm_chunks()))


def test_deepgram_error_without_transcript_raises_message(monkeypatch):
    # Same contract via the batch entry point.
    monkeypatch.setattr(deepgram_stt, "get_provider_key", lambda name: "key")
    monkeypatch.setattr(
        deepgram_stt, "get_model_config_for",
        lambda usage: ["stt", "deepgram", "nova-2", None],
    )
    ws = _FakeWS([json.dumps({"type": "Error", "description": "bad audio"})])
    monkeypatch.setattr(
        deepgram_stt.websockets, "connect",
        lambda url, additional_headers=None: ws,
    )
    provider = deepgram_stt.DeepgramSTTProvider()
    with pytest.raises(RuntimeError, match="bad audio"):
        asyncio.run(provider.transcribe(b"ogg-bytes", content_type="audio/ogg"))


def test_deepgram_transcript_after_error_is_kept(monkeypatch):
    # An error event must not discard speech Deepgram already recognized.
    monkeypatch.setattr(deepgram_stt, "get_provider_key", lambda name: "key")
    monkeypatch.setattr(
        deepgram_stt, "get_model_config_for",
        lambda usage: ["stt", "deepgram", "nova-2", None],
    )
    result = {
        "type": "Results",
        "is_final": True,
        "channel": {
            "alternatives": [{"transcript": "hola"}],
        },
    }
    ws = _FakeWS([
        json.dumps({"type": "Error", "description": "late warning"}),
        json.dumps(result),
    ])
    monkeypatch.setattr(
        deepgram_stt.websockets, "connect",
        lambda url, additional_headers=None: ws,
    )
    provider = deepgram_stt.DeepgramSTTProvider()
    assert asyncio.run(provider.transcribe_stream(_pcm_chunks())) == "hola"


def test_batch_empty_transcript_falls_back_to_other_provider(monkeypatch):
    first = _Provider("deepgram", result="")
    second = _Provider("openrouter", result="hola")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])
    assert asyncio.run(stt.transcribe_with_fallback(b"audio", provider_name="deepgram")) == "hola"
    assert len(first.calls) == 1 and len(second.calls) == 1


def test_batch_whitespace_only_transcript_falls_back(monkeypatch):
    first = _Provider("deepgram", result="   ")
    second = _Provider("openrouter", result="hola")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])
    assert asyncio.run(stt.transcribe_with_fallback(b"audio", provider_name="deepgram")) == "hola"


def test_stream_empty_transcript_falls_back_to_openrouter_wav(monkeypatch):
    first = _Provider("deepgram", result="")
    second = _Provider("openrouter", result="hola")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])

    async def chunks():
        yield b"one"
        yield b"two"

    assert asyncio.run(stt.transcribe_stream_with_fallback(chunks(), provider_name="deepgram")) == "hola"
    assert len(second.calls) == 1
    audio_data, kwargs = second.calls[0]
    assert kwargs["content_type"] == "audio/wav"
    with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
        assert wav_file.readframes(wav_file.getnframes()) == b"onetwo"


def test_batch_all_providers_empty_or_error_raises_clear_error(monkeypatch):
    first = _Provider("deepgram", result="   ")
    second = _Provider("openrouter", error=RuntimeError("quota exceeded"))
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])
    with pytest.raises(ValueError, match="deepgram, openrouter"):
        asyncio.run(stt.transcribe_with_fallback(b"audio", provider_name="deepgram"))


def test_stream_all_providers_empty_raises_clear_error(monkeypatch):
    first = _Provider("deepgram", result="")
    second = _Provider("openrouter", result="")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])

    async def chunks():
        yield b"one"

    with pytest.raises(ValueError, match="No STT provider produced a transcript"):
        asyncio.run(stt.transcribe_stream_with_fallback(chunks(), provider_name="deepgram"))


def test_batch_only_provider_error_keeps_underlying_error(monkeypatch):
    # One configured provider whose request fails must still surface its own
    # error (matching the previous contract); no empty-success path involved.
    first = _Provider("deepgram", error=RuntimeError("socket died"))
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: first)
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram"])
    with pytest.raises(RuntimeError, match="socket died"):
        asyncio.run(stt.transcribe_with_fallback(b"audio", provider_name="deepgram"))


# --- Chat STT router: 502 instead of an empty/silent success (S10) -----------


class _FakeUpload:
    def __init__(self, content_type):
        self.content_type = content_type

    async def read(self):
        return b"ogg-bytes"


def test_chat_stt_router_normalizes_browser_mime_and_returns_text(monkeypatch):
    seen = {}

    async def fake_transcribe(audio, **kwargs):
        seen["kwargs"] = kwargs
        return "hola"

    monkeypatch.setattr(ai_router, "transcribe_with_fallback", fake_transcribe)
    # Chrome sends "audio/ogg; codecs=opus" for MediaRecorder blobs.
    result = asyncio.run(ai_router.stt_conversion(_FakeUpload("audio/ogg; codecs=opus")))
    assert result == "hola"
    assert seen["kwargs"]["content_type"] == "audio/ogg"


def test_chat_stt_router_returns_502_when_every_provider_fails(monkeypatch):
    async def fail_transcribe(audio, **kwargs):
        raise ValueError("No STT provider produced a transcript (tried: deepgram, openrouter)")

    monkeypatch.setattr(ai_router, "transcribe_with_fallback", fail_transcribe)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(ai_router.stt_conversion(_FakeUpload("audio/ogg")))
    assert excinfo.value.status_code == 502
    assert "transcription failed" in str(excinfo.value.detail).lower()


def test_live_deepgram_receives_chunks_before_source_exhaustion_and_wav_fallback(monkeypatch):
    source_exhausted = [False]
    first = _FailAfterFirstChunk("deepgram", source_exhausted)
    second = _Provider("openrouter", result="hello")
    monkeypatch.setattr(stt, "get_stt_provider", lambda name: {"deepgram": first, "openrouter": second}[name])
    monkeypatch.setattr(stt, "get_available_stt_providers", lambda: ["deepgram", "openrouter"])

    async def chunks():
        yield b"one"
        yield b"two"
        source_exhausted[0] = True

    assert asyncio.run(stt.transcribe_stream_with_fallback(chunks(), provider_name="deepgram")) == "hello"
    audio_data, kwargs = second.calls[0]
    assert kwargs["content_type"] == "audio/wav"
    with wave.open(io.BytesIO(audio_data), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        assert wav_file.readframes(wav_file.getnframes()) == b"onetwo"
