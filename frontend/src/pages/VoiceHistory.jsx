import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

function fmtDate(s) {
  try {
    return new Date(s).toLocaleString();
  } catch {
    return s;
  }
}

export default function VoiceHistory() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [sessions, setSessions] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!authLoading && !user) {
      navigate("/login?redirect=/voice/history");
      return;
    }
    if (user) {
      api.get("/voice/history").then((r) => setSessions(r.data?.sessions || [])).catch(() => {
        // Not fatal — show empty state
      }).finally(() => setLoading(false));
    }
  }, [user, authLoading, navigate]);

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      toast.success("Copied");
    } catch {
      toast.error("Copy failed");
    }
  }

  if (authLoading || !user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8" data-testid="voice-history-page">
        <div className="flex items-center justify-between mb-5 flex-wrap gap-2">
          <div>
            <span className="tag tag-emerald mb-2 inline-block">VOICE · HISTORY</span>
            <h1 className="heading-display text-2xl sm:text-3xl">Past sessions</h1>
          </div>
          <Link to="/voice" className="btn-ghost text-sm" data-testid="voice-history-back">← Back to studio</Link>
        </div>

        {loading ? (
          <div className="text-muted font-mono text-sm">loading…</div>
        ) : sessions.length === 0 ? (
          <div className="glass-card p-8 text-center" data-testid="voice-history-empty">
            <div className="text-3xl mb-3">🎙</div>
            <h2 className="heading-display text-xl mb-2">No sessions yet</h2>
            <p className="text-sm text-ink/70">Record, upload, or paste your first message to get started.</p>
            <Link to="/voice" className="btn-brutal text-sm inline-block mt-4">Start a session →</Link>
          </div>
        ) : (
          <div className="space-y-4">
            {sessions.map((s) => (
              <div key={s.session_id} className="brutal-card p-4 sm:p-5" data-testid={`voice-history-session-${s.session_id}`}>
                <div className="flex items-center justify-between gap-2 flex-wrap mb-2">
                  <span className="tag" data-testid="voice-history-source">SOURCE · {(s.source_type || "?").toUpperCase()}</span>
                  <span className="text-xs font-mono text-muted">{fmtDate(s.created_at)}</span>
                </div>
                <p className="text-sm text-ink/85 leading-relaxed">
                  <span className="text-xs font-mono uppercase tracking-widest text-muted block mb-1">Cleaned input</span>
                  {s.cleaned_transcript || s.raw_transcript || "(empty)"}
                </p>
                {Array.isArray(s.messages) && s.messages.length > 0 && (
                  <div className="mt-3 space-y-2">
                    <span className="text-xs font-mono uppercase tracking-widest text-muted block">Messages</span>
                    {s.messages.map((m) => (
                      <div key={m.message_id} className="rounded-xl border border-white/5 bg-black/30 p-3">
                        <div className="flex items-center justify-between mb-1 gap-2 flex-wrap">
                          <span className="tag tag-emerald text-[10px]">{(m.tone || "").toUpperCase()}</span>
                          <button onClick={() => copy(m.generated_message)} className="text-[11px] font-mono uppercase tracking-widest text-muted hover:text-emerald-soft" data-testid={`voice-history-copy-${m.message_id}`}>Copy</button>
                        </div>
                        <p className="text-sm text-ink/85 whitespace-pre-wrap">{m.generated_message}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
