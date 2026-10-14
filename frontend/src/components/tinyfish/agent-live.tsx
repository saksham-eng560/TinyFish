"use client";

import { useState } from "react";
import { Bot, ExternalLink, Eye, EyeOff } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import type { FinderState } from "@/lib/types";
import { cn, safeUrl } from "@/lib/utils";

function host(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url.slice(0, 40);
  }
}

const RUN_TONE = { completed: "success", failed: "danger", running: "primary" } as const;

/** What the TinyFish Agent is doing right now, and (https only) its live browser view in a sandboxed frame. */
export function AgentLive({ agent }: { agent: FinderState["agent"] }) {
  const [watching, setWatching] = useState(true);
  const stream = safeUrl(agent.streaming_url); // https: only; anything else is never rendered
  if (!agent.active && !agent.purpose && !stream && !agent.runs.length) return null;

  return (
    <section aria-label="TinyFish Agent" className="mt-5 rounded-lg border border-line/70 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="label-caps flex items-center gap-2 text-[11px] text-primary">
            <Bot className="h-3.5 w-3.5" aria-hidden /> TinyFish Agent{agent.active ? " · working" : ""}
          </p>
          <p className="mt-2 text-sm" aria-live="polite">
            {agent.purpose || (agent.active ? "Opening the page…" : "Finished")}
          </p>
        </div>
        {stream && (
          <div className="flex flex-wrap items-center gap-2">
            <Button type="button" variant="outline" size="sm" aria-expanded={watching} aria-controls="agent-live-frame" onClick={() => setWatching((w) => !w)}>
              {watching ? <EyeOff /> : <Eye />} {watching ? "Hide live view" : "Watch live"}
            </Button>
            <a href={stream} target="_blank" rel="noopener noreferrer" className={buttonVariants({ variant: "ghost", size: "sm" })}>
              Open in new tab <ExternalLink />
            </a>
          </div>
        )}
      </div>

      {stream && watching && (
        <iframe id="agent-live-frame" src={stream} title="TinyFish Agent live browser view" sandbox="allow-scripts allow-same-origin"
          referrerPolicy="no-referrer" loading="lazy" className="mt-4 aspect-video w-full rounded-md border border-line bg-muted" />
      )}

      {agent.runs.length > 0 && (
        <ul className="mt-4 space-y-2" aria-label="Agent runs">
          {agent.runs.map((run, i) => (
            <li key={`${run.url}-${i}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="min-w-0 flex-1 truncate font-mono" title={run.url}>{host(run.url)}</span>
              <Badge tone={RUN_TONE[run.status as keyof typeof RUN_TONE] ?? "muted"} className={cn(run.status === "running" && "animate-pulse")}>{run.status}</Badge>
              <span className="tabular-nums text-muted-foreground">{run.steps} steps · {run.found} found</span>
              {run.error && <span className="basis-full text-warning">{run.error}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
