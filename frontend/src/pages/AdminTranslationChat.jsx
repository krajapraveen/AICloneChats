/**
 * Admin Translation Chat metrics — read-only.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function num(n) { return n === null || n === undefined ? "—" : (typeof n === "number" && n >= 1000 ? n.toLocaleString() : String(n)); }
function pct(n) { return n === null || n === undefined ? "—" : `${n}%`; }
function dur(s) {
  if (!s && s !== 0) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${(s / 60).toFixed(1)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${(s / 86400).toFixed(1)}d`;
}

function StatCard({ label, value, sub, testid }) {
  return (
    <div className="brutal-card p-4 sm:p-5" data-testid={testid}>
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted">{label}</div>
      <div className="font-display font-black text-2xl sm:text-3xl mt-1 text-ink break-words">{value}</div>
      {sub && <div className="text-xs text-muted mt-1">{sub}</div>}
    </div>
  );
}

export default function AdminTranslationChat() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [days, setDays] = useState(7);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const r = await api.get(`/admin/translation-chat/metrics?days=${days}`);
      setData(r.data);
      setForbidden(false);
    } catch (e) {
      if (e?.response?.status === 403) setForbidden(true);
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    if (!authLoading && !user) { navigate("/login?redirect=/admin/translation-chat"); return; }
    if (!user) return;
    setLoading(true);
    fetchAll();
  }, [user, authLoading, days, navigate, fetchAll]);

  if (authLoading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;
  if (forbidden) return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="max-w-3xl mx-auto px-4 py-10">
        <div className="brutal-card p-8 text-center" data-testid="admin-tx-forbidden">
          <h1 className="heading-display text-2xl mb-2">Admin only</h1>
        </div>
      </div>
    </div>
  );

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="admin-tx-page">
      <Navbar />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-10">
        <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 mb-3">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-widest text-muted">Admin · Translation Chat</div>
            <h1 className="heading-display text-3xl sm:text-4xl mt-1">Translation metrics</h1>
            <p className="text-sm text-muted mt-2 max-w-2xl">Read-only behavioral dashboard for `/translation-chat`.</p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {[1, 7, 14, 30].map((d) => (
              <button key={d} onClick={() => setDays(d)} data-testid={`admin-tx-window-${d}d`}
                className={`px-3 py-1.5 rounded-full text-xs font-mono uppercase tracking-widest border ${days === d ? "bg-ink text-bg border-ink" : "border-ink/20 text-ink/70 hover:border-ink/50"}`}>
                {d === 1 ? "24h" : `${d}d`}
              </button>
            ))}
          </div>
        </div>

        {loading && !data && <div className="text-muted font-mono text-sm">loading…</div>}

        {data && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4">
              <StatCard testid="tx-rooms-total" label="Rooms total" value={num(data.rooms_total)} />
              <StatCard testid="tx-rooms-active" label="Active rooms" value={num(data.rooms_active_in_window)} sub={`${days}d window`} />
              <StatCard testid="tx-msgs" label="Messages" value={num(data.messages_in_window)} sub={`${data.copy_events} copies`} />
              <StatCard testid="tx-blocks" label="Blocked" value={num(data.messages_blocked)} sub="safety blocks" />
              <StatCard testid="tx-members" label="Members joined" value={num(data.members_joined_in_window)} />
              <StatCard testid="tx-avg-msgs-per-room" label="Avg msgs / active room" value={num(data.avg_messages_per_room)} sub="signal of room depth" />
              <StatCard testid="tx-repeat-joiners" label="Repeat room joiners" value={num(data.repeat_room_joiners)} sub={data.repeat_room_joiner_pct !== null ? `${pct(data.repeat_room_joiner_pct)} of ${num(data.distinct_members)} distinct members` : "—"} />
              <StatCard testid="tx-median-duration" label="Median session duration" value={dur(data.median_session_duration_sec)} sub="member tenure proxy" />
              <StatCard testid="tx-d1-return" label="D1 return rate" value={pct(data.d1_return?.pct)} sub={`${num(data.d1_return?.returned)} of ${num(data.d1_return?.eligible)} cohort`} />
            </div>

            {/* P2 invite attribution */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 sm:gap-4 mt-4" data-testid="tx-invite-attribution">
              <StatCard testid="tx-invite-arrivals" label="Arrivals via invite" value={num(data.invite?.arrivals_via_invite)} sub={pct(data.invite?.invite_share_pct)} />
              <StatCard testid="tx-invite-organic" label="Organic arrivals (est.)" value={num(data.invite?.organic_arrivals_estimate)} sub="members - invite arrivals" />
              <StatCard testid="tx-invite-copies" label="Invite link copies" value={num(data.invite?.invite_link_copies)} sub="share intent" />
              <StatCard testid="tx-invite-joined-evt" label="Server join events" value={num(data.invite?.member_joined_events)} sub="raw stream" />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-6">
              <div className="brutal-card overflow-x-auto" data-testid="tx-by-source-lang">
                <div className="px-4 pt-3 text-[11px] font-mono uppercase tracking-widest text-muted">Messages by source language</div>
                <table className="w-full text-sm">
                  <tbody>
                    {(data.messages_by_source_language || []).length === 0 && <tr><td className="p-4 text-center text-muted text-xs">No data.</td></tr>}
                    {(data.messages_by_source_language || []).map((c) => (
                      <tr key={c.language} className="border-b border-ink/5">
                        <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">{c.language}</td>
                        <td className="p-3 text-right tabular-nums">{c.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="brutal-card overflow-x-auto" data-testid="tx-by-pref-lang">
                <div className="px-4 pt-3 text-[11px] font-mono uppercase tracking-widest text-muted">Members by preferred language</div>
                <table className="w-full text-sm">
                  <tbody>
                    {(data.members_by_preferred_language || []).length === 0 && <tr><td className="p-4 text-center text-muted text-xs">No data.</td></tr>}
                    {(data.members_by_preferred_language || []).map((c) => (
                      <tr key={c.language} className="border-b border-ink/5">
                        <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">{c.language}</td>
                        <td className="p-3 text-right tabular-nums">{c.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Language pair frequency — corridors */}
            <div className="brutal-card overflow-x-auto mt-4" data-testid="tx-lang-pairs">
              <div className="px-4 pt-3 text-[11px] font-mono uppercase tracking-widest text-muted">Language pair frequency · which corridors are active?</div>
              <table className="w-full text-sm">
                <thead className="text-[11px] font-mono uppercase tracking-widest text-muted">
                  <tr className="border-b border-ink/10">
                    <th className="text-left p-3">Source</th>
                    <th className="text-left p-3">Target</th>
                    <th className="text-right p-3">Translations</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.language_pair_frequency || []).length === 0 && <tr><td colSpan={3} className="p-4 text-center text-muted text-xs">No translation pairs in window.</td></tr>}
                  {(data.language_pair_frequency || []).map((p) => (
                    <tr key={`${p.source}-${p.target}`} className="border-b border-ink/5" data-testid={`tx-pair-${p.source}-${p.target}`}>
                      <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">{p.source}</td>
                      <td className="p-3 font-mono text-xs uppercase tracking-widest text-ink">→ {p.target}</td>
                      <td className="p-3 text-right tabular-nums">{p.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="mt-6 text-[10px] font-mono text-muted">{data.operator_note}</div>

            <div className="mt-4 text-[10px] font-mono text-muted">
              <Link to="/admin/chats" className="hover:text-ink underline">All chats</Link>
              {" · "}<Link to="/admin/safety" className="hover:text-ink underline">Safety</Link>
              {" · "}<Link to="/admin/debates/retention" className="hover:text-ink underline">Debates retention</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
