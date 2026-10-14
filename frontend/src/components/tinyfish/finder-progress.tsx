"use client";

import { AlertTriangle, Bot, Check, CircleSlash, Clock, Download, Globe, Loader2, Search, Square, type LucideIcon } from "lucide-react";
import { AgentLive } from "@/components/tinyfish/agent-live";
import { AnimatedNumber } from "@/components/motion";
import { ScanBar } from "@/components/scan-progress";
import { Button } from "@/components/ui/button";
import type { FinderStageStatus, FinderState } from "@/lib/types";
import { cn, plural } from "@/lib/utils";

const PHASES: Record<string, string> = {
  searching: "Searching the web", reading: "Reading careers pages", agent: "Agent browsing", ranking: "Ranking", done: "Done",
};

const ENDPOINTS: { key: "search" | "fetch" | "agent"; label: string; icon: LucideIcon; blurb: string }[] = [
  { key: "search", label: "Search", icon: Search, blurb: "Finds boards and careers pages" },
  { key: "fetch", label: "Fetch", icon: Download, blurb: "Reads the pages as text" },
  { key: "agent", label: "Agent", icon: Bot, blurb: "Browses JavaScript-heavy pages" },
];

function StageIcon({ status }: { status: FinderStageStatus | undefined }) {
  switch (status) {
    case "running": return <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" aria-label="running" />;
    case "done": return <Check className="h-3.5 w-3.5 text-success" aria-label="done" />;
    case "failed": return <AlertTriangle className="h-3.5 w-3.5 text-warning" aria-label="failed" />;
    case "skipped": return <CircleSlash className="h-3.5 w-3.5 text-muted-foreground" aria-label="skipped" />;
    default: return <Clock className="h-3.5 w-3.5 text-muted-foreground" aria-label="waiting" />;
  }
}

function EndpointCard({ spec, count, stat, state }: {
  spec: (typeof ENDPOINTS)[number]; count: number; stat: string; state: FinderState;
}) {
  const stage = state.stages.find((s) => s.name === spec.key);
  const used = count > 0;
  return (
    <li className={cn("rounded-lg border p-4 transition-colors", used ? "border-primary/60 bg-secondary" : "border-line/70")}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-semibold">
          <spec.icon className={cn("h-4 w-4", used ? "text-primary" : "text-muted-foreground")} aria-hidden /> {spec.label}
        </span>
        <StageIcon status={stage?.status} />
      </div>
      <p className="mt-3 font-mono text-2xl font-bold leading-none tabular-nums"><AnimatedNumber value={count} duration={0.4} /></p>
      <p className="mt-1 text-xs text-muted-foreground">{stat}</p>
      <p className="mt-2 truncate text-xs text-muted-foreground" title={stage?.detail || spec.blurb}>
        {stage?.detail || (stage?.status === "skipped" ? "Skipped" : spec.blurb)}
      </p>
    </li>
  );
}

/** Live progress of a Finder search: bar, one card per TinyFish endpoint, and the Agent's live view. */
export function FinderProgress({ state, onStop, stopping }: { state: FinderState; onStop: () => void; stopping: boolean }) {
  const running = state.status === "running";
  const { usage } = state;
  const used = ENDPOINTS.filter((e) => state.endpoints_used.includes(e.key)).length;
  const counts = { search: usage.search_calls, fetch: usage.fetch_urls, agent: usage.agent_runs };
  const stats = {
    search: plural(usage.search_calls, "search", "searches"),
    fetch: `${plural(usage.fetch_urls, "URL")} read`,
    agent: `${plural(usage.agent_runs, "run")} · ${plural(usage.agent_steps, "step")}`,
  };
  const headline = running ? PHASES[state.phase] ?? "Working" : state.status === "completed" ? "Search finished"
    : state.status === "cancelled" ? "Search stopped" : "Search failed";

  return (
    <section aria-label="Finder progress" className="rounded-xl border bg-card p-5 shadow-sm sm:p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="label-caps flex items-center gap-2 text-[11px] text-primary">
            {running ? <span className="h-2 w-2 animate-pulse-dot bg-primary" aria-hidden /> : <Globe className="h-3.5 w-3.5" aria-hidden />} {headline}
            {!running && state.duration_seconds != null ? ` · ${Math.round(state.duration_seconds)}s` : ""}
          </p>
          <p className="mt-2 text-sm text-muted-foreground" aria-live="polite">{state.error || state.message}</p>
        </div>
        {running && (
          <Button variant="outline" size="sm" onClick={onStop} loading={stopping}>
            {!stopping && <Square className="fill-current" />} {stopping ? "Stopping" : "Stop"}
          </Button>
        )}
      </div>

      <ScanBar percent={running ? state.percent : 100} active={running} label="Finder progress" className="mt-4" />

      <ul className="mt-5 grid gap-3 sm:grid-cols-3" aria-label="TinyFish endpoints">
        {ENDPOINTS.map((spec) => <EndpointCard key={spec.key} spec={spec} count={counts[spec.key]} stat={stats[spec.key]} state={state} />)}
      </ul>
      <p className="mt-3 text-xs text-muted-foreground" aria-live="polite">
        <span className="font-semibold text-foreground">{used} of 3</span> TinyFish endpoints used
        {usage.errors > 0 && <> · {plural(usage.errors, "call")} failed</>}
      </p>

      <AgentLive agent={state.agent} />

      {state.notes.length > 0 && (
        <ul className="mt-4 space-y-1 text-xs text-muted-foreground" aria-label="Notes">
          {state.notes.map((n, i) => <li key={i}>{n}</li>)}
        </ul>
      )}
    </section>
  );
}
