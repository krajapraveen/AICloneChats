import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

const STAGE_LABEL = {
  viewed: "Page viewed",
  input_started: "Input started",
  transcription_completed: "Transcription completed",
  generated: "Messages generated",
  copied: "Message copied",
  second_gen_same_day: "2nd generation same day",
  returned_next_day: "Returned next day",
};

function pct(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n.toFixed(1)}%`;
}

function StatCard({ label, value, sub, testid, tone = "default" }) {
  const toneClass = tone === "good" ? "border-emerald" : tone === "bad" ? "border-rose" : tone === "neutral" ? "border-amber" : "";
  return (
    <div className={`brutal-card p-4 sm:p-5 ${toneClass}`} data-testid={testid}>
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display font-black text-2xl sm:text-3xl mt-1 text-ink">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

export default function AdminVoiceMetrics() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);

  useEffect(() => {
    if (!authLoading && !user) {
      navigate("/login?redirect=/admin/voice-metrics");
      return;
    }
    if (!user) return;
    setLoading(true);
    api.get(`/admin/voice/metrics?days=${days}`).then((r) => {
      setData(r.data);
      setForbidden(false);
    }).catch((err) => {
      if (err?.response?.status === 403) setForbidden(true);
    }).finally(() => setLoading(false));
  }, [user, authLoading, days, navigate]);

  if (authLoading || !user) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">loading…</div>
      </div>
    );
  }

  if (forbidden) {
    return (
      <div className="page-bg min-h-screen min-h-[100dvh]">
        <Navbar />
        <div className="max-w-3xl mx-auto px-4 sm:px-5 md:px-8 py-10" data-testid="voice-metrics-forbidden">
          <div className="brutal-card p-8 text-center">
            <h1 className="heading-display text-2xl mb-2">Admin only</h1>
            <p className="text-sm text-ink/70">This dashboard is for admin accounts.</p>
          </div>
        </div>
      </div>
    );
  }

  const ns = data?.north_star;
  const tonePerf = data?.tone_performance;
  const trust = data?.trust_signals;
  const retention = data?.retention;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="orb orb-emerald w-[420px] h-[420px] -top-20 -right-32 opacity-25 animate-orb" aria-hidden />

      <div className="max-w-5xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-8 relative" data-testid="voice-metrics-page">
        <div className="flex items-end justify-between flex-wrap gap-3 mb-5">
          <div>
            <span className="tag tag-emerald mb-2 inline-block">VOICE · METRICS</span>
            <h1 className="heading-display text-2xl sm:text-3xl">Evidence dashboard</h1>
            <p className="text-xs text-muted font-mono mt-1">
              Same-day repeat usage and copy rate are the only signals that matter early on.
            </p>
          </div>
          <div className="flex gap-1 p-1 rounded-2xl bg-black/30 border border-white/5" data-testid="voice-metrics-window-tabs">
            {[1, 7, 30].map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setDays(d)}
                className={`text-xs font-display font-bold py-2 px-4 rounded-xl transition ${days === d ? "bg-ink text-bg" : "text-ink/70 hover:text-ink"}`}
                data-testid={`voice-metrics-window-${d}`}
              >
                {d === 1 ? "24h" : `${d}d`}
              </button>
            ))}
          </div>
        </div>

        {loading || !data ? (
          <div className="text-muted font-mono text-sm">loading…</div>
        ) : (
          <div className="space-y-6">
            {/* North star */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <StatCard
                label="🌟 GENERATION → COPY RATE"
                value={pct(ns?.overall_copy_rate_pct)}
                sub={`${ns?.messages_copied || 0} copied / ${ns?.messages_generated || 0} generated`}
                tone="good"
                testid="voice-metrics-copy-rate"
              />
              <StatCard
                label="D1 RETURN RATE"
                value={pct(retention?.d1_return_rate_pct)}
                sub={`${retention?.actors_returned_next_day || 0} actors came back next day`}
                tone="neutral"
                testid="voice-metrics-d1"
              />
              <StatCard
                label="2ND GENERATION SAME DAY"
                value={`${retention?.actors_with_2nd_gen_same_day || 0}`}
                sub="actors who used it twice in one day"
                tone="neutral"
                testid="voice-metrics-2nd-gen"
              />
            </div>

            {/* Funnel */}
            <div className="glass-card p-5" data-testid="voice-metrics-funnel">
              <h2 className="heading-display text-lg mb-3">Funnel · {days}d</h2>
              <div className="space-y-2">
                {(data.funnel || []).map((row) => (
                  <div key={row.stage} className="flex items-center gap-3" data-testid={`voice-metrics-funnel-${row.stage}`}>
                    <div className="w-44 sm:w-56 text-xs font-mono text-ink/85 shrink-0">{STAGE_LABEL[row.stage] || row.stage}</div>
                    <div className="flex-1 h-7 bg-black/40 rounded-lg overflow-hidden relative">
                      <div className="h-full bg-emerald/70 transition-all" style={{ width: `${Math.max(2, Math.min(100, row.pct_of_top))}%` }} />
                      <div className="absolute inset-0 flex items-center justify-between px-3 text-xs font-mono">
                        <span className="text-ink font-bold">{row.actors}</span>
                        <span className="text-muted">{row.pct_of_top}%</span>
                      </div>
                    </div>
                    {row.drop_from_prev_pct > 0 && (
                      <span className="text-[10px] font-mono text-rose-300 w-16 text-right" data-testid={`voice-metrics-drop-${row.stage}`}>
                        −{row.drop_from_prev_pct}%
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>

            {/* Tone performance */}
            <div className="glass-card p-5" data-testid="voice-metrics-tones">
              <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
                <h2 className="heading-display text-lg">Tone copy-rate · {days}d</h2>
                <div className="flex flex-wrap gap-2 text-xs font-mono">
                  {tonePerf?.best_tone && (
                    <span className="tag tag-emerald" data-testid="voice-metrics-best-tone">
                      BEST · {tonePerf.best_tone.tone.toUpperCase()} · {pct(tonePerf.best_tone.copy_rate_pct)}
                    </span>
                  )}
                  {tonePerf?.worst_tone && tonePerf.worst_tone.tone !== tonePerf.best_tone?.tone && (
                    <span className="tag tag-rose" data-testid="voice-metrics-worst-tone">
                      WEAKEST · {tonePerf.worst_tone.tone.toUpperCase()} · {pct(tonePerf.worst_tone.copy_rate_pct)}
                    </span>
                  )}
                </div>
              </div>
              <div className="space-y-2">
                {(tonePerf?.rows || []).map((row) => (
                  <div key={row.tone} className="flex items-center gap-3" data-testid={`voice-metrics-tone-${row.tone}`}>
                    <div className="w-28 text-xs font-mono text-ink/85 shrink-0 capitalize">{row.tone}</div>
                    <div className="flex-1 h-6 bg-black/40 rounded-lg overflow-hidden relative">
                      <div className="h-full bg-emerald/60" style={{ width: `${Math.max(2, Math.min(100, row.copy_rate_pct))}%` }} />
                      <div className="absolute inset-0 flex items-center justify-between px-3 text-xs font-mono">
                        <span className="text-ink">{row.copied}/{row.generated}</span>
                        <span className="text-muted">{row.copy_rate_pct}%</span>
                      </div>
                    </div>
                  </div>
                ))}
                {(!tonePerf?.rows || tonePerf.rows.length === 0) && (
                  <div className="text-xs text-muted">No data yet for this window.</div>
                )}
              </div>
            </div>

            {/* Trust + source split */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <div className="glass-card p-5" data-testid="voice-metrics-trust">
                <h2 className="heading-display text-lg mb-3">Trust signals</h2>
                <div className="grid grid-cols-2 gap-3">
                  <StatCard
                    label="EDIT-BEFORE-COPY"
                    value={pct(trust?.edit_before_copy_pct)}
                    sub={`${trust?.edited_then_copied_sessions || 0} of ${trust?.total_copied_sessions || 0} sessions`}
                    tone={trust?.edit_before_copy_pct > 30 ? "bad" : "good"}
                    testid="voice-metrics-edit-rate"
                  />
                  <StatCard
                    label="REFINE ACTIONS"
                    value={trust?.refine_actions ?? "—"}
                    sub="shorter / confident / polite / flirty / professional"
                    testid="voice-metrics-refines"
                  />
                </div>
                <p className="text-[11px] text-muted mt-3 font-mono leading-relaxed">
                  High edit-before-copy = users don't trust the generated tone. Low edit + high copy = strong signal.
                </p>
              </div>

              <div className="glass-card p-5" data-testid="voice-metrics-source-split">
                <h2 className="heading-display text-lg mb-3">Input source split</h2>
                <div className="space-y-2">
                  {(data.source_split || []).map((s) => (
                    <div key={s.source} className="flex items-center justify-between" data-testid={`voice-metrics-source-${s.source}`}>
                      <span className="text-sm font-mono text-ink/85 capitalize">{s.source}</span>
                      <span className="text-sm font-mono text-muted">{s.count}</span>
                    </div>
                  ))}
                  {(!data.source_split || data.source_split.length === 0) && (
                    <div className="text-xs text-muted">No data yet.</div>
                  )}
                </div>
                <h3 className="heading-display text-sm mt-5 mb-2">Actors</h3>
                <div className="space-y-1.5 text-xs font-mono text-ink/80">
                  <div className="flex justify-between"><span>Anonymous</span><span>{data.actors?.total_anonymous ?? 0}</span></div>
                  <div className="flex justify-between"><span>Authed</span><span>{data.actors?.total_authed ?? 0}</span></div>
                  <div className="flex justify-between"><span>Anon → Signup conversion</span><span data-testid="voice-metrics-anon-conversion">{pct(data.actors?.anonymous_to_signup_conversion_pct)}</span></div>
                </div>
              </div>
            </div>

            {/* Daily active */}
            {(data.daily_active_actors || []).length > 0 && (
              <div className="glass-card p-5" data-testid="voice-metrics-daily">
                <h2 className="heading-display text-lg mb-3">Daily active actors</h2>
                <div className="space-y-1.5">
                  {data.daily_active_actors.map((d) => (
                    <div key={d.day} className="flex items-center gap-3 text-xs font-mono">
                      <span className="w-24 text-muted">{d.day}</span>
                      <div className="flex-1 h-5 bg-black/40 rounded-lg overflow-hidden">
                        <div className="h-full bg-violet/60" style={{ width: `${Math.min(100, d.actors * 5)}%` }} />
                      </div>
                      <span className="text-ink w-12 text-right">{d.actors}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
