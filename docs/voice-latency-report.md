# Voice Latency Report — TUKI (wake voice pipeline)

> Informe de análisis. **No se editó código** en esta fase: solo diagnóstico + ideas priorizadas.
> Fecha: 2026-11-20. Rama: `perf/voice-race` (commit `85cbfdd`).

## 1. Resumen ejecutivo

El pipeline cascaded (STT → LLM → TTS) ya está bien pipelined para el caso "happy path" (PCM streaming, TTS solapado con STT, Jev race). Pero los **fast path turns (hora, weather) siguen a 3-9 s de perceived latency**, y el culpable principal NO es el LLM ni el TTS: es la **suma de VAD client wait (~2 s) + STT connect (0.8-3.2 s) + STT finalize (0.4-2.0 s)**. El TTS ya está en ~0.23-0.25 s (bueno). El LLM solo se usa en turns "other".

**Perceived latency (speech→audio) medida en vivo (2026-11-20):**

| Turn | STT | intent | TTS ttfb | speech→audio | total |
|------|-----|--------|----------|--------------|-------|
| "¿a qué hora es?" | 2.63 s (connect 0.81, listen 1.42, finalize 0.37) | time, conf 1.00 (+0.58 s) | 0.25 s | 3.25 s | 4.09 s |
| "¿qué temperatura hace?" | 5.22 s (connect 3.16, listen 0.00, finalize 2.04) | weather, conf 1.00 (+0.29 s) | 0.23 s | 4.45 s | 8.71 s |

**Objetivo razonable: fast path < 1.0 s perceived, LLM turn < 2.0 s.** Hoy estamos a 3-4x.

**Top 3 levers (por impacto, con evidencia):**

1. **VAD endpointing client-side (~2 s)** — el mayor coste fijo, server-independiente. Reducir `VAD_STOP_FRAMES` y/o usar endpointing semántico.
2. **STT finalize worst-case 2.0 s** — `SETTLE_S = 2.0` timeout cuando Deepgram no envía `Metadata`. Reducir a ~0.5-0.8 s.
3. **STT connect 0.8-3.2 s** — WebSocket abierto por utterance sin solapar con recepción de audio. Pre-conectar o persistir.

Lever secundario pero fácil: **Jev regex no matchea "¿a qué hora es?"** (negative lookbehind `(?<!a\s)`), forzando un round-trip de red de 0.58 s en la pregunta de hora más común.

---

## 2. Arquitectura actual (pipeline)

```
Browser (wake_script.js)
  openWakeWord (WASM) → detecta wake word
  → captura utterance (VAD_STOP_FRAMES frames de silencio)
  → envía PCM16 16kHz por WebSocket /api/ai/voice-agent-ws?proto=2
  → envía frame "end" con t_client_stop

Backend (backend/ai/voice/agent.py::voice_agent_logic)
  1. Abre sesión TTS (Deepgram Aura-2) en paralelo con STT  [connect_task]
  2. STT: Deepgram WebSocket (transcribe_stream_with_fallback) → transcript
  3. _strip_wake_word
  4. get_history (DB, ~10 ms)
  5. route_fast_path(transcript) [asyncio.to_thread(_classify)] → "time"/"weather"/None
     - _quick_intent (regex) → si match, instantáneo
     - else Jev network (~0.4-0.7 s)
  6. Producer: openai_agent (OpenRouter) → markdown sanitizer → sentence splitter → session.push_text/flush
  7. session.audio() → PCM chunks → WebSocket → browser
  8. _save_voice_turn (DB)
```

TTS y STT se solapan (connect_task). El LLM se arranca antes de que Jev responda (race). El sanitizer sostiene una frase hasta confirmar boundary.

**Archivos clave:**
- `backend/ai/voice/agent.py` (voice_agent_logic, _race_llm_vs_jev, producer, push_turn)
- `backend/ai/voice/fast_path.py` (_quick_intent, _classify, _prefab_time/weather)
- `backend/ai/stt/deepgram.py` (_run_websocket, SETTLE_S, endpointing)
- `backend/ai/tts/deepgram.py` (DeepgramTTTSession, split_for_speak)
- `backend/ai/tts/sanitize.py` (MarkdownSanitizer, is_sentence_end, PhraseSplitter)
- `frontend/wake_script.js` (VAD_STOP_FRAMES, client lag)
- `scripts/bench_voice_latency.py` (benchmark real-API)

---

## 3. Mediciones de referencia

### 3.1 Bench (2026-11-11, real APIs, `odd/tasks/voice-latency-bench.md`)

| Stage | Mediana | Rango |
|-------|---------|-------|
| STT Deepgram nova-2 streaming: connect | ~510 ms (solapado con speech) | — |
| STT finalize | 355 ms | 276-376 |
| get_history | 10.5 ms | — |
| Jev regex tier | 0.02 ms | — |
| Jev network tier ("other") | 403-570 ms | — |
| Jev network tier (soft weather) | 446 ms | — |
| LLM TTFT (gpt-oss-120b, effort low, cache ~97%) | 561 ms | 540-1040 |
| TTS Aura-2 antonia-es: connect | ~438 ms (solapado) | — |
| TTS TTFB (corto, 15 chars) | 679-712 ms | — |
| TTS TTFB (largo, 74 chars) | 1798 ms | — |
| Weather prefab (warm) | 232 ms | — |
| Weather prefab (cold) | ~5.9 s | — |
| E2E fast path (regex) | 1430 ms | — |
| E2E jev-weather (cold) | 3542 ms | — |
| E2E llm-chat (sin tools) | 5329 ms | — |
| E2E llm-tools | 3020 ms | 2124-3916 |

### 3.2 Vivo (2026-11-20, logs del usuario)

Ver tabla §1. Destaca:
- Weather: STT 5.22 s (connect 3.16 + finalize 2.04) — peor que el bench.
- Time: intent +0.58 s (debería ser 0.02 ms si el regex matcheara).
- TTS ttfb 0.23-0.25 s (bueno, Aura-2).
- VAD client wait 1.88-2.02 s (silence + queue drain).

### 3.3 Conclusiones del bench (ya documentadas)

1. PCM path funciona; el floor (fast path) sigue a 1430 ms, dominado por TTS TTFB + STT finalize.
2. TTS first-audio es el mayor lever en cada turn (~700 ms corto, ~1800 ms largo) porque Aura-2 batch-bursts cada Speak.
3. LLM replies de una frase larga esperan a completar la frase (~2.5 s) antes de TTS; splitting en primer comma/cláusula solaparía.
4. Jev race validado pero tercer lever (~570 ms en "other").
5. TTFB log metric roto (reportaba 0/-0.00) → arreglado en quick wins.
6. STT nova-2 finalize 355 ms; nova-3 candidato (pero ver §4.4).

---

## 4. Cuellos de botella (con evidencia)

### 4.1 VAD client wait ~2 s (el mayor coste fijo)

**Evidencia:** log "vad client wait 2.02s [silence + queue drain]". `clientLagMs = (end_frame_sent_ms - utterance_end_ms) + VAD_STOP_FRAMES * FRAME_MS` (wake_script.js:337-338). `VAD_STOP_FRAMES = 6` → 480 ms. El resto (~1.5 s) es "queue drain": el cliente sigue enviando frames del ring buffer / captura hasta que el VAD reporta silencio, y el browser procesa.

**Por qué importa:** es pure additive latency, no solapable (multigrid.ai: "Only `e` is a policy choice. Everything else is a system you must either speed up or overlap"). 2 s de espera antes de que el servidor pueda empezar.

**Referencias:** Deepgram blog "Turn detection in voice agents" (silence timeouts vs VAD vs semantic); LiveKit turn detection tuning; Multigrid VAD/endpointing.

### 4.2 STT connect 0.8-3.2 s (WebSocket abierto por utterance)

**Evidencia:** log weather connect 3.16 s, time connect 0.81 s. Deepgram docs: connection latency = DNS + TCP + TLS + WS upgrade, solo afecta al inicio de sesión. Recomendación oficial: "Use connection pooling to reuse established WebSocket connections across requests, avoiding repeated connection setup costs."

**Problema actual:** `_run_websocket` abre un socket nuevo por utterance (`websockets.connect`). No se solapa con la recepción de audio (los chunks no se consumen hasta que connect termina, porque `async for chunk in chunks` está después del connect). Así connect es pure latency.

**Opciones:** pre-abrir el socket al inicio del turn (en paralelo con TTS connect, como ya se hace con TTS), persistir una conexión entre turns (KeepAlive), o usar Deepgram SDK con connection pooling.

### 4.3 STT finalize 0.37-2.04 s (SETTLE_S = 2.0)

**Evidencia:** código `SETTLE_S = 2.0` (deepgram.py:164). Si Deepgram no envía `Metadata` (end-of-stream marker), el loop espera hasta 2.0 s timeout. Weather finalize 2.04 s = exactamente timeout. Time finalize 0.37 s = Metadata llegó rápido.

**Por qué weather no recibe Metadata:** posiblemente el audio es más largo o el modelo nova-3 (recién cambiado) no envía Metadata en todos los casos. Deepgram docs: "Metadata is Deepgram's end-of-stream marker". No está garantizado que llegue antes del timeout.

**Opciones:** reducir SETTLE_S a 0.5-0.8 s (tradeoff: cortar finals tardíos), usar `Finalize` message en vez de CloseStream (Deepgram docs: Finalize flushes buffered audio y responde `from_finalize`), o usar `speech_final`/`is_final` logic para detectar fin sin Metadata.

### 4.4 Nova-3 vs Nova-2 (finalize y latencia)

**Evidencia:** Deepgram blog: "Nova-3 ... sub-300 ms streaming latency", "parity latency with Nova-2 (< 300 ms)", "+<10 ms overhead". Pero el bench midió nova-2 finalize 355 ms. El cambio a nova-3 (quick win) puede haber empeorado finalize (weather 2.04 s). No está claro si nova-3 es más lento en finalize o si es el SETTLE_S.

**Recomendación:** medir nova-2 vs nova-3 con SETTLE_S reducido antes de decidir. Nova-2 sigue siendo el más rápido para IVR/agentes según Deepgram ("Pick Nova-2 for sub-second interactivity").

### 4.5 Jev regex no matchea "¿a qué hora es?" (+0.58 s)

**Evidencia:** `_TIME_RE` tiene `(?<!a\s)que\s+hora\s+(?:es|son)` con negative lookbehind "a ". "¿a qué hora es?" folded = "a que hora es?" → el "a " precede "que", así el lookbehind falla. Cae a Jev network (0.58 s). 

**Por qué existe el lookbehind:** evitar "a qué hora es la reunión" (scheduling). Pero ese caso no termina en "es/son" (termina en "reunión"), así que el terminal anchor `\s*[.!?]*\s*$` ya lo filtra. El lookbehind es redundante y harmful.

**Fix:** quitar `(?<!a\s)` del regex. Impacto: 0.58 s en la pregunta más común.

### 4.6 Weather cold fetch (~5.9 s) + cache

**Evidencia:** bench: weather cold 5.9 s, warm 232 ms. Ya cacheado (quick win, TTL 10 min). Pero el cold sigue siendo problema si cache expira o primera carga. El fetch a wttr.in puede tardar.

**Opciones:** pre-warm al startup, usar un provider más rápido (OpenWeatherMap con API key), o aumentar TTL.

### 4.7 TTS Aura-2 batch-burst (~700 ms corto, ~1800 ms largo)

**Evidencia:** bench: TTS TTFB escala con longitud de texto (15 chars 679 ms, 74 chars 1798 ms). Aura-2 emite el audio de la frase entera como burst, no progresivamente. Cekura/Speko benchmarks: Aura-2 median 263 ms, p95 525 ms (inglés). En español (antonia-es) tu bench midió 679-1798 ms. Puede ser la voz o la medición.

**Opciones:** frases más cortas como primer speech unit (clause splitting), voz Aura-2 más rápida (re-benchmark las 10 voces españolas), o swap a Cartesia sonic-3.5 (~173 ms), Inworld (~118 ms), Flux TTS (~194 ms), ElevenLabs Flash. Provider abstraction ya soporta swap.

### 4.8 LLM reply de una frase larga (~2.5 s antes de TTS)

**Evidencia:** bench conclusión 3. El sanitizer sostiene una frase hasta confirmar boundary (.!? + espacio). Un reply de una sola frase larga no emite nada hasta el flush final.

**Opciones:** clause-level splitting (coma/cláusula) con mínimo de chars, como Pipecat/LiveKit hacen. Impacto alto para LLM turns.

### 4.9 Speculative tool execution (avanzado)

**Evidencia:** paper "Hiding Tool Latency in On-Device Cascaded Voice Agent through Speculative Execution" (arxiv 2610.07641): reduce TTFA de 5.79 s a 4.60 s prediciendo tool calls de partial ASR. Complejo, requiere predictor module. TUKI ya tiene Jev que clasifica intent; se podría extender para pre-ejecutar tools (ej. Weather) mientras se habla. Pero riesgo alto.

### 4.10 Prompt: primera oración corta

**Evidencia:** AssemblyAI/Pipecat: "Keep replies short. The agent speaks after the first sentence, so a short first sentence starts the audio sooner." El prompt ya dice "One short sentence per thought". Reforzar con límite de palabras en la primera oración.

---

## 5. Quick wins ya aplicados (commit `85cbfdd`)

- Weather cache (TTL 10 min) — evita cold 5.9 s en turnos repetidos.
- STT default nova-3 — candidato a finalize más rápido (por medir).
- TTFB metric fix (FailoverTTSSession delega first_audio_s/t_first_push) — ya no reporta -0.00 s.
- Jev race ya implementado en la rama (commit `cdbf63e`).

---

## 6. Plan recomendado (fases)

### Fase 1 — Low risk, high impact (1-2 días)
1. Fix Jev regex (quitar lookbehind) → 0.58 s en time queries.
2. Reducir SETTLE_S de 2.0 a 0.6-0.8 s → evita finalize de 2 s.
3. Reducir endpointing de 600 a 300 ms → finalize más rápido (tradeoff: puede split wake word; mitigar con client VAD).
4. Clause-level splitting en sanitizer (configurable, on en voice) → LLM turn 5.3 → ~1.5 s.
5. Reforzar prompt: primera oración ≤ 12 palabras.

### Fase 2 — Medium risk, high impact (3-5 días)
6. STT connect solapado: pre-abrir socket al inicio del turn (en paralelo con TTS connect), o persistir conexión con KeepAlive entre turns.
7. VAD endpointing: reducir client wait (VAD_STOP_FRAMES a 4-5, o usar endpointing semántico). Medir cut-off rate.
8. TTS voice re-benchmark: medir las 10 voces Aura-2 españolas (TTFB por voz). Si ninguna baja de 500 ms, evaluar swap a Cartesia/Inworld/Flux.

### Fase 3 — Advanced (1-2 semanas)
9. Migrar STT a Deepgram Flux STT (EOT integrado, -200-600 ms) — requiere cambiar el protocolo de eventos (EagerEndOfTurn).
10. Speculative tool execution: pre-ejecutar Weather/Time mientras se habla (extender Jev).
11. Pre-warm weather al startup + cache más largo.

### Fase 4 — Observabilidad
12. Fix/validar TTFB metric (hecho) y añadir métricas por fase (connect/listen/finalize/ttfb/speech→audio) al log estructurado.
13. Re-correr bench (`scripts/bench_voice_latency.py`) tras cada fase para medir impacto real.

---

## 7. Riesgos y tradeoffs

| Cambio | Riesgo | Mitigación |
|--------|--------|------------|
| Reducir SETTLE_S | Cortar finals tardíos (transcript incompleto) | Usar Metadata break cuando llegue; fallback a 0.6 s solo si no Metadata |
| endpointing 600→300 | Split del wake word en su propio segmento | Client VAD ya filtra; probar con utterances reales |
| VAD_STOP_FRAMES 6→4 | Cortes prematuros (cut-off) | Medir cut-off rate; context-dependent thresholds |
| Clause splitting | TTS naturalidad (frases cortas) | Mínimo de chars (40) + solo primera cláusula; tests de sanitize |
| Swap TTS provider | Coste, voz diferente, latencia de red | Bench antes; mantener Deepgram como fallback |
| Flux STT | Cambio de protocolo, EOT events | Feature flag, fallback a Nova |
| Nova-2 vs Nova-3 | Accuracy vs latencia | Medir WER y finalize en español |

---

## 8. Métricas a trackear (por turn)

- `vad client wait` (s) — silence + queue drain
- `stt connect` (ms)
- `stt listen` (ms) — duración del utterance
- `stt finalize` (ms)
- `intent` + tiempo (+X s) y confianza
- `tts ttfb` (ms) — primer audio
- `speech→audio` (s) — perceived latency (el KPI principal)
- `total` (s) — incluye audio playback
- `ttfb` (ms) — TTS first byte (ahora medido correctamente)
- Cut-off rate (false interruptions) — medir offline

**KPI principal: speech→audio < 1.0 s en fast path, < 2.0 s en LLM turn.**

---

## 9. Referencias

### Internas
- `odd/tasks/voice-latency-bench.md` (bench real-API, 2026-11-11)
- `scripts/bench_voice_latency.py`
- `backend/ai/voice/agent.py`, `fast_path.py`
- `backend/ai/stt/deepgram.py`, `backend/ai/tts/deepgram.py`, `backend/ai/tts/sanitize.py`
- `frontend/wake_script.js`

### Externas
- Deepgram: "Measuring STT latency" (transcript vs EOT latency, connection latency, buffer size)
- Deepgram: "Finalize" message (flush buffered audio)
- Deepgram: "Model comparison: Nova-2 vs Nova-3" (parity latency, <10 ms overhead; Nova-2 fastest for IVR)
- Deepgram: "Turn detection in voice agents: silence timeouts vs VAD vs semantic models"
- Deepgram: Flux STT (EOT integrado, -200-600 ms)
- Multigrid: "Voice Activity Detection and Endpointing" (min_silence pure additive latency)
- LiveKit: turn detection tuning (min_delay/max_delay, dynamic mode)
- OpenAI Realtime VAD (silence_duration_ms, semantic VAD)
- Pipecat: sentence aggregation, TOKEN vs SENTENCE mode, clause-level splitting
- AssemblyAI: streaming LLM in voice pipeline (clause-by-clause synthesis)
- arxiv 2603.05413: "Building Enterprise Realtime Voice Agents" (cascaded pipeline, sentence buffer, 947 ms P50 TTFA)
- arxiv 2610.07641: "Hiding Tool Latency via Speculative Execution" (5.79→4.60 s)
- Twilio: "Guide to Core Latency in AI Voice Agents" (mouth-to-ear turn gap, targets)
- Benchmarks: Coval (Aura-2 263 ms median / 525 ms p95 TTFB), Speko, Cekura
- Stivers et al. PNAS 2009: turn-taking response offsets ~200 ms

---

## 10. Próximo paso sugerido

Antes de editar: confirmar con el usuario qué fase atacar. Recomendación: **Fase 1 completa** (regex fix + SETTLE_S + endpointing + clause splitting + prompt), que debería bajar fast path de 3.25 s a ~1.0-1.5 s con riesgo bajo. Medir con bench + logs tras cada cambio.

---

## 11. Fase 2 — Investigación (2.1 implementado, 2.2/2.3/2.4 analizados)

### 2.1 STT connect solapado — IMPLEMENTADO (commit pendiente)
Pre-abre el socket Deepgram en paralelo con el handshake TTS (`stt_preconnect_task`), igual que ya se hace con TTS. El connect (0.4-1.9 s) se solapa con el client wait / TTS handshake en vez de ser pure latency. Código en `backend/ai/stt/deepgram.py` (preconnect + _run_websocket con preopened_ws) y `backend/ai/voice/agent.py`.

### 2.2 VAD queue drain — ANÁLISIS
**Hallazgo:** El client wait (1.85-1.95 s) se descompone en:
- VAD (4 frames × 80 ms = 320 ms) — reducible a 2-3 frames (~160-240 ms) con riesgo de cortes.
- **Pre-roll replay (13 frames × 80 ms = 1.04 s)** — el mayor componente. El ring buffer (13 frames = 1.04 s) se envía a realtime (80 ms/frame) para que Deepgram reciba el wake word + inicio del comando.
- Queue drain restante (~0.5 s) — procesamiento del browser.

**Por qué existe el pre-roll:** "Dropping it lost the first frames of FAST commands — the command often starts inside the ring, before the classifier fires" (comentario en wake_script.js). El wake word se captura en el ring y se envía para que Deepgram transcriba "alexa ¿qué hora es?" y el server strippee "alexa".

**Opciones:**
- Reducir PREROLL_FRAMES de 13 a 6-8 (0.48-0.64 s). Ahorro ~0.4-0.6 s. Riesgo: puede perder el inicio de comandos rápidos. Mitigación: probar con 8 frames y medir.
- Enviar pre-roll a 2x velocidad. Deepgram soporta hasta 1.25x realtime (docs). Riesgo de backlog si se excede. Ahorro ~0.5 s.
- Eliminar pre-roll y confiar en server-side wake word stripping. El comando se captura desde la detección del wake word. Riesgo: pierde el inicio de comandos que empiezan antes de que el classifier dispare. Pero el server strippee el wake word de todos modos, así que sin pre-roll Deepgram solo recibe el comando (más limpio).

**Recomendación:** Probar PREROLL_FRAMES = 8 primero (bajo riesgo, ~0.4 s ahorro). Si funciona, probar 5.

### 2.3 TTS read error "no close frame received or sent" — ANÁLISIS
**Hallazgo:** El error ocurre en turns largos (>10 s de audio STT). Deepgram Aura-2 cierra el WebSocket sin enviar un close frame (probablemente por timeout de inactividad: Deepgram cierra conexiones sin audio en 10 s).

**Causa raíz:** El TTS socket se abre al inicio del turn (`session.start()` en paralelo con STT). Si el STT tarda >10 s (utterance larga), el TTS socket no recibe audio y Deepgram lo cierra por inactividad (NET-0001/NET-0002).

**Opciones:**
- Enviar KeepAlive messages al TTS socket durante la espera (`{"type": "KeepAlive"}` cada 5 s). Deepgram docs: "Send a KeepAlive message periodically to keep the connection open."
- Abrir el TTS socket más tarde (justo antes del primer push_text), sacrificando el solapamiento. Pero el handshake (~0.4 s) se añadiría al primer audio.
- Manejar el cierre abrupto gracefully en `_read()` (ya se hace: log + put None). El error es cosmético pero puede dejar el turn sin audio si ocurre mid-stream.

**Recomendación:** Implementar KeepAlive en `DeepgramTTTSession` (enviar cada 5 s mientras no hay audio). Bajo riesgo, resuelve el timeout.

### 2.4 TTS voice re-benchmark — PENDIENTE
No se ha medido. El bench original solo midió antonia-es. Necesita modificar `scripts/bench_voice_latency.py` para iterar las 10 voces Aura-2 españolas y reportar TTFB por voz. Si alguna voz es consistentemente <400 ms, swapear.

**Voces Aura-2 españolas (según Deepgram docs):** antonia-es, valerio-es, celeste-es, etc. (10 voces). El bench midió antonia-es en 679-1798 ms. Deepgram's benchmarks (inglés) dicen 263 ms mediana. La diferencia puede ser la voz o la medición (batch-burst real).

### 2.5 Persistencia de conexiones — POSPUESTO
Se discutirá después. Requiere KeepAlive + manejo de reconexión. Complejidad alta.

---

## 12. Proyección actualizada

| Fase | speech→audio (fast path) | speech→audio (LLM turn) | Riesgo |
|------|------------------------|----------------------|--------|
| Antes (baseline) | 3.25 s | 4.96 s | — |
| Fase 1 (aplicada) | 2.59 s | 4.96 s (first text +0.97) | Bajo |
| Fase 2.1 (connect solapado) | ~2.0-2.2 s | ~4.5 s | Medio |
| Fase 2.2 (pre-roll 13→8) | ~1.6-1.8 s | ~4.0 s | Medio |
| Fase 2.3 (KeepAlive TTS) | sin cambio directo | sin cambio directo | Bajo |
| Fase 2.4 (voz más rápida) | ~1.2-1.6 s | ~3.5-4.0 s | Bajo |
| Fase 3 (Flux + Cartesia + semantic) | 0.3-0.6 s | 0.8-1.5 s | Alto |