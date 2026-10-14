import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { Logo } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";

export const LEGAL_LINKS = [
  { href: "/privacy", label: "Privacy" },
  { href: "/terms", label: "Terms" },
  { href: "/responsible-use", label: "Responsible use" },
];

/** Shared frame for /privacy, /terms and /responsible-use: plain, readable, linked to each other. */
export function LegalPage({ title, intro, updated, children }: {
  title: string;
  intro: React.ReactNode;
  updated: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b">
        <div className="mx-auto flex h-16 max-w-3xl items-center gap-3 px-4">
          <Logo />
          <div className="flex-1" />
          <Link href="/" className="inline-flex items-center gap-1.5 text-sm font-semibold text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-4 w-4" aria-hidden /> Home
          </Link>
          <ThemeToggle />
        </div>
      </header>
      <main className="mx-auto max-w-3xl px-4 py-10 sm:py-14">
        <p className="text-xs font-semibold uppercase tracking-wider text-primary">Last updated {updated}</p>
        <h1 className="display mt-3 text-4xl sm:text-5xl">{title}</h1>
        <div className="mt-4 text-base text-muted-foreground sm:text-lg">{intro}</div>
        <div className="legal mt-10 space-y-10">{children}</div>
      </main>
      <footer className="border-t">
        <nav aria-label="Policies" className="mx-auto flex max-w-3xl flex-wrap gap-x-6 gap-y-2 px-4 py-6 text-sm text-muted-foreground">
          {LEGAL_LINKS.map((l) => <Link key={l.href} href={l.href} className="hover:text-foreground hover:underline">{l.label}</Link>)}
          <a href="https://github.com/saksham-eng560/HireFlow" className="hover:text-foreground hover:underline">GitHub</a>
        </nav>
      </footer>
    </div>
  );
}

/** One section: a heading and its paragraphs / lists. */
export function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={title.replace(/\W+/g, "-").toLowerCase()}>
      <h2 id={title.replace(/\W+/g, "-").toLowerCase()} className="text-xl font-bold tracking-tight sm:text-2xl">{title}</h2>
      <div className="mt-3 space-y-3 text-[15px] leading-7 text-foreground/90 [&_a]:font-semibold [&_a]:text-primary [&_a:hover]:underline [&_li]:pl-1 [&_ul]:list-disc [&_ul]:space-y-1.5 [&_ul]:pl-5">
        {children}
      </div>
    </section>
  );
}
