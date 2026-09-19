import * as ort from "onnxruntime-web";

// Silero VAD expects 480-sample (30 ms @ 16 kHz) chunks.
const VAD_FRAME = 480;

/**
 * Thin wrapper around the Silero VAD ONNX model.
 * Maintains the LSTM hidden/cell state between calls for accurate streaming.
 */
export class VAD {
  constructor(session) {
    this._session = session;
    // Discover I/O names from the session to be robust across ORT versions.
    this._inName = session.inputNames[0];   // "input"
    this._hName  = session.inputNames.find((n) => n === "h")  ?? session.inputNames[1];
    this._cName  = session.inputNames.find((n) => n === "c")  ?? session.inputNames[2];
    this._srName = session.inputNames.find((n) => n === "sr") ?? session.inputNames[3];
    this._outName = session.outputNames[0]; // speech probability
    this._hnName  = session.outputNames[1]; // updated h
    this._cnName  = session.outputNames[2]; // updated c
    // sr is a constant int64 scalar passed every call.
    this._sr = new ort.Tensor("int64", BigInt64Array.from([BigInt(16000)]), []);
    this.reset();
  }

  static async create(url, opts = {}) {
    const session = await ort.InferenceSession.create(url, opts);
    return new VAD(session);
  }

  /** Reset the LSTM state (call this when starting a new utterance). */
  reset() {
    this._h = new Float32Array(2 * 1 * 64);
    this._c = new Float32Array(2 * 1 * 64);
  }

  /**
   * Run VAD on a chunk of 16-bit PCM audio and return the average speech
   * probability over all complete 30 ms sub-frames.
   *
   * @param {Int16Array} int16 - 16 kHz audio; any length, partial tail ignored.
   * @returns {Promise<number>} speech probability in 0..1 (0 = silence, 1 = speech)
   */
  async predict(int16) {
    const scores = [];
    for (let i = 0; i + VAD_FRAME <= int16.length; i += VAD_FRAME) {
      const float = new Float32Array(VAD_FRAME);
      for (let j = 0; j < VAD_FRAME; j++) float[j] = int16[i + j] / 32767;

      const out = await this._session.run({
        [this._inName]: new ort.Tensor("float32", float, [1, VAD_FRAME]),
        [this._hName]:  new ort.Tensor("float32", this._h.slice(), [2, 1, 64]),
        [this._cName]:  new ort.Tensor("float32", this._c.slice(), [2, 1, 64]),
        [this._srName]: this._sr,
      });

      scores.push(out[this._outName].data[0]);
      this._h = Float32Array.from(out[this._hnName].data);
      this._c = Float32Array.from(out[this._cnName].data);
    }

    if (scores.length === 0) return 0;
    return scores.reduce((a, b) => a + b) / scores.length;
  }
}
