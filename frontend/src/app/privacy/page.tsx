import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, Section } from "@/components/legal-page";

export const metadata: Metadata = {
  title: "Privacy",
  description: "What HireFlow stores about you, where it goes, and how to download or delete it.",
};

export default function PrivacyPage() {
  return (
    <LegalPage title="Privacy" updated="5 October 2026"
      intro={<>HireFlow is open-source software that runs on one server: yours, if you host it, or ours for the public
        demo. This page says what it keeps about you, who else sees any of it, and how to take it back.</>}>
      <Section title="What HireFlow stores">
        <ul>
          <li><strong>Your account:</strong> your name, e-mail address and a hashed password (bcrypt, never the password itself).</li>
          <li><strong>Your resume:</strong> the file you upload and the text read from it, plus the tailored copies made for each job.</li>
          <li><strong>Your profile and answers:</strong> phone, city, profile links (LinkedIn, GitHub…), and the answers you save for application forms (work authorization, start date…).</li>
          <li><strong>Your search:</strong> the roles, locations and sources you pick, and your settings.</li>
          <li><strong>Your applications:</strong> the jobs found for you, your keep / skip decisions, cover letters, form answers, a screenshot of each filled form, and the history of every step.</li>
          <li><strong>If you connect Google:</strong> recruiter e-mails about your applications, and interview events on your calendar.</li>
          <li><strong>If you sync a job-site login</strong> with the browser extension (LinkedIn, Internshala): that site&apos;s session cookies, so HireFlow can act as you there.</li>
        </ul>
      </Section>
      <Section title="How it's protected">
        <ul>
          <li>Google tokens, synced session cookies and saved site passwords are encrypted at rest (AES-256-GCM).</li>
          <li>The dashboard is served over HTTPS, your session cookie is HTTP-only and secure, and other sites can&apos;t make changes with it.</li>
          <li>Files are only ever served to their owner.</li>
          <li>E-mail addresses, phone numbers and tokens are masked in the server&apos;s logs.</li>
        </ul>
      </Section>
      <Section title="Who else sees it">
        <ul>
          <li><strong>The AI provider the server is set up with</strong> (Anthropic, OpenAI, or a local Ollama model that never leaves the machine): your resume text, job descriptions and form questions are sent to it to score jobs, tailor your resume and draft answers. Their own privacy terms apply. With no AI set up, nothing is sent and rule-based versions are used.</li>
          <li><strong>Employers you apply to</strong> receive what their form asks for: your resume, answers and cover letter. Only for jobs you kept.</li>
          <li><strong>Google</strong>, if you connect it: HireFlow reads e-mails about your applications, drafts replies for you (sent only when you click Send), adds interviews to your calendar, and can e-mail you your own updates.</li>
          <li>Nobody else. Nothing is sold, and there are no advertising trackers.</li>
        </ul>
      </Section>
      <Section title="How long it's kept">
        <p>For as long as you have an account. Closed applications (rejected, withdrawn, skipped) and their files are
          removed after two years by default, and notifications after 90 days. In demo mode, everything is reset
          every night: please don&apos;t upload a resume you wouldn&apos;t want on a demo server.</p>
      </Section>
      <Section title="Your choices">
        <ul>
          <li><strong>Download my data</strong> (Settings › Privacy): everything about you as JSON, plus every file in a .zip.</li>
          <li><strong>Delete my account</strong> (Settings › Privacy): every row and file is deleted, and Google access is revoked. It can&apos;t be undone.</li>
          <li>Disconnect Google, LinkedIn or Internshala at any time in Settings › Integrations.</li>
          <li>Change or remove any answer, link or setting in Settings.</li>
        </ul>
      </Section>
      <Section title="Questions">
        <p>Open an issue on <a href="https://github.com/saksham-eng560/HireFlow/issues">GitHub</a>. See also the{" "}
          <Link href="/terms">terms</Link> and <Link href="/responsible-use">responsible use</Link>.</p>
      </Section>
    </LegalPage>
  );
}
