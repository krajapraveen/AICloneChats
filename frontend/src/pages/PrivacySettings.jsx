import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import LegalPage, { LegalSection, LegalAlert } from "./LegalPage";
import { useAuth } from "../contexts/AuthContext";

const COOKIE_PREF_KEY = "aicc_cookie_prefs_v1";
const DEFAULT_PREFS = { analytics: false, marketing: false };

function loadPrefs() {
  try {
    const raw = localStorage.getItem(COOKIE_PREF_KEY);
    if (!raw) return DEFAULT_PREFS;
    const parsed = JSON.parse(raw);
    return { ...DEFAULT_PREFS, ...parsed };
  } catch {
    return DEFAULT_PREFS;
  }
}

function savePrefs(prefs) {
  try {
    localStorage.setItem(COOKIE_PREF_KEY, JSON.stringify(prefs));
  } catch {
    /* ignore */
  }
}

function Toggle({ label, description, checked, onChange, testId }) {
  return (
    <label className="flex items-start gap-4 py-4 border-b border-white/5 last:border-0 cursor-pointer" data-testid={`${testId}-row`}>
      <div className="flex-1">
        <div className="text-sm font-medium text-ink">{label}</div>
        {description && <div className="text-xs text-muted mt-1 leading-relaxed">{description}</div>}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${checked ? "bg-amber" : "bg-white/10"}`}
        data-testid={testId}
      >
        <span className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${checked ? "translate-x-5" : "translate-x-0.5"}`} />
      </button>
    </label>
  );
}

function ActionRow({ title, description, buttonLabel, onClick, danger, testId, disabled, loading }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 py-4 border-b border-white/5 last:border-0" data-testid={`${testId}-row`}>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium text-ink">{title}</div>
        <div className="text-xs text-muted mt-1 leading-relaxed">{description}</div>
      </div>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled || loading}
        className={`shrink-0 px-4 py-2 rounded-lg text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed ${
          danger
            ? "bg-red-500/15 text-red-300 border border-red-500/40 hover:bg-red-500/25"
            : "bg-white/5 text-ink border border-white/10 hover:bg-white/10"
        }`}
        data-testid={testId}
      >
        {loading ? "Working…" : buttonLabel}
      </button>
    </div>
  );
}

function emailRequest(subject, body) {
  const url = `mailto:admin@aiclonechats.com?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  window.location.href = url;
}

export default function PrivacySettings() {
  const { user, logout } = useAuth();
  const [prefs, setPrefs] = useState(DEFAULT_PREFS);

  useEffect(() => {
    setPrefs(loadPrefs());
  }, []);

  const updatePref = (key, value) => {
    const next = { ...prefs, [key]: value };
    setPrefs(next);
    savePrefs(next);
    toast.success(`${key === "analytics" ? "Analytics" : "Marketing"} cookies ${value ? "enabled" : "disabled"}.`);
  };

  const handleDownload = () => {
    if (!user) {
      toast.error("Please sign in first to request your data export.");
      return;
    }
    const subject = `Data export request — ${user.email}`;
    const body = `Hello,\n\nI am requesting a portable export of my AI Clone Chats account data.\n\nAccount email: ${user.email}\nUser ID: ${user.user_id || user.id || "(see account)"}\n\nPlease include clones, memories, chat transcripts, uploaded media references, and payment history.\n\nThanks.`;
    emailRequest(subject, body);
    toast.success("Opening email client. We respond within 30 days.");
  };

  const handleDeleteMedia = () => {
    if (!user) { toast.error("Please sign in first."); return; }
    const subject = `Delete uploaded media — ${user.email}`;
    const body = `Hello,\n\nPlease delete all images, reference audio, and other uploaded media tied to my account.\n\nAccount email: ${user.email}\n\nI understand AI-generated outputs derived from this media may already be cached and that you will purge them as part of this request.`;
    emailRequest(subject, body);
    toast.success("Opening email client. Confirmation will follow within 7 days.");
  };

  const handleDeleteOutputs = () => {
    if (!user) { toast.error("Please sign in first."); return; }
    const subject = `Delete AI-generated outputs — ${user.email}`;
    const body = `Hello,\n\nPlease delete all AI-generated outputs (voice clips, lipsynced videos, saved chat transcripts) tied to my account, while leaving the account itself intact.\n\nAccount email: ${user.email}`;
    emailRequest(subject, body);
    toast.success("Opening email client.");
  };

  const handleDeleteAccount = () => {
    if (!user) { toast.error("Please sign in first."); return; }
    const ok = window.confirm(
      "Delete your AI Clone Chats account?\n\nThis is irreversible. Your clones, memories, transcripts, uploaded media, and AI-generated outputs will be queued for permanent deletion. Payment records may be retained as required by law.\n\nWe will send a confirmation email before completing the deletion."
    );
    if (!ok) return;
    const subject = `Account deletion request — ${user.email}`;
    const body = `Hello,\n\nI am requesting permanent deletion of my AI Clone Chats account.\n\nAccount email: ${user.email}\n\nI understand:\n- Clones, memories, transcripts, and uploaded media will be erased.\n- Payment records may be retained as required by tax/dispute law.\n- This action is irreversible.\n\nPlease confirm before completing.`;
    emailRequest(subject, body);
    toast.success("Opening email client. We will confirm by email before deletion.");
  };

  const handleLogoutEverywhere = async () => {
    if (!user) { toast.error("You are not signed in."); return; }
    try {
      await logout();
      toast.success("Signed out on this device. Email us to invalidate all other devices.");
    } catch {
      toast.error("Could not sign out — please clear browser storage manually.");
    }
  };

  return (
    <LegalPage
      title="Privacy Settings"
      eyebrow="aiclonechats.com · controls for your data"
      updated="February 11, 2026"
      description="Manage your cookie preferences, download your data, delete uploaded media or AI outputs, and request account deletion from AI Clone Chats."
      testId="privacy-settings-page"
    >
      <p>
        These controls let you exercise the data rights described in our{" "}
        <a className="text-amber underline" href="/privacy-policy">Privacy Policy</a>. Cookie preferences apply to this browser immediately. Data-export and deletion requests
        are processed by our privacy team within the timeframes stated below.
      </p>

      {!user && (
        <LegalAlert title="Some actions require sign-in" testId="privacy-settings-signin-alert">
          You can manage cookie preferences without an account. To download or delete account data, please{" "}
          <Link to="/login?next=/privacy-settings" className="text-amber underline" data-testid="privacy-settings-login-link">sign in</Link>.
        </LegalAlert>
      )}

      <LegalSection number={1} title="Cookie & analytics preferences" testId="privacy-settings-cookies">
        <p className="text-sm text-muted">
          Strictly necessary cookies (session, payment, security) cannot be disabled — they keep the service working. The toggles below control everything else.
        </p>
        <div className="brutal-card mt-3 px-4 sm:px-5" data-testid="privacy-settings-cookie-card">
          <Toggle
            label="Optional analytics"
            description="Aggregate, anonymized telemetry used to count generations and detect outages. Off by default. No third-party trackers."
            checked={prefs.analytics}
            onChange={(v) => updatePref("analytics", v)}
            testId="privacy-settings-toggle-analytics"
          />
          <Toggle
            label="Product-update emails"
            description="Occasional release notes and new-feature announcements (max ~1 / month). Transactional emails (password reset, payment receipts) are always sent regardless."
            checked={prefs.marketing}
            onChange={(v) => updatePref("marketing", v)}
            testId="privacy-settings-toggle-marketing"
          />
        </div>
        <p className="text-xs text-muted mt-2">
          For the full cookie breakdown, see the <Link to="/cookie-policy" className="text-amber underline" data-testid="privacy-settings-link-cookies">Cookie Policy</Link>.
        </p>
      </LegalSection>

      <LegalSection number={2} title="Your data" testId="privacy-settings-data">
        <div className="brutal-card px-4 sm:px-5" data-testid="privacy-settings-data-card">
          <ActionRow
            title="Download my data"
            description="Receive a portable export of your clones, memories, transcripts, uploaded media references, and payment history. Delivered to your account email within 30 days."
            buttonLabel="Request export"
            onClick={handleDownload}
            disabled={!user}
            testId="privacy-settings-download"
          />
          <ActionRow
            title="Delete uploaded media"
            description="Remove all images and reference audio you have uploaded. Your clones may stop working until you re-upload references."
            buttonLabel="Delete media"
            onClick={handleDeleteMedia}
            danger
            disabled={!user}
            testId="privacy-settings-delete-media"
          />
          <ActionRow
            title="Delete AI-generated outputs"
            description="Remove voice clips, lipsynced videos, and saved AI replies tied to your account. Account, clones, and memory entries are preserved."
            buttonLabel="Delete outputs"
            onClick={handleDeleteOutputs}
            danger
            disabled={!user}
            testId="privacy-settings-delete-outputs"
          />
        </div>
      </LegalSection>

      <LegalSection number={3} title="Account" testId="privacy-settings-account">
        <div className="brutal-card px-4 sm:px-5" data-testid="privacy-settings-account-card">
          <ActionRow
            title="Sign out on this device"
            description="Clears the session token from this browser. Other sessions remain active."
            buttonLabel="Sign out"
            onClick={handleLogoutEverywhere}
            disabled={!user}
            testId="privacy-settings-logout"
          />
          <ActionRow
            title="Delete my account"
            description="Permanent deletion of your account, clones, memories, transcripts, uploaded media, and AI outputs. Payment records may be retained as required by law. Irreversible."
            buttonLabel="Delete account"
            onClick={handleDeleteAccount}
            danger
            disabled={!user}
            testId="privacy-settings-delete-account"
          />
        </div>
      </LegalSection>

      <LegalSection number={4} title="Contact privacy support" testId="privacy-settings-contact">
        <p>
          For any other privacy request — including objection, restriction, correction, or questions about how your data is processed — email{" "}
          <a className="text-amber underline" href="mailto:admin@aiclonechats.com?subject=Privacy%20request">admin@aiclonechats.com</a>. We acknowledge within 3 business
          days and resolve within 30 days.
        </p>
        <button
          type="button"
          onClick={() => emailRequest("Privacy request", `Hello,\n\nMy account: ${user?.email || "(not signed in)"}\n\nI'd like to ask about:\n\n`)}
          className="mt-2 inline-flex items-center px-4 py-2 rounded-lg text-sm font-medium bg-amber text-black hover:bg-amber-soft transition"
          data-testid="privacy-settings-contact-btn"
        >
          Email privacy support
        </button>
      </LegalSection>
    </LegalPage>
  );
}
