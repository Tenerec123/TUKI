// T.U.K.I. WAKE WORD — browser-side wake word prototype.
//
// Runs the openWakeWord pipeline (melspectrogram -> embedding -> alexa)
// entirely in the browser via onnxruntime-web WASM. When the wake word is
// detected, the follow-up utterance is STREAMED as 16 kHz PCM to the backend
// over a WebSocket (/api/ai/voice-agent-ws), so the backend can forward it to
// Deepgram in real time while the user still speaks. The reply travels back
// over the same socket in one of two shapes: with ?proto=2 a JSON pcm_start
// preamble declares the sample rate and the audio then arrives as raw PCM16
// binary frames scheduled through Web Audio; without the param each sentence
// arrives as its own WAV frame and is queued for sequential <audio> playback.
// Either way the first sentence is audible while the LLM is still generating
// the rest.
//
// Why WebSocket instead of HTTP streaming: browser streaming uploads (a
// ReadableStream request body with duplex "half") require HTTP/2, which
// requires TLS (Chrome enforces ALPN; Firefox silently sends an empty body
// over HTTP/1.1). WebSocket works over plain HTTP/1.1 with no TLS.
//
// Runtime constraints (secure context only):
//   - AudioWorklet + getUserMedia require http://localhost or https://.
//     Accessing this page over http://192.168.x.x will NOT work.

import { OpenWakeWord, configureOrt } from "openwakeword-web";
import { Microphone } from "openwakeword-web/microphone";

const WS_URL = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/api/ai/voice-agent-ws?proto=2`;

const statusEl = document.getElementById("status");
const toggleBtn = document.getElementById("toggle-btn");
const scoreEl = document.getElementById("score");
const logEl = document.getElementById("log");

const iconMic = `<i class="bi bi-mic"></i>`;
const iconMicFill = `<i class="bi bi-mic-fill"></i>`;

const WAKE_MODEL = "alexa";
const THRESHOLD = 0.5;

// Mic sensitivity for the WAKE MODEL ONLY: scale the captured int16 samples
// before feature extraction (3.0 = ~+9.5 dB). The raw frame is still streamed
// unamplified to the backend. If you still have to shout, raise this; if the
// wake word triggers on background noise, lower it. Clipping is guarded.
const INPUT_GAIN = 3.0;

// Utterance-end tuning (1 frame = 1280 samples @ 16 kHz = 80 ms).
// The engine waits for this many CONSECUTIVE silent VAD frames before
// calling onUtterance; 12 frames = ~960 ms of silence.
const VAD_STOP_FRAMES = 12;
// Hard cap on capture duration: never buffer longer than this (seconds),
// even if the VAD never reports silence.
const MAX_CAPTURE_DURATION = 20;

// Pre-roll: keep the last PREROLL_FRAMES mic frames (~1 s) so a detection
// can rewind past the wake word. The model only crosses the threshold AFTER
// the wake word has been said, so starting the capture at the detection
// frame loses whatever the user said in between. Nothing is trimmed — the
// whole ring ships with the utterance, wake word included.
const PREROLL_FRAMES = 13;        // 13 x 80 ms = 1.04 s

// Local wake-word confirmation tone. Generate it in the browser with Web Audio
// so detection feedback needs no backend request or audio-file download.
const CONFIRMATION_TONE_HZ = 880;
const CONFIRMATION_DURATION_SECONDS = 0.12;
const CONFIRMATION_GAIN = 0.08;

let engine = null;              // OpenWakeWord instance (recreated on each start)
let microphone = null;          // Microphone instance
let running = false;
let isPlaying = false;          // true while a sentence WAV is playing or queued
let playbackQueue = [];         // ArrayBuffer[] of sentence WAVs awaiting playback
let currentAudio = null;        // <audio> element currently playing a sentence
let confirmationAudioContext = null; // Web Audio context for the detection tone

// Streaming voice agent state (wake detected -> utterance end):
let ws = null;                 // WebSocket to /api/ai/voice-agent-ws
let streaming = false;         // true between wake detection and utterance end
let bytesStreamed = 0;         // audio bytes actually sent to the server
let bytesReceived = 0;         // audio bytes received from server (PCM or WAV)
let firstPcmReceived = false;  // first frame received for this reply
let pcmAudioCtx = null;
let pcmNextTime = 0;
let pcmGen = 0;                // reply generation; bumping invalidates scheduled sources
let pcmPending = 0;            // current generation's buffers scheduled but not ended
let pcmMode = false;
let pcmModeDecided = false;    // the first binary frame decided WAV vs PCM
let pcmSrcRate = 0;            // rate declared by the reply's pcm_start
let resampleTail = new Float32Array(0); // input samples carried across frames when resampling
let resampleFrac = 0;          // fractional read position carried across frames

// Pre-roll ring — always rotating, even while idle.
let ringFrames = [];
// Pre-roll snapshot plus the frames that arrive while the socket is still
// CONNECTING; flushed in one batch on open, then dropped.
let pendingFrames = null;

// The server applies ONE uniform treatment to every phrase (tail fade + short
// inter-phrase silence) and cannot know which phrase ends the reply, so the
// longer closing tail is applied here — the client is what knows the stream is
// over. Played as the last queued item, it also keeps the wake word re-armed
// only after the response has finished sounding.
const CLOSING_TAIL_SECONDS = 0.4;
// Silence is generated at this rate; a silent WAV lasts nframes/framerate, so
// the rate only needs to be self-consistent to get the duration right.
const SILENCE_WAV_RATE = 24000;

// ---------------------------------------------------------------------------
// Tiny console that mirrors key events to both the page log and the browser
// console (careful: browsers throttle console.errors from the same origin).
// ---------------------------------------------------------------------------
function setStatus(text, isError = false) {
  statusEl.textContent = text;
  statusEl.classList.toggle("error", isError);
}

function log(message, cls = "info") {
  const line = document.createElement("div");
  line.className = cls;
  const stamp = new Date().toTimeString().slice(0, 8);
  line.textContent = `[${stamp}] ${message}`;
  logEl.appendChild(line);
  logEl.scrollTop = logEl.scrollHeight;
  if (cls === "error") console.error(`[wake] ${message}`);
  else console.log(`[wake] ${message}`);
}

function setScore(value) {
  scoreEl.textContent = value === null ? "score: --" : `score: ${value.toFixed(2)}`;
}

// ---------------------------------------------------------------------------
// Wake-word confirmation tone: generated locally with Web Audio. The context is
// prepared from the start button's user gesture so browser autoplay rules do
// not suspend the sound when onDetection fires later.
// ---------------------------------------------------------------------------
async function prepareConfirmationAudio() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    log("Confirmation sound unavailable: Web Audio is not supported.", "error");
    return false;
  }

  if (!confirmationAudioContext || confirmationAudioContext.state === "closed") {
    confirmationAudioContext = new AudioContextClass();
  }
  if (confirmationAudioContext.state !== "running") {
    await confirmationAudioContext.resume();
  }
  return confirmationAudioContext.state === "running";
}

// The PCM player's AudioContext is created and resumed from the start button's
// gesture, so autoplay rules do not suspend reply playback later. The per-reply
// rate check lives in configurePcmPlayer: a context is pinned to one rate.
async function preparePcmAudio() {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) {
    log("PCM playback unavailable: Web Audio is not supported.", "error");
    return false;
  }

  if (!pcmAudioCtx || pcmAudioCtx.state === "closed") {
    pcmAudioCtx = new AudioContextClass();
  }
  if (pcmAudioCtx.state !== "running") {
    await pcmAudioCtx.resume();
  }
  return pcmAudioCtx.state === "running";
}

async function playConfirmationTone() {
  try {
    if (!(await prepareConfirmationAudio())) return;

    const context = confirmationAudioContext;
    const now = context.currentTime;
    const oscillator = context.createOscillator();
    const gain = context.createGain();

    oscillator.type = "sine";
    oscillator.frequency.setValueAtTime(CONFIRMATION_TONE_HZ, now);
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(CONFIRMATION_GAIN, now + 0.01);
    gain.gain.setValueAtTime(CONFIRMATION_GAIN, now + CONFIRMATION_DURATION_SECONDS - 0.02);
    gain.gain.linearRampToValueAtTime(0, now + CONFIRMATION_DURATION_SECONDS);

    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start(now);
    oscillator.stop(now + CONFIRMATION_DURATION_SECONDS);
    oscillator.addEventListener("ended", () => {
      oscillator.disconnect();
      gain.disconnect();
    }, { once: true });
  } catch (err) {
    log(`Confirmation sound failed: ${err}`, "error");
  }
}

async function closeConfirmationAudio() {
  if (!confirmationAudioContext) return;
  try {
    await confirmationAudioContext.close();
  } catch (err) {
    log(`Confirmation audio close error: ${err}`, "error");
  } finally {
    confirmationAudioContext = null;
  }
}

// ---------------------------------------------------------------------------
// Secure context check: AudioWorklet + getUserMedia need it.
// ---------------------------------------------------------------------------
if (!window.isSecureContext) {
  setStatus("ERROR", true);
  toggleBtn.disabled = true;
  log(
    "INVALID CONTEXT: AudioWorklet and getUserMedia require a secure context. " +
    "Open this page via http://localhost or an https:// origin. " +
    "An IP address like http://192.168.x.x will NOT work.",
    "error"
  );
}

// ---------------------------------------------------------------------------
// Pre-roll ring: the last PREROLL_FRAMES frames. The worklet posts a fresh
// slice per frame (mic-worklet.js:44), so holding references is safe — no
// copy needed.
// ---------------------------------------------------------------------------
function pushRing(frame) {
  ringFrames.push(frame);
  if (ringFrames.length > PREROLL_FRAMES) ringFrames.shift();
}

// Send the held pre-roll as soon as the socket can take it.
function flushPending() {
  if (!pendingFrames || !ws || ws.readyState !== WebSocket.OPEN) return;
  const frames = pendingFrames;
  pendingFrames = null;
  for (const frame of frames) sendFrame(frame);
}

// ---------------------------------------------------------------------------
// Voice agent call: STREAM the utterance as 16 kHz mono PCM over a WebSocket
// (/api/ai/voice-agent-ws). The backend forwards each binary frame to
// Deepgram's WebSocket in real time, so transcription starts while the user
// is still speaking.
//
// Flow: wake word detected -> the ring is frozen into pendingFrames at the
// wake word's onset and startVoiceStream() opens the socket; frames keep
// joining pendingFrames until the socket is OPEN, then the whole batch goes
// out and live frames follow via pushAudioFrame(); VAD end ->
// finishVoiceStream() sends {"type":"end"} so the backend can answer. The
// reply arrives either as a JSON pcm_start preamble followed by raw PCM16
// frames (?proto=2, played by the Web Audio PCM player) or as one binary WAV
// per sentence (legacy, queued for sequential <audio> playback).
// ---------------------------------------------------------------------------
function startVoiceStream() {
  if (streaming) return; // already streaming (another callback fired early)
  streaming = true;
  bytesStreamed = 0;
  bytesReceived = 0;
  firstPcmReceived = false;
  setStatus("PROCESSING");

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    // Socket is up: release the pre-roll held while it was CONNECTING.
    flushPending();
  };

  // New reply: invalidate the previous generation's scheduled sources. The
  // AudioContext is deliberately kept — closing it per reply would throw away
  // the context unlocked in the start-button gesture (see preparePcmAudio).
  pcmMode = false;
  pcmModeDecided = false;
  pcmSrcRate = 0;
  pcmGen++;
  pcmPending = 0;
  pcmNextTime = 0;
  resetPcmResampler();

  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      // JSON frame: pcm_start or error
      let parsed;
      try {
        parsed = JSON.parse(event.data);
      } catch {
        log(`Voice agent message: ${event.data}`, "info");
        return;
      }
      if (parsed.type === "error") {
        log(`Voice agent error: ${parsed.detail || event.data}`, "error");
        if (running) {
          setStatus("LISTENING");
          log("Reverted to LISTENING — say the wake word again.", "info");
        }
        return;
      }
      if (parsed.type === "pcm_start") {
        pcmMode = true;
        configurePcmPlayer(parsed.sample_rate || 24000);
      }
      return;
    }
    // Binary frame
    if (!running) {
      log("Audio frame discarded — engine stopped.", "info");
      return;
    }
    const arrayBuffer = event.data;
    const bytes = arrayBuffer.byteLength;
    bytesReceived += bytes;
    if (!pcmModeDecided) {
      // The FIRST binary frame decides the mode for the whole reply; later
      // frames are never re-sniffed (a PCM chunk could start with "RIFF").
      pcmModeDecided = true;
      if (!pcmMode) {
        const first4 = new Uint8Array(arrayBuffer, 0, Math.min(4, bytes));
        const isRiff = first4[0] === 82 && first4[1] === 73 && first4[2] === 70 && first4[3] === 70;
        pcmMode = !isRiff;
      }
    }
    if (pcmMode) {
      if (!firstPcmReceived) {
        log(`PCM frame received (${bytes} bytes)`);
        firstPcmReceived = true;
      }
      enqueuePcm(arrayBuffer);
      return;
    }
    if (!firstPcmReceived) {
      log(`WAV received (${bytes} bytes)`);
      firstPcmReceived = true;
    }
    enqueueWav(arrayBuffer);
  };

  ws.onerror = () => {
    log(`Voice agent socket error`, "error");
  };

  ws.onclose = () => {
    ws = null;
    if (streaming) {
      // Socket closed mid-utterance (server went away before "end") — bail.
      streaming = false;
      pendingFrames = null;
      bytesStreamed = 0;
      closePcmPlayer();
      isPlaying = false; // the context close stopped any sounding audio
      if (running) {
        setStatus("LISTENING");
        log("Reverted to LISTENING — say the wake word again.", "info");
      }
      return;
    }
    // Normal end of the reply: socket closed after streaming. The closing
    // tail is appended as the last scheduled item so the response settles
    // naturally (see CLOSING_TAIL_SECONDS).
    if (bytesReceived > 0) {
      if (pcmMode) appendPcmSilence(CLOSING_TAIL_SECONDS);
      else enqueueWav(makeSilenceWav(CLOSING_TAIL_SECONDS));
    }
    if (pcmMode) {
      // Status stays RESPONSE PLAYING until the last buffer ends (drain).
      maybeFinishPcm(pcmGen);
    } else if (playbackQueue.length === 0 && !isPlaying) {
      finishReplyPlayback();
    }
  };
}

function sendFrame(frame) {
  // ws.send() serializes synchronously and the worklet hands out a fresh
  // slice per frame, so neither a copy nor a lifetime guard is needed.
  bytesStreamed += frame.byteLength;
  ws.send(new Uint8Array(frame.buffer, frame.byteOffset, frame.byteLength));
}

function pushAudioFrame(frame) {
  if (!streaming || !ws) return;
  // CONNECTING frames never reach here: pendingFrames holds them until
  // ws.onopen flushes the batch.
  if (ws.readyState === WebSocket.OPEN) sendFrame(frame);
}

async function finishVoiceStream(label) {
  if (!streaming) return;
  streaming = false;
  // A socket that opened late still owes us the pre-roll; after this the
  // batch is dead either way, so never leave pendingFrames behind.
  flushPending();
  pendingFrames = null;

  log(`Utterance streamed (${label}): ${(bytesStreamed / 2 / 16000).toFixed(1)} s of audio`);
  if (bytesStreamed === 0) {
    log("Empty utterance captured — skipping request.", "info");
    ws?.close();
    ws = null;
    if (running) setStatus("LISTENING");
    return;
  }

  // Tell the backend the utterance is complete; it answers with the WAV as a
  // binary frame handled by ws.onmessage.
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: "end" }));
  }
}

// ---------------------------------------------------------------------------
// Sequential playback queue: every phrase WAV arriving over the socket is
// enqueued and played one after the other (FIFO), so the user hears the first
// phrase while the LLM is still producing the rest.
// ---------------------------------------------------------------------------
function enqueueWav(wavBuffer) {
  playbackQueue.push(wavBuffer);
  if (!isPlaying) playNext();
}

// Build a silent mono 16-bit WAV of the requested duration, used as the
// closing tail so the reply ends with a natural pause instead of a hard stop.
function makeSilenceWav(seconds) {
  const frames = Math.round(SILENCE_WAV_RATE * seconds);
  const dataSize = frames * 2; // 1 channel * 16-bit
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);
  const writeAscii = (offset, text) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeAscii(0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeAscii(8, "WAVE");
  writeAscii(12, "fmt ");
  view.setUint32(16, 16, true); // PCM fmt chunk size
  view.setUint16(20, 1, true);  // audio format: PCM
  view.setUint16(22, 1, true);  // channels: mono
  view.setUint32(24, SILENCE_WAV_RATE, true);
  view.setUint32(28, SILENCE_WAV_RATE * 2, true); // byte rate
  view.setUint16(32, 2, true);  // block align
  view.setUint16(34, 16, true); // bits per sample
  writeAscii(36, "data");
  view.setUint32(40, dataSize, true); // samples are already zero
  return buffer;
}

function playNext() {
  if (playbackQueue.length === 0) {
    if (ws === null) finishReplyPlayback();
    else isPlaying = false;
    return;
  }
  isPlaying = true;
  setStatus("RESPONSE PLAYING");
  const wavBuffer = playbackQueue.shift();
  const blob = new Blob([wavBuffer], { type: "audio/wav" });
  const url = URL.createObjectURL(blob);
  const player = new Audio(url);
  currentAudio = player;

  const done = () => {
    if (currentAudio !== player) return; // superseded by stopEngine
    currentAudio = null;
    URL.revokeObjectURL(url);
    player.removeEventListener("ended", done);
    player.removeEventListener("error", done);
    playNext();
  };
  player.addEventListener("ended", done);
  player.addEventListener("error", done);
  player.play().catch((err) => {
    log(`Playback failed: ${err}`, "error");
    done(); // no "ended" event will fire — move to the next sentence
  });
}

// Backward-compatible wrapper: enqueue a single WAV for sequential playback.
function playResponse(wavBuffer) {
  enqueueWav(wavBuffer);
}

// PCM player (proto=2): raw PCM16 frames scheduled as AudioBufferSources on
// pcmAudioCtx. pcmGen stamps every scheduled source so a superseded reply's
// callbacks cannot touch state; pcmPending counts the current generation's
// sources that have not ended. The reply is drained — and only then is the
// wake word re-armed — when the socket is closed AND pcmPending hits zero.
function configurePcmPlayer(sampleRate) {
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextClass) return;
  try {
    pcmSrcRate = sampleRate;
    // A context is pinned to one rate: reuse only when it already matches the
    // reply's declared rate; otherwise close it and recreate (never orphan one).
    if (pcmAudioCtx && pcmAudioCtx.state !== "closed" && pcmAudioCtx.sampleRate !== sampleRate) {
      try {
        pcmAudioCtx.close();
      } catch (e) {
        log(`PCM context close error: ${e}`, "error");
      }
      pcmAudioCtx = null;
    }
    if (!pcmAudioCtx || pcmAudioCtx.state === "closed") {
      try {
        pcmAudioCtx = new AudioContextClass({ sampleRate });
      } catch (e) {
        // Browser rejected the rate: fall back — enqueuePcm resamples to
        // whatever rate the context actually runs at.
        pcmAudioCtx = new AudioContextClass();
      }
    }
    if (pcmAudioCtx.state !== "running") {
      pcmAudioCtx.resume().catch((err) => log(`PCM context resume failed: ${err}`, "error"));
    }
    resetPcmResampler();
    pcmNextTime = pcmAudioCtx.currentTime + 0.08; // prebuffer ~80ms
    isPlaying = true;
    setStatus("RESPONSE PLAYING");
  } catch (e) {
    console.error("PCM player config failed", e);
  }
}

// Queue one raw PCM16 frame (ArrayBuffer) for gapless playback, resampling
// linearly to the context's rate when the browser rejected the declared one.
function enqueuePcm(arrayBuffer) {
  if (!pcmAudioCtx || pcmAudioCtx.state === "closed") return;
  const gen = pcmGen;
  const ctx = pcmAudioCtx;
  const byteLength = arrayBuffer.byteLength;
  const inputCount = Math.floor(byteLength / 2);
  if (inputCount === 0) return;
  // Arm the wake guard BEFORE decoding: a decode error must not leave the
  // status claiming idle while buffers are already scheduled.
  isPlaying = true;
  setStatus("RESPONSE PLAYING");

  // Decode int16 little-endian; the odd trailing byte of a malformed frame
  // is dropped by the (i * 2 + 1) < byteLength guard.
  const dv = new DataView(arrayBuffer);
  const input = new Float32Array(inputCount);
  for (let i = 0; i < inputCount && (i * 2 + 1) < byteLength; i++) {
    input[i] = dv.getInt16(i * 2, true) / 32768;
  }

  const ratio = pcmSrcRate > 0 ? pcmSrcRate / ctx.sampleRate : 1;
  let output = input;
  if (ratio !== 1) {
    // Linear-interpolated resample with a carried tail, mirroring
    // frontend/vendor/openwakeword-web/mic-worklet.js so frame boundaries
    // neither drop nor duplicate samples when downsampling.
    const data = new Float32Array(resampleTail.length + input.length);
    data.set(resampleTail, 0);
    data.set(input, resampleTail.length);
    const out = new Float32Array(Math.ceil(data.length / ratio) + 2);
    let t = resampleFrac;
    let n = 0;
    while (Math.floor(t) + 1 < data.length) {
      const i = Math.floor(t);
      out[n++] = data[i] + (data[i + 1] - data[i]) * (t - i);
      t += ratio;
    }
    const keepFrom = Math.floor(t);
    resampleTail = data.slice(keepFrom);
    resampleFrac = t - keepFrom;
    output = out.subarray(0, n);
    if (output.length === 0) return; // everything carried into the tail
  }

  const audioBuffer = ctx.createBuffer(1, output.length, ctx.sampleRate);
  audioBuffer.getChannelData(0).set(output);
  const src = ctx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(ctx.destination);
  const startAt = Math.max(pcmNextTime, ctx.currentTime);
  src.start(startAt);
  pcmNextTime = startAt + audioBuffer.duration;
  if (pcmNextTime < ctx.currentTime) {
    pcmNextTime = ctx.currentTime;
  }
  pcmPending++;
  src.onended = () => onPcmSourceEnded(gen);
}

function appendPcmSilence(seconds) {
  if (!pcmAudioCtx || pcmAudioCtx.state === "closed" || seconds <= 0) return;
  const gen = pcmGen;
  const ctx = pcmAudioCtx;
  const samples = Math.round(ctx.sampleRate * seconds);
  if (samples <= 0) return;
  const audioBuffer = ctx.createBuffer(1, samples, ctx.sampleRate);
  const src = ctx.createBufferSource();
  src.buffer = audioBuffer;
  src.connect(ctx.destination);
  const startAt = Math.max(pcmNextTime, ctx.currentTime);
  src.start(startAt);
  pcmNextTime = startAt + audioBuffer.duration;
  pcmPending++;
  src.onended = () => onPcmSourceEnded(gen);
}

// One scheduled buffer of generation `gen` ended. A stale generation (a reply
// that has since been superseded) must not touch shared state.
function onPcmSourceEnded(gen) {
  if (gen !== pcmGen) return;
  pcmPending--;
  maybeFinishPcm(gen);
}

// Drain signal: socket closed AND every buffer of the current generation has
// ended — only then does the reply count as finished.
function maybeFinishPcm(gen) {
  if (gen !== pcmGen || ws !== null || pcmPending > 0) return;
  finishReplyPlayback();
}

// Reply playback fully drained: re-arm the wake word (isPlaying is what the
// onDetection guard checks).
function finishReplyPlayback() {
  isPlaying = false;
  if (running) {
    setStatus("LISTENING");
    log("Response finished — back to LISTENING.", "info");
  }
}

function resetPcmResampler() {
  resampleTail = new Float32Array(0);
  resampleFrac = 0;
}

function closePcmPlayer() {
  pcmGen++; // stale sources' onended callbacks must not touch state
  pcmPending = 0;
  pcmNextTime = 0;
  if (pcmAudioCtx) {
    try {
      pcmAudioCtx.close();
    } catch (e) {}
    pcmAudioCtx = null;
  }
}

// Amplify a mic frame for the wake model only (the raw frame is streamed to
// the backend unchanged). The melspectrogram consumes the int16 magnitudes
// as-is, so scaling the samples scales the features and therefore the score.
function amplifyFrame(frame) {
  if (INPUT_GAIN === 1) return frame;
  const out = new Int16Array(frame.length);
  for (let i = 0; i < frame.length; i++) {
    const v = frame[i] * INPUT_GAIN;
    out[i] = v > 32767 ? 32767 : v < -32768 ? -32768 : Math.round(v);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Wake word engine lifecycle (recreated on every start, kept simple).
// ---------------------------------------------------------------------------
async function startEngine() {
  setStatus("LOADING MODELS");
  log("Loading wake word engine... (models: python scripts/download_wake_models.py)");

  // Unlock audio during the start-button gesture so wake detection feedback
  // and reply playback remain audible even though they happen outside that
  // gesture.
  try {
    await prepareConfirmationAudio();
  } catch (err) {
    log(`Confirmation sound unavailable: ${err}`, "error");
  }
  try {
    await preparePcmAudio();
  } catch (err) {
    log(`PCM playback unavailable: ${err}`, "error");
  }

  // numThreads: 1 avoids the COOP/COEP headers required for shared memory.
  // Object form of wasmPaths (mjs + wasm) lets ORT dynamically import the
  // vendored jsep module; files are named .js (not .mjs) so the static file
  // server sends a valid JavaScript MIME type that browsers accept.
  configureOrt({
    wasmPaths: {
      mjs: "/frontend/vendor/onnxruntime-web/ort-wasm-simd-threaded.jsep.js",
      wasm: "/frontend/vendor/onnxruntime-web/ort-wasm-simd-threaded.jsep.wasm",
    },
    numThreads: 1,
  });

  engine = await OpenWakeWord.create({
    baseUrl: "/frontend/models/",
    wakewordModels: [WAKE_MODEL],
    threshold: THRESHOLD,
    vadStopFrames: VAD_STOP_FRAMES,
    maxCaptureDuration: MAX_CAPTURE_DURATION,
    onDetection: ({ label, score }) => {
      if (!running || streaming || ws !== null || isPlaying) return;
      setStatus("WAKE WORD DETECTED");
      log(`DETECTED "${label}" score=${score.toFixed(3)} — streaming command...`, "detection");
      // Freeze the pre-roll: the whole ring ships with this utterance.
      // Frames arriving while the socket connects join the same batch.
      pendingFrames = ringFrames.slice();
      log(`Pre-roll: ${pendingFrames.length}/${PREROLL_FRAMES} frames`);
      playConfirmationTone();
      startVoiceStream();
    },
    onUtterance: async ({ label }) => {
      if (!running) return;
      await finishVoiceStream(label);
    },
  });

  // Microphone feed: every 80 ms frame of Int16 PCM@16 kHz goes to predict().
  microphone = new Microphone(async (frame) => {
    try {
      const predictions = await engine.predict(amplifyFrame(frame));
      const score = predictions[WAKE_MODEL];
      if (typeof score === "number") setScore(score);
      pushRing(frame);
      // onDetection runs INSIDE predict(), so for the triggering frame
      // pendingFrames already exists and must receive it too.
      if (!streaming) return;
      if (pendingFrames) {
        pendingFrames.push(frame);
        flushPending(); // no-op until the socket reaches OPEN
      } else {
        pushAudioFrame(frame);
      }
    } catch (err) {
      log(`Predict error: ${err}`, "error");
    }
  });

  await microphone.start();
  running = true;
  setStatus("LISTENING");
  setScore(null);
  log(`Engine ready — listening for "${WAKE_MODEL}" (threshold ${THRESHOLD}).`, "info");
}

async function stopEngine() {
  running = false;
  // Abort any in-flight voice stream.
  streaming = false;
  pendingFrames = null;
  ws?.close();
  ws = null;
  bytesStreamed = 0;
  bytesReceived = 0;
  firstPcmReceived = false;
  pcmMode = false;
  pcmModeDecided = false;
  pcmSrcRate = 0;
  playbackQueue = [];
  isPlaying = false;
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  closePcmPlayer();
  await closeConfirmationAudio();
  try {
    if (microphone) await microphone.stop();
  } catch (err) {
    log(`Mic stop error: ${err}`, "error");
  }
  microphone = null;
  engine = null;
  // Stale frames from a previous run must not become the next pre-roll.
  ringFrames = [];
  setStatus("IDLE");
  setScore(null);
  log("Stopped. Engine will be recreated on next start.", "info");
}

// ---------------------------------------------------------------------------
// Button wiring (start also serves as the getUserMedia user gesture).
// ---------------------------------------------------------------------------
toggleBtn.addEventListener("click", async () => {
  if (running) {
    await stopEngine();
    toggleBtn.innerHTML = iconMic;
    toggleBtn.classList.remove("listening");
    return;
  }
  if (!window.isSecureContext) return; // already disabled + logged on load

  toggleBtn.disabled = true;
  try {
    await startEngine();
    toggleBtn.innerHTML = iconMicFill;
    toggleBtn.classList.add("listening");
  } catch (err) {
    await closeConfirmationAudio();
    setStatus("ERROR", true);
    log(`Engine failed to start: ${err}`, "error");
    log(
      "The AudioWorklet and ONNX fetches require a secure context — use " +
      "http://localhost or https:// (an http://LAN IP will not work).",
      "error"
    );
  } finally {
    toggleBtn.disabled = false;
  }
});