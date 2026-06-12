import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";
import api from "../lib/api";

const NAV = [
  { type: "link", to: "/account/space", label: "My Space", testId: "tab-my-space" },
  { type: "link", to: "/account/inbox", label: "Inbox", testId: "tab-inbox", showBadge: true },
  { type: "link", to: "/account/concerns", label: "Concerns / Recommendations", testId: "tab-concerns" },
  { type: "group", label: "Settings", children: [
    { to: "/account/settings/change-password", label: "Change Password", testId: "tab-change-password" },
    { to: "/account/settings/subscriptions", label: "Manage Subscriptions", testId: "tab-subscriptions" },
  ]},
];

export default function Account() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [unread, setUnread] = useState(0);

  useEffect(() => {
    if (!loading && !user) navigate("/login?next=/account/space", { replace: true });
  }, [user, loading, navigate]);

  useEffect(() => {
    if (!user) return;
    let cancel = false;
    api.get("/support/threads")
      .then((r) => !cancel && setUnread(r.data?.unread || 0))
      .catch(() => {});
    return () => { cancel = true; };
  }, [user]);

  if (loading || !user) {
    return <div className="page-bg min-h-screen flex items-center justify-center text-ink">Loading…</div>;
  }

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[400px] h-[400px] -top-20 -right-32 opacity-20 animate-orb" aria-hidden />
      <div className="max-w-6xl mx-auto px-4 sm:px-5 md:px-8 py-8 sm:py-12 relative">
        <p className="font-mono text-[11px] uppercase tracking-widest text-amber mb-2">aiclonechats.com · my profile</p>
        <h1 className="heading-display text-3xl sm:text-4xl mb-1" data-testid="account-page-title">My Profile</h1>
        <p className="text-sm text-muted mb-8 break-all">{user.email}</p>

        <div className="grid grid-cols-1 lg:grid-cols-[220px_1fr] gap-6 lg:gap-10">
          {/* Sidebar */}
          <nav className="space-y-1.5" aria-label="Account sections" data-testid="account-sidebar">
            {NAV.map((item, idx) =>
              item.type === "link" ? (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/account/space"}
                  className={({ isActive }) =>
                    `block px-4 py-2.5 rounded-lg text-sm font-medium transition border ${
                      isActive
                        ? "bg-amber/15 border-amber/40 text-amber"
                        : "bg-white/[0.02] border-white/5 text-ink/85 hover:bg-white/[0.06] hover:border-white/15"
                    }`
                  }
                  data-testid={item.testId}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span>{item.label}</span>
                    {item.showBadge && unread > 0 && (
                      <span className="inline-flex items-center justify-center min-w-[20px] h-5 px-1.5 rounded-full bg-amber text-black text-[10px] font-bold" data-testid="inbox-unread-badge">
                        {unread}
                      </span>
                    )}
                  </span>
                </NavLink>
              ) : (
                <div key={`group-${idx}`} className="pt-3">
                  <div className="text-[10px] font-mono uppercase tracking-widest text-muted px-4 mb-1.5" data-testid={`nav-group-${item.label.toLowerCase()}`}>
                    {item.label}
                  </div>
                  {item.children.map((c) => (
                    <NavLink
                      key={c.to}
                      to={c.to}
                      className={({ isActive }) =>
                        `block px-4 py-2 rounded-lg text-sm transition border ${
                          isActive
                            ? "bg-amber/15 border-amber/40 text-amber"
                            : "bg-white/[0.02] border-white/5 text-ink/80 hover:bg-white/[0.06] hover:border-white/15"
                        }`
                      }
                      data-testid={c.testId}
                    >
                      {c.label}
                    </NavLink>
                  ))}
                </div>
              )
            )}
          </nav>

          {/* Outlet content */}
          <div className="min-w-0" data-testid="account-content">
            <Outlet context={{ user, refreshUnread: () => api.get("/support/threads").then((r) => setUnread(r.data?.unread || 0)).catch(() => {}) }} />
          </div>
        </div>
      </div>
    </div>
  );
}
