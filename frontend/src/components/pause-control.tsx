"use client";

import { useState } from "react";
import { useSWRConfig } from "swr";
import { CirclePause, Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { ApiError, post } from "@/lib/api-client";
import type { AgentStatus } from "@/lib/types";

function usePauseToggle() {
  const { mutate } = useSWRConfig();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const toggle = async (pause: boolean) => {
    setBusy(true);
    try {
      const next = await post<AgentStatus>(pause ? "/agent/pause" : "/agent/resume");
      await mutate("/agent/status", next, { revalidate: false });
      mutate((key) => typeof key === "string" && (key.startsWith("/applications") || key.startsWith("/agent/runs")));
      toast(pause
        ? { title: "Everything is paused", description: "No scans, no preparation, nothing sent until you resume.", tone: "info" }
        : { title: "Resumed", description: "Anything that was due to be sent waits 10 minutes in Sending soon first.", tone: "success" });
    } catch (err) {
      toast({ title: pause ? "Couldn't pause" : "Couldn't resume", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  return { busy, toggle };
}

/** Header switch: "Pause everything" stops scans, preparation and submissions until you resume. */
export function PauseButton({ status }: { status?: AgentStatus }) {
  const { busy, toggle } = usePauseToggle();
  if (!status) return null;
  return status.paused ? (
    <Button size="sm" onClick={() => toggle(false)} loading={busy} aria-label="Resume HireFlow">
      {!busy && <Play />} <span className="hidden sm:inline">Resume</span>
    </Button>
  ) : (
    <Button size="sm" variant="outline" onClick={() => toggle(true)} loading={busy} aria-label="Pause everything"
      title="Pause everything: no scans, no preparation, nothing sent">
      {!busy && <Pause />} <span className="hidden sm:inline">Pause</span>
    </Button>
  );
}

/** Shown on every dashboard page while paused, so it's never forgotten. */
export function PausedBanner({ status }: { status?: AgentStatus }) {
  const { busy, toggle } = usePauseToggle();
  if (!status?.paused) return null;
  return (
    <div role="status" className="flex flex-col gap-3 border-b border-warning/40 bg-secondary px-4 py-3 text-sm text-secondary-foreground sm:flex-row sm:items-center lg:px-8">
      <CirclePause className="h-5 w-5 shrink-0 text-primary" aria-hidden />
      <p className="flex-1">
        <span className="font-semibold">Everything is paused.</span> Nothing is scanned, prepared or sent until you resume.
      </p>
      <Button size="sm" onClick={() => toggle(false)} loading={busy}>{!busy && <Play />} Resume</Button>
    </div>
  );
}
