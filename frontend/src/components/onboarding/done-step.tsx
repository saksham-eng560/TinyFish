"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { useSWRConfig } from "swr";
import { ArrowLeft, ArrowRight, Layers, PartyPopper, Radar } from "lucide-react";
import { clearAllDrafts, FormError } from "@/components/onboarding/shared";
import { ScanBar, ScanProgressPanel } from "@/components/scan-progress";
import { Button } from "@/components/ui/button";
import { useScan } from "@/hooks/use-scan";
import { ApiError, fetcher, post } from "@/lib/api-client";
import type { AgentRun, OnboardingState } from "@/lib/types";

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[9rem_minmax(0,1fr)] gap-3 border-b py-2.5 text-sm last:border-0">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words font-medium">{value || <span className="text-muted-foreground">Not set</span>}</dd>
    </div>
  );
}

/** Step 8: summary, then "Run my first scan" → live progress → the first Swipe Review deck. */
export function DoneStep({ state, onBack }: { state: OnboardingState; onBack: () => void }) {
  const router = useRouter();
  const { mutate } = useSWRConfig();
  const scan = useScan();
  const [runId, setRunId] = useState<string | null>(state.run?.id ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: run } = useSWR<AgentRun>(runId ? `/agent/runs/${runId}` : null, fetcher, {
    refreshInterval: (r?: AgentRun) => (!r || r.status === "running" ? 1500 : 0),
  });
  const finished = !!run && run.status !== "running";
  const { welcome, targets, apply } = state.data;

  const finish = async (startScan: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const result = await post<OnboardingState>("/users/me/onboarding/complete", { start_scan: startScan });
      clearAllDrafts();
      await Promise.all([mutate("/auth/me"), mutate("/users/me/onboarding", result, { revalidate: false }), mutate("/agent/status")]);
      if (startScan && result.run) setRunId(result.run.id);
      else router.replace("/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // The first deck is ready: open Swipe Review
  useEffect(() => {
    if (!finished) return;
    mutate((key) => typeof key === "string" && key.startsWith("/review"));
    const t = setTimeout(() => router.replace("/dashboard/review"), 1500);
    return () => clearTimeout(t);
  }, [finished, mutate, router]);

  if (runId) {
    const percent = run?.progress?.percent ?? (finished ? 100 : 5);
    return (
      <section aria-live="polite">
        <header className="mb-6">
          <h1 className="display flex items-center gap-3 text-3xl sm:text-4xl"><Radar className="h-8 w-8 text-primary" aria-hidden /> {finished ? "Your first deck is ready" : "Finding internships for you"}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {finished ? `Found ${run?.jobs_discovered ?? 0} postings. Opening Swipe Review…`
              : "Searching your sources and scoring each posting against your resume. This usually takes a minute or two."}
          </p>
        </header>
        {scan.running ? <ScanProgressPanel scan={scan} /> : (
          <div className="mb-8 rounded-xl border bg-card p-5 shadow-sm"><ScanBar percent={percent} active={!finished} /></div>
        )}
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => router.replace("/dashboard/review")}><Layers /> Go to Swipe Review <ArrowRight /></Button>
          {!finished && <Button variant="ghost" onClick={() => router.replace("/dashboard")}>Wait on the dashboard</Button>}
        </div>
      </section>
    );
  }

  return (
    <section>
      <header className="mb-6">
        <h1 className="display flex items-center gap-3 text-3xl sm:text-4xl"><PartyPopper className="h-8 w-8 text-primary" aria-hidden /> All set</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground sm:text-base">Here’s what HireFlow will do for you. Run your first scan and your Swipe Review deck fills up in a minute or two.</p>
      </header>
      <FormError message={error} />
      {state.missing.length > 0 && (
        <p role="alert" className="mb-4 rounded-lg border border-warning/50 bg-secondary px-3 py-2 text-sm">
          Still needed before you finish: <strong>{state.missing.join(", ")}</strong>. Go back to add {state.missing.length > 1 ? "them" : "it"}.
        </p>
      )}
      <dl className="max-w-2xl rounded-xl border bg-card px-4 shadow-sm">
        <Row label="Name" value={welcome.full_name} />
        <Row label="Roles" value={targets.target_roles?.join(" · ")} />
        <Row label="Locations" value={targets.target_locations?.join(" · ")} />
        <Row label="Season" value={targets.internship_season} />
        <Row label="Review" value={apply.review_mode === "swipe" ? "Swipe Review: you keep or skip every job" : "Automatic threshold"} />
        <Row label="Submitting" value={apply.auto_submit_kept ? "Automatically, once a form is filled" : "Waits for your click in Ready to submit"} />
        <Row label="Daily limit" value={`${apply.max_applications_per_day} applications a day`} />
      </dl>
      <div className="sticky bottom-0 -mx-4 mt-8 flex flex-wrap items-center gap-2 border-t bg-background/95 px-4 py-4 backdrop-blur sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:px-0">
        <Button type="button" variant="outline" onClick={onBack} disabled={busy}><ArrowLeft /> Back</Button>
        <div className="flex-1" />
        <Button type="button" variant="ghost" onClick={() => finish(false)} disabled={busy || state.missing.length > 0}>Finish without scanning</Button>
        <Button type="button" size="lg" onClick={() => finish(true)} loading={busy} disabled={state.missing.length > 0}><Radar /> Run my first scan</Button>
      </div>
    </section>
  );
}
