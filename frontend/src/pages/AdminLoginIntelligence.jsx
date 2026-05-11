import { useEffect, useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const METHOD_LABEL = {
  email_password: "Email/Password",
  google_oauth: "Google OAuth",
};
const EVENT_LABEL = {
  login_success: "Success",
  login_failed: "Failed",
  logout: "Logout",
};
const EVENT_TONE = {
  login_success: "tag-emerald",
  login_failed: "tag-rose",
  logout: "tag-violet",
};
const DEVICE_ICON = {
  mobile: "📱",
  tablet: "🖥",
  desktop: "💻",
  unknown: "·",
};

function formatTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function SummaryCard({ label, value, tone, testId }) {
  return (
    <div className={`brutal-card p-5 ${tone || ""}`} data-testid={testId}>
      <p className="label-brutal mb-1">{label}</p>
      <p className="heading-display text-3xl">{value ?? "—"}</p>
    </div>
  );
}

export default function AdminLoginIntelligence() {
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [authorized, setAuthorized] = useState(null); // null | true | false
  const [summary, setSummary] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [total, setTotal] = useState(0);

  // filters
  const [emailFilter, setEmailFilter] = useState("");
  const [methodFilter, setMethodFilter] = useState("");
  const [eventTypeFilter, setEventTypeFilter] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // Auth gate
  useEffect(() => {
    if (authLoading) return;
    if (!user) {
      navigate("/login?redirect=/admin/login-intelligence");
      return;
    }
    api.get("/admin/me")
      .then(() => setAuthorized(true))
      .catch((err) => {
        if (err?.response?.status === 403) setAuthorized(false);
        else navigate("/login?redirect=/admin/login-intelligence");
      });
  }, [user, authLoading, navigate]);

  const fetchEvents = useCallback(async (resetPage = false) => {
    if (!authorized) return;
    setLoading(true);
    const targetPage = resetPage ? 1 : page;
    try {
      const params = { page: targetPage, limit: 50 };
      if (emailFilter) params.email = emailFilter;
      if (methodFilter) params.login_method = methodFilter;
      if (eventTypeFilter) params.event_type = eventTypeFilter;
      if (countryFilter) params.country = countryFilter;
      if (dateFrom) params.date_from = new Date(dateFrom).toISOString();
      if (dateTo) params.date_to = new Date(dateTo).toISOString();
      const { data } = await api.get("/admin/login-events", { params });
      setEvents(data.events || []);
      setPages(data.pages || 1);
      setTotal(data.total || 0);
      if (resetPage) setPage(1);
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed to load events");
    } finally {
      setLoading(false);
    }
  }, [authorized, page, emailFilter, methodFilter, eventTypeFilter, countryFilter, dateFrom, dateTo]);

  const fetchSummary = useCallback(async () => {
    if (!authorized) return;
    try {
      const { data } = await api.get("/admin/login-events/summary");
      setSummary(data);
    } catch {
      // silent
    }
  }, [authorized]);

  useEffect(() => {
    if (authorized) {
      fetchSummary();
      fetchEvents();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authorized, page]);

  const applyFilters = () => fetchEvents(true);
  const clearFilters = () => {
    setEmailFilter("");
    setMethodFilter("");
    setEventTypeFilter("");
    setCountryFilter("");
    setDateFrom("");
    setDateTo("");
    setTimeout(() => fetchEvents(true), 0);
  };

  if (authLoading || authorized === null) {
    return (
      <div className="page-bg min-h-screen flex items-center justify-center">
        <div className="text-muted font-mono text-sm">Checking access…</div>
      </div>
    );
  }
  if (authorized === false) {
    return (
      <div className="page-bg min-h-screen">
        <Navbar />
        <div className="max-w-md mx-auto px-5 py-20 text-center">
          <div className="brutal-card p-8" data-testid="admin-forbidden">
            <span className="tag tag-rose mb-3 inline-block">403</span>
            <h1 className="heading-display text-3xl mb-2">Admins only.</h1>
            <p className="text-muted text-sm font-medium">
              This page is restricted. If you believe this is an error, contact the site owner.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]">
      <Navbar />
      <div className="orb orb-amber w-[420px] h-[420px] -top-20 -right-32 opacity-20 animate-orb" aria-hidden />

      <div className="max-w-7xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10" data-testid="admin-login-intelligence">
        <div className="mb-6">
          <span className="tag tag-violet mb-2 inline-block">ADMIN · OBSERVABILITY</span>
          <h1 className="heading-display text-3xl sm:text-4xl">Login Intelligence</h1>
          <p className="text-muted text-sm font-medium mt-1">Who logged in, from where, and how. Raw IPs are never shown — only hashed.</p>
        </div>

        {/* Summary cards */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 mb-8" data-testid="admin-summary-grid">
          <SummaryCard label="Logins today" value={summary?.total_logins_today} testId="admin-card-logins-today" />
          <SummaryCard label="Unique users today" value={summary?.unique_users_today} testId="admin-card-unique-today" />
          <SummaryCard label="Failed today" value={summary?.failed_logins_today} testId="admin-card-failed-today" />
          <SummaryCard label="Top country (7d)" value={summary?.top_countries?.[0]?.country || "—"} testId="admin-card-top-country" />
          <SummaryCard label="Google logins (7d)" value={summary?.top_login_methods?.find((m) => m.method === "google_oauth")?.count || 0} testId="admin-card-google" />
          <SummaryCard label="Email logins (7d)" value={summary?.top_login_methods?.find((m) => m.method === "email_password")?.count || 0} testId="admin-card-email" />
        </div>

        {/* Filters */}
        <div className="glass-card p-5 mb-5" data-testid="admin-filters">
          <p className="label-brutal mb-3">Filters</p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            <input className="input-brutal" placeholder="Email contains…" value={emailFilter} onChange={(e) => setEmailFilter(e.target.value)} data-testid="admin-filter-email" />
            <select className="input-brutal" value={methodFilter} onChange={(e) => setMethodFilter(e.target.value)} data-testid="admin-filter-method">
              <option value="">All methods</option>
              <option value="email_password">Email/Password</option>
              <option value="google_oauth">Google OAuth</option>
            </select>
            <select className="input-brutal" value={eventTypeFilter} onChange={(e) => setEventTypeFilter(e.target.value)} data-testid="admin-filter-event">
              <option value="">All events</option>
              <option value="login_success">Success</option>
              <option value="login_failed">Failed</option>
              <option value="logout">Logout</option>
            </select>
            <input className="input-brutal" placeholder="Country code (e.g. US, IN)" value={countryFilter} onChange={(e) => setCountryFilter(e.target.value.toUpperCase())} maxLength={3} data-testid="admin-filter-country" />
            <input className="input-brutal" type="datetime-local" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} data-testid="admin-filter-from" />
            <input className="input-brutal" type="datetime-local" value={dateTo} onChange={(e) => setDateTo(e.target.value)} data-testid="admin-filter-to" />
          </div>
          <div className="flex gap-2 mt-4 flex-wrap">
            <button onClick={applyFilters} className="btn-brutal text-sm" data-testid="admin-apply-filters">Apply filters</button>
            <button onClick={clearFilters} className="btn-ghost text-sm" data-testid="admin-clear-filters">Clear</button>
            <button onClick={() => { fetchSummary(); fetchEvents(); }} className="btn-ghost text-sm" data-testid="admin-refresh">↻ Refresh</button>
            <span className="text-xs font-mono text-muted self-center ml-auto">{total} events</span>
          </div>
        </div>

        {/* Events list — mobile only (stacked cards) */}
        <div className="md:hidden space-y-3" data-testid="admin-events-list-mobile">
          {loading && (
            <div className="glass-card p-6 text-center text-muted text-sm">Loading…</div>
          )}
          {!loading && events.length === 0 && (
            <div className="glass-card p-6 text-center text-muted text-sm" data-testid="admin-events-empty-mobile">No events match your filters.</div>
          )}
          {!loading && events.map((e) => (
            <div
              key={e.event_id}
              className="glass-card p-3 min-w-0 overflow-hidden"
              data-testid={`admin-event-card-${e.event_id}`}
            >
              <div className="flex items-center justify-between gap-2 mb-2 min-w-0">
                <div className="font-mono text-[11px] text-muted whitespace-nowrap truncate min-w-0">{formatTime(e.created_at)}</div>
                <span className={`tag ${EVENT_TONE[e.event_type] || ""} shrink-0`}>{EVENT_LABEL[e.event_type] || e.event_type}</span>
              </div>
              <div className="min-w-0">
                <div
                  className="text-sm font-medium overflow-hidden text-ellipsis whitespace-nowrap"
                  title={e.email || ""}
                  data-testid={`admin-event-card-email-${e.event_id}`}
                >
                  {e.email || "—"}
                </div>
                {e.name && (
                  <div className="text-[11px] text-muted overflow-hidden text-ellipsis whitespace-nowrap" title={e.name}>
                    {e.name}
                  </div>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] font-mono text-muted min-w-0">
                <span className="whitespace-nowrap">{METHOD_LABEL[e.login_method] || e.login_method}</span>
                {e.ip_country && <span className="whitespace-nowrap">· {e.ip_country}</span>}
                {e.device_type && <span className="whitespace-nowrap">· {DEVICE_ICON[e.device_type] || "·"} {e.device_type}</span>}
              </div>
              {e.failure_reason && (
                <div className="mt-2 text-[11px] text-rose-soft break-words" data-testid={`admin-event-card-reason-${e.event_id}`}>
                  {e.failure_reason}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* Events table — desktop only */}
        <div className="glass-card overflow-hidden hidden md:block" data-testid="admin-events-table">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-white/5">
                <tr className="text-left text-xs font-mono uppercase tracking-widest text-muted whitespace-nowrap">
                  <th className="px-4 py-3">When</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Email</th>
                  <th className="px-4 py-3 hidden md:table-cell">User ID</th>
                  <th className="px-4 py-3">Method</th>
                  <th className="px-4 py-3 hidden sm:table-cell">Country</th>
                  <th className="px-4 py-3 hidden lg:table-cell">Region</th>
                  <th className="px-4 py-3 hidden md:table-cell">Device</th>
                  <th className="px-4 py-3 hidden md:table-cell">Browser</th>
                  <th className="px-4 py-3 hidden lg:table-cell">OS</th>
                  <th className="px-4 py-3">Reason</th>
                </tr>
              </thead>
              <tbody>
                {loading && (
                  <tr><td colSpan={11} className="px-4 py-8 text-center text-muted text-sm">Loading…</td></tr>
                )}
                {!loading && events.length === 0 && (
                  <tr><td colSpan={11} className="px-4 py-8 text-center text-muted text-sm" data-testid="admin-events-empty">No events match your filters.</td></tr>
                )}
                {!loading && events.map((e) => (
                  <tr key={e.event_id} className="border-t border-white/5 hover:bg-white/5" data-testid={`admin-event-row-${e.event_id}`}>
                    <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">{formatTime(e.created_at)}</td>
                    <td className="px-4 py-3">
                      <span className={`tag ${EVENT_TONE[e.event_type] || ""}`}>{EVENT_LABEL[e.event_type] || e.event_type}</span>
                    </td>
                    <td className="px-4 py-3 max-w-[260px]">
                      <div className="font-medium truncate" title={e.email || ""}>{e.email || "—"}</div>
                      {e.name && <div className="text-xs text-muted truncate" title={e.name}>{e.name}</div>}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs hidden md:table-cell">{e.user_id || "—"}</td>
                    <td className="px-4 py-3 text-xs whitespace-nowrap">{METHOD_LABEL[e.login_method] || e.login_method}</td>
                    <td className="px-4 py-3 hidden sm:table-cell">{e.ip_country || "—"}</td>
                    <td className="px-4 py-3 text-xs hidden lg:table-cell">{[e.ip_city, e.ip_region].filter(Boolean).join(", ") || "—"}</td>
                    <td className="px-4 py-3 hidden md:table-cell whitespace-nowrap">{DEVICE_ICON[e.device_type] || "·"} {e.device_type}</td>
                    <td className="px-4 py-3 hidden md:table-cell">{e.browser}</td>
                    <td className="px-4 py-3 hidden lg:table-cell">{e.os}</td>
                    <td className="px-4 py-3 text-xs text-rose-soft">{e.failure_reason || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {pages > 1 && (
            <div className="flex items-center justify-between p-4 border-t border-white/10">
              <button disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)} className="btn-ghost text-sm" data-testid="admin-prev-page">← Prev</button>
              <span className="text-xs font-mono text-muted">Page {page} of {pages}</span>
              <button disabled={page >= pages || loading} onClick={() => setPage((p) => p + 1)} className="btn-ghost text-sm" data-testid="admin-next-page">Next →</button>
            </div>
          )}
        </div>

        <p className="text-xs text-muted font-mono uppercase tracking-widest mt-6 text-center">
          Privacy: raw IPs never leave the server. Only hashed fingerprints + country/device are shown.
        </p>
      </div>
    </div>
  );
}
