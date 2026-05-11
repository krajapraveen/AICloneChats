import LegalPage from "./LegalPage";

export default function Terms() {
  return (
    <LegalPage title="Terms of Service" updated="May 11, 2026" testId="terms-page">
      <p>By creating an account on aiclonechats.com you agree to these terms. If you disagree with any part of them, do not use the service.</p>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">1. Eligibility</h2>
        <p>You must be at least 13 years old (or the digital-consent age in your jurisdiction, whichever is higher) to use this service. If you are using it on behalf of an organization, you must have authority to bind that organization.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">2. Account & Security</h2>
        <p>You are responsible for keeping your account credentials confidential and for all activity under your account. Notify us immediately if you suspect unauthorized access.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">3. Acceptable Use</h2>
        <p>You agree to use the service only for lawful, ethical, and original purposes. See our <a className="text-amber underline" href="/acceptable-use">Acceptable Use Policy</a> for the full list of prohibited content and conduct. Highlights:</p>
        <ul className="list-disc pl-6 space-y-1 mt-2">
          <li>Create original AI personas. Do not impersonate real people without their explicit written permission.</li>
          <li>Use only content you own or have rights to use. No copyrighted, trademarked, or pirated material.</li>
          <li>No celebrity, brand, franchise, or fictional-character impersonation.</li>
          <li>No sexual content involving minors, harassment, hate speech, doxxing, or instructions for illegal activity.</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">4. Payments & Credits</h2>
        <p>Subscription and top-up purchases are processed by Cashfree. Credits are non-transferable, non-refundable except where required by law or where the AI provider fails to deliver a response (in which case credits are auto-refunded). All prices are displayed in your local currency where supported.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">5. Termination</h2>
        <p>We may suspend or terminate your account for any violation of these terms, particularly Acceptable Use violations. You may delete your account at any time by emailing us.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">6. Disclaimer</h2>
        <p>The service is provided "as is" without warranties. AI-generated content is not a substitute for professional medical, legal, financial, or safety-critical advice. You are responsible for how you use the output.</p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">7. Changes</h2>
        <p>We may update these terms. Material changes will be communicated by updating the "Last updated" date above and, where appropriate, by notice to your account email.</p>
      </section>
    </LegalPage>
  );
}
