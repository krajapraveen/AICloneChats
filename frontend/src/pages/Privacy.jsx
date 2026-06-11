import LegalPage, { LegalSection, LegalAlert, LegalTable } from "./LegalPage";

export default function Privacy() {
  return (
    <LegalPage
      title="Privacy Policy"
      eyebrow="aiclonechats.com · how we handle your data"
      updated="February 11, 2026"
      description="How AI Clone Chats collects, uses, retains, and protects your personal data, uploaded media, AI-generated content, and payment information."
      testId="privacy-page"
    >
      <p>
        AI Clone Chats (operated at <a className="text-amber underline" href="https://www.aiclonechats.com">www.aiclonechats.com</a>, hereafter "we", "us", "the service")
        is a premium AI platform that lets you create, talk to, and share original AI personas — sometimes referred to as "clones" — through chat, voice, video avatar, and
        scheduled messaging surfaces. This Privacy Policy describes what data we collect, why we collect it, who we share it with, how long we keep it, and the rights you have over it.
      </p>
      <p>
        Our governing philosophy is simple: <em>the system remembers; it does not chase.</em> We collect only the minimum required to run the product, and we never sell, rent, or
        weaponise your data for advertising.
      </p>

      <LegalAlert title="Identity-content notice" testId="privacy-identity-alert">
        Because AI Clone Chats produces voice, image, and persona-style content that can resemble real people, you are responsible for ensuring you only upload, describe, or
        generate likenesses of <strong>yourself</strong> or of <strong>people who have given you explicit, verifiable permission</strong>. Misuse — including non-consensual
        likenesses, deepfakes, and identity impersonation — is prohibited under our <a className="text-amber underline" href="/terms-of-service">Terms of Service</a> and{" "}
        <a className="text-amber underline" href="/acceptable-use">Acceptable Use Policy</a>.
      </LegalAlert>

      <LegalSection number={1} title="What AI Clone Chats does" testId="privacy-sec-overview">
        <p>
          We let you (a) create AI personas with custom personality, tone, and memory; (b) chat with them in text, voice, or scheduled-delivery formats; (c) generate short video
          avatar responses; (d) participate in moderated debate rooms and translation chats; and (e) purchase credit packs or subscriptions to use those features.
        </p>
        <p>
          To do this we operate a web frontend, a backend API, a MongoDB database, and we route certain workloads to third-party providers (large language models, lipsync video
          generation, transactional email, payment processing). Those providers are listed in <a className="text-amber underline" href="#sec-third-parties">Section 7</a>.
        </p>
      </LegalSection>

      <LegalSection number={2} title="Data we collect" testId="privacy-sec-collect">
        <p><strong>2.1 Account data.</strong> Email address; display name (optional); a salted bcrypt hash of your password (never the raw password); your auth provider
        (email + password, Google OAuth); email-verification status; account creation timestamp; admin/role flags; plan tier; credits balance.</p>

        <p><strong>2.2 Authentication & session data.</strong> Server-side session tokens (rotated on login and logout); login attempts; failed-login counters used for brute-force
        protection; password-reset request timestamps; IP address (one-way hashed before long-term retention — we do not store raw IPs in audit logs); browser User-Agent;
        a stable device-id stored in your browser to support anonymous trials.</p>

        <p><strong>2.3 Content you create.</strong> Clone profiles (name, traits, system prompts, personality sliders, memory entries); chat transcripts (text, mood signals, debate
        turns, scheduled "delayed" messages); uploaded reference images and audio; AI-generated outputs (text replies, voice clips, lipsynced video frames); favourites, share IDs,
        and public clone links.</p>

        <p><strong>2.4 Voice & video media.</strong> Voice clips you record or upload as reference; voice clips synthesised on your behalf; short lipsynced video files generated
        by our lipsync provider; metadata such as duration, language, and provider job IDs.</p>

        <p><strong>2.5 Payment & subscription data.</strong> Plan you subscribed to, credit pack purchases, internal order IDs, payment provider order/session IDs, payment status
        (created / paid / failed / refunded), webhook delivery logs (signature-validated). <strong>We never store card numbers, CVVs, UPI PINs, or full bank credentials.</strong>
        Those live exclusively with our payment provider (currently Cashfree in production).</p>

        <p><strong>2.6 Telemetry & abuse signals.</strong> Cost-meter events (token counts, generations per session), safety/moderation flags, paywall hits, anonymized error
        traces. This is what powers our admin observability dashboards and lets us detect fraud.</p>

        <p><strong>2.7 Communications.</strong> Emails you send to <code>admin@aiclonechats.com</code> or <code>krajapraveen@aiclonechats.com</code>, and our replies.</p>
      </LegalSection>

      <LegalSection number={3} title="Cookies and similar technologies" testId="privacy-sec-cookies">
        <p>
          We use a small number of strictly necessary cookies (session, CSRF, payment continuity) and some optional analytics signals (only if you opt in via{" "}
          <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a>). For the full breakdown including names, lifetimes, and purposes, see our{" "}
          <a className="text-amber underline" href="/cookie-policy">Cookie Policy</a>.
        </p>
      </LegalSection>

      <LegalSection number={4} title="How we use your data" testId="privacy-sec-use">
        <ul className="list-disc pl-6 space-y-1">
          <li><strong>Operate the service:</strong> authenticate you, render your clones, deliver chats, debit credits atomically.</li>
          <li><strong>Generate AI output:</strong> forward chat prompts, voice prompts, and image references to large-language-model and media providers (see Section 7).</li>
          <li><strong>Process payments:</strong> create Cashfree orders, verify webhook signatures, grant credits upon confirmed payment.</li>
          <li><strong>Send transactional email:</strong> verification, password reset, delivery notifications. We do <em>not</em> send marketing emails or engagement nudges.</li>
          <li><strong>Detect abuse:</strong> brute-force, rate-limit violations, safety policy hits, payment fraud.</li>
          <li><strong>Improve reliability:</strong> aggregate, anonymized cost telemetry. Individual chat content is never used to "train" a model on your behalf without explicit
          opt-in.</li>
        </ul>
      </LegalSection>

      <LegalSection number={5} title="Data retention" testId="privacy-sec-retention">
        <LegalTable
          testId="privacy-retention-table"
          headers={["Data category", "Retention period"]}
          rows={[
            ["Account record (email, hash, role, plan)", "Until you delete your account."],
            ["Clones, chat transcripts, memory entries", "Until you delete them, or until account deletion."],
            ["Uploaded images and reference audio", "Until you delete them, or until account deletion."],
            ["AI-generated voice clips & lipsync videos", "Up to 90 days unless explicitly saved by you."],
            ["Payment orders + webhook logs", "7 years (statutory tax / dispute window)."],
            ["Audit logs (hashed IPs, login events)", "Up to 24 months."],
            ["Anonymized telemetry & cost meters", "Indefinitely, in aggregate form only."],
          ]}
        />
      </LegalSection>

      <LegalSection number={6} title="Data deletion & user rights" testId="privacy-sec-rights">
        <p>You may, at any time:</p>
        <ul className="list-disc pl-6 space-y-1">
          <li><strong>Access</strong> a copy of the data we hold about you.</li>
          <li><strong>Export</strong> your clones, transcripts, memory entries, and account record in a portable format.</li>
          <li><strong>Correct</strong> inaccurate profile fields.</li>
          <li><strong>Delete</strong> individual clones, transcripts, uploads, or your entire account.</li>
          <li><strong>Object</strong> to or restrict certain processing where required by law.</li>
          <li><strong>Withdraw consent</strong> for optional analytics at any time without affecting service.</li>
        </ul>
        <p>
          Use the controls on <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a> or email{" "}
          <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a>. We respond within 30 days; complex requests may require identity
          verification.
        </p>
      </LegalSection>

      <LegalSection number={7} title="Third-party processors" testId="privacy-sec-third-parties">
        <p id="sec-third-parties">We share strictly the data necessary to deliver the service with the following processors. Each is contractually bound to use that data
        only on our instructions.</p>
        <LegalTable
          testId="privacy-third-party-table"
          headers={["Provider", "Purpose", "Data shared"]}
          rows={[
            ["Cashfree Payments (India)", "Payments, refunds, webhooks", "Order ID, amount, masked customer email/phone"],
            ["OpenAI / Anthropic / Google", "LLM text generation", "Chat prompts and context — not stored by us for training"],
            ["fal.ai", "Lipsync video generation", "Voice clip + reference image for the duration of generation"],
            ["Resend", "Transactional email delivery", "Recipient email + email content"],
            ["MongoDB Atlas / managed hosting", "Database + compute", "Encrypted at rest; least-privilege access"],
            ["Cloudflare", "CDN, DDoS protection, TLS", "Request metadata"],
          ]}
        />
      </LegalSection>

      <LegalSection number={8} title="Children and minors" testId="privacy-sec-children">
        <p>
          AI Clone Chats is <strong>not directed at children under 13</strong>. Users between 13 and 18 may use the service only with the involvement of a parent or legal
          guardian and only on plan tiers that the guardian has explicitly authorized. If we become aware that we hold data of a child under 13, we will delete the account and
          all associated content. Report concerns to <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a>.
        </p>
      </LegalSection>

      <LegalSection number={9} title="International transfers" testId="privacy-sec-international">
        <p>
          Our servers and processors operate in India, the United States, and the European Union. By using the service you consent to your data being processed in those
          jurisdictions. Where required, we rely on standard contractual clauses or equivalent safeguards with our processors.
        </p>
      </LegalSection>

      <LegalSection number={10} title="Security" testId="privacy-sec-security">
        <p>
          We hash passwords with bcrypt, encrypt traffic with TLS 1.2+, sign payment webhooks with HMAC-SHA256, and isolate admin operations behind explicit role checks. See our
          full <a className="text-amber underline" href="/security">Security</a> page and report vulnerabilities to{" "}
          <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a> with the subject line <code>SECURITY</code>.
        </p>
      </LegalSection>

      <LegalSection number={11} title="Changes to this policy" testId="privacy-sec-changes">
        <p>
          We will revise this document as the product evolves. Material changes will be announced at the top of this page with an updated "Last updated" date and, where
          appropriate, an in-app notice on your next login.
        </p>
      </LegalSection>

      <LegalSection number={12} title="Contact" testId="privacy-sec-contact">
        <p>
          Privacy questions, data requests, or compliance enquiries can be sent to{" "}
          <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a> or{" "}
          <a className="text-amber underline" href="mailto:krajapraveen@aiclonechats.com">krajapraveen@aiclonechats.com</a>. We aim to acknowledge within 3 business days and
          resolve within 30 days.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
