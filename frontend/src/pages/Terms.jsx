import LegalPage, { LegalSection, LegalAlert } from "./LegalPage";

export default function Terms() {
  return (
    <LegalPage
      title="Terms of Service"
      eyebrow="aiclonechats.com · the rules of the road"
      updated="February 11, 2026"
      description="The binding terms that govern your use of AI Clone Chats — eligibility, acceptable use, AI-generated content, payments, refunds, IP, and liability."
      testId="terms-page"
    >
      <p>
        These Terms of Service ("<strong>Terms</strong>") form a binding agreement between you ("<strong>you</strong>", "<strong>your</strong>") and the operators of AI Clone
        Chats ("<strong>we</strong>", "<strong>us</strong>", "<strong>aiclonechats.com</strong>"). By creating an account, logging in, or using any part of the service, you
        confirm that you have read, understood, and agree to be bound by these Terms, together with our{" "}
        <a className="text-amber underline" href="/privacy-policy">Privacy Policy</a>,{" "}
        <a className="text-amber underline" href="/cookie-policy">Cookie Policy</a>, and{" "}
        <a className="text-amber underline" href="/acceptable-use">Acceptable Use Policy</a>.
      </p>
      <p>If you disagree with any part of these Terms, do not use the service.</p>

      <LegalAlert tone="danger" title="Identity, voice & likeness — read this first" testId="terms-identity-alert">
        AI Clone Chats produces persona-style content including voice, video avatars, and text in the style of a person. You may only upload, describe, or generate likenesses of{" "}
        <strong>yourself</strong> or of <strong>people who have provided you explicit, verifiable, revocable consent</strong>. Non-consensual likenesses, deepfakes, sexual or
        defamatory impersonation, or any use that misleads a third party about who is speaking is strictly prohibited and is grounds for immediate account termination and
        reporting to relevant authorities.
      </LegalAlert>

      <LegalSection number={1} title="Acceptance of Terms" testId="terms-sec-acceptance">
        <p>
          By accessing the service you accept these Terms in full. If you are using the service on behalf of an organization, you represent that you are authorised to bind that
          organization to these Terms. We may update these Terms from time to time; material changes will be announced with an updated date and, where appropriate, an in-app
          notice. Continued use after a change constitutes acceptance.
        </p>
      </LegalSection>

      <LegalSection number={2} title="Eligibility" testId="terms-sec-eligibility">
        <p>
          You must be at least 13 years old (or the digital-consent age in your jurisdiction, whichever is higher) to create an account. Users between 13 and 18 must have the
          involvement of a parent or legal guardian. Accounts created in violation of this section may be terminated without refund.
        </p>
      </LegalSection>

      <LegalSection number={3} title="Account & security responsibilities" testId="terms-sec-account">
        <ul className="list-disc pl-6 space-y-1">
          <li>You are responsible for safeguarding your password and any session tokens.</li>
          <li>You are responsible for all activity that occurs under your account.</li>
          <li>You must provide a working email address. We use it for verification, password reset, and delivery notices only.</li>
          <li>You agree not to share, sell, or sublicense your account credentials.</li>
          <li>You must notify us immediately at <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a> if you suspect
          unauthorized access.</li>
        </ul>
      </LegalSection>

      <LegalSection number={4} title="User content & consent for uploaded media" testId="terms-sec-user-content">
        <p>
          "User Content" means any text, image, audio, prompt, persona description, memory entry, debate input, or other material you upload, generate, or share via the service.
          You retain ownership of your User Content. By submitting User Content you grant us a worldwide, non-exclusive, royalty-free licence solely to host, route to AI
          providers, render to other users you have shared with, and back up that User Content for the purpose of operating the service.
        </p>
        <p>
          You warrant and represent that, for every piece of User Content you submit:
        </p>
        <ul className="list-disc pl-6 space-y-1">
          <li>You own it, or you have obtained all rights, consents, and permissions required to use it on AI Clone Chats;</li>
          <li>If the content depicts, describes, or simulates a real person — including their face, voice, mannerisms, or identifying details — you have that person's{" "}
          <strong>explicit, verifiable, revocable</strong> consent;</li>
          <li>Submitting it does not violate any law, contract, court order, or third-party right.</li>
        </ul>
        <p>
          You may revoke consent and request deletion of any submitted media at any time via{" "}
          <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a>.
        </p>
      </LegalSection>

      <LegalSection number={5} title="No impersonation, no deepfake misuse" testId="terms-sec-impersonation">
        <p>The following are categorically prohibited and constitute material breach of these Terms:</p>
        <ul className="list-disc pl-6 space-y-1">
          <li>Creating a persona, voice, or avatar that resembles a real person <strong>without that person's consent</strong>, including celebrities, politicians, journalists,
          minors, and members of the public;</li>
          <li>Sexual or pornographic content involving real people, regardless of consent if it is intended to be passed off as authentic;</li>
          <li>Using AI-generated voice or video to defraud, deceive, defame, harass, blackmail, or commit identity theft;</li>
          <li>Posting AI-generated content to social platforms or messaging apps without a clear "AI-generated" disclosure when it could reasonably be mistaken for real;</li>
          <li>Any content sexualising minors — this is prohibited absolutely and will be reported to law enforcement.</li>
        </ul>
      </LegalSection>

      <LegalSection number={6} title="AI-generated content disclaimer" testId="terms-sec-ai-disclaimer">
        <p>
          AI-generated text, voice, image, and video output ("<strong>AI Output</strong>") is produced by third-party large language and media models. AI Output may be
          inaccurate, offensive, contradictory, or out of date. AI Output is <strong>not</strong> professional medical, legal, financial, mental-health, or safety-critical
          advice. You are solely responsible for evaluating, fact-checking, and using AI Output. We make no warranty that AI Output will be fit for any particular purpose.
        </p>
      </LegalSection>

      <LegalSection number={7} title="Subscription, credits & top-ups" testId="terms-sec-payments">
        <p>
          AI Clone Chats operates a strict zero-free-credit premium model. To use the paid surfaces you must be on an active subscription tier, which entitles you to a monthly
          credit allotment. Subscribers may also purchase additional one-off credit top-up packs.
        </p>
        <ul className="list-disc pl-6 space-y-1">
          <li>Prices and credit amounts are displayed on <a className="text-amber underline" href="/pricing">/pricing</a>.</li>
          <li>Payments are processed by our gateway provider (currently <strong>Cashfree</strong> in production). We do not store or have access to your card / UPI credentials.</li>
          <li>Credits are deducted server-side, atomically, at the moment a generation succeeds. If a generation fails for an upstream reason, the deduction is reversed.</li>
          <li>Credits are non-transferable between accounts.</li>
          <li>Subscriptions renew on the stated cadence. You may cancel a renewal at any time through Privacy Settings or by emailing us. Cancellation takes effect at the end of
          the current billing period.</li>
        </ul>
      </LegalSection>

      <LegalSection number={8} title="Refund policy" testId="terms-sec-refunds">
        <p>
          Digital credits are non-refundable except in the following cases:
        </p>
        <ul className="list-disc pl-6 space-y-1">
          <li><strong>Failed generation auto-refund:</strong> if an AI provider returns an error or empty response, the credit is automatically refunded to your balance — you do
          not need to file a request.</li>
          <li><strong>Duplicate charge:</strong> if our webhook deduplication logic fails to suppress a duplicate payment for the same order, contact us with the order ID and we
          will refund the duplicate.</li>
          <li><strong>Statutory rights:</strong> where local consumer law grants a non-waivable refund right (for example certain cooling-off periods for first-time digital
          purchases), we will honour it.</li>
        </ul>
        <p>
          To file a refund request, email <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a> with the subject{" "}
          <code>REFUND – &lt;order_id&gt;</code> within 7 days of the charge. We respond within 5 business days.
        </p>
      </LegalSection>

      <LegalSection number={9} title="Prohibited use" testId="terms-sec-prohibited">
        <p>You agree not to, and not to permit anyone else to:</p>
        <ul className="list-disc pl-6 space-y-1">
          <li>Reverse engineer, scrape, or attempt to extract source code or model weights;</li>
          <li>Use the service to develop a competing AI persona product;</li>
          <li>Probe for vulnerabilities other than through our coordinated disclosure process (see <a className="text-amber underline" href="/security">Security</a>);</li>
          <li>Bypass the credit system, paywall, rate limits, or admin controls;</li>
          <li>Use the service in connection with weapons, surveillance of private individuals, or political disinformation campaigns;</li>
          <li>Upload malware, automate API calls in violation of stated rate limits, or impersonate other users;</li>
          <li>Resell access without our prior written consent.</li>
        </ul>
        <p>For the complete list of prohibited content categories, see our{" "}
          <a className="text-amber underline" href="/acceptable-use">Acceptable Use Policy</a>.
        </p>
      </LegalSection>

      <LegalSection number={10} title="Intellectual property" testId="terms-sec-ip">
        <p>
          <strong>Our IP.</strong> The aiclonechats.com name, marks, UI, prompts, system prompts, code, and infrastructure are owned by us. You receive a limited, revocable,
          non-exclusive licence to use the service for personal or internal business use, subject to these Terms.
        </p>
        <p>
          <strong>Your IP.</strong> You retain ownership of your User Content (Section 4). To the extent AI Output is generated <em>solely</em> in response to your prompts and
          User Content and is sufficiently original under applicable law, you own such AI Output, subject to (a) the rights of the underlying model providers, and (b) the
          consent obligations in Sections 4 and 5.
        </p>
        <p>
          <strong>Trademarks.</strong> AI Clone Chats is not affiliated with, endorsed by, or sponsored by any celebrity, brand, fictional character, franchise, or public
          figure. Any mention of such names is descriptive and does not imply endorsement.
        </p>
      </LegalSection>

      <LegalSection number={11} title="Account suspension & termination" testId="terms-sec-termination">
        <p>
          We may suspend or terminate your account, with or without notice, if we reasonably believe you have violated these Terms, particularly the consent and impersonation
          provisions. Egregious violations — non-consensual likeness misuse, child sexual content, fraud — result in immediate termination and reporting to authorities. Where
          permitted, we may forfeit any unused credits on termination for cause.
        </p>
        <p>
          You may delete your account at any time via{" "}
          <a className="text-amber underline" href="/privacy-settings">Privacy Settings</a> or by emailing us. Account deletion is irreversible and erases your clones, memory
          entries, transcripts, and uploaded media, subject to the retention windows in our Privacy Policy.
        </p>
      </LegalSection>

      <LegalSection number={12} title="Limitation of liability" testId="terms-sec-liability">
        <p>
          To the maximum extent permitted by law: (a) the service is provided "as is" and "as available" without warranties of any kind, express or implied; (b) we shall not be
          liable for indirect, incidental, consequential, special, exemplary, or punitive damages, or for lost profits, lost revenue, lost data, or business interruption,
          arising out of or related to the service or these Terms; and (c) our aggregate liability for any direct damages shall not exceed the greater of (i) the fees you paid
          to us in the three (3) months preceding the event giving rise to the claim, or (ii) USD 50.
        </p>
        <p>
          Nothing in these Terms limits liability that cannot lawfully be limited, including liability for gross negligence, willful misconduct, or for personal injury caused by
          our negligence.
        </p>
      </LegalSection>

      <LegalSection number={13} title="Indemnity" testId="terms-sec-indemnity">
        <p>
          You agree to indemnify and hold us harmless from any claim, loss, or expense (including reasonable legal fees) arising from (a) your breach of these Terms, (b) your
          User Content, (c) your use of AI Output, or (d) your violation of any law or the rights of any third party — particularly anyone whose likeness or voice you have
          uploaded, described, or simulated without consent.
        </p>
      </LegalSection>

      <LegalSection number={14} title="Governing law & dispute resolution" testId="terms-sec-law">
        <p>
          These Terms are governed by the laws of India, without regard to its conflict-of-laws rules. Any dispute will first be attempted to be resolved through good-faith
          negotiation. Failing that, the courts of competent jurisdiction in Hyderabad, Telangana, India shall have exclusive jurisdiction, except where mandatory consumer law
          gives you the right to bring proceedings in your country of residence.
        </p>
      </LegalSection>

      <LegalSection number={15} title="Changes to these Terms" testId="terms-sec-changes">
        <p>
          We will revise these Terms as the product evolves. Material changes will be announced at the top of this page with an updated date and, where appropriate, an in-app
          notice. Your continued use of the service after a change constitutes acceptance of the revised Terms.
        </p>
      </LegalSection>

      <LegalSection number={16} title="Contact" testId="terms-sec-contact">
        <p>
          Questions about these Terms: <a className="text-amber underline" href="mailto:admin@aiclonechats.com">admin@aiclonechats.com</a>. Founder:{" "}
          <a className="text-amber underline" href="mailto:krajapraveen@aiclonechats.com">krajapraveen@aiclonechats.com</a>. For security-only reports, see our{" "}
          <a className="text-amber underline" href="/security">Security</a> page.
        </p>
      </LegalSection>
    </LegalPage>
  );
}
