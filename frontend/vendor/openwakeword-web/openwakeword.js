import * as ort from "onnxruntime-web";
import { AudioFeatures, CHUNK } from "./audio-features.js";
import {
  FEATURE_MODELS,
  PRETRAINED_MODELS,
  VAD_MODELS,
  MODEL_CLASS_MAPPINGS,
} from "./models.js";
import { VAD } from "./vad.js";

const DEFAULT_INPUT_FRAMES = 16; // openWakeWord models use 16 feature frames

/**
 * Configure the ONNX Runtime Web environment. Call once before creating a model
 * if you want to point at self-hosted wasm binaries or tweak threading.
 * @param {{wasmPaths?: string, numThreads?: number, simd?: boolean}} opts
 */
export function configureOrt(opts = {}) {
  if (opts.wasmPaths !== undefined) ort.env.wasm.wasmPaths = opts.wasmPaths;
  if (opts.numThreads !== undefined) ort.env.wasm.numThreads = opts.numThreads;
  if (opts.simd !== undefined) ort.env.wasm.simd = opts.simd;
}

function readShapeDim(session, which, idx) {
  // Best-effort read of an input/output dimension across ort-web versions.
  const meta =
    which === "input"
      ? session.inputMetadata?.[0]
      : session.outputMetadata?.[0];
  const shape = meta?.shape ?? meta?.dimensions;
  const v = shape?.[idx];
  return typeof v === "number" && v > 0 ? v : null;
}

/**
 * Native browser port of `openwakeword.Model`.
 *
 * Runs the full melspectrogram -> embedding -> wake word pipeline client-side
 * using ONNX Runtime Web. No server required.
 */
export class OpenWakeWord {
  constructor() {
    this.models = {}; // name -> { session, inputName, inputFrames, outputClasses, classMapping }
    this.features = null;
    this.predictionBuffer = {}; // label -> number[] (max 30)
    this.threshold = 0.5;
    this.onDetection = null;

    // Utterance capture
    this.onUtterance = null;
    this.vadStopThreshold = 0.5;  // VAD score below this counts as silence
    this.vadStopFrames = 6;       // consecutive silent predict() calls before firing
    this.maxCaptureDuration = 10; // seconds — hard cap to prevent infinite buffering
    this._vad = null;
    this._captureState = "idle";  // "idle" | "capturing"
    this._captureBuffer = [];     // Int16Array frames collected since wakeword
    this._captureLabel = null;    // which wakeword triggered capture
    this._vadSilenceCount = 0;
    this._captureMinFrames = 0;   // countdown; don't check VAD stop until 0
  }

  /**
   * Create and initialise a model.
   *
   * @param {object} opts
   * @param {string} [opts.baseUrl="./models/"] Base URL/path for model files.
   * @param {Array<string|{name:string,url:string,inputFrames?:number,classMapping?:object}>} [opts.wakewordModels]
   *        Wake word models to load. Strings are looked up in the pre-trained
   *        registry (e.g. "hey_jarvis"); objects allow custom models by URL.
   *        Defaults to all pre-trained models.
   * @param {string} [opts.melspectrogramUrl] Override the melspectrogram model URL.
   * @param {string} [opts.embeddingUrl] Override the embedding model URL.
   * @param {string} [opts.vadUrl] Override the Silero VAD model URL.
   *        Required when {@link opts.onUtterance} is set and `baseUrl` does not
   *        resolve to a directory that contains `silero_vad.onnx`.
   * @param {string[]} [opts.executionProviders=["wasm"]] ORT execution providers.
   * @param {object} [opts.ort] Options forwarded to {@link configureOrt}.
   * @param {number} [opts.threshold=0.5] Wake word detection threshold.
   * @param {function} [opts.onDetection] Callback fired when a wake word is detected.
   * @param {function} [opts.onUtterance] Callback fired after a detected wake word
   *        followed by speech that has ended. Receives `{ label, audio }` where
   *        `audio` is an `Int16Array` of the captured PCM from detection to speech end.
   *        Requires `silero_vad.onnx` to be available at `baseUrl` (or `vadUrl`).
   * @param {number} [opts.vadStopThreshold=0.5] VAD score below which a frame
   *        is considered silence. Only used when `onUtterance` is set.
   * @param {number} [opts.vadStopFrames=6] Consecutive silent predict() calls
   *        (~480 ms) before the utterance is considered complete.
   * @param {number} [opts.maxCaptureDuration=10] Hard cap on capture duration
   *        in seconds. Fires `onUtterance` even if VAD has not gone silent.
   * @returns {Promise<OpenWakeWord>}
   */
  static async create(opts = {}) {
    const {
      baseUrl = "./models/",
      wakewordModels = Object.keys(PRETRAINED_MODELS),
      executionProviders = ["wasm"],
      ort: ortOpts,
      threshold = 0.5,
      onDetection = null,
      onUtterance = null,
      vadStopThreshold = 0.5,
      vadStopFrames = 6,
      maxCaptureDuration = 10,
    } = opts;

    if (ortOpts) configureOrt(ortOpts);

    const join = (file) =>
      /^https?:|^\.|^\//.test(file) ? file : baseUrl + file;
    const sessOpts = { executionProviders };

    const melspectrogramUrl = opts.melspectrogramUrl
      ? opts.melspectrogramUrl
      : join(FEATURE_MODELS.melspectrogram);
    const embeddingUrl = opts.embeddingUrl
      ? opts.embeddingUrl
      : join(FEATURE_MODELS.embedding);

    const instance = new OpenWakeWord();

    // Load feature models + create the streaming feature extractor.
    const [melspecSession, embeddingSession] = await Promise.all([
      ort.InferenceSession.create(melspectrogramUrl, sessOpts),
      ort.InferenceSession.create(embeddingUrl, sessOpts),
    ]);
    instance.features = new AudioFeatures(melspecSession, embeddingSession);

    // Load wake word models.
    for (const entry of wakewordModels) {
      let name, url, inputFrames, classMapping;
      if (typeof entry === "string") {
        name = entry;
        const file = PRETRAINED_MODELS[entry] || entry;
        url = join(file);
        classMapping = MODEL_CLASS_MAPPINGS[entry];
      } else {
        name = entry.name;
        url = /^https?:|^\.|^\//.test(entry.url) ? entry.url : join(entry.url);
        inputFrames = entry.inputFrames;
        classMapping = entry.classMapping || MODEL_CLASS_MAPPINGS[name];
      }

      const session = await ort.InferenceSession.create(url, sessOpts);
      const detectedFrames = readShapeDim(session, "input", 1);
      const outputClasses = readShapeDim(session, "output", 1) ?? 1;
      instance.models[name] = {
        session,
        inputName: session.inputNames[0],
        inputFrames: inputFrames ?? detectedFrames ?? DEFAULT_INPUT_FRAMES,
        outputClasses,
        classMapping: classMapping || null,
      };
    }

    instance.threshold = threshold;
    instance.onDetection = onDetection;
    instance.onUtterance = onUtterance;
    instance.vadStopThreshold = vadStopThreshold;
    instance.vadStopFrames = vadStopFrames;
    instance.maxCaptureDuration = maxCaptureDuration;

    // Load VAD model only when utterance capture is requested.
    if (onUtterance) {
      const vadUrl = opts.vadUrl ? opts.vadUrl : join(VAD_MODELS.silero_vad);
      instance._vad = await VAD.create(vadUrl, sessOpts);
    }

    await instance.features.warmup();
    return instance;
  }

  /** Names of the loaded wake word models. */
  get modelNames() {
    return Object.keys(this.models);
  }

  /** Reset all streaming/prediction state, including any in-progress capture. */
  async reset() {
    this.features.reset(true);
    await this.features.warmup();
    this.predictionBuffer = {};
    this._captureState = "idle";
    this._captureBuffer = [];
    this._captureLabel = null;
    this._vadSilenceCount = 0;
    this._captureMinFrames = 0;
    this._vad?.reset();
  }

  async _runModel(m, feat) {
    const tensor = new ort.Tensor("float32", feat.data, feat.dims);
    const out = await m.session.run({ [m.inputName]: tensor });
    const data = out[m.session.outputNames[0]].data;
    return Array.from(data); // length = outputClasses (the [0] row)
  }

  _pushPrediction(label, value) {
    if (!this.predictionBuffer[label]) this.predictionBuffer[label] = [];
    this.predictionBuffer[label].push(value);
    if (this.predictionBuffer[label].length > 30) {
      this.predictionBuffer[label].shift();
    }
  }

  _concatCapture() {
    const total = this._captureBuffer.reduce((s, f) => s + f.length, 0);
    const audio = new Int16Array(total);
    let offset = 0;
    for (const frame of this._captureBuffer) {
      audio.set(frame, offset);
      offset += frame.length;
    }
    return audio;
  }

  /**
   * Predict wake word scores for a frame of 16-bit PCM @ 16 kHz audio.
   * Ideally pass multiples of 1280 samples (80 ms).
   *
   * @param {Int16Array} x
   * @returns {Promise<Record<string, number>>} label -> score (0..1)
   */
  async predict(x) {
    if (!(x instanceof Int16Array)) {
      throw new TypeError("Input audio (x) must be an Int16Array of 16 kHz PCM.");
    }

    const nPrepared = await this.features.streamingFeatures(x);
    const predictions = {};

    for (const [name, m] of Object.entries(this.models)) {
      let prediction; // array of length outputClasses

      if (nPrepared > CHUNK) {
        const group = [];
        for (let i = Math.floor(nPrepared / CHUNK) - 1; i >= 0; i--) {
          const feat = this.features.getFeatures(
            m.inputFrames,
            -m.inputFrames - i
          );
          group.push(await this._runModel(m, feat));
        }
        prediction = group.reduce((acc, row) =>
          acc.map((v, idx) => Math.max(v, row[idx]))
        );
      } else if (nPrepared === CHUNK) {
        const feat = this.features.getFeatures(m.inputFrames);
        prediction = await this._runModel(m, feat);
      } else {
        // Not enough new samples yet: reuse the previous prediction.
        if (m.outputClasses === 1) {
          const buf = this.predictionBuffer[name];
          prediction = [buf && buf.length > 0 ? buf[buf.length - 1] : 0];
        } else {
          prediction = new Array(m.outputClasses).fill(0);
        }
      }

      if (m.outputClasses === 1) {
        predictions[name] = prediction[0];
      } else if (m.classMapping) {
        for (const [intLabel, cls] of Object.entries(m.classMapping)) {
          predictions[cls] = prediction[Number.parseInt(intLabel, 10)];
        }
      } else {
        for (let c = 0; c < m.outputClasses; c++) {
          predictions[`${name}_${c}`] = prediction[c];
        }
      }
    }

    // Zero out predictions for the first 5 frames during model warm-up.
    for (const label of Object.keys(predictions)) {
      if (!this.predictionBuffer[label] || this.predictionBuffer[label].length < 5) {
        predictions[label] = 0;
      }
    }

    // Update the prediction history buffers.
    for (const label of Object.keys(predictions)) {
      this._pushPrediction(label, predictions[label]);
    }

    // Check which labels (if any) exceed the detection threshold.
    let detectedLabel = null;
    for (const [label, score] of Object.entries(predictions)) {
      if (score >= this.threshold) {
        if (this.onDetection) this.onDetection({ label, score });
        if (detectedLabel === null) detectedLabel = label;
      }
    }

    // --- Utterance capture state machine ---
    if (this.onUtterance && this._vad) {
      if (this._captureState === "idle") {
        if (detectedLabel !== null) {
          // Wake word detected — start capturing.
          this._captureState = "capturing";
          this._captureLabel = detectedLabel;
          this._captureBuffer = [x.slice()];
          this._vadSilenceCount = 0;
          // Require at least ~240 ms of capture before checking for speech end
          // so we don't immediately fire on the silence between the wakeword
          // and the user's follow-up command.
          this._captureMinFrames = 3;
          this._vad.reset();
        }
      } else {
        // Currently capturing — buffer this frame and run VAD.
        this._captureBuffer.push(x.slice());

        const vadScore = await this._vad.predict(x);

        if (detectedLabel !== null) {
          // Re-triggered while capturing — reset silence counter so we don't
          // cut off mid-utterance if the user repeats the wake word.
          this._vadSilenceCount = 0;
        } else if (vadScore < this.vadStopThreshold) {
          this._vadSilenceCount++;
        } else {
          this._vadSilenceCount = 0;
        }

        const maxFrames = Math.ceil(this.maxCaptureDuration * 16000 / CHUNK);
        const silenceReached =
          this._captureMinFrames <= 0 &&
          this._vadSilenceCount >= this.vadStopFrames;
        const durationCapped = this._captureBuffer.length >= maxFrames;

        if (this._captureMinFrames > 0) this._captureMinFrames--;

        if (silenceReached || durationCapped) {
          const audio = this._concatCapture();
          const label = this._captureLabel;
          this._captureState = "idle";
          this._captureBuffer = [];
          this._captureLabel = null;
          this._vadSilenceCount = 0;
          this._captureMinFrames = 0;
          this.onUtterance({ label, audio });
        }
      }
    }

    return predictions;
  }
}

export { AudioFeatures } from "./audio-features.js";
export { VAD } from "./vad.js";
export * from "./models.js";
export default OpenWakeWord;
