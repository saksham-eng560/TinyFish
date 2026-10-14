import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, Section } from "@/components/legal-page";

export const metadata: Metadata = {
  title: "Terms",
  description: "The terms for using HireFlow and its demo mode.",
};

export default function TermsPage() {
  return (
    <LegalPage title="Terms" updated="5 October 2026"
      intro={<>HireFlow helps you find internships and apply to them. Using it, or its demo, means agreeing to these
        terms. They&apos;re short on purpose.</>}>
      <Section title="You're the applicant">
        <ul>
          <li>Every application HireFlow sends goes out under your name, so it&apos;s yours: you choose the jobs (nothing is sent for a job you didn&apos;t keep), the limits, and whether anything is sent without your click.</li>
          <li>The information you give HireFlow must be true. It never adds skills, experience or degrees to your resume and never guesses eligibility answers, but you are responsible for checking what&apos;s sent.</li>
          <li>AI-written text (cover letters, open-ended answers) is marked as such and can be wrong: read it before it&apos;s sent, or keep automatic sending off.</li>
        </ul>
      </Section>
      <Section title="Other sites have their own rules">
        <ul>
          <li>Job sites and employers&apos; forms have their own terms. Some (LinkedIn, Internshala, Indeed, Glassdoor) don&apos;t allow automated access: HireFlow leaves them off unless you turn them on after reading the risk, and using them may get your account there restricted.</li>
          <li>HireFlow never tries to get around a captcha or bot check: when a site asks for a person, it stops and waits.</li>
        </ul>
      </Section>
      <Section title="Acceptable use">
        <ul>
          <li>Use HireFlow for your own job search, honestly.</li>
          <li>Don&apos;t use it to send false information, to apply for someone else without their consent, to spam employers, or to collect other people&apos;s data.</li>
          <li>Don&apos;t try to break, overload or get around the limits of a HireFlow server you don&apos;t run.</li>
        </ul>
      </Section>
      <Section title="The demo">
        <p>The demo is for trying HireFlow out. Nothing is really sent: applications go to a bundled sample careers site,
          job-site logins and Gmail are off, and all data is reset every night. It may be slow, limited or unavailable at
          any time.</p>
      </Section>
      <Section title="No warranty">
        <p>HireFlow is open-source software under the MIT license, provided &ldquo;as is&rdquo;, without warranty of any kind.
          Nobody guarantees that it finds a job, that an application arrives, or that a site accepts it, and its authors
          aren&apos;t liable for what happens when you use it. If you host it yourself, you are responsible for your server and
          your users&apos; data.</p>
      </Section>
      <Section title="Changes">
        <p>These terms may change as HireFlow does; the date above shows the last change. See also the{" "}
          <Link href="/privacy">privacy policy</Link> and <Link href="/responsible-use">responsible use</Link>.</p>
      </Section>
    </LegalPage>
  );
}
