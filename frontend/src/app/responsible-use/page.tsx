import type { Metadata } from "next";
import Link from "next/link";
import { LegalPage, Section } from "@/components/legal-page";

export const metadata: Metadata = {
  title: "Responsible use",
  description: "What HireFlow does, what it will never do, and the risks of automating job applications.",
};

export default function ResponsibleUsePage() {
  return (
    <LegalPage title="Responsible use" updated="5 October 2026"
      intro={<>Automating applications can save you hours, or burn your reputation with recruiters. HireFlow is built
        to do the first: here is exactly what it does, what it won&apos;t do, and how to use it well.</>}>
      <Section title="What HireFlow does">
        <ul>
          <li>Searches job boards and company career pages for internships that fit your roles, locations and season.</li>
          <li>Ranks them against your resume and puts them in Swipe Review: you keep or skip each one.</li>
          <li>For a job you keep: tailors your resume (reordering and rewording what&apos;s already there), writes a cover letter if you want one, and fills in the application form in a real browser.</li>
          <li>Sends it when you click Submit, or on its own if you turned that on, after a 10-minute window in which you can stop it.</li>
          <li>Watches your inbox for replies and puts interviews on your calendar, if you connect Google.</li>
        </ul>
      </Section>
      <Section title="What it will never do">
        <ul>
          <li><strong>Make things up.</strong> A tailored resume can only reorder and reword your own facts. Every one is checked against your original, and if anything new slips in, your original is sent instead.</li>
          <li><strong>Guess eligibility.</strong> Visa, work authorization, age, criminal record, background checks, ID numbers: only your own saved answer is used. Without one, the application waits for you.</li>
          <li><strong>Apply without your say-so.</strong> Nothing is sent for a job you didn&apos;t keep, and new accounts start with automatic sending off.</li>
          <li><strong>Apply twice</strong> to the same job, or to the same role at the same company.</li>
          <li><strong>Flood anyone.</strong> At most your daily limit (25 a day at the very most), and at most 3 applications to one company in a week.</li>
          <li><strong>Get around a site&apos;s defences.</strong> When a site asks for a captcha or says &ldquo;slow down&rdquo;, HireFlow stops and backs off, longer each time.</li>
          <li><strong>Use sites that forbid automation</strong> (LinkedIn, Internshala, Indeed, Glassdoor) unless you&apos;ve turned them on yourself, after reading the risk.</li>
        </ul>
      </Section>
      <Section title="The risks">
        <ul>
          <li><strong>Your accounts:</strong> sites that forbid automation can restrict or close an account they think is automated.</li>
          <li><strong>Your reputation:</strong> many low-fit applications to one company get noticed. Keep only jobs you&apos;d really take.</li>
          <li><strong>Mistakes:</strong> AI-written answers and form filling can be wrong. Every AI-written answer is marked, and you can see a screenshot of each filled form.</li>
        </ul>
      </Section>
      <Section title="Using it well">
        <ul>
          <li>Start with Swipe Review and automatic sending off, and check your first few filled forms in Ready to submit.</li>
          <li>Try Dry run (Settings): forms are filled and screenshotted, but nothing is sent.</li>
          <li>Keep your daily limit low, and your saved answers truthful and up to date.</li>
          <li>Use <strong>Pause</strong> (top right of the dashboard) to stop scans, preparation and sending at once.</li>
        </ul>
      </Section>
      <Section title="More">
        <p>See the <Link href="/privacy">privacy policy</Link> and the <Link href="/terms">terms</Link>. Found HireFlow doing
          something it shouldn&apos;t? Please open an issue on <a href="https://github.com/saksham-eng560/HireFlow/issues">GitHub</a>.</p>
      </Section>
    </LegalPage>
  );
}
