import { ArrowDown, Bot, Calendar, Database, Globe, HardDrive, LayoutDashboard, ListTodo, Puzzle, Server, Sparkles, Timer } from "lucide-react";
import { cn } from "@/lib/utils";

type Box = { icon: typeof Server; title: string; text: string; strong?: boolean };

const LAYERS: { label: string; boxes: Box[]; link?: string }[] = [
  {
    label: "You",
    boxes: [
      { icon: LayoutDashboard, title: "Dashboard", text: "Next.js + React: onboarding, Swipe Review, tracking, analytics", strong: true },
      { icon: Puzzle, title: "Browser extension", text: "Optional: syncs your job-site sign-ins, only for sources you opt in to" },
    ],
    link: "HTTPS · same-origin /api proxy · httpOnly session cookie · live updates over WebSocket",
  },
  {
    label: "API",
    boxes: [
      { icon: Server, title: "FastAPI", text: "Auth, the review deck, settings, guardrails, rate limits", strong: true },
      { icon: Database, title: "PostgreSQL + pgvector", text: "Jobs, applications, history, resume and job embeddings" },
      { icon: HardDrive, title: "File storage", text: "Resumes and form screenshots, on disk or S3 / R2" },
    ],
    link: "Tasks on Redis queues (a crashed task is retried; submissions are claimed atomically)",
  },
  {
    label: "Workers",
    boxes: [
      { icon: Timer, title: "Celery beat", text: "Scans every few hours, the held-send sweep, nightly jobs" },
      { icon: ListTodo, title: "Scan + prepare", text: "Fetch, dedupe, match, then tailor and write answers", strong: true },
      { icon: Bot, title: "Browser worker", text: "Playwright fills each form, screenshots it, then submits" },
    ],
    link: "Outbound, rate-limited per site, with back-off",
  },
  {
    label: "Outside",
    boxes: [
      { icon: Globe, title: "ATS + careers pages", text: "Greenhouse, Lever, Ashby, Workday and any careers page" },
      { icon: Sparkles, title: "LLM providers", text: "Anthropic → OpenAI → local Ollama, whichever is set up" },
      { icon: Calendar, title: "Gmail + Calendar", text: "Replies update each application; interviews are scheduled" },
    ],
  },
];

export const STACK = ["Next.js", "React", "TypeScript", "Tailwind CSS", "FastAPI", "SQLAlchemy", "PostgreSQL", "pgvector",
  "Redis", "Celery", "Playwright", "Docker", "GitHub Actions"];

/** How the pieces fit, as plain HTML so it reads on a phone and with a screen reader. */
export function ArchitectureDiagram({ className }: { className?: string }) {
  return (
    <figure className={cn("space-y-2", className)} aria-label="HireFlow architecture">
      {LAYERS.map((layer) => (
        <div key={layer.label}>
          <div className="grid gap-3 rounded-2xl border bg-card p-3 sm:grid-cols-[88px_1fr] sm:items-stretch sm:p-4">
            <p className="label-caps self-center text-primary">{layer.label}</p>
            <ul className={cn("grid gap-3", layer.boxes.length === 2 ? "sm:grid-cols-2" : "sm:grid-cols-3")}>
              {layer.boxes.map((box) => (
                <li key={box.title} className={cn("flex gap-3 rounded-xl border p-3",
                  box.strong ? "border-primary/40 bg-secondary" : "bg-background")}>
                  <box.icon className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
                  <span>
                    <span className="block text-sm font-semibold">{box.title}</span>
                    <span className="block text-[13px] leading-snug text-muted-foreground">{box.text}</span>
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {layer.link && (
            <p className="flex items-center justify-center gap-2 py-2 text-center text-xs text-muted-foreground">
              <ArrowDown className="h-4 w-4 shrink-0 text-primary" aria-hidden /> {layer.link}
            </p>
          )}
        </div>
      ))}
    </figure>
  );
}
