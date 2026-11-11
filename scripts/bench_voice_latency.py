#!/usr/bin/env python3
"""Real-API latency benchmark for the TUKI wake voice pipeline.

Measures every critical-path stage with the REAL providers (Deepgram STT/TTS,
OpenRouter orchestrator + Jev) and the REAL model config from the database,
then runs complete end-to-end turns through ``voice_agent_logic`` on the
streaming PCM path. STT input is TTS-generated speech, so every hop is
production traffic, not a stub.

Disclosed side effects: e2e turns append rows to the singleton memory
conversation (exactly like a real wake turn) and utterances use read-only
tools only. The benchmark measures the CURRENT working tree.

Usage:
    .venv/bin/python scripts/bench_voice_latency.py            # full run
    .venv/bin/python scripts/bench_voice_latency.py --quick    # 1 iteration
    .venv/bin/python scripts/bench_voice_latency.py --skip-e2e # stages only
"""

from __future__ import annotations

import argparse
import array
import asyncio
import contextlib
import os
import statistics
import sys
import time
from pathlib import Path
from typing import AsyncIterator

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.ai.agent import openai_agent  # noqa: E402
from backend.ai.config import get_app_setting, get_model_config_for  # noqa: E402
from backend.ai.stt import (  # noqa: E402
    get_available_stt_providers,
    get_stt_provider,
    transcribe_stream_with_fallback,
)
from backend.ai.tts import get_tts_provider  # noqa: E402
from backend.ai.tts.base import TTSTurnEnd  # noqa: E402
from backend.ai.tools.discovery import ORCHESTRATOR_TOOL_SCHEMAS  # noqa: E402
from backend.ai.voice import agent as voice_agent_module  # noqa: E402
from backend.ai.voice.fast_path import (  # noqa: E402
    _classify,
    _prefab_weather,
    _quick_intent,
)

# Utterances double as TTS benchmark sentences and (resampled to 16 kHz) as
# STT input for the end-to-end turns. The labels describe the routing tier
# each phrase is expected to exercise.
E2E_VARIANTS: list[tuple[str, str]] = [
    ("time", "¿Qué hora es?"),
    ("jev-weather", "¿Va a hacer frío esta noche?"),
    ("llm-chat", "Contame un dato curioso sobre el espacio en una sola frase."),
    ("llm-tools", "¿Qué tareas tengo para hoy?"),
]
JEV_PHRASES: list[str] = [
    "¿Qué hora es?",                 # tier 0: local regex hit, no network
    "Contame un chiste",             # network tier, expected "other"
    "¿Va a hacer frío esta noche?",  # network tier, soft weather phrasing
]
LLM_PROMPT = "Contame un dato curioso sobre el espacio en una sola frase."

RESULTS: dict[str, dict] = {}


def ms(seconds: float) -> float:
    return seconds * 1000.0


def stats_line(label: str, samples_ms: list[float], suffix: str = "") -> None:
    if not samples_ms:
        print(f"  {label:<28} NO SAMPLES {suffix}")
        return
    ordered = sorted(samples_ms)
    p95_idx = min(len(ordered) - 1, max(0, round(0.95 * (len(ordered) - 1))))
    print(
        f"  {label:<28} median={statistics.median(ordered):8.1f}ms  "
        f"p95={ordered[p95_idx]:8.1f}ms  min={ordered[0]:8.1f}ms  "
        f"max={ordered[-1]:8.1f}ms  n={len(ordered)} {suffix}"
    )


def resample_linear(pcm: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Linear-interpolated mono PCM16 resample (TTS 24 kHz -> STT 16 kHz)."""
    samples = array.array("h")
    samples.frombytes(pcm)
    if not samples or src_rate == dst_rate:
        return pcm
    ratio = src_rate / dst_rate
    n_out = max(1, int(len(samples) / ratio))
    out = array.array("h", bytes(2 * n_out))
    for i in range(n_out):
        pos = i * ratio
        i0 = int(pos)
        frac = pos - i0
        i1 = min(i0 + 1, len(samples) - 1)
        out[i] = int((1.0 - frac) * samples[i0] + frac * samples[i1])
    return out.tobytes()


async def paced_frames(
    pcm16: bytes,
    frame_ms: int = 80,
    rate: int = 16000,
    anchor: dict | None = None,
) -> AsyncIterator[bytes]:
    """Yield PCM16 frames paced at realtime, like the browser client does.

    Sets ``anchor["t_client_stop"]`` when the last frame has been handed
    over — the same anchor the WS router records on the "end" frame.
    """
    frame_bytes = 2 * rate * frame_ms // 1000
    t_start = time.perf_counter()
    n = 0
    for i in range(0, len(pcm16), frame_bytes):
        due = t_start + (n + 1) * frame_ms / 1000.0
        delay = due - time.perf_counter()
        if delay > 0:
            await asyncio.sleep(delay)
        yield pcm16[i : i + frame_bytes]
        n += 1
    if anchor is not None:
        anchor["t_client_stop"] = time.perf_counter()


async def synth_utterance(text: str) -> tuple[bytes, int]:
    """One duplex TTS turn; returns (pcm at native rate, sample_rate).

    A concurrent consumer observes chunks as the provider's pump delivers
    them, so the first-chunk timestamp is a real TTFB, not a post-drain one.
    """
    session = get_tts_provider().session()
    pcm = bytearray()
    timings: dict[str, float | None] = {
        "connect_s": None,
        "t_push": None,
        "first_s": None,
        "turn_end_s": None,
    }

    async def consume() -> None:
        async for item in session.audio():
            if isinstance(item, TTSTurnEnd):
                timings["turn_end_s"] = time.perf_counter()
                return
            if isinstance(item, (bytes, bytearray)):
                if timings["first_s"] is None:
                    timings["first_s"] = time.perf_counter()
                pcm.extend(item)

    consumer = asyncio.create_task(consume())
    t0 = time.perf_counter()
    try:
        await session.start()
        timings["connect_s"] = time.perf_counter() - t0
        timings["t_push"] = time.perf_counter()
        await session.push_text(text)
        await session.flush()
        await asyncio.wait_for(consumer, timeout=30.0)
    finally:
        await session.close()
        with contextlib.suppress(Exception):
            await consumer

    if not pcm:
        raise RuntimeError(f"TTS produced no audio for: {text!r}")
    connect = ms(timings["connect_s"] or 0.0)
    ttfb = ms((timings["first_s"] or 0.0) - (timings["t_push"] or 0.0))
    turn = ms((timings["turn_end_s"] or 0.0) - (timings["t_push"] or 0.0))
    audio_ms = len(pcm) / 2 / session.sample_rate * 1000.0
    RESULTS.setdefault("tts", {"connect": [], "ttfb": [], "turn": []})
    RESULTS["tts"]["connect"].append(connect)
    RESULTS["tts"]["ttfb"].append(ttfb)
    RESULTS["tts"]["turn"].append(turn)
    print(
        f"  synth {text[:34]!r:<36} connect={connect:6.1f}ms  ttfb={ttfb:6.1f}ms  "
        f"turn={turn:6.1f}ms  audio={audio_ms:6.1f}ms"
    )
    return bytes(pcm), session.sample_rate


def preflight() -> None:
    orchestrator = get_model_config_for("orchestrator")
    print("== preflight ==")
    print(f"  orchestrator : provider={orchestrator[1]} model={orchestrator[2]} effort={orchestrator[3]}")
    print(f"  tts provider : {get_app_setting('tts_provider') or 'deepgram (default)'}")
    print(f"  stt providers: {get_available_stt_providers()}")
    print(f"  timezone     : {os.environ.get('TIMEZONE', 'Europe/Madrid (default)')}")
    print(f"  tool schemas : {len(ORCHESTRATOR_TOOL_SCHEMAS)}")


async def stage_tts(iterations: int) -> dict[str, bytes]:
    """Synthesize every utterance (plus repeats for stats); return 16 kHz PCM."""
    print("\n== stage A: TTS (Deepgram duplex, one turn per sentence) ==")
    pcm16: dict[str, bytes] = {}
    for label, phrase in E2E_VARIANTS:
        pcm, rate = await synth_utterance(phrase)
        pcm16[label] = resample_linear(pcm, rate, 16000)
    for _ in range(max(0, iterations - 1)):
        await synth_utterance(E2E_VARIANTS[0][1])
    tts = RESULTS["tts"]
    stats_line("tts connect", tts["connect"])
    stats_line("tts ttfb (push->chunk)", tts["ttfb"])
    stats_line("tts turn (push->end)", tts["turn"])
    return pcm16


async def stage_stt(pcm16: bytes, iterations: int) -> None:
    print("\n== stage B: STT (Deepgram streaming, realtime-paced frames) ==")
    finalize: list[float] = []
    wall: list[float] = []
    for i in range(iterations):
        provider = get_stt_provider()
        t0 = time.perf_counter()
        transcript = await transcribe_stream_with_fallback(
            paced_frames(pcm16), selected_provider=provider, language="es"
        )
        wall.append(ms(time.perf_counter() - t0))
        timing = getattr(provider, "timing", {}) or {}
        if "finalize" in timing:
            finalize.append(timing["finalize"])
        print(
            f"  #{i + 1} wall={wall[-1]:8.1f}ms  timing={timing}  "
            f'transcript="{transcript.strip()[:40]}"'
        )
    RESULTS["stt"] = {"finalize": finalize, "wall": wall}
    stats_line("stt finalize (crit path)", finalize)
    stats_line("stt wall (incl. feed)", wall)


async def stage_history(iterations: int) -> None:
    """History load sits on the critical path between STT finalize and routing."""
    print("\n== stage B2: wake history load (get_history, DB read + replay) ==")
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        messages, conv_id = await asyncio.to_thread(voice_agent_module.get_history)
        samples.append(ms(time.perf_counter() - t0))
    RESULTS["history"] = samples
    stats_line("history load", samples, suffix=f"messages={len(messages)} conv={conv_id}")


async def stage_jev(iterations: int) -> None:
    print("\n== stage C: fast-path router (regex tier vs Jev network tier) ==")
    network: dict[str, list[float]] = {}
    for phrase in JEV_PHRASES:
        t0 = time.perf_counter()
        quick = _quick_intent(phrase)
        regex_ms = ms(time.perf_counter() - t0)
        samples: list[float] = []
        last: dict = {}
        for _ in range(iterations):
            info: dict = {}
            t0 = time.perf_counter()
            intent = await asyncio.to_thread(_classify, phrase, info)
            samples.append(ms(time.perf_counter() - t0))
            last = info
        network[phrase] = samples
        print(
            f"  {phrase[:36]!r:<38} regex={regex_ms:6.3f}ms -> {quick!r} | "
            f"jev median={statistics.median(samples):7.1f}ms "
            f"p95={sorted(samples)[max(0, round(0.95 * (len(samples) - 1)))]:7.1f}ms "
            f"intent={last.get('intent')} conf={last.get('confidence')}"
        )
    RESULTS["jev"] = network
    # One shot at the weather prefab: the e2e turn suggested the external
    # Weather tool, not Jev, dominates the weather fast path.
    try:
        t0 = time.perf_counter()
        reply = await asyncio.to_thread(_prefab_weather)
        weather_ms = ms(time.perf_counter() - t0)
        RESULTS["weather_prefab_ms"] = weather_ms
        print(f"  _prefab_weather: {weather_ms:.1f}ms -> {str(reply)[:60]!r}")
    except Exception as exc:
        print(f"  _prefab_weather: FAILED {exc}")


async def stage_llm_ttft(iterations: int) -> None:
    print("\n== stage D: LLM (orchestrator config, TTFT to first text token) ==")
    orchestrator = get_model_config_for("orchestrator")
    ttft: list[float] = []
    totals: list[float] = []
    for i in range(iterations):
        messages = [{"role": "user", "content": LLM_PROMPT}]
        t0 = time.perf_counter()
        first: float | None = None
        tokens = 0
        tools_fired = False
        async for token in openai_agent(
            messages=messages,
            model=orchestrator[2],
            provider=orchestrator[1],
            thinking_process=orchestrator[3],
            max_rounds=3,
            tool_schemas=ORCHESTRATOR_TOOL_SCHEMAS,
            conv_id=-1,
        ):
            if isinstance(token, str):
                print(f"  #{i + 1} stream error token")
                break
            kind = token.get("type")
            if kind == "tool_call":
                tools_fired = True
            if kind == "agent" and first is None:
                first = time.perf_counter()
            if kind == "agent":
                tokens += 1
            if kind == "finish":
                break
        if first is not None:
            ttft.append(ms(first - t0))
        totals.append(ms(time.perf_counter() - t0))
        print(f"  #{i + 1} ttft={ttft[-1] if ttft else float('nan'):8.1f}ms  stream_total={totals[-1]:8.1f}ms  text_tokens={tokens}  tools={tools_fired}")
    RESULTS["llm"] = {"ttft": ttft, "totals": totals}
    stats_line("llm ttft", ttft)
    stats_line("llm stream total", totals)


async def stage_e2e(pcm16: dict[str, bytes], iterations: int) -> None:
    print("\n== stage E: end-to-end turns (voice_agent_logic, streaming PCM) ==")
    print("  (each turn also prints the production [voice] turn log)")
    e2e: dict[str, list[float]] = {}
    for label, _phrase in E2E_VARIANTS:
        samples: list[float] = []
        for i in range(iterations):
            anchor: dict = {}
            t0 = time.perf_counter()
            first_pcm: float | None = None
            received = 0
            async for item in voice_agent_module.voice_agent_logic(
                paced_frames(pcm16[label], anchor=anchor),
                streaming_path=True,
                client_meta=anchor,
            ):
                if isinstance(item, (bytes, bytearray)):
                    if first_pcm is None:
                        first_pcm = time.perf_counter()
                    received += len(item)
            total = time.perf_counter() - t0
            speech_to_audio = (
                ms(first_pcm - anchor["t_client_stop"])
                if first_pcm is not None and "t_client_stop" in anchor
                else float("nan")
            )
            samples.append(speech_to_audio)
            print(
                f"  E2E[{label}] #{i + 1}: speech->audio={speech_to_audio:8.1f}ms  "
                f"total={total:6.2f}s  pcm={received}B"
            )
        e2e[label] = samples
    RESULTS["e2e"] = e2e
    for label, _ in E2E_VARIANTS:
        stats_line(f"e2e {label} speech->audio", e2e.get(label, []))


def print_conclusions() -> None:
    print("\n== conclusions (from measured values only) ==")
    stt = RESULTS.get("stt", {}).get("finalize", [])
    jev = RESULTS.get("jev", {}).get("Contame un chiste", [])
    llm = RESULTS.get("llm", {}).get("ttft", [])
    tts = RESULTS.get("tts", {}).get("ttfb", [])
    if not (stt and llm and tts):
        print("  (insufficient data: some stage failed — see above)")
        return
    stt_f = statistics.median(stt)
    llm_t = statistics.median(llm)
    tts_t = statistics.median(tts)
    jev_m = statistics.median(jev) if jev else 0.0
    serial = stt_f + jev_m + llm_t + tts_t
    raced = stt_f + max(jev_m, llm_t) + tts_t
    print(f"  component medians: stt_finalize={stt_f:.0f}ms  jev={jev_m:.0f}ms  "
          f"llm_ttft={llm_t:.0f}ms  tts_ttfb={tts_t:.0f}ms")
    hist = RESULTS.get("history", [])
    if hist:
        print(f"  history load median: {statistics.median(hist):.0f}ms (serial, between finalize and routing)")
    if "weather_prefab_ms" in RESULTS:
        print(f"  weather prefab total: {RESULTS['weather_prefab_ms']:.0f}ms (external Weather tool)")
    print(f"  'other' turn, serial today  ≈ {serial:.0f}ms  (jev blocks the LLM start)")
    print(f"  'other' turn, raced fastpath ≈ {raced:.0f}ms  (saving ≈ {serial - raced:.0f}ms/turn)")
    print("  reference: human turn-taking gap 0-200ms; industry cascade target ~1000ms")
    e2e = RESULTS.get("e2e", {})
    if "time" in e2e and e2e["time"]:
        print(f"  measured pipeline floor (fast-path time turn): "
              f"{statistics.median(e2e['time']):.0f}ms speech->audio")
    if "llm-chat" in e2e and e2e["llm-chat"]:
        print(f"  measured LLM turn (chat, no tools): "
              f"{statistics.median(e2e['llm-chat']):.0f}ms speech->audio")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="1 iteration everywhere")
    parser.add_argument("--iterations", type=int, default=3, help="stage iterations")
    parser.add_argument("--e2e-iterations", type=int, default=2, help="e2e turns per variant")
    parser.add_argument("--skip-e2e", action="store_true")
    parser.add_argument("--skip-stages", action="store_true")
    args = parser.parse_args()
    if args.quick:
        args.iterations = 1
        args.e2e_iterations = 1

    preflight()
    if not args.skip_stages:
        pcm16 = await stage_tts(args.iterations)
        await stage_stt(pcm16["time"], args.iterations)
        await stage_history(args.iterations)
        await stage_jev(args.iterations)
        await stage_llm_ttft(args.iterations)
    else:
        pcm16 = {}
    if not args.skip_e2e:
        if not pcm16:
            pcm16 = await stage_tts(1)
        await stage_e2e(pcm16, args.e2e_iterations)
    print_conclusions()


if __name__ == "__main__":
    asyncio.run(main())
