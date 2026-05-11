import LegalPage from "./LegalPage";

export default function Privacy() {
  return (
    <LegalPage title="Privacy Policy" updated="May 11, 2026" testId="privacy-page">
      <p>This policy explains what data aiclonechats.com collects, why, and what your rights are. We try to collect as little as possible.</p>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">1. Data we collect</h2>
        <ul className="list-disc pl-6 space-y-1">
          <li><strong>Account data:</strong> email, name (if provided), password hash, auth provider.</li>
          <li><strong>Usage data:</strong> credit transactions, chat sessions, audit/login events with a hashed IP. We do not store raw IPs in long-term logs.</li>
          <li><strong>Payment data:</strong> processed entirely by Cashfree. We never see card numbers or CVVs. We store only order IDs and payment status webhooks.</li>
          <li><strong>Content you create:</strong> AI clones, memories, chat transcripts. Visible to you and (for public clones) to people you share the link with.</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">2. How we use it</h2>
        <ul className="list-disc pl-6 space-y-1">
          <li>Provide and operate the service.</li>
          <li>Generate AI responses via third-party providers (OpenAI, Anthropic, Google, fal.ai).</li>
          <li>Detect fraud, abuse, and policy violations.</li>
          <li>Send transactional email via Resend (password reset, verification, delivery notices).</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">3. What we do NOT do</h2>
        <ul className="list-disc pl-6 space-y-1">
          <li>Sell or rent your personal data.</li>
          <li>Send marketing emails or "engagement nudges" — the system remembers, it does not chase.</li>
          <li>Log raw passwords, raw reset tokens, or unredacted IPs.</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">4. Third parties</h2>
        <p>We share data with payment, email, and AI providers strictly as required to deliver the service. Each has its own privacy policy.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">5. Your rights</h2>
        <p>You can request access, export, correction, or deletion of your data by emailing us. We process requests within 30 days.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">6. Retention</h2>
        <p>We keep account data for as long as the account exists. Audit logs are retained for up to 24 months for fraud/abuse investigation. Payment records are retained as required by law.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">7. Children</h2>
        <p>Not directed at children under 13. If you believe a minor has created an account, email us and we'll remove it.</p>
      </section>
    </LegalPage>
  );
}
