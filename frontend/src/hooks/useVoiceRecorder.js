import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Browser MediaRecorder hook.
 * Returns: { recording, elapsed, level, error, supported, start, stop, reset, blob, mimeType }
 *
 * Pipes audio bytes back as a Blob (audio/webm; codecs=opus when available).
 * Uses an AnalyserNode to expose a 0..1 level for live waveform display.
 */
export default function useVoiceRecorder({ maxSeconds = 90 } = {}) {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState("");
  const [blob, setBlob] = useState(null);
  const [mimeType, setMimeType] = useState("");

  const mediaRecorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);
  const startTimeRef = useRef(0);
  const tickRef = useRef(0);
  const audioCtxRef = useRef(null);
  const rafRef = useRef(0);
  const supported = typeof window !== "undefined" && typeof navigator !== "undefined" && !!navigator.mediaDevices && typeof window.MediaRecorder !== "undefined";

  const cleanup = useCallback(() => {
    if (tickRef.current) {
      clearInterval(tickRef.current);
      tickRef.current = 0;
    }
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = 0;
    }
    if (audioCtxRef.current) {
      try { audioCtxRef.current.close(); } catch (_) { /* noop */ }
      audioCtxRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
  }, []);

  useEffect(() => () => cleanup(), [cleanup]);

  const reset = useCallback(() => {
    setBlob(null);
    setElapsed(0);
    setLevel(0);
    setError("");
  }, []);

  const start = useCallback(async () => {
    setError("");
    setBlob(null);
    setElapsed(0);
    if (!supported) {
      setError("Your browser doesn't support recording. Try uploading an audio file or pasting text.");
      return false;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      streamRef.current = stream;
      const mt = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", ""].find((t) => !t || (window.MediaRecorder.isTypeSupported && window.MediaRecorder.isTypeSupported(t))) || "";
      const mr = mt ? new window.MediaRecorder(stream, { mimeType: mt }) : new window.MediaRecorder(stream);
      mediaRecorderRef.current = mr;
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = () => {
        const finalType = mr.mimeType || mt || "audio/webm";
        const b = new Blob(chunksRef.current, { type: finalType });
        setBlob(b);
        setMimeType(finalType);
      };
      mr.start();

      // Level meter
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        const ctx = new Ctx();
        audioCtxRef.current = ctx;
        const src = ctx.createMediaStreamSource(stream);
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        src.connect(analyser);
        const buf = new Uint8Array(analyser.frequencyBinCount);
        const tick = () => {
          analyser.getByteTimeDomainData(buf);
          let sum = 0;
          for (let i = 0; i < buf.length; i += 1) {
            const v = (buf[i] - 128) / 128;
            sum += v * v;
          }
          const rms = Math.sqrt(sum / buf.length);
          setLevel(Math.min(1, rms * 2.5));
          rafRef.current = requestAnimationFrame(tick);
        };
        rafRef.current = requestAnimationFrame(tick);
      } catch (_) {
        // level meter optional
      }

      startTimeRef.current = Date.now();
      tickRef.current = setInterval(() => {
        const sec = Math.floor((Date.now() - startTimeRef.current) / 1000);
        setElapsed(sec);
        if (sec >= maxSeconds) {
          // auto-stop
          try { mr.stop(); } catch (_) { /* noop */ }
          setRecording(false);
          cleanup();
        }
      }, 200);

      setRecording(true);
      return true;
    } catch (err) {
      const name = err && err.name;
      if (name === "NotAllowedError" || name === "PermissionDeniedError") {
        setError("Microphone permission denied. Use the upload or paste options instead.");
      } else if (name === "NotFoundError") {
        setError("No microphone found. Use the upload or paste options instead.");
      } else {
        setError("Could not start recording. Use the upload or paste options instead.");
      }
      cleanup();
      return false;
    }
  }, [supported, maxSeconds, cleanup]);

  const stop = useCallback(() => {
    try {
      const mr = mediaRecorderRef.current;
      if (mr && mr.state !== "inactive") mr.stop();
    } catch (_) {
      // noop
    }
    setRecording(false);
    cleanup();
  }, [cleanup]);

  return { recording, elapsed, level, error, supported, start, stop, reset, blob, mimeType };
}
