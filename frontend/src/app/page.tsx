import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import {
  ArrowRight, ArrowUpRight, BarChart3, CalendarCheck, Check, ClipboardCheck, Copy, Eye, FileText, FormInput, Hand,
  Layers, MailCheck, PauseCircle, Search, Send, ShieldCheck, Timer, X,
} from "lucide-react";
import { BrushHeadline, Logo, TagPile, TunnelGrid } from "@/components/brand";
import { ArchitectureDiagram, STACK } from "@/components/landing/architecture";
import { Faq, FAQS } from "@/components/landing/faq";
import { GithubMark } from "@/components/landing/github-mark";
import { VideoSlot } from "@/components/landing/video-slot";
import { LEGAL_LINKS } from "@/components/legal-page";
import { ThemeToggle } from "@/components/theme-toggle";
import { buttonVariants } from "@/components/ui/button";
import { AUTHOR, GITHUB_URL, OPEN_GRAPH, SITE_DESCRIPTION, SITE_TITLE, SITE_URL } from "@/lib/site";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: { absolute: SITE_TITLE },
  alternates: { canonical: "/" },
  openGraph: { ...OPEN_GRAPH, url: "/" },
};

const NAV = [
  { href: "#how", label: "How it works" },
  { href: "#features", label: "Features" },
  { href: "#safety", label: "Safety" },
  { href: "#faq", label: "FAQ" },
];

const STEPS = [
  { n: "01", icon: Search, title: "Find",
    text: "The agent scans ATS job boards, curated internship lists and careers pages every few hours, removes duplicates and ranks every posting against your resume." },
  { n: "02", icon: Hand, title: "Swipe",
    text: "Every job that passed your filters lands in a deck with its match score. Keep it or skip it in a second; nothing is skipped behind your back." },
  { n: "03", icon: Send, title: "Apply & track",
    text: "Kept jobs get a truthful tailored resume, a cover letter and a filled form. Replies and interviews are tracked from Gmail." },
];

const FEATURES = [
  { icon: Layers, title: "Swipe Review", text: "A deck of every matching internship, best first. Drag, tap or use ← → to keep or skip, undo any swipe, or keep everything above a score." },
  { icon: FileText, title: "Resume tailoring", text: "Each kept job gets a resume reordered for it and a cover letter, checked line by line against your original." },
  { icon: FormInput, title: "Form filling across ATSs", text: "Greenhouse, Lever, Ashby, Workday and most careers pages, filled in a real browser with your saved answers." },
  { icon: MailCheck, title: "Gmail tracking", text: "Replies are read and sorted (acknowledged, interview, rejection, offer) and each application's status follows." },
  { icon: CalendarCheck, title: "Interview prep", text: "Invites go on your calendar with prep notes built from the job and your own projects." },
  { icon: BarChart3, title: "Analytics", text: "Your pipeline by status, daily activity, which platforms get responses and the keywords that get callbacks." },
];

const SAFETY = [
  { icon: ClipboardCheck, title: "You pick every job", text: "Only jobs you keep are prepared. New accounts start with “Submit automatically” off." },
  { icon: Timer, title: "10-minute undo", text: "Each application waits in “Sending soon”, so you can cancel it before it goes." },
  { icon: Copy, title: "Caps and no duplicates", text: "At most 25 a day and 3 per company a week, enforced by the server; never the same job twice." },
  { icon: ShieldCheck, title: "Eligibility never guessed", text: "Visa, work authorization and background questions wait for your own answer." },
  { icon: FileText, title: "Never invents experience", text: "Anything not on your resume (skills, titles, dates, numbers) is reverted." },
  { icon: Eye, title: "Proof of what was sent", text: "A screenshot before every submit, AI-written answers labelled and editable, and a dry-run mode." },
  { icon: PauseCircle, title: "Pause everything", text: "One switch stops scans, preparation and sending at once." },
  { icon: Hand, title: "Respects site rules", text: "Sites that forbid automation stay off unless you opt in; rate limits and back-off everywhere." },
];

const SAMPLE_CARD_TAGS = ["Python", "FastAPI", "PostgreSQL", "Summer 2027"];

export default function Landing() {
  const faqJsonLd = {
    "@context": "https://schema.org",
    "@graph": [
      { "@type": "SoftwareApplication", name: "HireFlow", applicationCategory: "BusinessApplication", operatingSystem: "Web",
        description: SITE_DESCRIPTION, url: SITE_URL, offers: { "@type": "Offer", price: "0", priceCurrency: "USD" } },
      { "@type": "FAQPage", mainEntity: FAQS.map((f) => ({ "@type": "Question", name: f.q, acceptedAnswer: { "@type": "Answer", text: f.a } })) },
    ],
  };
  return (
    <main className="min-h-screen bg-background text-foreground">
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(faqJsonLd).replace(/</g, "\\u003c") }} />
      <a href="#how" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-primary focus:px-4 focus:py-2 focus:text-primary-foreground">
        Skip to content
      </a>
      <div className="mx-auto max-w-[1440px] border-x border-line/70">
        {/* ---------------------------------------------------------------- header */}
        <header className="flex h-20 items-center justify-between gap-3 border-b border-line/70 px-5 sm:px-10">
          <Logo />
          <nav aria-label="Main" className="flex items-center gap-1 sm:gap-6">
            {NAV.map((n) => (
              <a key={n.href} href={n.href} className="label-caps hidden text-[12px] text-foreground/85 hover:text-foreground lg:inline">{n.label}</a>
            ))}
            <a href={GITHUB_URL} className="hidden h-9 w-9 items-center justify-center rounded-lg text-foreground/80 hover:bg-accent hover:text-foreground sm:inline-flex"
              aria-label="HireFlow on GitHub"><GithubMark className="h-5 w-5" /></a>
            <Link href="/login" className="label-caps hidden text-[12px] text-foreground/85 hover:text-foreground sm:inline">Sign in</Link>
            <ThemeToggle />
            <Link href="/register" className={buttonVariants({ size: "default" })}>Get started <ArrowUpRight /></Link>
          </nav>
        </header>

        {/* ---------------------------------------------------------------- hero */}
        <section className="grid border-b border-line/70 lg:grid-cols-[1fr_minmax(0,460px)]">
          <div className="relative flex flex-col overflow-hidden px-5 pb-16 pt-14 sm:px-10 md:min-h-[600px] md:pb-36 lg:px-20 lg:pt-24">
            <p className="label-caps mb-8 flex items-center gap-2 text-muted-foreground">
              <span className="h-2 w-2 animate-pulse-dot rounded-full bg-primary motion-reduce:animate-none" /> Open source · self-hostable
            </p>
            <BrushHeadline lines={["Swipe right.", "We apply.", "Internships", "on autopilot"]} />
            <p className="mt-8 max-w-xl text-lg leading-relaxed text-foreground/80">
              HireFlow finds internships, lets you keep or skip each one with a swipe, then tailors your resume, fills the
              application form and tracks the replies — only for the jobs you keep, and with you in control.
            </p>
            <div className="mt-10 flex flex-wrap items-center gap-4">
              <Link href="/register" className={buttonVariants({ size: "xl" })}>Get started <ArrowRight /></Link>
              <a href={GITHUB_URL} className={buttonVariants({ size: "xl", variant: "outline" })}>
                <GithubMark className="h-5 w-5" /> View on GitHub
              </a>
            </div>
            <TagPile className="absolute bottom-0 right-4 hidden origin-bottom-right scale-[0.8] md:block xl:scale-100" />
          </div>
          <div className="relative hidden min-h-[600px] overflow-hidden border-l border-line/70 lg:block">
            <TunnelGrid />
          </div>
        </section>

        {/* ---------------------------------------------------------------- product shot */}
        <section aria-label="Product screenshot" className="border-b border-line/70 bg-secondary/40 px-5 py-12 sm:px-10 lg:px-20">
          <div className="mx-auto max-w-5xl overflow-hidden rounded-2xl border bg-card shadow-lg">
            <div className="flex items-center gap-1.5 border-b bg-muted px-4 py-3" aria-hidden>
              <span className="h-2.5 w-2.5 rounded-full bg-line" /><span className="h-2.5 w-2.5 rounded-full bg-line" />
              <span className="h-2.5 w-2.5 rounded-full bg-line" />
              <span className="ml-3 truncate rounded-md bg-background px-3 py-0.5 text-xs text-muted-foreground">hireflow · Swipe Review</span>
            </div>
            <Image src="/screenshots/swipe-review.png" alt="Swipe Review: a job card with its match score, skills you have and skills it wants, next to bulk-keep controls"
              width={1440} height={900} priority sizes="(min-width: 1100px) 1024px, 100vw" className="h-auto w-full" />
          </div>
        </section>

        {/* ---------------------------------------------------------------- how it works */}
        <section id="how" className="scroll-mt-4 border-b border-line/70">
          <div className="flex flex-col justify-between gap-4 px-5 py-14 sm:px-10 md:flex-row md:items-end">
            <div>
              <p className="label-caps text-primary">How it works</p>
              <h2 className="display mt-4 text-4xl sm:text-5xl">Find → Swipe → Apply</h2>
            </div>
            <p className="max-w-md text-muted-foreground">Three steps, and only one of them needs you: the swipe.</p>
          </div>
          <ol className="grid border-t border-line/70 md:grid-cols-3">
            {STEPS.map((s, i) => (
              <li key={s.n} className={cn("p-8 sm:p-10", i > 0 && "border-t border-line/70 md:border-l md:border-t-0")}>
                <div className="flex items-center justify-between">
                  <p className="font-mono text-5xl font-bold tracking-tight text-primary">{s.n}</p>
                  <s.icon className="h-7 w-7 text-primary" aria-hidden />
                </div>
                <h3 className="label-caps mt-8 text-sm font-bold text-foreground">{s.title}</h3>
                <p className="mt-3 leading-relaxed text-muted-foreground">{s.text}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* ---------------------------------------------------------------- video */}
        <section id="video" aria-labelledby="video-title" className="scroll-mt-4 border-b border-line/70 px-5 py-14 sm:px-10 lg:px-20">
          <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
            <div>
              <p className="label-caps text-primary">Watch</p>
              <h2 id="video-title" className="display mt-4 text-4xl sm:text-5xl">HireFlow in 60 seconds</h2>
            </div>
            <p className="max-w-md text-muted-foreground">From a fresh sign-up to the first deck, a kept job and a filled form.</p>
          </div>
          <VideoSlot />
        </section>

        {/* ---------------------------------------------------------------- features */}
        <section id="features" aria-labelledby="features-title" className="scroll-mt-4 border-b border-line/70">
          <div className="px-5 py-14 sm:px-10">
            <p className="label-caps text-primary">Features</p>
            <h2 id="features-title" className="display mt-4 text-4xl sm:text-5xl">Everything after “I want this one”</h2>
          </div>
          <ul className="grid gap-px border-t border-line/70 bg-line/70 sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map((f) => (
              <li key={f.title} className="bg-background p-8 transition-colors hover:bg-card sm:p-10">
                <f.icon className="h-6 w-6 text-primary" aria-hidden />
                <h3 className="mt-6 text-lg font-semibold">{f.title}</h3>
                <p className="mt-2 leading-relaxed text-muted-foreground">{f.text}</p>
              </li>
            ))}
          </ul>
        </section>

        {/* ---------------------------------------------------------------- swipe preview */}
        <section aria-labelledby="swipe-title" className="grid border-b border-line/70 lg:grid-cols-2">
          <div className="px-5 py-16 sm:px-10 lg:py-24">
            <p className="label-caps text-primary">Swipe Review</p>
            <h2 id="swipe-title" className="display mt-4 text-4xl sm:text-5xl">Keep. Skip.<br />Repeat.</h2>
            <p className="mt-6 max-w-lg text-lg leading-relaxed text-foreground/80">
              Drag right to keep and left to skip, swipe on your phone, or use the arrow keys. Each card shows why it
              matched, what you&apos;re missing and whether the company sponsors visas.
            </p>
            <ul className="mt-8 space-y-3">
              {["Match score with the skills you have and the ones they want", "Location, term and visa sponsorship at a glance",
                "Undo any swipe until preparation starts"].map((t) => (
                <li key={t} className="flex items-center gap-3"><Check className="h-4 w-4 shrink-0 text-primary" aria-hidden /> {t}</li>
              ))}
            </ul>
          </div>
          <div className="relative flex min-h-[460px] items-center justify-center overflow-hidden border-t border-line/70 bg-card lg:border-l lg:border-t-0" aria-hidden>
            <TunnelGrid className="absolute inset-0 opacity-40" animated={false} />
            <div className="relative h-[330px] w-[290px]">
              <div className="absolute inset-0 translate-x-5 translate-y-3 rotate-6 rounded-xl border border-line bg-background" />
              <div className="absolute inset-0 -translate-x-3 -rotate-3 rounded-xl border border-line bg-background" />
              <div className="absolute inset-0 flex flex-col rounded-xl border bg-card p-6 shadow-sm">
                <div className="flex items-start justify-between">
                  <span className="label-caps text-muted-foreground">Bengaluru · On-site</span>
                  <span className="font-mono text-3xl font-bold tracking-tight text-primary">86</span>
                </div>
                <p className="mt-6 font-display text-xl font-extrabold uppercase leading-tight">Backend Engineering Intern</p>
                <p className="mt-1 text-muted-foreground">Acme Robotics · fictional</p>
                <div className="mt-5 flex flex-wrap gap-1.5">
                  {SAMPLE_CARD_TAGS.map((t) => <span key={t} className="rounded-full border border-foreground/40 px-2.5 py-0.5 text-xs">{t}</span>)}
                </div>
                <div className="mt-auto grid grid-cols-2 gap-2">
                  <span className="flex h-11 items-center justify-center gap-1 rounded-lg border border-foreground/50 text-xs font-bold uppercase tracking-wider"><X className="h-4 w-4" /> Skip</span>
                  <span className="flex h-11 items-center justify-center gap-1 rounded-lg bg-primary text-xs font-bold uppercase tracking-wider text-primary-foreground"><Check className="h-4 w-4" /> Keep</span>
                </div>
              </div>
              <span className="absolute -left-10 top-[22%] -rotate-12 rounded-lg border-2 border-primary bg-background px-3 py-1 font-display text-lg font-extrabold text-primary">KEEP</span>
            </div>
          </div>
        </section>

        {/* ---------------------------------------------------------------- safe by design */}
        <section id="safety" aria-labelledby="safety-title" className="scroll-mt-4 border-b border-line/70">
          <div className="flex flex-col justify-between gap-4 px-5 py-14 sm:px-10 md:flex-row md:items-end">
            <div>
              <p className="label-caps text-primary">Safe by design</p>
              <h2 id="safety-title" className="display mt-4 text-4xl sm:text-5xl">An agent with brakes</h2>
            </div>
            <p className="max-w-md text-muted-foreground">
              Applying for you is only useful if it never embarrasses you. These rules are enforced by the server, not just the UI.{" "}
              <Link href="/responsible-use" className="text-primary underline underline-offset-4">Responsible use</Link>
            </p>
          </div>
          <ul className="grid gap-px border-t border-line/70 bg-line/70 sm:grid-cols-2 lg:grid-cols-4">
            {SAFETY.map((r) => (
              <li key={r.title} className="bg-background p-8">
                <r.icon className="h-6 w-6 text-primary" aria-hidden />
                <h3 className="mt-5 font-semibold">{r.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{r.text}</p>
              </li>
            ))}
          </ul>
        </section>

        {/* ---------------------------------------------------------------- architecture */}
        <section id="architecture" aria-labelledby="architecture-title" className="scroll-mt-4 grid border-b border-line/70 lg:grid-cols-[minmax(0,420px)_1fr]">
          <div className="px-5 py-14 sm:px-10">
            <p className="label-caps text-primary">Under the hood</p>
            <h2 id="architecture-title" className="display mt-4 text-4xl sm:text-5xl">How it&apos;s built</h2>
            <p className="mt-6 leading-relaxed text-muted-foreground">
              A FastAPI service and Celery workers share PostgreSQL and Redis. Slow work (scanning, AI calls, a browser
              filling a form) runs on queues, so the dashboard stays fast and a crashed task is retried.
            </p>
            <ul className="mt-8 flex flex-wrap gap-2" aria-label="Tech stack">
              {STACK.map((t) => <li key={t} className="rounded-full border bg-card px-3 py-1 text-sm">{t}</li>)}
            </ul>
            <a href={`${GITHUB_URL}/blob/main/docs/ARCHITECTURE.md`} className="mt-8 inline-flex items-center gap-1 font-semibold text-primary underline-offset-4 hover:underline">
              Read the architecture notes <ArrowUpRight className="h-4 w-4" />
            </a>
          </div>
          <div className="border-t border-line/70 px-5 py-10 sm:px-10 lg:border-l lg:border-t-0">
            <ArchitectureDiagram />
          </div>
        </section>

        {/* ---------------------------------------------------------------- FAQ */}
        <section id="faq" aria-labelledby="faq-title" className="scroll-mt-4 grid gap-10 border-b border-line/70 px-5 py-14 sm:px-10 lg:grid-cols-[minmax(0,360px)_1fr]">
          <div>
            <p className="label-caps text-primary">FAQ</p>
            <h2 id="faq-title" className="display mt-4 text-4xl sm:text-5xl">Questions</h2>
          </div>
          <Faq />
        </section>

        {/* ---------------------------------------------------------------- CTA */}
        <section className="flex flex-col items-start justify-between gap-8 px-5 py-16 sm:px-10 md:flex-row md:items-center">
          <h2 className="display text-4xl sm:text-6xl">Your next<br /><span className="text-primary">internship</span> is<br />one swipe away</h2>
          <div className="flex flex-wrap gap-4">
            <Link href="/register" className={buttonVariants({ size: "xl" })}>Create your agent <ArrowUpRight /></Link>
          </div>
        </section>

        {/* ---------------------------------------------------------------- footer */}
        <footer className="grid gap-8 border-t border-line/70 px-5 py-10 text-sm sm:grid-cols-2 sm:px-10 lg:grid-cols-[1.4fr_1fr_1fr_1fr]">
          <div>
            <Logo className="text-base text-foreground" />
            <p className="mt-3 max-w-xs text-muted-foreground">Internship applications on autopilot, with you in control. MIT licensed and self-hostable.</p>
          </div>
          <nav aria-label="Product">
            <p className="label-caps mb-3 text-muted-foreground">Product</p>
            <ul className="space-y-2">
              {NAV.map((n) => <li key={n.href}><a href={n.href} className="hover:text-primary hover:underline">{n.label}</a></li>)}
            </ul>
          </nav>
          <nav aria-label="Legal">
            <p className="label-caps mb-3 text-muted-foreground">Legal</p>
            <ul className="space-y-2">
              {LEGAL_LINKS.map((l) => <li key={l.href}><Link href={l.href} className="hover:text-primary hover:underline">{l.label}</Link></li>)}
            </ul>
          </nav>
          <nav aria-label="Project">
            <p className="label-caps mb-3 text-muted-foreground">Project</p>
            <ul className="space-y-2">
              <li><a href={GITHUB_URL} className="inline-flex items-center gap-1.5 hover:text-primary hover:underline"><GithubMark /> GitHub</a></li>
              <li><a href={`${GITHUB_URL}/blob/main/docs/ARCHITECTURE.md`} className="hover:text-primary hover:underline">Architecture</a></li>
              <li><a href={AUTHOR.url} className="hover:text-primary hover:underline">Built by {AUTHOR.name}</a></li>
            </ul>
          </nav>
        </footer>
      </div>
    </main>
  );
}
