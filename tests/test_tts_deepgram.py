"""Tests for the Deepgram TTS session's input handling.

The session talks to a real network service, so what is tested here is the part
that must be right BEFORE any bytes go out: the 2000-character ``Speak`` limit
and the word boundary. A wrong split is a 4xx (whole reply lost) or two words
welded into one (the text is billed correctly and pronounced wrong).
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai.tts.base import iter_wav_turns
from backend.ai.tts import deepgram as deepgram_module
from backend.ai.tts.deepgram import (
    FLUSH_LIMIT_PER_WINDOW,
    SPEAK_CHUNK_LIMIT,
    THROUGHPUT_LIMIT_CHARS_PER_MIN,
    DeepgramTTTSession,
    split_for_speak,
)
from backend.ai.tts.openrouter import OpenRouterTTTSession


def test_short_text_is_a_single_message():
    assert split_for_speak("Hola.") == ["Hola."]


def test_every_piece_respects_the_hard_limit():
    text = "Esta es una oracion deliberadamente larga. " * 70
    for piece in split_for_speak(text):
        assert len(piece) <= SPEAK_CHUNK_LIMIT, len(piece)


def test_split_never_loses_a_single_character():
    """Deepgram concatenates Speak messages, so a dropped space welds words."""
    text = "Esta es una oracion deliberadamente larga. " * 70
    assert "".join(split_for_speak(text)) == text


def test_split_cuts_on_a_word_boundary_when_one_is_near():
    text = "palabra " * 400
    pieces = split_for_speak(text)
    assert len(pieces) > 1
    for piece in pieces[:-1]:
        assert piece.endswith(" "), repr(piece[-10:])


def test_split_falls_back_to_a_hard_cut_when_there_is_no_space():
    text = "x" * 5000
    pieces = split_for_speak(text)
    assert "".join(pieces) == text
    assert all(len(p) <= SPEAK_CHUNK_LIMIT for p in pieces)


@pytest.mark.parametrize("size", [2000, 2001, 3999, 4000, 4001])
def test_boundary_sizes_do_not_loop_or_drop_text(size):
    text = ("a" * 50 + " ") * (size // 51 + 2)
    pieces = split_for_speak(text)
    assert "".join(pieces) == text
    assert all(len(p) <= SPEAK_CHUNK_LIMIT for p in pieces)


def test_documented_limits_match_the_spec():
    # Pinned so a dependency bump or a careless edit cannot silently change a
    # limit the provider is written against.
    assert SPEAK_CHUNK_LIMIT == 2000
    assert THROUGHPUT_LIMIT_CHARS_PER_MIN == 2400
    assert FLUSH_LIMIT_PER_WINDOW == 20


def test_each_session_reports_its_own_sample_rate():
    # Every turn is concatenated into one WAV, so the framing rate has to be the
    # one the session emits. It is read from the provider, not from a constant.
    assert DeepgramTTTSession().sample_rate == 24000
    assert OpenRouterTTTSession().sample_rate == 24000


# --- lifecycle guarantees ----------------------------------------------------
#
# These do not need the network. They pin the failure paths, which are the ones
# that cannot be exercised by the happy-path smoke test and that turn a bad
# moment into a HUNG REQUEST rather than a failed one.


# --- PCM streaming path -----------------------------------------------------
from backend.ai.tts.base import TTSTurnEnd


class FakePCMStream:
    async def __anext__(self):
        raise StopAsyncIteration
    def __aiter__(self):
        return self


class FakeStreamingSession:
    def __init__(self, audio_items):
        self._audio_items = audio_items
        self._sample_rate = 24000
    @property
    def sample_rate(self):
        return self._sample_rate
    async def start(self):
        pass
    async def push_text(self, text):
        pass
    async def flush(self):
        pass
    async def clear(self):
        pass
    async def audio(self):
        for item in self._audio_items:
            yield item
    async def close(self):
        pass


def _stub_streaming_pipeline(monkeypatch, va, audio_items, sample_rate=24000):
    """Stub every external dependency of the streaming path.

    The producer task must never reach a live LLM/DB call even if the event
    loop gets a chance to run it before the consumer cancels it.
    """
    async def fake_transcribe_stream(stream):
        return "hola"
    monkeypatch.setattr(va, "get_stt_provider", lambda name=None: type('P', (), {'transcribe_stream': staticmethod(fake_transcribe_stream)})())

    async def fake_openai_agent(**_kwargs):
        return
        yield  # unreachable: without a yield this would be a coroutine, not an async generator
    monkeypatch.setattr(va, "openai_agent", fake_openai_agent)

    class FakeTTSProvider:
        def session(self):
            session = FakeStreamingSession(audio_items)
            session._sample_rate = sample_rate
            return session
    monkeypatch.setattr(va, "get_tts_provider", lambda: FakeTTSProvider())


async def _collect_streaming(va):
    return [item async for item in va.voice_agent_logic(FakePCMStream(), streaming_path=True)]


def test_pcm_chunks_forwarded_unbuffered(monkeypatch):
    from backend.ai import voice_agent as va
    chunk = b"\x01\x00\x02\x00"
    _stub_streaming_pipeline(monkeypatch, va, [chunk, TTSTurnEnd(index=1, chars=4, audio_ms=10.0)])

    res = asyncio.run(_collect_streaming(va))
    # Exact wire contract: preamble dict, then the raw chunk bytes — markers
    # must never reach the wire.
    assert res == [{"type": "pcm_start", "sample_rate": 24000}, chunk]
    assert not any(isinstance(item, TTSTurnEnd) for item in res)


def test_pcm_start_carries_session_sample_rate(monkeypatch):
    from backend.ai import voice_agent as va
    _stub_streaming_pipeline(
        monkeypatch, va,
        [b"\x01\x00", TTSTurnEnd(index=1, chars=4, audio_ms=1.0)],
        sample_rate=48000,
    )

    res = asyncio.run(_collect_streaming(va))
    assert res[0] == {"type": "pcm_start", "sample_rate": 48000}


def test_pcm_marker_accounting_pins_turns_and_first_audio(monkeypatch):
    """Markers drive turns/p1_audio (C5); payload bytes are counted exactly."""
    from backend.ai import voice_agent as va
    chunk1 = b"\x01\x00" * 10
    chunk2 = b"\x02\x00" * 20
    _stub_streaming_pipeline(monkeypatch, va, [
        chunk1, TTSTurnEnd(index=1, chars=5, audio_ms=123.0),
        chunk2, TTSTurnEnd(index=2, chars=7, audio_ms=456.0),
    ])
    captured: list[dict] = []
    monkeypatch.setattr(
        va, "_print_voice_summary",
        lambda perf, stats, session: captured.append(dict(stats)),
    )

    res = asyncio.run(_collect_streaming(va))

    assert res == [{"type": "pcm_start", "sample_rate": 24000}, chunk1, chunk2]
    assert len(captured) == 1
    stats = captured[0]
    assert stats["turns"] == 2
    assert stats["p1_audio"] == 123.0  # the FIRST marker, not the last
    assert stats["bytes"] == len(chunk1) + len(chunk2)


def test_voice_ws_dispatches_frames_by_proto(monkeypatch):
    """?proto=2 prepends the JSON pcm_start; without it only binary WAV frames
    may go on the wire (the legacy contract)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.routers import ai as ai_router

    wav_payload = b"RIFF\x24\x00\x00\x00WAVEfmt"
    pcm_payload = b"\x01\x00\x02\x00"

    async def fake_logic(stream, streaming_path=False):
        if streaming_path:
            yield {"type": "pcm_start", "sample_rate": 24000}
            yield pcm_payload
        else:
            yield wav_payload

    monkeypatch.setattr(ai_router, "voice_agent_logic", fake_logic)
    app = FastAPI()
    app.include_router(ai_router.router)

    def drain(ws) -> list[dict]:
        frames = []
        while True:
            message = ws.receive()
            if message["type"] != "websocket.send":
                return frames  # websocket.close ends the read
            frames.append(message)

    with TestClient(app) as client:
        with client.websocket_connect("/api/ai/voice-agent-ws") as ws:
            legacy = drain(ws)
        assert legacy == [{"type": "websocket.send", "bytes": wav_payload}]

        with client.websocket_connect("/api/ai/voice-agent-ws?proto=2") as ws:
            streaming = drain(ws)
        assert streaming[0]["type"] == "websocket.send"
        assert json.loads(streaming[0]["text"]) == {"type": "pcm_start", "sample_rate": 24000}
        assert streaming[1] == {"type": "websocket.send", "bytes": pcm_payload}
        assert len(streaming) == 2


def test_close_without_start_terminates_the_consumer():
    """A failed connect must end the iterator, not hang it forever."""
    session = DeepgramTTTSession()

    async def scenario():
        await session.close()
        return [item async for item in session.audio()]

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=5.0)) == []


def test_close_is_idempotent_and_yields_no_duplicate_sentinels():
    session = DeepgramTTTSession()

    async def scenario():
        await session.close()
        await session.close()
        return [item async for item in session.audio()]

    # The second close must not append a second sentinel, which would be read
    # as an audio chunk and corrupt the next turn.
    assert asyncio.run(asyncio.wait_for(scenario(), timeout=5.0)) == []


def test_iter_wav_turns_ends_on_a_failed_start():
    """The full consumer path must terminate without a live connection."""
    session = DeepgramTTTSession()

    async def scenario():
        tasks = [asyncio.create_task(session.close())]
        payloads = [p async for p in iter_wav_turns(session)]
        await asyncio.gather(*tasks)
        return payloads

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=5.0)) == []


def test_fallback_close_without_start_terminates_the_consumer():
    session = OpenRouterTTTSession()

    async def scenario():
        await session.close()
        return [item async for item in session.audio()]

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=5.0)) == []


def test_flush_before_start_raises_instead_of_attribute_error():
    session = OpenRouterTTTSession()

    async def scenario():
        await session.push_text("hola")
        await session.flush()

    with pytest.raises(RuntimeError, match="before start"):
        asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))


def test_turn_marker_splits_turns_into_separate_wavs():
    """Marker handling is what the browser contract depends on."""
    from backend.ai.tts.base import TTSTurnEnd, wav_bytes

    class FakeSession:
        """Minimal session: audio() ends by returning, never by yielding None."""

        def __init__(self):
            self.items = [
                b"\x01\x00" * 100, TTSTurnEnd(index=1, chars=3, audio_ms=2.0),
                b"\x02\x00" * 200, TTSTurnEnd(index=2, chars=4, audio_ms=4.0),
            ]

        @property
        def sample_rate(self) -> int:
            return 24000

        async def audio(self):
            for item in self.items:
                yield item

    async def scenario():
        return [p async for p in iter_wav_turns(FakeSession())]

    payloads = asyncio.run(scenario())
    assert len(payloads) == 2, "each turn must be one self-contained WAV"
    for payload in payloads:
        assert payload[:4] == b"RIFF", "each frame needs its own WAV header"


# --- flush budget -------------------------------------------------------------
#
# Deepgram ignores Flush past its per-window count, so the provider holds the
# request instead of dropping it. The window is the provider's business alone:
# no other layer tracks it.


class _FakeWS:
    """Records the control messages the session would put on the wire."""

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    @property
    def types(self) -> list[str]:
        return [message["type"] for message in self.sent]


def _session_with_spent_budget() -> tuple[DeepgramTTTSession, _FakeWS]:
    ws = _FakeWS()
    session = DeepgramTTTSession()
    session._ws = ws
    session._flush_windows = [time.perf_counter()] * FLUSH_LIMIT_PER_WINDOW
    return session, ws


def test_a_flush_inside_a_spent_window_is_held_then_sent_on_its_own(monkeypatch):
    # The window is shortened so the held flush can be observed in real time.
    monkeypatch.setattr(deepgram_module, "_FLUSH_LIMIT_SECONDS", 0.05)
    session, ws = _session_with_spent_budget()

    async def scenario():
        await session.push_text("Uno.")
        await session.flush()
        held = ws.types
        await asyncio.sleep(0.15)
        return held, ws.types

    held, after = asyncio.run(asyncio.wait_for(scenario(), timeout=5.0))
    assert held == ["Speak"], "a spent window must hold the Flush, not send it"
    assert after[-1] == "Flush", "the held Flush must go out once the window rolls"
    assert session.flushes == 1


def test_a_held_flush_is_released_before_new_text_is_pushed():
    # Otherwise the Flush drags the following sentence into the previous turn.
    session, ws = _session_with_spent_budget()

    async def scenario():
        await session.push_text("Uno.")
        await session.flush()
        session._flush_windows.clear()
        await session.push_text("Dos.")
        return ws.types

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=5.0)) == [
        "Speak", "Flush", "Speak",
    ]


def test_clear_drops_a_held_flush():
    # Barge-in discards the buffer, so there is nothing left to flush.
    session, ws = _session_with_spent_budget()

    async def scenario():
        await session.push_text("Uno.")
        await session.flush()
        await session.clear()
        return ws.types

    assert asyncio.run(asyncio.wait_for(scenario(), timeout=5.0)) == ["Speak", "Clear"]
    assert not session._flush_pending
