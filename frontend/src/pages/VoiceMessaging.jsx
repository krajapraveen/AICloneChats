import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { copyToClipboard } from "../lib/clipboard";
import { shareText } from "../lib/share";
import { useAuth } from "../contexts/AuthContext";
import { useUpgradeToPro } from "../lib/upgrade";
import Navbar from "../components/Navbar";
import VoiceSignupWall from "../components/VoiceSignupWall";
import UsageLimitModal from "../components/UsageLimitModal";
import useVoiceRecorder from "../hooks/useVoiceRecorder";

const TONES = [
  { id: "concise", label: "Concise", desc: "Stripped to the essential" },
  { id: "professional", label: "Professional", desc: "Polished, decisive" },
  { id: "friendly", label: "Friendly", desc: "Warm, casual, human" },
  { id: "apology", label: "Apology", desc: "Sincere, owns it" },
  { id: "dating", label: "Dating", desc: "Chemistry, not coercive" },
  { id: "negotiation", label: "Negotiation", desc: "Firm, value-anchored" },
];

const REFINE_CHIPS = [
  { id: "shorter", label: "↘ Shorter" },
  { id: "confident", label: "⚡ More confident" },
  { id: "polite", label: "🌿 More polite" },
  { id: "flirty", label: "✨ More flirty" },
  { id: "professional", label: "🎯 More professional" },
];

const EXAMPLES = [
  "Tell my boss I'll be late",
  "Reply to my girlfriend after argument",
  "Politely reject a meeting",
  "Convert angry speech into calm message",
];

const ALLOWED_UPLOAD_EXTS = [".mp3", ".wav", ".m4a", ".webm", ".ogg", ".mp4"];
const STAGE_LABEL = {
  idle: "",
  transcribing: "Transcribing your voice…",
  cleaning: "Cleaning up the message…",
  generating: "Generating 6 smart replies…",
  refining: "Rewriting…",
  editing: "Updating transcript…",
};
// Duration (ms) after which we surface "Still working…" reassurance
const SLOW_PROCESSING_THRESHOLD_MS = 15000;
// Duration (ms) the "Voice captured successfully" splash stays visible
const CAPTURED_SPLASH_MS = 700;

function fmtTime(sec) {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function track(event_name, extra) {
  api.post("/voice/track", { event_name, ...(extra || {}) }).catch(() => { /* analytics best-effort */ });
}

export default function VoiceMessaging() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const upgradeToPro = useUpgradeToPro();
  const [tab, setTab] = useState("record"); // record | upload | text
  const [stage, setStage] = useState("idle");
  const [usage, setUsage] = useState(null);
  const [session, setSession] = useState(null); // {session_id, raw_transcript, cleaned_transcript, source_type}
  const [editedTranscript, setEditedTranscript] = useState("");
  const [transcriptSaving, setTranscriptSaving] = useState(false);
  const [messages, setMessages] = useState([]); // [{message_id, tone, message, copiedAt?, refining?}]
  const [textInput, setTextInput] = useState("");
  const [signupWall, setSignupWall] = useState(false);
  const [paywall, setPaywall] = useState(false);
  const [copiedId, setCopiedId] = useState("");

  // Post-recording processing UX (2026-05-11):
  //   capturedAt: timestamp set when audio is captured, used to show the
  //               brief "Voice captured successfully" splash and to measure
  //               total processing duration for analytics.
  //   isSlow:     becomes true after SLOW_PROCESSING_THRESHOLD_MS so we can
  //               append the "Still working… complex audio can take a little
  //               longer." reassurance below the primary banner.
  const [capturedAt, setCapturedAt] = useState(0);
  const [isSlow, setIsSlow] = useState(false);
  const processingStartRef = useRef(0);
  const slowTimerRef = useRef(null);

  const fileInputRef = useRef(null);
  const transcriptBlockRef = useRef(null);
  const messagesBlockRef = useRef(null);
  const recorder = useVoiceRecorder({ maxSeconds: 90 });

  // Initial usage + page-view
  useEffect(() => {
    track("voice_page_viewed");
    api.get("/voice/usage").then((r) => setUsage(r.data)).catch(() => { /* network — handled lazily */ });
  }, [user?.user_id]);

  // Cleanup the slow-banner timer if the component unmounts mid-flight
  useEffect(() => () => {
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
  }, []);

  // Auto-fade copy chip
  useEffect(() => {
    if (!copiedId) return;
    const t = setTimeout(() => setCopiedId(""), 1400);
    return () => clearTimeout(t);
  }, [copiedId]);

  // When recorder produces a blob, send it up
  useEffect(() => {
    if (recorder.blob && tab === "record" && stage === "idle") {
      submitAudio(recorder.blob, "recording", `recording_${Date.now()}.webm`);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recorder.blob]);

  const remainingPill = useMemo(() => {
    if (!usage) return null;
    if (usage.is_pro) return <span className="tag tag-violet" data-testid="voice-usage-pill">PRO · UNLIMITED</span>;
    if (usage.is_anonymous) {
      const left = usage.anon_remaining ?? 3;
      const klass = left === 0 ? "tag-rose" : left === 1 ? "tag-amber" : "tag-emerald";
      return <span className={`tag ${klass}`} data-testid="voice-usage-pill">{left} free trial{left === 1 ? "" : "s"} left</span>;
    }
    const left = usage.daily_remaining ?? 20;
    const klass = left === 0 ? "tag-rose" : left <= 5 ? "tag-amber" : "tag-emerald";
    return <span className={`tag ${klass}`} data-testid="voice-usage-pill">{left} / {usage.daily_limit ?? 20} left today</span>;
  }, [usage]);

  function refreshUsage(serverPayload) {
    if (!serverPayload) {
      api.get("/voice/usage").then((r) => setUsage(r.data)).catch(() => { /* ignore — UI fine without a fresh count */ });
      return;
    }
    setUsage((u) => ({
      ...(u || {}),
      is_anonymous: !!serverPayload.is_anonymous,
      anon_remaining: serverPayload.anon_remaining ?? (u?.anon_remaining ?? null),
      daily_remaining: serverPayload.daily_remaining ?? (u?.daily_remaining ?? null),
      is_pro: !!u?.is_pro,
    }));
  }

  function handleLimitError(err) {
    const status = err?.response?.status;
    const detail = err?.response?.data?.detail;
    const code = typeof detail === "object" ? detail?.code : null;
    if (status === 402 && code === "anon_limit_reached") {
      track("voice_signup_wall_shown");
      setSignupWall(true);
      return true;
    }
    if (status === 402 && code === "usage_limit_reached") {
      setPaywall(true);
      return true;
    }
    // Any other 402 (insufficient_balance, subscription_required, etc.) is
    // already surfaced by the global paywall modal mounted at the app root —
    // we just need to swallow the local generic error toast so the UI shows
    // ONE consistent paywall, not a toast + modal stacked on top of each other.
    if (status === 402) {
      return true;
    }
    return false;
  }

  // Begin a processing run: stamp the start time, reset slow flag, schedule
  // the slow-banner reveal, and emit start telemetry. Idempotent if called
  // twice within the same run.
  function beginProcessing(sourceType) {
    if (processingStartRef.current) return;
    const t0 = Date.now();
    processingStartRef.current = t0;
    setCapturedAt(t0);
    setIsSlow(false);
    if (slowTimerRef.current) clearTimeout(slowTimerRef.current);
    slowTimerRef.current = setTimeout(() => {
      setIsSlow(true);
      track("voice_processing_slow", { source_type: sourceType });
    }, SLOW_PROCESSING_THRESHOLD_MS);
    track("voice_processing_started", { source_type: sourceType });
  }

  function endProcessing(outcome /* "completed" | "failed" */, sourceType, failure_reason) {
    if (slowTimerRef.current) {
      clearTimeout(slowTimerRef.current);
      slowTimerRef.current = null;
    }
    const t0 = processingStartRef.current;
    processingStartRef.current = 0;
    const duration_ms = t0 ? Date.now() - t0 : 0;
    track(outcome === "completed" ? "voice_processing_completed" : "voice_processing_failed", {
      source_type: sourceType,
      processing_duration_ms: duration_ms,
      ...(failure_reason ? { failure_reason } : {}),
    });
    // Hold the "Voice captured successfully" splash a beat after completion so
    // the transition feels reassuring instead of a flicker
    setTimeout(() => setCapturedAt(0), 200);
    setIsSlow(false);
  }

  async function submitAudio(blob, sourceType, filename) {
    beginProcessing(sourceType);
    setStage("transcribing");
    setMessages([]);
    setSession(null);
    try {
      const fd = new FormData();
      fd.append("audio_file", blob, filename || "audio.webm");
      fd.append("source_type", sourceType);
      const { data } = await api.post("/voice/transcribe", fd, { headers: { "Content-Type": "multipart/form-data" } });
      setStage("cleaning"); // brief flash; cleaning already happened server-side, but show the stage
      setSession(data);
      setEditedTranscript(data.cleaned_transcript || "");
      refreshUsage(data);
      // immediately generate-all
      await runGenerateAll(data.session_id, sourceType);
      setTimeout(() => transcriptBlockRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
      endProcessing("completed", sourceType);
    } catch (err) {
      if (handleLimitError(err)) {
        endProcessing("failed", sourceType, "limit_reached");
        return;
      }
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : "Could not process audio. Try again.");
      endProcessing("failed", sourceType, "audio_error");
    } finally {
      setStage("idle");
      recorder.reset();
    }
  }

  async function submitText(text) {
    if (!text.trim()) return;
    beginProcessing("text");
    setStage("cleaning");
    setMessages([]);
    setSession(null);
    try {
      const { data } = await api.post("/voice/text-input", { text: text.trim() });
      setSession(data);
      setEditedTranscript(data.cleaned_transcript || "");
      refreshUsage(data);
      await runGenerateAll(data.session_id, "text");
      setTimeout(() => transcriptBlockRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
      endProcessing("completed", "text");
    } catch (err) {
      if (handleLimitError(err)) {
        endProcessing("failed", "text", "limit_reached");
        return;
      }
      const detail = err?.response?.data?.detail;
      toast.error(typeof detail === "string" ? detail : "Could not process text. Try again.");
      endProcessing("failed", "text", "text_error");
    } finally {
      setStage("idle");
    }
  }

  async function runGenerateAll(sessionId, sourceType) {
    setStage("generating");
    try {
      const { data } = await api.post("/voice/generate-all", { session_id: sessionId });
      const ordered = TONES.map((t) => (data.messages || []).find((m) => m.tone === t.id)).filter(Boolean);
      setMessages(ordered);
      setTimeout(() => messagesBlockRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (err) {
      if (!handleLimitError(err)) {
        toast.error("Could not generate messages. Try again.");
      }
    } finally {
      setStage("idle");
    }
  }

  async function regenerateOne(tone) {
    if (!session?.session_id) return;
    try {
      const { data } = await api.post("/voice/generate", { session_id: session.session_id, tone });
      setMessages((prev) => prev.map((m) => (m.tone === tone ? { ...m, message_id: data.message_id, message: data.generated_message } : m)));
      track("voice_message_regenerated");
    } catch (err) {
      handleLimitError(err) || toast.error("Could not regenerate. Try again.");
    }
  }

  async function refineMessage(messageId, refineType, tone) {
    setMessages((prev) => prev.map((m) => (m.message_id === messageId ? { ...m, refining: true } : m)));
    try {
      const { data } = await api.post("/voice/refine", { message_id: messageId, refine_type: refineType });
      setMessages((prev) => prev.map((m) => (
        m.tone === tone ? { ...m, message_id: data.message_id, message: data.generated_message, refining: false } : m
      )));
    } catch (err) {
      setMessages((prev) => prev.map((m) => (m.message_id === messageId ? { ...m, refining: false } : m)));
      handleLimitError(err) || toast.error("Could not refine. Try again.");
    }
  }

  async function copyMessage(m) {
    const ok = await copyToClipboard(m.message);
    if (!ok) {
      toast.error("Copy failed — long-press the text to copy manually.");
      return;
    }
    setCopiedId(m.message_id);
    api.post("/voice/copy-event", { message_id: m.message_id }).catch(() => { /* analytics best-effort */ });
  }

  // Share the reply text itself (2026-05-11).
  //
  // Behaviour:
  //   - navigator.share when available → native share sheet (WhatsApp,
  //     Twitter, Mail, etc).
  //   - Else copyToClipboard + toast "Reply copied. Paste it anywhere to
  //     share."
  //   - User cancel of native sheet is a silent no-op.
  //   - Total failure → "Could not share. Please copy manually."
  async function shareReply(message) {
    const text = (message?.message || "").trim();
    if (!text) {
      toast.error("Nothing to share yet.");
      return;
    }
    const result = await shareText({ text });
    if (result.cancelled) return; // user dismissed the sheet
    if (result.ok) {
      if (result.method === "clipboard_fallback") {
        toast.success("Reply copied. Paste it anywhere to share.");
      }
      track("smart_reply_shared", {
        tone: message?.tone,
        source_type: session?.source_type || "unknown",
        share_method: result.method,
      });
      return;
    }
    toast.error("Could not share. Please copy manually.");
    track("smart_reply_share_failed", {
      tone: message?.tone,
      source_type: session?.source_type || "unknown",
      failure_reason: result.reason || "unknown",
    });
  }

  async function handleFile(file) {
    if (!file) return;
    const ext = "." + (file.name.split(".").pop() || "").toLowerCase();
    if (!ALLOWED_UPLOAD_EXTS.includes(ext)) {
      toast.error(`Unsupported format. Use: ${ALLOWED_UPLOAD_EXTS.join(", ")}`);
      return;
    }
    if (file.size > 15 * 1024 * 1024) {
      toast.error("File too large (max 15 MB)");
      return;
    }
    submitAudio(file, "upload", file.name);
  }

  async function saveTranscriptEdit() {
    if (!session?.session_id) return;
    if (editedTranscript.trim() === (session.cleaned_transcript || "").trim()) return;
    setTranscriptSaving(true);
    try {
      await api.patch(`/voice/sessions/${session.session_id}`, { cleaned_transcript: editedTranscript.trim() });
      setSession((s) => ({ ...s, cleaned_transcript: editedTranscript.trim() }));
      await runGenerateAll(session.session_id);
    } catch {
      toast.error("Could not save edit");
    } finally {
      setTranscriptSaving(false);
    }
  }

  function startRecording() {
    track("voice_record_started");
    recorder.start();
  }

  function stopRecording() {
    track("voice_record_stopped");
    recorder.stop();
  }

  function pickExample(text) {
    track("voice_example_clicked");
    setTab("text");
    setTextInput(text);
    submitText(text);
  }

  // Stage banner
  const stageActive = stage !== "idle";

  if (authLoading) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="orb orb-emerald w-[420px] h-[420px] -top-20 -right-32 opacity-30 animate-orb" aria-hidden />
      <div className="orb orb-violet w-[380px] h-[380px] top-72 -left-32 opacity-20 animate-orb" style={{ animationDelay: "2s" }} aria-hidden />

      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-5 sm:py-8 relative" data-testid="voice-messaging-page">
        {/* Header */}
        <div className="glass-card p-5 sm:p-6 mb-4">
          <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
            <div className="flex-1 min-w-0">
              <span className="tag tag-emerald mb-2 inline-block">VOICE → MESSAGE</span>
              <h1 className="heading-display text-2xl sm:text-3xl md:text-4xl leading-tight">
                Say what you mean — clearly.
              </h1>
              <p className="text-sm font-medium text-ink/70 leading-relaxed mt-2">
                Turn messy thoughts into clear messages. Speak naturally, upload a voice note, or paste rough text — we'll write 6 tone-matched versions instantly.
              </p>
            </div>
            <div className="flex flex-row sm:flex-col items-start sm:items-end gap-2 flex-wrap">
              {remainingPill}
              {user && (
                <Link to="/voice/history" className="text-xs font-mono uppercase tracking-widest text-muted hover:text-emerald-soft" data-testid="link-voice-history" onClick={() => track("voice_history_opened")}>
                  History
                </Link>
              )}
            </div>
          </div>

          {/* Examples — instant ideation */}
          <div className="mt-4 flex flex-wrap gap-2" data-testid="voice-examples-row">
            <span className="text-[11px] font-mono uppercase tracking-widest text-muted self-center mr-1">Try:</span>
            {EXAMPLES.map((e) => (
              <button
                key={e}
                type="button"
                onClick={() => pickExample(e)}
                disabled={stageActive}
                className="tag cursor-pointer hover:bg-emerald/15 hover:text-emerald-soft disabled:opacity-40"
                data-testid={`voice-example-${e.split(" ")[0].toLowerCase()}`}
              >
                {e}
              </button>
            ))}
          </div>
        </div>

        {/* Tabs: record | upload | text */}
        <div className="glass-card p-5 sm:p-6 mb-4 space-y-4" data-testid="voice-input-block">
          <div className="grid grid-cols-3 gap-2 p-1 rounded-2xl bg-black/30 border border-white/5" data-testid="voice-tabs">
            {[
              { id: "record", label: "🎙 Record" },
              { id: "upload", label: "↑ Upload" },
              { id: "text", label: "✎ Paste text" },
            ].map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => { if (!stageActive) setTab(t.id); }}
                className={`text-xs sm:text-sm font-display font-bold py-2.5 px-2 rounded-xl transition ${tab === t.id ? "bg-ink text-bg shadow-glow-amber" : "text-ink/70 hover:text-ink"}`}
                data-testid={`voice-tab-${t.id}`}
              >
                {t.label}
              </button>
            ))}
          </div>

          {tab === "record" && (
            <div data-testid="voice-record-panel">
              <div className="flex flex-col items-center text-center gap-3 py-2">
                {!recorder.supported && (
                  <p className="text-xs text-amber-soft" data-testid="voice-record-unsupported">
                    Recording unavailable in this browser. Try the Upload or Paste text tabs.
                  </p>
                )}
                {recorder.error && (
                  <p className="text-xs text-rose-300" data-testid="voice-record-error">{recorder.error}</p>
                )}
                <button
                  type="button"
                  onClick={recorder.recording ? stopRecording : startRecording}
                  disabled={stageActive || !recorder.supported}
                  className={`relative w-24 h-24 sm:w-28 sm:h-28 rounded-full border-2 border-black flex items-center justify-center font-display font-black text-3xl text-bg transition-all ${recorder.recording ? "bg-rose animate-pulse" : "bg-emerald hover:scale-105"} disabled:opacity-50`}
                  data-testid={recorder.recording ? "voice-record-stop-btn" : "voice-record-start-btn"}
                  aria-label={recorder.recording ? "Stop recording" : "Start recording"}
                >
                  {recorder.recording ? "■" : "●"}
                </button>
                <div className="font-mono text-xs uppercase tracking-widest text-muted">
                  {recorder.recording ? `Recording… ${fmtTime(recorder.elapsed)} / 1:30` : "Tap to record (max 90s)"}
                </div>
                {recorder.recording && (
                  <div className="w-full max-w-xs h-2 rounded-full bg-black/40 overflow-hidden" data-testid="voice-record-meter">
                    <div className="h-full bg-emerald transition-all" style={{ width: `${Math.round(recorder.level * 100)}%` }} />
                  </div>
                )}
              </div>
            </div>
          )}

          {tab === "upload" && (
            <div data-testid="voice-upload-panel">
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={stageActive}
                className="brutal-card w-full p-6 sm:p-8 text-center hover:bg-emerald/5 transition disabled:opacity-50"
                data-testid="voice-upload-btn"
              >
                <div className="text-3xl mb-2">↑</div>
                <div className="font-display font-bold text-base">Upload a voice note</div>
                <div className="text-xs text-muted mt-1">MP3 · WAV · M4A · WEBM · OGG · MP4 (max 15 MB)</div>
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept="audio/*,.mp3,.wav,.m4a,.webm,.ogg,.mp4"
                onChange={(e) => handleFile(e.target.files?.[0])}
                className="hidden"
                data-testid="voice-upload-input"
              />
            </div>
          )}

          {tab === "text" && (
            <div data-testid="voice-text-panel">
              <label className="block text-xs font-mono uppercase tracking-widest text-muted mb-2">
                Or paste what you wanted to say
              </label>
              <textarea
                className="input-brutal w-full min-h-[120px] resize-y"
                placeholder="e.g. tell my boss i'll be like 30 mins late, traffic is crazy, don't want to sound flaky"
                value={textInput}
                onChange={(e) => setTextInput(e.target.value)}
                maxLength={2000}
                disabled={stageActive}
                data-testid="voice-text-textarea"
              />
              <div className="flex items-center justify-between mt-2">
                <span className="text-xs font-mono text-muted">{textInput.length}/2000</span>
                <button
                  type="button"
                  onClick={() => submitText(textInput)}
                  disabled={!textInput.trim() || stageActive}
                  className="btn-brutal text-sm"
                  data-testid="voice-text-submit-btn"
                >
                  Generate 6 messages →
                </button>
              </div>
            </div>
          )}
        </div>

        {/* Processing-state banner — staged feedback after recording stops.
            Renders immediately when `capturedAt` is set OR when the pipeline
            is mid-flight. Sticky-positioned so it remains visible on mobile
            (390px) during the longer generate step. */}
        {(stageActive || capturedAt > 0) && (
          <div
            className="glass-card p-4 sm:p-5 mb-4 space-y-2.5 sticky top-2 z-30 border-emerald/30 bg-emerald-500/5 shadow-glow-amber"
            data-testid="voice-stage-banner"
            role="status"
            aria-live="polite"
          >
            <div className="flex items-center gap-3" data-testid="voice-stage-captured">
              <span className="text-lg leading-none">🎙️</span>
              <div className="text-sm font-display font-bold text-ink">
                Voice captured successfully
              </div>
            </div>
            <div className="flex items-start gap-3 pl-7">
              <span className="inline-flex items-center justify-center w-4 h-4 mt-0.5 flex-shrink-0">
                <span className="absolute inline-block w-4 h-4 rounded-full bg-emerald/40 animate-ping" />
                <span className="relative inline-block w-2 h-2 rounded-full bg-emerald" />
              </span>
              <div className="min-w-0">
                <div className="text-sm text-ink/90 font-mono" data-testid="voice-stage-primary">
                  {stage === "generating"
                    ? "Generating smart replies…"
                    : stage === "cleaning"
                    ? "Cleaning up the message…"
                    : "Transcribing and generating smart replies…"}
                </div>
                <div className="text-[11px] font-mono uppercase tracking-widest text-muted mt-0.5" data-testid="voice-stage-eta">
                  {isSlow ? "Still working… complex audio can take a little longer." : "This usually takes a few seconds."}
                </div>
              </div>
            </div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted/70 pl-7" data-testid="voice-stage-privacy">
              Audio is never stored.
            </div>
          </div>
        )}

        {/* Transcript editor */}
        {session && (
          <div ref={transcriptBlockRef} className="glass-card p-5 sm:p-6 mb-4 space-y-3" data-testid="voice-transcript-block">
            <div className="flex items-center justify-between flex-wrap gap-2">
              <span className="text-xs font-mono uppercase tracking-widest text-muted">Cleaned input — edit if anything's off</span>
              <span className="tag" data-testid="voice-source-tag">SOURCE · {session.source_type?.toUpperCase()}</span>
            </div>
            <textarea
              className="input-brutal w-full min-h-[80px] resize-y"
              value={editedTranscript}
              onChange={(e) => setEditedTranscript(e.target.value)}
              maxLength={4000}
              data-testid="voice-transcript-edit"
            />
            {session.raw_transcript && session.raw_transcript !== session.cleaned_transcript && (
              <details className="text-xs text-muted">
                <summary className="cursor-pointer hover:text-ink">Show raw transcript</summary>
                <p className="mt-2 italic" data-testid="voice-raw-transcript">{session.raw_transcript}</p>
              </details>
            )}
            <button
              type="button"
              onClick={saveTranscriptEdit}
              disabled={transcriptSaving || stageActive || editedTranscript.trim() === (session.cleaned_transcript || "").trim() || !editedTranscript.trim()}
              className="btn-ghost text-sm"
              data-testid="voice-transcript-save-btn"
            >
              {transcriptSaving ? "Updating…" : "↻ Re-generate with this edit"}
            </button>
          </div>
        )}

        {/* Messages */}
        {messages.length > 0 && (
          <div ref={messagesBlockRef} className="space-y-3" data-testid="voice-messages-block">
            {messages.map((m) => {
              const tone = TONES.find((t) => t.id === m.tone);
              const justCopied = copiedId === m.message_id;
              return (
                <div key={`${m.tone}-${m.message_id}`} className="brutal-card p-4 sm:p-5" data-testid={`voice-message-card-${m.tone}`}>
                  <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="tag tag-emerald" data-testid={`voice-message-tone-${m.tone}`}>{(tone?.label || m.tone).toUpperCase()}</span>
                      {tone?.desc && <span className="text-[11px] text-muted">{tone.desc}</span>}
                    </div>
                    <button
                      type="button"
                      onClick={() => regenerateOne(m.tone)}
                      disabled={stageActive || m.refining}
                      className="text-xs font-mono uppercase tracking-widest text-muted hover:text-emerald-soft disabled:opacity-50"
                      data-testid={`voice-regenerate-${m.tone}`}
                    >
                      {m.refining ? "…" : "↻ Regenerate"}
                    </button>
                  </div>
                  <p className="font-medium text-base text-ink leading-relaxed whitespace-pre-wrap" data-testid={`voice-message-text-${m.tone}`}>
                    {m.message}
                  </p>

                  {/* refine chips */}
                  <div className="flex flex-wrap gap-1.5 mt-3" data-testid={`voice-refine-chips-${m.tone}`}>
                    {REFINE_CHIPS.map((chip) => (
                      <button
                        key={chip.id}
                        type="button"
                        onClick={() => refineMessage(m.message_id, chip.id, m.tone)}
                        disabled={stageActive || m.refining}
                        className="tag cursor-pointer hover:bg-violet/15 hover:text-violet-soft disabled:opacity-40 text-[11px]"
                        data-testid={`voice-refine-${m.tone}-${chip.id}`}
                      >
                        {chip.label}
                      </button>
                    ))}
                  </div>

                  <div className="flex items-center gap-2 mt-3 flex-wrap">
                    <button
                      type="button"
                      onClick={() => copyMessage(m)}
                      className={`btn-brutal text-sm transition-all ${justCopied ? "bg-emerald scale-105" : ""}`}
                      data-testid={`voice-copy-${m.tone}`}
                    >
                      {justCopied ? "✓ Copied!" : "Copy"}
                    </button>
                    <button
                      type="button"
                      onClick={() => shareReply(m)}
                      disabled={!m.message || !m.message.trim()}
                      className="btn-ghost text-sm disabled:opacity-40 disabled:cursor-not-allowed"
                      data-testid={`voice-share-${m.tone}`}
                    >
                      Share
                    </button>
                    {justCopied && (
                      <span className="text-xs font-mono text-emerald-soft animate-pulse" data-testid={`voice-copy-toast-${m.tone}`}>
                        Paste it where you need it.
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Empty state hint */}
        {!session && !stageActive && (
          <div className="text-center text-xs font-mono uppercase tracking-widest text-muted/70 mt-6" data-testid="voice-empty-hint">
            no recording yet · privacy: audio never leaves transcription, only your text is stored
          </div>
        )}
      </div>

      <VoiceSignupWall
        open={signupWall}
        onClose={() => setSignupWall(false)}
        anonRemaining={usage?.anon_remaining || 0}
      />
      <UsageLimitModal
        open={paywall}
        onClose={() => setPaywall(false)}
        onUpgradeClick={() => { setPaywall(false); upgradeToPro({ source: "voice_messaging" }); }}
        daily_limit={20}
      />
    </div>
  );
}
