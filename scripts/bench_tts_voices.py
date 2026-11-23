#!/usr/bin/env python3
"""Benchmark TTFB of every Aura-2 Spanish voice (Deepgram)."""
from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from backend.ai.tts.deepgram import DeepgramTTSProvider
from backend.ai.tts.base import TTSTurnEnd

SPANISH_VOICES = [
    "aura-2-sirio-es", "aura-2-nestor-es", "aura-2-carina-es",
    "aura-2-celeste-es", "aura-2-alvaro-es", "aura-2-diana-es",
    "aura-2-aquila-es", "aura-2-selena-es", "aura-2-estrella-es",
    "aura-2-javier-es", "aura-2-agustina-es", "aura-2-antonia-es",
    "aura-2-gloria-es", "aura-2-luciano-es", "aura-2-olivia-es",
    "aura-2-silvia-es", "aura-2-valerio-es",
]

SHORT_PHRASE = "Son las 16:40."
LONG_PHRASE = "En Burgos hay 12 grados y patchy rain nearby, con sensación de 8 grados."


async def measure(voice: str, phrase: str):
    session = DeepgramTTSProvider(voice=voice).session()
    pcm = bytearray()
    first_s = None
    turn_end_s = None

    async def consume():
        nonlocal first_s, turn_end_s
        async for item in session.audio():
            if isinstance(item, TTSTurnEnd):
                turn_end_s = time.perf_counter()
                return
            if isinstance(item, (bytes, bytearray)):
                if first_s is None:
                    first_s = time.perf_counter()
                pcm.extend(item)

    consumer = asyncio.create_task(consume())
    try:
        await session.start()
        t_push = time.perf_counter()
        await session.push_text(phrase)
        await session.flush()
        await asyncio.wait_for(consumer, timeout=20.0)
        ttfb = (first_s - t_push) * 1000 if first_s else None
        turn = (turn_end_s - t_push) * 1000 if turn_end_s else None
        return ttfb, turn, len(pcm)
    except Exception as exc:
        return None, None, 0
    finally:
        await session.close()
        with contextlib.suppress(Exception):
            await consumer


async def main():
    print(f"{'voice':<22} {'short ttfb':>12} {'short turn':>12} {'long ttfb':>12} {'long turn':>12}")
    results = []
    for voice in SPANISH_VOICES:
        sttfb, sturn, _ = await measure(voice, SHORT_PHRASE)
        lttfb, lturn, _ = await measure(voice, LONG_PHRASE)
        results.append((voice, sttfb, sturn, lttfb, lturn))
        print(f"{voice:<22} {sttfb if sttfb else 0:12.1f} {sturn if sturn else 0:12.1f} {lttfb if lttfb else 0:12.1f} {lturn if lturn else 0:12.1f}")

    print("\nSorted by short TTFB:")
    for voice, sttfb, sturn, lttfb, lturn in sorted(results, key=lambda r: r[1] or 9999):
        print(f"  {voice:<22} short={sttfb:7.1f}ms  long={lttfb:7.1f}ms")


if __name__ == "__main__":
    asyncio.run(main())