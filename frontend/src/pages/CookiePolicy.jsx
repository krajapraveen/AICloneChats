import LegalPage, { LegalSection, LegalTable, LegalAlert } from "./LegalPage";

export default function CookiePolicy() {
  return (
    <LegalPage
      title="Cookie Policy"
      eyebrow="aiclonechats.com · cookies & local storage"
      updated="February 11, 2026"
      description="What cookies and local storage AI Clone Chats sets, why, and how you can control them through Privacy Settings or your browser."
      testId="cookie-policy-page"
    >
      <p>
        This Cookie Policy explains what cookies (and similar technologies such as <code>localStorage</code> and session storage) we set when you visit{" "}
        <a className="text-amber underline" href="https://www.aiclonechats.com">www.aiclonechats.com</a>, what each one does, and how you can control them. It supplements our{" "}
        <a className="text-amber underline" href="/privacy-policy">Privacy Policy</a> and should be read together with it.
      </p>

      <LegalAlert title="One-line summary" testId="cookie-summary-alert">
        We use only the minimum cookies needed to authenticate you, process payments securely, and remember your interface preferences. We do not use third-party advertising
        cookies. Optional analytics is off by default and you can toggle it from{" "}
        <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a>.
      </LegalAlert>

      <LegalSection number={1} title="What is a cookie?" testId="cookie-sec-what">
        <p>
          A cookie is a small text file a site stores in your browser. Some cookies last only until you close the tab ("session cookies"), others persist for a defined period
          ("persistent cookies"). Modern browsers also expose <code>localStorage</code> and <code>sessionStorage</code> for similar purposes; we group those under "cookies" in
          this policy for simplicity.
        </p>
      </LegalSection>

      <LegalSection number={2} title="Strictly necessary cookies" testId="cookie-sec-essential">
        <p>
          These cookies are required for the service to function. You cannot opt out of them; if you block them, login, payments, and core features will break.
        </p>
        <LegalTable
          testId="cookie-essential-table"
          headers={["Name", "Set by", "Purpose", "Lifetime"]}
          rows={[
            ["session_token (localStorage)", "aiclonechats.com (frontend)", "Stores your Bearer auth token after login.", "Until logout or you clear browser storage."],
            ["session_token (HttpOnly cookie)", "aiclonechats.com (backend)", "Server-side session reference, secure & SameSite=Lax.", "30 days, rotated on login."],
            ["aicc_device_id (localStorage)", "aiclonechats.com (frontend)", "Stable identifier for anonymous trials and rate limiting.", "Until you clear browser storage."],
            ["__cf_bm", "Cloudflare", "Bot management & DDoS protection.", "30 minutes (per session)."],
            ["cf_clearance", "Cloudflare", "Confirms you passed a security challenge.", "Up to 1 year."],
          ]}
        />
      </LegalSection>

      <LegalSection number={3} title="Authentication & session cookies" testId="cookie-sec-auth">
        <p>
          When you log in we issue a session token in two places: (a) <code>localStorage</code> for use as a Bearer header on API calls, and (b) an HttpOnly cookie that the
          backend uses to identify you across requests. Both are rotated on each login, invalidated on logout, and tied to your user record server-side. Tokens never contain
          personally-identifying information directly — they are random opaque strings.
        </p>
      </LegalSection>

      <LegalSection number={4} title="Payment & security cookies" testId="cookie-sec-payment">
        <p>
          When you initiate a checkout via our payment provider (Cashfree), the provider sets its own cookies on its checkout pages to maintain the payment session, prevent
          fraud, and complete 3-D Secure / UPI / NetBanking flows. Those cookies are governed by the provider's own privacy policy, not ours.
        </p>
        <LegalTable
          testId="cookie-payment-table"
          headers={["Name", "Set by", "Purpose"]}
          rows={[
            ["cf_session, cf_payment_*", "Cashfree", "Maintains your checkout session through redirects."],
            ["aicc_order_pending (sessionStorage)", "aiclonechats.com", "Remembers the order ID you initiated so we can poll for completion on return."],
          ]}
        />
      </LegalSection>

      <LegalSection number={5} title="Analytics cookies (optional)" testId="cookie-sec-analytics">
        <p>
          We run our own aggregate, anonymized telemetry to count generations, paywall hits, and cost meters. None of it sets a third-party cookie or tracks you across sites.
          If we ever add an external analytics tool (we have not as of the date above), it will be off by default and you will be able to enable or disable it from{" "}
          <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a>.
        </p>
      </LegalSection>

      <LegalSection number={6} title="What we do NOT do" testId="cookie-sec-not">
        <ul className="list-disc pl-6 space-y-1">
          <li>We do not set third-party advertising or remarketing cookies.</li>
          <li>We do not sell cookie-derived data to brokers.</li>
          <li>We do not run pixel trackers from social networks.</li>
          <li>We do not use cookies for behavioural profiling outside the strictly necessary set above.</li>
        </ul>
      </LegalSection>

      <LegalSection number={7} title="How to control cookies" testId="cookie-sec-control">
        <p><strong>On AI Clone Chats:</strong> visit <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a> to toggle optional preferences and to
        clear local data tied to your account.</p>

        <p><strong>In your browser:</strong> all major browsers let you view, block, or delete cookies. Quick links:</p>
        <ul className="list-disc pl-6 space-y-1">
          <li><a className="text-amber underline" href="https://support.google.com/chrome/answer/95647" target="_blank" rel="noopener noreferrer">Chrome — manage cookies</a></li>
          <li><a className="text-amber underline" href="https://support.mozilla.org/en-US/kb/cookies-information-websites-store-on-your-computer" target="_blank" rel="noopener noreferrer">Firefox — manage cookies</a></li>
          <li><a className="text-amber underline" href="https://support.apple.com/en-us/HT201265" target="_blank" rel="noopener noreferrer">Safari — manage cookies</a></li>
          <li><a className="text-amber underline" href="https://support.microsoft.com/en-us/microsoft-edge" target="_blank" rel="noopener noreferrer">Edge — manage cookies</a></li>
        </ul>
        <p>
          If you block strictly necessary cookies, parts of the service — including login and checkout — will not work. That is a property of the cookie, not a punishment.
        </p>
      </LegalSection>

      <LegalSection number={8} title="Changes to this policy" testId="cookie-sec-changes">
        <p>
          If we add, remove, or change the purpose of a cookie, we will update this page and the "Last updated" date. Material changes that affect non-essential cookies will
          also be surfaced in <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a> on your next visit.
        </p>
      </LegalSection>

      <LegalSection number={9} title="Contact" testId="cookie-sec-contact">
        <p>
          Cookie questions can be sent to{" "}
          <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a>.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
