import LegalPage from "./LegalPage";

export default function AcceptableUse() {
  return (
    <LegalPage title="Acceptable Use Policy" updated="May 11, 2026" testId="acceptable-use-page">
      <p className="text-base font-medium">
        aiclonechats.com is a tool for building <strong>original AI personas you own or have rights to create</strong>. This policy spells out what is and isn't allowed. Violations may result in immediate account suspension and removal of content without refund.
      </p>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">Allowed</h2>
        <ul className="list-disc pl-6 space-y-1">
          <li>An AI version of <strong>yourself</strong>, with your tone, opinions, and memories.</li>
          <li>An AI version of a <strong>person who has given you explicit written permission</strong> (and uploaded that permission, where requested).</li>
          <li>An <strong>original fictional persona</strong> — a character of your own creation.</li>
          <li>A <strong>style-inspired companion</strong> that does not name or impersonate a specific real person or copyrighted character.</li>
          <li>Educational, coaching, productivity, and entertainment use.</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">Not allowed</h2>
        <ul className="list-disc pl-6 space-y-1">
          <li>Impersonating a <strong>real person without their explicit permission</strong> (celebrities, politicians, friends, exes — anyone).</li>
          <li>Impersonating <strong>copyrighted or trademarked characters</strong> (cartoon, anime, movie, comic, game, or franchise characters).</li>
          <li>Impersonating <strong>brand voices, products, or trademarks</strong> you do not own.</li>
          <li>Sexual content involving minors. Period.</li>
          <li>Non-consensual sexual content, harassment, doxxing, stalking, or threats.</li>
          <li>Hate speech, incitement, or content that targets people based on protected characteristics.</li>
          <li>Instructions for illegal activity, drug synthesis, weapon manufacture, or terrorism.</li>
          <li>Piracy, software cracking, license bypass, or unauthorized distribution of copyrighted material.</li>
          <li>Fraud, scams, impersonation for financial or social-engineering gain.</li>
          <li>Self-harm or suicide encouragement.</li>
          <li>Medical, legal, or financial advice presented as professional counsel.</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">Content you upload</h2>
        <ul className="list-disc pl-6 space-y-1">
          <li>Upload only content <strong>you own</strong> or have explicit rights to use.</li>
          <li>Do not upload copyrighted images, audio, video, or text without a valid license.</li>
          <li>Do not upload someone else's photo or voice without their permission.</li>
          <li>Trademarks, logos, and brand assets you do not own are not permitted.</li>
        </ul>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">Enforcement</h2>
        <p>
          We use a combination of automated safety filters and human review. Public clone bios, names, catchphrases, blocked topics, and uploaded media are screened on creation. Violations trigger an admin audit log, content removal, and in serious cases, account suspension. Repeat offenders are banned.
        </p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">Report a violation</h2>
        <p>
          See content that violates this policy? Email{" "}
          <a href="mailto:admin@aiclonechats.com" className="text-amber underline">admin@aiclonechats.com</a>
          {" "}with the URL and a brief description. We investigate every report.
        </p>
      </section>

      <section>
        <h2 className="heading-display text-xl mt-6 mb-2">Counter-notice</h2>
        <p>
          If you believe your content was removed in error, reply to the removal email with details and any evidence of rights or consent. We'll review within 7 business days.
        </p>
      </section>
    </LegalPage>
  );
}
