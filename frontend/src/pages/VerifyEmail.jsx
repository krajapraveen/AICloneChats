/**
 * Email verification page — collects the 6-digit OTP and triggers the
 * free-credit grant on the backend.
 *
 * Device fingerprint is a stable browser-local hash sent along with the
 * confirm call so the backend can dedup repeat free-credit farming.
 * This is a heuristic, not a security boundary (the real check is the
 * unique index on credit_grants by user_id + email).
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

function getOrMakeDeviceId() {
  try {
    let id = localStorage.getItem("acc_device_id");
    if (!id) {
      id = (crypto?.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now()).replace(/-/g, "").slice(0, 32);
      localStorage.setItem("acc_device_id", id);
    }
    return id;
  } catch {
    return null;
  }
}

export default function VerifyEmail() {
  const { user, refreshUser } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const redirect = params.get("redirect") || "/dashboard";

  const [code, setCode] = useState("");
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [sentOnce, setSentOnce] = useState(false);
  const [emailConfigured, setEmailConfigured] = useState(true);
  const sendInFlight = useRef(false);

  useEffect(() => {
    if (!user) {
      navigate("/login?redirect=/verify-email");
      return;
    }
    if (user.email_verified) {
      navigate(redirect, { replace: true });
    }
  }, [user, navigate, redirect]);

  const sendCode = async () => {
    if (sendInFlight.current) return;
    sendInFlight.current = true;
    setSending(true);
    try {
      const { data } = await api.post("/auth/verify-email/send");
      setSentOnce(true);
      setEmailConfigured(Boolean(data?.email_send_configured));
      if (data?.sent) {
        toast.success("Code sent. Check your inbox.");
      } else {
        toast.warning("Email sending is not configured in this environment. Check the backend logs for the code.");
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not send code.");
    } finally {
      sendInFlight.current = false;
      setSending(false);
    }
  };

  const confirm = async () => {
    if (!/^\d{6}$/.test(code)) {
      toast.error("Code must be 6 digits.");
      return;
    }
    setConfirming(true);
    try {
      const device_id = getOrMakeDeviceId();
      const { data } = await api.post("/auth/verify-email/confirm", { code, device_id });
      if (data?.verified || data?.already_verified) {
        if (data.granted) {
          toast.success(`Verified. ${data.credits} free credits added.`);
        } else if (data.reason && data.reason !== "ok") {
          toast.info(`Verified. (${data.reason.replace(/_/g, " ")} — no free credits granted)`);
        } else {
          toast.success("Email verified.");
        }
        await refreshUser?.();
        navigate(redirect, { replace: true });
      }
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Invalid or expired code.");
    } finally {
      setConfirming(false);
    }
  };

  return (
    <div className="min-h-screen page-bg" data-testid="verify-email-page">
      <Navbar />
      <div className="max-w-md mx-auto px-4 sm:px-8 py-12 space-y-6">
        <header className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-[0.18em] text-amber">VERIFY EMAIL</div>
          <h1 className="heading-display text-3xl">One last step.</h1>
          <p className="text-sm text-muted">
            We need to confirm you control <span className="text-ink font-mono">{user?.email}</span>. Enter the 6-digit code we sent you.
            Your 50 free credits land the moment you confirm.
          </p>
        </header>

        <div className="brutal-card p-5 space-y-4">
          {!sentOnce ? (
            <button onClick={sendCode} disabled={sending} className="btn-brutal w-full text-sm" data-testid="verify-send-code">
              {sending ? "Sending…" : "Send code to my email"}
            </button>
          ) : (
            <>
              <label className="block">
                <span className="text-[11px] font-mono uppercase tracking-widest text-muted">6-digit code</span>
                <input
                  type="text"
                  inputMode="numeric"
                  pattern="\d{6}"
                  maxLength={6}
                  autoComplete="one-time-code"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
                  className="brutal-input mt-1 text-center font-mono text-2xl tracking-[0.3em]"
                  placeholder="000000"
                  data-testid="verify-code-input"
                />
              </label>
              <button onClick={confirm} disabled={confirming || code.length !== 6} className="btn-brutal w-full text-sm" data-testid="verify-confirm">
                {confirming ? "Verifying…" : "Confirm & claim 50 credits"}
              </button>
              <button onClick={sendCode} disabled={sending} className="btn-ghost w-full text-xs" data-testid="verify-resend">
                {sending ? "Sending…" : "Resend code"}
              </button>
            </>
          )}
          {!emailConfigured && sentOnce && (
            <div className="text-[11px] font-mono uppercase tracking-widest text-amber/80" data-testid="verify-mock-banner">
              Email send is in mock mode (no Resend key). Check backend logs for the OTP.
            </div>
          )}
        </div>

        <div className="text-[11px] font-mono uppercase tracking-widest text-muted text-center" data-testid="verify-footer">
          Used 5/day max · Code expires in 10 minutes
        </div>
      </div>
    </div>
  );
}
