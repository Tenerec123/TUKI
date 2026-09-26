// T.U.K.I. WAKE WORD — browser-side wake word prototype.
//
// Runs the openWakeWord pipeline (melspectrogram -> embedding -> alexa)
// entirely in the browser via onnxruntime-web WASM. When the wake word is
// detected, the follow-up utterance is STREAMED as 16 kHz PCM to the backend
// over a WebSocket (/api/ai/voice-agent-ws), so the backend can forward it to
// Deepgram in real time while the user still speaks. The backend synthesizes
// the reply SENTENCE BY SENTENCE: each sentence WAV arrives as its own binary
// frame and is queued for sequential playback with plain <audio> elements, so
// the user hears the first sentence while the LLM is still generating the rest.
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

const WS_URL = `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/api/ai/voice-agent-ws`;

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
// calling onUtterance; 25 frames = ~2 s of silence.
const VAD_STOP_FRAMES = 25;
// Hard cap on capture duration: never buffer longer than this (seconds),
// even if the VAD never reports silence.
const MAX_CAPTURE_DURATION = 20;

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
let phrasesReceived = 0;      // phrase WAVs received for the current reply

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
// Voice agent call: STREAM the utterance as 16 kHz mono PCM over a WebSocket
// (/api/ai/voice-agent-ws). The backend forwards each binary frame to
// Deepgram's WebSocket in real time, so transcription starts while the user
// is still speaking.
//
// Flow: wake word detected -> startVoiceStream() opens the socket; every mic
// frame is pushed by pushAudioFrame(); VAD end -> finishVoiceStream() sends
// {"type":"end"} so the backend can answer; each sentence WAV arrives as its
// own binary frame and is queued for sequential playback.
// ---------------------------------------------------------------------------
function startVoiceStream() {
  if (streaming) return; // already streaming (another callback fired early)
  streaming = true;
  bytesStreamed = 0;
  phrasesReceived = 0;
  setStatus("PROCESSING");

  ws = new WebSocket(WS_URL);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    // Socket is up; mic frames pushed by pushAudioFrame() land here.
  };

  ws.onmessage = (event) => {
    if (typeof event.data === "string") {
      // JSON error frame from the backend.
      let detail = event.data;
      try {
        const parsed = JSON.parse(event.data);
        if (parsed.detail) detail = parsed.detail;
      } catch { /* not JSON — log the raw text */ }
      log(`Voice agent error: ${detail}`, "error");
      if (running) {
        setStatus("LISTENING");
        log("Reverted to LISTENING — say the wake word again.", "info");
      }
      return;
    }
    // Binary frame: a WAV for one phrase — enqueue for sequential playback.
    // Multiple frames arrive over time; the socket stays open until the whole
    // reply has been streamed, so do NOT treat each frame as the final one.
    if (!running) {
      // Engine stopped after this frame was dispatched: discard it, otherwise
      // it would play and set the status over IDLE.
      log("Phrase WAV discarded — engine stopped.", "info");
      return;
    }
    const bytes = event.data.byteLength;
    phrasesReceived++;
    log(`Phrase WAV received (${bytes} bytes)`);
    enqueueWav(event.data);
  };

  ws.onerror = () => {
    log(`Voice agent socket error`, "error");
  };

  ws.onclose = () => {
    ws = null;
    if (streaming) {
      // Socket closed mid-utterance (server went away before "end") — bail.
      streaming = false;
      bytesStreamed = 0;
      if (running) {
        setStatus("LISTENING");
        log("Reverted to LISTENING — say the wake word again.", "info");
      }
      return;
    }
    // Normal end of the reply: the server streamed every phrase and closed the
    // socket. Append the closing tail as the last queued item so the response
    // settles naturally (see CLOSING_TAIL_SECONDS).
    if (phrasesReceived > 0) enqueueWav(makeSilenceWav(CLOSING_TAIL_SECONDS));
  };
}

function pushAudioFrame(frame) {
  if (!streaming || !ws) return;
  // Copy the frame: the Microphone may reuse the underlying buffer after the
  // callback returns, but the socket sends asynchronously.
  const copy = new Uint8Array(frame.byteLength);
  copy.set(new Uint8Array(frame.buffer, frame.byteOffset, frame.byteLength));
  bytesStreamed += copy.byteLength;
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(copy);
  }
  // CONNECTING: drop the frame — the socket opens in milliseconds and the
  // first few frames are negligible, so no buffering keeps this simple.
}

async function finishVoiceStream(label) {
  if (!streaming) return;
  streaming = false;

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
    // Queue drained — back to listening.
    isPlaying = false;
    if (running) {
      setStatus("LISTENING");
      log("Response finished — back to LISTENING.", "info");
    }
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
  // remains audible even though onDetection happens outside that gesture.
  try {
    await prepareConfirmationAudio();
  } catch (err) {
    log(`Confirmation sound unavailable: ${err}`, "error");
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
      if (!running || isPlaying || streaming) return; // one confirmation per command
      setStatus("WAKE WORD DETECTED");
      log(`DETECTED "${label}" score=${score.toFixed(3)} — streaming command...`, "detection");
      playConfirmationTone();
      startVoiceStream();
    },
    onUtterance: async ({ label }) => {
      if (!running || isPlaying) return; // ignore while a response is playing
      await finishVoiceStream(label);
    },
  });

  // Microphone feed: every 80 ms frame of Int16 PCM@16 kHz goes to predict().
  microphone = new Microphone(async (frame) => {
    try {
      const predictions = await engine.predict(amplifyFrame(frame));
      const score = predictions[WAKE_MODEL];
      if (typeof score === "number") setScore(score);
      // While an utterance is streaming, forward every mic frame to the
      // backend in real time (copied: frames may be reused after the call).
      if (streaming) pushAudioFrame(frame);
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
  ws?.close();
  ws = null;
  bytesStreamed = 0;
  // Cleared so the onclose of the socket we just closed cannot see the
  // discarded reply's count and enqueue a closing tail that would set the
  // status to RESPONSE PLAYING after IDLE.
  phrasesReceived = 0;
  // Barge-in: clear the queue and silence the currently playing sentence.
  playbackQueue = [];
  isPlaying = false;
  if (currentAudio) {
    currentAudio.pause();
    currentAudio = null;
  }
  await closeConfirmationAudio();
  try {
    if (microphone) await microphone.stop();
  } catch (err) {
    log(`Mic stop error: ${err}`, "error");
  }
  microphone = null;
  engine = null;
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