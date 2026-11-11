"""Tests for the LLM-vs-Jev race in the voice pipeline's producer.

The producer used to await the Jev router BEFORE starting the orchestrator,
so every non-time/weather turn paid Jev's round-trip before the LLM even
connected. The race starts the LLM stream immediately and lets Jev classify
in parallel. What must be pinned here is the resolution contract:

- no token is lost, duplicated or reordered across the replay seam;
- no tool side effect lands while the race is open (the stream is never
  stepped past a ``tool_call`` yield before the router has decided);
- a prefab win discards and CLOSES the LLM stream;
- a cap expiry or a router failure leaves the stream live and the turn on
  the LLM path.
"""

import asyncio
import contextlib
import sys
import time
from pathlib import Path
from typing import AsyncIterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _scripted_stream(tokens: list, delay: float, state: dict) -> AsyncIterator:
    """Build an async generator yielding ``tokens`` after a pause before each.

    ``state`` records the two lifecycle facts the race must control.
    ``executed`` flips True only when the consumer steps the generator PAST a
    ``tool_call`` yield: in the real agent, execution is one ``__anext__``
    away from that yield, so resuming it is the tool side effect. ``closed``
    flips True when the generator's finally runs (``aclose()`` or
    cancellation).
    """
    state.setdefault("executed", False)
    state.setdefault("closed", False)

    async def gen():
        try:
            for token in tokens:
                await asyncio.sleep(delay)
                yield token
                if isinstance(token, dict) and token.get("type") == "tool_call":
                    # Resuming past this yield is the tool execution step.
                    state["executed"] = True
        finally:
            state["closed"] = True

    return gen()


def _pending_route(result: "str | None | Exception", delay: float) -> asyncio.Task:
    """Create the router task: sleep, then return ``result`` (or raise it)."""

    async def route() -> "str | None":
        await asyncio.sleep(delay)
        if isinstance(result, Exception):
            raise result
        return result

    return asyncio.create_task(route())


def _text(content: str) -> dict:
    return {"type": "agent", "content": content}


# --- _race_llm_vs_jev: prefab wins --------------------------------------------


def test_race_prefab_wins_before_first_token():
    from backend.ai.voice import agent as va

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream([_text("hola")], delay=0.05, state=state)
        route = _pending_route("Son las diez.", delay=0.01)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        return buffered, quick, outcome, state

    buffered, quick, outcome, state = asyncio.run(scenario())
    assert outcome == "jev"
    assert quick == "Son las diez."
    assert buffered == []
    assert state["closed"] is True, "the discarded stream must be closed"
    assert state["executed"] is False


def test_race_other_while_token_in_flight_replays_in_order():
    from backend.ai.voice import agent as va

    tokens = [_text("uno"), _text("dos"), {"type": "finish", "content": ""}]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.05, state=state)
        route = _pending_route(None, delay=0.01)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        closed_after_race = state["closed"]
        replayed = [t async for t in va._replay_then_live(agent_iter, buffered)]
        return quick, outcome, closed_after_race, replayed

    quick, outcome, closed_after_race, replayed = asyncio.run(scenario())
    assert outcome == "jev"
    assert quick is None
    # The stream stays live: the in-flight token was drained (never
    # cancelled) and the tail continues after the replay.
    assert closed_after_race is False
    assert replayed == tokens


def test_race_holds_stream_at_tool_call_until_jev_says_other():
    from backend.ai.voice import agent as va

    tokens = [
        _text("voy a mirar"),
        {"type": "tool_call", "content": {"name": "GetAllTasks"}},
        {"type": "tool_result", "content": {"ok": True}},
        {"type": "finish", "content": ""},
    ]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.02, state=state)
        route = _pending_route(None, delay=0.10)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        executed_after_race = state["executed"]
        replayed = [t async for t in va._replay_then_live(agent_iter, buffered)]
        return buffered, quick, outcome, executed_after_race, replayed, state

    buffered, quick, outcome, executed_after_race, replayed, state = asyncio.run(scenario())
    assert outcome == "jev"
    assert quick is None
    # The buffer ends at the tool_call and the generator was suspended there:
    # one __anext__ away from running the tool.
    assert buffered == tokens[:2]
    assert executed_after_race is False, "the tool must not execute while the race is open"
    # Once the race is over, replaying and continuing runs the tool and the
    # whole tail in order.
    assert replayed == tokens
    assert state["executed"] is True


def test_race_tool_call_then_prefab_win_closes_stream_before_execution():
    from backend.ai.voice import agent as va

    tokens = [
        _text("voy a mirar"),
        {"type": "tool_call", "content": {"name": "GetAllTasks"}},
        {"type": "tool_result", "content": {"ok": True}},
        {"type": "finish", "content": ""},
    ]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.02, state=state)
        route = _pending_route("Son las diez.", delay=0.10)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        return buffered, quick, outcome, state

    buffered, quick, outcome, state = asyncio.run(scenario())
    assert outcome == "jev"
    assert quick == "Son las diez."
    assert buffered == tokens[:2]
    assert state["closed"] is True, "the prefab win must close the stream"
    assert state["executed"] is False, "the tool must never run on a discarded stream"


def test_race_cap_expiry_leaves_stream_live_and_route_detached():
    from backend.ai.voice import agent as va

    # 20 tokens at 0.02s pace the stream past the 0.2s cap without ending.
    tokens = [_text(f"parte {i}") for i in range(20)]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.02, state=state)
        route = _pending_route(None, delay=10.0)
        buffered, quick, outcome = await va._race_llm_vs_jev(
            agent_iter, route, cap_s=0.2
        )
        route_cancelled = route.cancelled()
        replayed = [t async for t in va._replay_then_live(agent_iter, buffered)]
        # Teardown: the detached router task must never outlive the test loop.
        route.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await route
        return buffered, quick, outcome, route_cancelled, replayed

    buffered, quick, outcome, route_cancelled, replayed = asyncio.run(scenario())
    assert outcome == "cap"
    assert quick is None
    assert route_cancelled is False, "the race must leave the route task running detached"
    # The buffer is an in-order prefix and nothing is lost or duplicated
    # across the seam: the replay plus the live tail is the whole stream.
    assert buffered, "some tokens should have been buffered before the cap"
    assert buffered == tokens[: len(buffered)]
    assert replayed == tokens


def test_race_llm_finishes_before_jev():
    from backend.ai.voice import agent as va

    tokens = [_text("respuesta corta"), {"type": "finish", "content": ""}]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.02, state=state)
        route = _pending_route(None, delay=0.5)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        route.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await route
        return buffered, quick, outcome

    buffered, quick, outcome = asyncio.run(scenario())
    assert outcome == "llm"
    assert quick is None
    assert buffered == tokens, "the buffer must hold the complete tail"


def test_race_llm_error_token_before_jev():
    from backend.ai.voice import agent as va

    # The agent yields the literal string 'ERROR_TOKEN' on failure; that
    # ends the stream and the race with it.
    tokens = [_text("partial"), "ERROR_TOKEN"]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.02, state=state)
        route = _pending_route(None, delay=0.5)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        route.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await route
        return buffered, quick, outcome

    buffered, quick, outcome = asyncio.run(scenario())
    assert outcome == "llm"
    assert quick is None
    assert buffered == tokens


def test_race_router_exception_degrades_to_other():
    from backend.ai.voice import agent as va

    tokens = [_text("uno"), _text("dos"), {"type": "finish", "content": ""}]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tokens, delay=0.05, state=state)
        route = _pending_route(ValueError("jev is down"), delay=0.01)
        buffered, quick, outcome = await va._race_llm_vs_jev(agent_iter, route)
        closed_after_race = state["closed"]
        replayed = [t async for t in va._replay_then_live(agent_iter, buffered)]
        return quick, outcome, closed_after_race, replayed

    quick, outcome, closed_after_race, replayed = asyncio.run(scenario())
    # A router failure must never kill the turn: same as "other", the LLM
    # stream stands and replays cleanly.
    assert outcome == "jev"
    assert quick is None
    assert closed_after_race is False
    assert replayed == tokens


# --- _replay_then_live --------------------------------------------------------


def test_replay_then_live_orders_buffer_before_tail():
    from backend.ai.voice import agent as va

    buffered = [
        _text("primero"),
        {"type": "tool_call", "content": {"name": "GetAllTasks"}},
    ]
    tail = [_text("despues"), {"type": "finish", "content": ""}]

    async def scenario():
        state: dict = {}
        agent_iter = _scripted_stream(tail, delay=0.01, state=state)
        return [t async for t in va._replay_then_live(agent_iter, buffered)]

    assert asyncio.run(scenario()) == [*buffered, *tail]


# --- integration through voice_agent_logic -----------------------------------


class _FakePCMStream:
    async def __anext__(self):
        raise StopAsyncIteration

    def __aiter__(self):
        return self


def _stub_race_pipeline(
    monkeypatch,
    va,
    *,
    transcript: str,
    route_result: "str | None",
    route_delay: float,
    llm_tokens: list,
    llm_delay: float,
):
    """Stub every external dependency of one voice turn and record the race.

    The router and the LLM are timed against each other (``route_delay`` vs
    ``llm_delay``): ``route_state``/``llm_state`` expose when each side ran,
    so a test can pin that the LLM stream was STARTED before the router
    answered — the whole point of the race. The TTS session records every
    pushed sentence and only ends its audio stream at close(), like the real
    duplex sessions.
    """
    from backend.ai import stt as stt_pkg
    from backend.ai.tts.base import TTSTurnEnd

    async def fake_transcribe_stream(stream, **_kwargs):
        return transcript

    fake_stt = type("P", (), {"transcribe_stream": staticmethod(fake_transcribe_stream)})()
    monkeypatch.setattr(va, "get_stt_provider", lambda name=None: fake_stt)
    # The retry wrapper resolves provider ordering from the STT package.
    monkeypatch.setattr(stt_pkg, "get_available_stt_providers", lambda: ["deepgram"])
    monkeypatch.setattr(
        stt_pkg, "get_model_config_for", lambda usage: [usage, "deepgram", "nova-2", None]
    )

    route_state = {"done_at": None}

    async def fake_route_fast_path(text, info=None):
        await asyncio.sleep(route_delay)
        route_state["done_at"] = time.perf_counter()
        return route_result

    monkeypatch.setattr(va, "route_fast_path", fake_route_fast_path)

    llm_state = {"started_at": None, "closed": False}

    async def fake_openai_agent(**_kwargs):
        # The body runs on the first pull: this stamp is the moment the
        # race (or the old serial path) first stepped the LLM stream.
        llm_state["started_at"] = time.perf_counter()
        try:
            for token in llm_tokens:
                await asyncio.sleep(llm_delay)
                yield token
        finally:
            llm_state["closed"] = True

    monkeypatch.setattr(va, "openai_agent", fake_openai_agent)
    monkeypatch.setattr(
        va, "get_model_config_for", lambda usage: [usage, "openrouter", "test-model", None]
    )

    class _RecordingSession:
        """Duplex-shaped fake: text is recorded, audio ends only at close()."""

        def __init__(self):
            self.pushed: list[str] = []
            self.sample_rate = 24000
            self.closed = False
            self._pending: asyncio.Queue = asyncio.Queue()

        async def start(self):
            pass

        async def push_text(self, text):
            self.pushed.append(text)

        async def flush(self):
            if self.pushed:
                await self._pending.put(
                    TTSTurnEnd(index=len(self.pushed), chars=len(self.pushed[-1]), audio_ms=1.0)
                )

        async def clear(self):
            pass

        async def close(self):
            if self.closed:
                return
            self.closed = True
            await self._pending.put(None)

        async def audio(self):
            while True:
                item = await self._pending.get()
                if item is None:
                    return
                yield item

    holder = {}

    class _Provider:
        def session(self):
            session = _RecordingSession()
            holder["session"] = session
            return session

    monkeypatch.setattr(va, "get_tts_provider", lambda: _Provider())
    return route_state, llm_state, holder


def test_prefab_turn_pushes_only_prefab_and_closes_llm_stream(monkeypatch):
    """A prefab win must reach TTS alone and discard the LLM stream.

    The router answers in 0.01s while the LLM's first token needs 0.05s, so
    the race resolves as a prefab win mid-pull: the pending pull is
    cancelled and the generator closed, and TTS never hears the LLM token.
    """
    from backend.ai.voice import agent as va

    llm_tokens = [
        _text("esto no debe llegar al tts."),
        {"type": "finish", "content": ""},
    ]
    route_state, llm_state, holder = _stub_race_pipeline(
        monkeypatch,
        va,
        transcript="que hora es",
        route_result="Son las diez y media.",
        route_delay=0.01,
        llm_tokens=llm_tokens,
        llm_delay=0.05,
    )

    async def collect():
        return [
            item
            async for item in va.voice_agent_logic(_FakePCMStream(), streaming_path=True)
        ]

    res = asyncio.run(asyncio.wait_for(collect(), timeout=10.0))
    session = holder["session"]
    # Only the prefab was spoken.
    assert session.pushed == ["Son las diez y media."]
    assert not any(isinstance(item, bytes) for item in res)
    # The LLM stream was started (race, not serial await) and then closed
    # before its first token ever arrived.
    assert llm_state["started_at"] is not None
    assert llm_state["started_at"] < route_state["done_at"]
    assert llm_state["closed"] is True


def test_other_turn_replays_llm_sentences_in_order(monkeypatch):
    """An "other" verdict keeps the LLM reply: buffered then live, in order.

    The router says None at 0.05s while tokens arrive every 0.02s, so the
    race resolves mid-pull with buffered tokens that must be replayed
    before the live tail — TTS hears the sentences once, in order.
    """
    from backend.ai.voice import agent as va

    llm_tokens = [
        _text("Primera frase."),
        _text(" Segunda frase."),
        {"type": "inference_end", "content": ""},
    ]
    route_state, llm_state, holder = _stub_race_pipeline(
        monkeypatch,
        va,
        transcript="cuenta me un chiste",
        route_result=None,
        route_delay=0.05,
        llm_tokens=llm_tokens,
        llm_delay=0.02,
    )

    async def collect():
        return [
            item
            async for item in va.voice_agent_logic(_FakePCMStream(), streaming_path=True)
        ]

    asyncio.run(asyncio.wait_for(collect(), timeout=10.0))
    session = holder["session"]
    # The sanitizer emits one sentence per boundary: both LLM sentences
    # reached TTS, once each, in order.
    assert [p.strip() for p in session.pushed] == ["Primera frase.", "Segunda frase."]
    # The LLM stream was started before the router answered (the race), and
    # it ran to exhaustion.
    assert llm_state["started_at"] is not None
    assert llm_state["started_at"] < route_state["done_at"]
    assert llm_state["closed"] is True
