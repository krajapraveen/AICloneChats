import LegalPage, { LegalSection, LegalAlert, LegalTable } from "./LegalPage";

export default function Security() {
  return (
    <LegalPage
      title="Security"
      eyebrow="aiclonechats.com · how we protect you"
      updated="February 11, 2026"
      description="How AI Clone Chats secures accounts, payments, uploaded media, and AI-generated content — plus how to report a vulnerability."
      testId="security-page"
    >
      <p>
        Security at AI Clone Chats is treated as a product feature, not a checkbox. Because the service handles identity-style content, voice, and payments, we apply
        defence-in-depth across the stack — from frontend session handling to atomic credit deduction, signed payment webhooks, and admin observability dashboards.
      </p>

      <LegalAlert title="Reporting a vulnerability" testId="security-report-alert">
        Found a bug that could compromise an account, leak data, or bypass payment? Email{" "}
        <a className="text-amber underline" href="mailto:admin@aiclonechats.com?subject=SECURITY">admin@aiclonechats.com</a> with the subject line <code>SECURITY</code>.
        Please give us a reasonable window to remediate before public disclosure. We acknowledge reports within 72 hours and prioritize remediation.
      </LegalAlert>

      <LegalSection number={1} title="Account protection" testId="security-sec-account">
        <ul className="list-disc pl-6 space-y-1">
          <li><strong>Password hashing:</strong> bcrypt with a per-user salt and a tuned cost factor. Raw passwords are never logged, persisted, or transmitted to any LLM provider.</li>
          <li><strong>Session tokens:</strong> opaque random strings, rotated on login and logout, validated server-side against a session record on every protected request.</li>
          <li><strong>Brute-force defence:</strong> failed-login attempts are counted per email + IP-hash. Repeated failures trigger temporary lockouts and admin alerts.</li>
          <li><strong>OAuth:</strong> Google sign-in uses the auth-code flow with server-side token exchange and ID-token verification. We never receive your Google password.</li>
          <li><strong>Password reset:</strong> single-use, time-limited tokens; reset emails go via Resend over TLS; the raw token is never logged.</li>
          <li><strong>Admin access:</strong> role-based; sensitive admin endpoints additionally check an explicit admin email allow-list defined in environment variables.</li>
        </ul>
      </LegalSection>

      <LegalSection number={2} title="Secure payments" testId="security-sec-payments">
        <ul className="list-disc pl-6 space-y-1">
          <li><strong>No card storage:</strong> we never see, store, or transmit your card number, CVV, UPI PIN, or bank credentials. The payment provider (Cashfree in production)
          handles all sensitive payment data on PCI-DSS-compliant infrastructure.</li>
          <li><strong>Signed webhooks:</strong> payment confirmations arrive via HTTPS webhooks signed with HMAC-SHA256 keyed on a secret known only to us and the gateway. Webhooks
          with invalid signatures are rejected and logged.</li>
          <li><strong>Idempotency & deduplication:</strong> credit grants are tied to a unique internal order ID. Duplicate webhook deliveries are detected and suppressed.</li>
          <li><strong>Atomic deductions:</strong> credits are debited from your balance at the moment a generation succeeds, using an atomic database operation. Failed generations
          auto-refund.</li>
          <li><strong>Audit trail:</strong> every payment, credit grant, deduction, and refund is logged with timestamp, user ID, and provider order ID.</li>
        </ul>
      </LegalSection>

      <LegalSection number={3} title="Data encryption" testId="security-sec-encryption">
        <p>
          <strong>In transit.</strong> All traffic between you and aiclonechats.com is encrypted with TLS 1.2 or higher, with modern cipher suites and HSTS enforcement.
          Traffic to our payment, email, and AI providers is similarly encrypted end-to-end.
        </p>
        <p>
          <strong>At rest.</strong> Our database storage is encrypted at rest via the underlying managed disk infrastructure. Backups are encrypted. Sensitive secret values
          (API keys, webhook secrets) are stored as environment variables in our deployment platform, never committed to source control.
        </p>
        <p>
          <strong>Hashing.</strong> Passwords use bcrypt. IP addresses, where retained for fraud detection, are one-way hashed before long-term storage.
        </p>
      </LegalSection>

      <LegalSection number={4} title="Uploaded media protection" testId="security-sec-media">
        <ul className="list-disc pl-6 space-y-1">
          <li>Uploaded images and audio are scanned for size, format, and basic safety signals before storage.</li>
          <li>Reference media routed to AI providers (e.g. fal.ai for lipsync) is shared only for the duration of the generation and not retained by the provider beyond their
          operational windows.</li>
          <li>Access to uploaded media is gated by user ID and clone ownership — random media URLs are not enumerable.</li>
          <li>You can delete any uploaded media from <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a> or by emailing us.</li>
        </ul>
      </LegalSection>

      <LegalSection number={5} title="Abuse prevention" testId="security-sec-abuse">
        <p>We run several layers of abuse detection:</p>
        <LegalTable
          testId="security-abuse-table"
          headers={["Surface", "Defence"]}
          rows={[
            ["Auth", "Brute-force lockouts, password-reset rate limits, email-verification gate for paid features"],
            ["LLM prompts", "Safety classifiers on every prompt; refusals logged to admin safety dashboard"],
            ["Voice & lipsync uploads", "Format / size / duration limits + content moderation"],
            ["Payments", "Webhook signature checks, replay protection, duplicate-order suppression"],
            ["API", "Per-user and per-IP-hash rate limiting on hot endpoints"],
            ["Admin", "Role-based + email allow-list, every action logged"],
          ]}
        />
      </LegalSection>

      <LegalSection number={6} title="Vulnerability reporting" testId="security-sec-report">
        <p>
          We welcome coordinated disclosure from security researchers. To report:
        </p>
        <ol className="list-decimal pl-6 space-y-1">
          <li>Email <a className="text-amber underline" href="mailto:admin@aiclonechats.com?subject=SECURITY">admin@aiclonechats.com</a> with the subject <code>SECURITY</code>.</li>
          <li>Include reproduction steps, an estimate of impact, and any logs / screenshots that help us triage.</li>
          <li>Do <strong>not</strong> publish the issue publicly or share it with third parties until we have had a reasonable window to remediate (typically 90 days, shorter for
          critical issues).</li>
          <li>Do <strong>not</strong> access, modify, or exfiltrate data belonging to other users while testing.</li>
        </ol>
        <p>
          We will acknowledge your report within 72 hours, work with you on fixes, and credit you in our release notes if you wish. We do not currently run a paid bug-bounty
          program but appreciate responsible reporting and may offer ex-gratia recognition for high-impact reports.
        </p>
      </LegalSection>

      <LegalSection number={7} title="User safety tips" testId="security-sec-tips">
        <ul className="list-disc pl-6 space-y-1">
          <li>Use a unique, strong password (12+ characters). A password manager is your friend.</li>
          <li>Prefer "Continue with Google" if you already trust that provider; the auth-code flow keeps your credentials away from us.</li>
          <li>Never share your <code>session_token</code> from browser storage with anyone — it is equivalent to your password while valid.</li>
          <li>Verify the URL bar reads <code>https://aiclonechats.com</code> before entering payment information.</li>
          <li>Only upload media you own or have written consent for. Treat AI-generated voice and avatars as production-grade — disclose them as AI-generated when sharing externally.</li>
          <li>If you suspect your account has been compromised, log out everywhere via Privacy Settings and email us immediately.</li>
        </ul>
      </LegalSection>

      <LegalSection number={8} title="Contact" testId="security-sec-contact">
        <p>
          Security questions or reports:{" "}
          <a className="text-amber underline" href="mailto:admin@aiclonechats.com?subject=SECURITY">admin@aiclonechats.com</a> (subject: <code>SECURITY</code>).
        </p>
      </LegalSection>
    </LegalPage>
  );
}
