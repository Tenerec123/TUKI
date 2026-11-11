import asyncio
import contextlib
import io
import time
import wave
from typing import AsyncIterable, AsyncIterator

from ...logic.conversations import edit_conversation_logic
from ...logic.memory import get_or_create_memory_conversation, reset_if_stale
from ...database import SessionLocal
from ...schemas import ConversationSchema, ConversationUpdate, MessageBase
from ..chat import _db_format_new_messages, build_messages
from ..stt import get_stt_provider, transcribe_stream_with_fallback
from ..agent import openai_agent
from .fast_path import route_fast_path
from ..config import get_model_config_for, AUDIO_SYSTEM_PROMPT
from ..tools.discovery import ORCHESTRATOR_TOOL_SCHEMAS
from ..tts import TTSSession, get_tts_provider, iter_wav_turns, TTSTurnEnd
from ..tts.sanitize import MarkdownSanitizer, sanitize_chunk_report, sanitize_stream_report
from . import log as voicelog


def _wav_duration_ms(wav_bytes: bytes) -> float:
    """Return the playable duration of a WAV payload in ms.

    Reads the container header only, so the cost does not depend on payload length.
    """
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            return reader.getnframes() / reader.getframerate() * 1000
    except Exception:
        return 0.0


_WAKE_TOKENS = ("alexa", "rexa")


def _strip_wake_word(transcript: str) -> str:
    """Drop a leading wake-word token from the transcript.

    Defense in depth: the client no longer ships its pre-roll ring (the wake
    word lives there), but a user speaking over the wake word, or a frame
    captured while the socket was connecting, can still put "alexa" first.
    A wake word alone is not a command, so stripping it here also routes
    wake-word-only detections to the empty-turn path.
    """
    parts = transcript.strip().split()
    if not parts:
        return transcript
    first = parts[0].strip(".,;:!?¿¡").lower()
    if first in _WAKE_TOKENS:
        return " ".join(parts[1:]).strip()
    return transcript


def _perceived_anchor(perf: dict) -> float:
    t_client_stop = perf.get("t_client_stop")
    if t_client_stop is not None:
        lag_s = perf.get("client_lag_ms", 0) or 0
        try:
            lag_s = float(lag_s) / 1000.0
        except (TypeError, ValueError):
            lag_s = 0.0
        return t_client_stop - lag_s
    return perf["t_req"]


def _print_empty_turn(perf: dict) -> None:
    """One-line block for a turn that produced no speech."""
    anchor = _perceived_anchor(perf)
    lag_ms = perf.get("client_lag_ms", 0) or 0
    try:
        lag_s = float(lag_ms) / 1000.0
    except (TypeError, ValueError):
        lag_s = 0.0
    voicelog.log("---- turn ----")
    voicelog.log('heard    ""')
    if lag_s:
        voicelog.log(f"vad      client wait {lag_s:.2f}s  [silence + queue drain]")
    voicelog.log(f"total    {time.perf_counter() - anchor:.2f}s  (empty transcript)")


def _print_voice_summary(perf: dict, stats: dict, session: TTSSession) -> None:
    """Emit the per-turn block: one line per step, top-down, all durations in seconds.

    ``perf`` durations are milliseconds except the raw perf_counter anchors
    (``t_req``/``t_client_stop``/``first_audio``), which are seconds. The
    perceived timeline anchors at ``t_client_stop`` (when the user stopped
    talking) when the router provides it, else at ``t_req``.
    """
    t_end = time.perf_counter()
    first = stats.get("first_audio")
    anchor = _perceived_anchor(perf)
    speech_to_audio = (first - anchor) if first is not None else None
    timing = perf.get("stt_timing", {})
    hidden = "yes" if perf.get("connect_overlapped") else "no"

    lag_ms = perf.get("client_lag_ms", 0) or 0
    try:
        lag_s = float(lag_ms) / 1000.0
    except (TypeError, ValueError):
        lag_s = 0.0

    voicelog.log("---- turn ----")
    voicelog.log(f'heard    "{perf.get("transcript", "")}"')
    if lag_s:
        voicelog.log(f"vad      client wait {lag_s:.2f}s  [silence + queue drain]")
    voicelog.log(
        f"stt      {perf['stt'] / 1000:.2f}s  "
        f"[connect {timing.get('connect', 0.0) / 1000:.2f} | "
        f"listen {timing.get('listen', 0.0) / 1000:.2f} | "
        f"finalize {timing.get('finalize', 0.0) / 1000:.2f}]"
    )

    if perf.get("fast_path"):
        confidence = perf.get("confidence")
        conf_s = "?" if confidence is None else f"{confidence:.2f}"
        voicelog.log(
            f"intent   {perf.get('intent', '?')}  conf {conf_s}  "
            f"(+{perf.get('intent_ms', -1.0) / 1000:.2f}s)"
        )
    else:
        voicelog.log(
            f"agent    first text +{perf.get('first_text_ms', -1.0) / 1000:.2f}s  "
            f"[connect ready: {hidden}]"
        )

    voicelog.log(
        f"tts      open {getattr(session, 'connect_ms', 0.0) / 1000:.2f}s  "
        f"[hidden under stt: {hidden}]  ttfb {stats.get('ttfb', -1.0) / 1000:.2f}s"
    )

    if perf.get("fast_path"):
        voicelog.log(
            f'said     "{perf.get("say_text", "")}"  '
            f"audio {stats.get('audio_ms', 0.0) / 1000:.2f}s"
        )
    else:
        voicelog.log(
            f"said     (llm) {int(stats.get('turns', 0))} turn(s)  "
            f"audio {stats.get('audio_ms', 0.0) / 1000:.2f}s"
        )

    s2a = "n/a" if speech_to_audio is None else f"{speech_to_audio:.2f}s"
    voicelog.log(f"total    {t_end - anchor:.2f}s  speech->audio {s2a}")
    with contextlib.suppress(Exception):
        session.report()

def get_history() -> tuple[list[dict], int]:
    """Load the wake conversation history as OpenAI messages, plus its ID.

    Backed by the singleton memory conversation (logic/memory.py). The replay
    reuses chat.build_messages, so prompt/agent/tool rows are rebuilt exactly
    like the chat UI does — one format, one source of truth. If the
    conversation has been idle for over an hour it is reset first (the hourly
    renewal), so a session that slept starts fresh. Best-effort: a DB failure
    degrades to a stateless turn with no session id, never an error.
    """
    try:
        with SessionLocal() as db:
            conversation = get_or_create_memory_conversation(db)
            reset_if_stale(conversation, db)
            schema = ConversationSchema.model_validate(conversation)
            return build_messages(schema, AUDIO_SYSTEM_PROMPT), conversation.id
    except Exception as exc:
        voicelog.log(f"history ! load failed: {exc}")
        return [], -1


def _save_voice_turn(new_rows: list[MessageBase]) -> None:
    """Persist one voice turn's rows (prompt first) into the wake conversation.

    ``new_rows`` is composed at the call site: the transcript as the first
    'prompt' row, then the agent's actions in DB format (agent text and
    tool call/result records, built by chat._db_format_new_messages).
    Best-effort: a DB failure must never break the audio flow. The append via
    edit_conversation_logic refreshes last_used, which is exactly the idle
    clock reset_if_stale reads, so usage keeps the session rolling.
    """
    if not new_rows or not new_rows[0].text.strip():
        return
    try:
        with SessionLocal() as db:
            conversation = get_or_create_memory_conversation(db)
            edit_conversation_logic(conversation.id, ConversationUpdate(messages=new_rows), db=db)
    except Exception as exc:
        voicelog.log(f"history ! save failed: {exc}")


# Race window: Jev classifies while the orchestrator's first tokens stream.
# Jev answers in 0.4-0.7s (measured 2026-11-11); 1.5s covers p95 with margin.
_RACE_CAP_S = 1.5


async def _race_llm_vs_jev(
    agent_iter: AsyncIterator[dict | str],
    route_task: asyncio.Task[str | None],
    cap_s: float = _RACE_CAP_S,
) -> tuple[list, str | None, str]:
    """Pull orchestrator tokens while Jev classifies, then resolve the race.

    The producer used to await the router first, so every non-time/weather
    turn paid Jev's round-trip before the LLM even connected. Here the
    stream is pulled immediately — TTFT and the first sentence overlap the
    classification — but every pulled token is HELD in a buffer: nothing is
    pushed, and the stream is never stepped past a ``tool_call`` yield while
    the race is open (the real agent executes tools one ``__anext__`` after
    yielding the call), so no tool side effect can land before the router
    has had its say.

    Returns ``(buffered, quick, outcome)``:

    - ``"jev"``: the router answered within the cap. ``quick`` is its result
      (prefab sentence, or None for "other"). With a prefab the stream is
      discarded and closed: the pending ``__anext__`` is cancelled, then
      ``aclose()`` runs the generator's finally blocks, so tools never
      execute. With None the stream stays live at its current position and
      ``buffered`` must be replayed before continuing it; an in-flight
      ``__anext__`` is drained to completion — never cancelled, because
      cancellation would kill the generator.
    - ``"cap"``: the cap expired without an answer. ``quick`` is None, the
      stream is live, and ``route_task`` keeps running detached (it never
      raises, so cancelling it buys nothing).
    - ``"llm"``: the stream ended (StopAsyncIteration, ``finish``, or the
      ERROR_TOKEN string) before Jev answered. ``buffered`` holds the
      complete tail: a finished LLM reply is strictly faster than waiting
      for a prefab, so Jev loses.

    A router exception degrades to ``quick=None`` / ``"jev"``: a router
    failure must never kill the turn.
    """
    buffered: list = []
    deadline = time.perf_counter() + cap_s
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return buffered, None, "cap"
        tok_task = asyncio.ensure_future(agent_iter.__anext__())
        waiters = {tok_task} if route_task.done() else {tok_task, route_task}
        done, _ = await asyncio.wait(
            waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED
        )
        if route_task in done and tok_task not in done:
            # Jev answered mid-pull: decide now.
            try:
                quick = route_task.result()
            except Exception:
                quick = None
            if quick is not None:
                # Prefab win: discard the stream. The in-flight pull is
                # cancelled (it never steps past a tool_call yield) and the
                # generator closed so its finally blocks run.
                tok_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration, Exception):
                    await tok_task
                with contextlib.suppress(Exception):
                    await agent_iter.aclose()
                return buffered, quick, "jev"
            # "other": the LLM stands. Drain the in-flight token instead of
            # cancelling it — cancellation would kill the generator.
            try:
                buffered.append(await tok_task)
            except StopAsyncIteration:
                pass
            return buffered, None, "jev"
        if tok_task not in done:
            # Cap expired with the next token still in flight: the cap is a
            # heuristic, not a hard deadline, so drain it — never cancel.
            try:
                buffered.append(await tok_task)
            except StopAsyncIteration:
                return buffered, None, "llm"
            return buffered, None, "cap"
        # The token arrived first.
        try:
            token = tok_task.result()
        except StopAsyncIteration:
            return buffered, None, "llm"
        buffered.append(token)
        if isinstance(token, str) or token.get("type") == "finish":
            return buffered, None, "llm"
        if token.get("type") == "tool_call" and not route_task.done():
            # Execution is one __anext__ away: hold the stream and let Jev
            # decide first. Never execute a tool while the race is open.
            done2, _ = await asyncio.wait(
                {route_task}, timeout=max(0.0, deadline - time.perf_counter())
            )
            if route_task in done2:
                try:
                    quick = route_task.result()
                except Exception:
                    quick = None
                if quick is not None:
                    with contextlib.suppress(Exception):
                        await agent_iter.aclose()
                    return buffered, quick, "jev"
                return buffered, None, "jev"
            return buffered, None, "cap"
        if route_task.done() and tok_task in done:
            # The route resolved while a benign token (text/inference_end)
            # was in flight; decide now, with no pull pending.
            try:
                quick = route_task.result()
            except Exception:
                quick = None
            if quick is not None:
                with contextlib.suppress(Exception):
                    await agent_iter.aclose()
                return buffered, quick, "jev"
            return buffered, None, "jev"
        # Benign token buffered; keep racing.


async def _replay_then_live(
    agent_iter: AsyncIterator[dict | str],
    buffered: list,
) -> AsyncIterator[dict | str]:
    """Yield the race's buffered tokens, then the agent's remaining stream."""
    for token in buffered:
        yield token
    async for token in agent_iter:
        yield token


async def voice_agent_logic(
    stream: AsyncIterable[bytes],
    streaming_path: bool = False,
    client_meta: dict | None = None,
) -> AsyncIterator[dict | bytes]:
    """Stream the agent's reply. Supports both legacy WAV-per-turn and PCM streaming.

    Pipeline: STT (utterance) -> LLM streaming (openai_agent) -> markdown sanitizer -> sentence splitter -> duplex TTS session.

    ``client_meta`` optionally carries the timestamp (perf_counter) at which the
    client stopped talking (the router records it when the end frame arrives,
    after the client's VAD trailing silence) and the client-reported lag. When
    present, the turn log's ``total``/``speech->audio`` measure the perceived
    timeline as ``t_client_stop - client_lag_ms/1000`` (true end of speech).

    Paths:
    - Legacy (streaming_path=False): yields bytes as WAV per turn via iter_wav_turns.
    - Streaming (streaming_path=True): yields dict {"type":"pcm_start","sample_rate":<int>} before first PCM chunk,
      then yields bytes chunks of raw PCM16. TTSTurnEnd markers are used for accounting/logging.
    """
    # Real-time streaming STT: the Deepgram WebSocket opens first, then each raw PCM16 (16 kHz mono) chunk is forwarded as it arrives. No WAV wrapper, no buffering.
    perf: dict = {"t_req": time.perf_counter()}

    # Open the TTS socket NOW, in parallel with STT: the handshake costs ~0.5s and
    # does not depend on the transcript, so overlapping it with STT hides it from
    # the user's wait instead of adding it at the end.
    session = get_tts_provider().session()
    stats: dict = {"turns": 0, "audio_ms": 0.0, "bytes": 0}
    sanitizer = MarkdownSanitizer()
    connect_task = asyncio.create_task(session.start())
    perf["t_connect"] = time.perf_counter()

    try:
        provider = get_stt_provider()
        perf["t_stt"] = time.perf_counter()
        transcription = await transcribe_stream_with_fallback(
            stream,
            selected_provider=provider,
            language="es",
        )
    except BaseException:
        # The socket was opened before STT; when STT itself raises (S11: every
        # provider failed or none is configured) the error must not leave the
        # handshake task or the session running. Same teardown as an empty
        # turn: cancel + join the handshake, close the session exactly once,
        # then re-raise the original STT error unchanged for the route's
        # error frame.
        connect_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await connect_task
        with contextlib.suppress(Exception):
            await session.close()
        raise
    # Defense in depth: never let the wake word reach the router or the LLM.
    transcription = _strip_wake_word(transcription)
    perf["eos"] = time.perf_counter()
    perf["stt"] = (perf["eos"] - perf["t_stt"]) * 1000
    perf["transcript"] = transcription
    # Surface the Deepgram provider's phase timings (connect/listen/finalize)
    # instead of an always-empty dict, so the turn log reports real values.
    perf["stt_timing"] = getattr(provider, "timing", {}) or {}
    # The handshake overlapped STT if it finished before the user stopped talking.
    perf["connect_overlapped"] = connect_task.done()
    # Perceived timeline: anchor at t_client_stop minus client-reported lag
    # (true end of speech). The router fills it when the end frame arrives.
    meta = client_meta or {}
    perf["t_client_stop"] = meta.get("t_client_stop")
    perf["client_lag_ms"] = meta.get("client_lag_ms", 0)

    if not transcription.strip():
        # The socket was opened before STT; an empty turn must tear it down or it leaks.
        connect_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await connect_task
        with contextlib.suppress(Exception):
            await session.close()
        _print_empty_turn(perf)
        if streaming_path:
            yield {"type": "end"}
        return
    messages, conv_id = await asyncio.to_thread(get_history)
    base_len = len(messages)
    messages.append({'role': 'user', 'content': transcription})

    perf["t_route_start"] = time.perf_counter()
    route_task = asyncio.create_task(route_fast_path(transcription, perf))

    async def push_turn(text: str) -> None:
        """Send one turn's text and ask for its audio.

        The connect is awaited lazily on the FIRST push only, so the handshake overlaps STT and the LLM's time-to-first-token.
        """
        if not text.strip():
            return
        if not perf.get("t_connected"):
            await connect_task
            perf["t_connected"] = time.perf_counter()
        await session.push_text(text)
        await session.flush()

    async def producer() -> None:
        """Consume LLM tokens, sanitizing and pushing each complete sentence."""
        pending: list[str] = []
        t_agent = time.perf_counter()
        try:
            # Fast path checked INSIDE try: returning before it would skip the
            # finally below and leave session.audio() waiting forever.
            # The iterator is created BEFORE the race: calling the async
            # generator only builds it (the body, including the client
            # connect, runs on the first pull inside the race), so the LLM's
            # TTFT overlaps Jev's classification instead of queueing behind
            # it. Side effect: get_model_config_for now also runs on prefab
            # turns — one single-row read, ms-scale.
            orchestrator = get_model_config_for('orchestrator')
            agent_iter = openai_agent(
                messages=messages,
                model=orchestrator[2],
                provider=orchestrator[1],
                thinking_process=orchestrator[3],
                max_rounds=10,
                tool_schemas=ORCHESTRATOR_TOOL_SCHEMAS,
                conv_id=conv_id,
            )
            buffered, quick, race_outcome = await _race_llm_vs_jev(agent_iter, route_task)
            perf["intent_ms"] = (time.perf_counter() - perf["t_route_start"]) * 1000
            perf["race"] = race_outcome
            if quick is not None:
                perf["fast_path"] = True
                perf["say_text"] = quick
                await push_turn(quick)
                return
            # Known approximation: perf["first_text"] is stamped at replay
            # time below, slightly after the token truly arrived (it sat in
            # the race buffer first).
            async for token in _replay_then_live(agent_iter, buffered):
                if isinstance(token, str):
                    # The agent yields the literal string 'ERROR_TOKEN' on failure.
                    voicelog.log(f"agent ! stream error token: {token}")
                    break
                if token.get("type") == "finish":
                    break
                if token.get("type") == "inference_end":
                    pending = sanitizer.release()
                    sanitize_chunk_report(sanitizer, force=True)
                    for unit in pending:
                        await push_turn(unit)
                    continue
                if token.get("type") != "agent":
                    continue  # tool_call / tool_result carry no spoken content
                if "first_text" not in perf:
                    perf["first_text"] = time.perf_counter()
                    perf["first_text_ms"] = (perf["first_text"] - t_agent) * 1000
                pending = sanitizer.push(token.get("content", ""))
                sanitize_chunk_report(sanitizer)
                for unit in pending:
                    await push_turn(unit)
        except Exception as exc:
            voicelog.log(f"agent ! error: {exc}")
        finally:
            try:
                pending = sanitizer.flush()
                sanitize_chunk_report(sanitizer, force=True)
                sanitize_stream_report(sanitizer)
                for unit in pending:
                    await push_turn(unit)
            except Exception as exc:
                voicelog.log(f"agent ! tail flush error: {exc}")
            finally:
                # The LLM may yield nothing and leave the handshake pending;
                # close() can't kill it (it sees no _ws yet), so cancel it first
                # or the started Deepgram socket/pump leaks.
                connect_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await connect_task
                with contextlib.suppress(Exception):
                    await session.close()

    producer_task = asyncio.create_task(producer())
    try:
        if streaming_path:
            sent_pcm_start = False
            async for item in session.audio():
                if isinstance(item, TTSTurnEnd):
                    stats["turns"] += 1
                    # Per-turn detail is folded into the end-of-turn block.
                    t_first_push = getattr(session, "t_first_push", None)
                    if stats["turns"] == 1:
                        stats["p1_audio"] = item.audio_ms
                        if t_first_push is not None and getattr(session, "first_audio_s", None):
                            try:
                                stats["ttfb"] = (session.first_audio_s - t_first_push) * 1000
                            except Exception:
                                stats["ttfb"] = -1.0
                    continue
                if isinstance(item, bytes):
                    if not sent_pcm_start:
                        rate = session.sample_rate
                        yield {"type": "pcm_start", "sample_rate": rate}
                        sent_pcm_start = True
                    nbytes = len(item)
                    rate = session.sample_rate
                    if rate > 0:
                        audio_ms = nbytes / 2 / rate * 1000
                    else:
                        audio_ms = 0.0
                    stats["bytes"] += nbytes
                    stats["audio_ms"] += audio_ms
                    if stats.get("first_audio") is None:
                        stats["first_audio"] = time.perf_counter()
                    yield item
                    continue
        else:
            async for wav_bytes in iter_wav_turns(session):
                audio_ms = _wav_duration_ms(wav_bytes)
                nbytes = len(wav_bytes)
                stats["turns"] += 1
                stats["audio_ms"] += audio_ms
                stats["bytes"] += nbytes
                if stats["turns"] == 1:
                    stats["first_audio"] = time.perf_counter()
                    stats["p1_audio"] = audio_ms
                    t_first_push = getattr(session, "t_first_push", None)
                    if t_first_push is not None and getattr(session, "first_audio_s", None):
                        try:
                            stats["ttfb"] = (session.first_audio_s - t_first_push) * 1000
                        except Exception:
                            stats["ttfb"] = -1.0
                yield wav_bytes
    finally:
        producer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await producer_task
        with contextlib.suppress(Exception):
            await session.close()
        # Persist the turn even when the stream ended early (error/disconnect):
        # the transcript is what was said, and messages[base_len:] carries
        # everything the agent did (text + tool call/result records). The
        # transform ignores the 'user' row, so base_len may sit before it.
        if perf.get("fast_path"):
            say_text = perf.get("say_text", "")
            agent_rows = [MessageBase(type="agent", text=say_text)] if say_text.strip() else []
        else:
            agent_rows = _db_format_new_messages(messages[base_len:])
        new_rows = [MessageBase(type='prompt', text=transcription), *agent_rows]
        await asyncio.to_thread(_save_voice_turn, new_rows)
        _print_voice_summary(perf, stats, session)
