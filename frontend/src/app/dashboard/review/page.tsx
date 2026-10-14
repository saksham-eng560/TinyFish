"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Check, CheckCheck, FileText, Layers, Play, Radar, RotateCcw, Search, X } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { AnimatedNumber } from "@/components/motion";
import { PageHeader } from "@/components/page-header";
import { ScanProgressPanel } from "@/components/scan-progress";
import { DeckShadowCard, SwipeCard, type Decision, type SwipeCardHandle } from "@/components/swipe-card";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useToast } from "@/components/ui/toast";
import { useAgentStatus } from "@/hooks/use-applications";
import { useScan } from "@/hooks/use-scan";
import { ApiError, fetcher, post, put } from "@/lib/api-client";
import type { ReviewCard, ReviewQueue, ReviewStats } from "@/lib/types";
import { cn } from "@/lib/utils";

type Filters = { jobType: string; remote: boolean; q: string };
type Swiped = { card: ReviewCard; decision: Decision };

function queueKey(f: Filters, limit = 30, minScore?: number) {
  const p = new URLSearchParams({ limit: String(limit) });
  if (f.jobType !== "all") p.set("job_type", f.jobType);
  if (f.remote) p.set("remote", "true");
  if (f.q.trim()) p.set("q", f.q.trim());
  if (minScore != null) p.set("min_score", String(minScore));
  return `/review/queue?${p.toString()}`;
}

function StatCell({ label, value, accent }: { label: string; value: number | string; accent?: boolean }) {
  return (
    <div className="px-4 py-4 sm:px-5">
      <p className="label-caps text-[10px] text-muted-foreground">{label}</p>
      <p className={cn("mt-2 font-mono font-bold tracking-tight text-3xl leading-none tabular-nums", accent && "text-primary")}>
        {typeof value === "number" ? <AnimatedNumber value={value} /> : value}
      </p>
    </div>
  );
}

export default function SwipeReviewPage() {
  const toast = useToast();
  const [filters, setFilters] = useState<Filters>({ jobType: "all", remote: false, q: "" });
  const [search, setSearch] = useState("");
  const key = queueKey(filters);
  const { data, isLoading, error, mutate } = useSWR<ReviewQueue>(key, fetcher, { revalidateOnFocus: false });
  const { mutate: refreshStatus } = useAgentStatus();
  const [decided, setDecided] = useState<Set<string>>(new Set());
  const [front, setFront] = useState<ReviewCard[]>([]); // cards put back by "undo"
  const [history, setHistory] = useState<Swiped[]>([]);
  const [stats, setStats] = useState<ReviewStats | null>(null);
  const [busy, setBusy] = useState(false);
  const [bulkScore, setBulkScore] = useState(60);
  const [bulkPreviewScore, setBulkPreviewScore] = useState(60);
  const scanner = useScan();
  const scanning = scanner.busy;
  const topRef = useRef<SwipeCardHandle>(null);

  useEffect(() => { if (data?.stats) setStats(data.stats); }, [data?.stats]);
  useEffect(() => { const t = setTimeout(() => setFilters((f) => ({ ...f, q: search })), 350); return () => clearTimeout(t); }, [search]);

  // Keep the order cards were first shown in: a refetch may add cards at the end, never reshuffle the top.
  const [order, setOrder] = useState<string[]>([]);
  useEffect(() => {
    if (!data?.items) return;
    const live = new Set(data.items.map((c) => c.application_id));
    setOrder((prev) => {
      const kept = prev.filter((id) => live.has(id));
      const known = new Set(kept);
      return [...kept, ...data.items.map((c) => c.application_id).filter((id) => !known.has(id))];
    });
  }, [data?.items]);
  useEffect(() => { setOrder([]); setFront([]); setDecided(new Set()); }, [key]);

  const deck = useMemo(() => {
    const byId = new Map((data?.items || []).map((c) => [c.application_id, c]));
    const seen = new Set<string>();
    return [...front, ...order.map((id) => byId.get(id)).filter((c): c is ReviewCard => !!c)].filter((c) => {
      if (decided.has(c.application_id) || seen.has(c.application_id)) return false;
      seen.add(c.application_id);
      return true;
    });
  }, [data?.items, decided, front, order]);

  // Top the deck up before it runs dry.
  useEffect(() => {
    if (data && deck.length < 5 && (data.items.length || 0) >= 30) mutate();
  }, [deck.length, data, mutate]);

  const { data: preview } = useSWR<ReviewQueue>(queueKey(filters, 1, bulkPreviewScore), fetcher, { revalidateOnFocus: false });

  const top = deck[0];
  const autoSubmit = data?.settings.auto_submit_kept ?? true;
  const noResume = data && !data.has_master_resume;

  const decide = useCallback(async (card: ReviewCard, decision: Decision) => {
    setDecided((prev) => new Set(prev).add(card.application_id));
    setFront((prev) => prev.filter((c) => c.application_id !== card.application_id));
    setHistory((prev) => [...prev.slice(-49), { card, decision }]);
    try {
      const res = await post<{ stats: ReviewStats }>(`/review/${card.application_id}`, { decision });
      setStats(res.stats);
      refreshStatus();
    } catch (err) {
      setDecided((prev) => { const next = new Set(prev); next.delete(card.application_id); return next; });
      setHistory((prev) => prev.filter((h) => h.card.application_id !== card.application_id));
      toast({ title: `Could not ${decision} this job`, description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  }, [refreshStatus, toast]);

  const undo = useCallback(async () => {
    const last = history[history.length - 1];
    if (!last) return;
    try {
      const res = await post<{ card: ReviewCard; stats: ReviewStats }>(`/review/${last.card.application_id}/undo`);
      setHistory((prev) => prev.slice(0, -1));
      setDecided((prev) => { const next = new Set(prev); next.delete(last.card.application_id); return next; });
      setFront((prev) => [res.card, ...prev]);
      setStats(res.stats);
      refreshStatus();
    } catch (err) {
      setHistory((prev) => prev.slice(0, -1));
      toast({ title: "Can't undo that one", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  }, [history, refreshStatus, toast]);

  const swipe = useCallback(async (decision: Decision) => {
    if (!top || busy || (decision === "keep" && noResume)) return;
    setBusy(true);
    try {
      if (topRef.current) await topRef.current.fling(decision);
      else await decide(top, decision);
    } finally {
      setBusy(false);
    }
  }, [top, busy, noResume, decide]);

  // "I Applied": you applied to the top card yourself. It leaves the deck and is tracked in Applied.
  const iApplied = useCallback(async () => {
    if (!top || busy) return;
    setBusy(true);
    try {
      await post(`/applications/${top.application_id}/mark-applied`);
      if (topRef.current) await topRef.current.flyUp();
      setDecided((prev) => new Set(prev).add(top.application_id));
      setFront((prev) => prev.filter((c) => c.application_id !== top.application_id));
      setStats((prev) => prev && { ...prev, remaining: Math.max(0, prev.remaining - 1) });
      toast({ title: `Tracking ${top.job.company_name}`, description: "Moved to Applied. You'll get updates on Gmail and here whenever they reply.", tone: "success" });
      refreshStatus();
    } catch (err) {
      toast({ title: "Could not mark as applied", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  }, [top, busy, toast, refreshStatus]);

  // Keyboard: ← skip, → keep, A I applied, Z / Backspace undo.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement;
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable)) return;
      if (e.key === "ArrowRight") { e.preventDefault(); void swipe("keep"); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); void swipe("skip"); }
      else if (e.key === "z" || e.key === "Z" || e.key === "Backspace") { e.preventDefault(); void undo(); }
      else if ((e.key === "a" || e.key === "A") && !e.metaKey && !e.ctrlKey) { e.preventDefault(); void iApplied(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [swipe, undo, iApplied]);

  const bulk = async (decision: Decision, minScore?: number) => {
    try {
      const res = await post<{ count: number; stats: ReviewStats }>("/review/bulk", {
        decision, min_score: minScore, job_type: filters.jobType === "all" ? undefined : filters.jobType,
        remote: filters.remote || undefined, q: filters.q || undefined,
      });
      setStats(res.stats);
      toast({ title: decision === "keep" ? `Kept ${res.count} jobs` : `Skipped ${res.count} jobs`,
        description: decision === "keep" ? (autoSubmit ? "They're being tailored, filled and submitted." : "They're being prepared for your review.") : undefined,
        tone: "success" });
      setFront([]);
      setDecided(new Set());
      mutate();
      refreshStatus();
    } catch (err) {
      toast({ title: "Bulk action failed", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  };

  const setAutoSubmit = async (value: boolean) => {
    await put("/users/me/preferences", { preferences: { auto_submit_kept: value } });
    mutate();
  };

  const loadDetails = async (card: ReviewCard) => {
    const fresh = await post<ReviewCard>(`/review/${card.application_id}/details`);
    setFront((prev) => [fresh, ...prev.filter((c) => c.application_id !== card.application_id)]);
    mutate((current) => current && { ...current, items: current.items.map((c) => c.application_id === fresh.application_id ? fresh : c) }, false);
  };

  const scan = scanner.start;

  const s = stats || data?.stats;

  return (
    <div>
      <PageHeader eyebrow="Mass apply" title="Swipe Review" compact
        description="Every job that passed your filters, best matches first. Keep it and the agent tailors, fills and applies. Skip it and it's gone. Nothing is skipped for you."
        actions={
          <>
            <Button variant="outline" onClick={scan} loading={scanning}>
              {!scanning && <Radar />} {scanner.running ? `Scanning ${Math.round(scanner.progress?.percent ?? 0)}%` : "Scan for more"}
            </Button>
            <Link href="/dashboard/applications" className={cn(buttonVariants({ variant: "ghost" }), "hidden sm:inline-flex")}>Applications</Link>
          </>
        }
      />

      <ScanProgressPanel scan={scanner} />

      {/* phones: one line, so the deck starts near the top */}
      <p className="mb-4 flex justify-between gap-2 rounded-xl border bg-card px-4 py-2 text-sm text-muted-foreground sm:hidden">
        <span><b className="font-mono text-primary">{s?.remaining ?? "—"}</b> left</span>
        <span><b className="font-mono text-foreground">{s?.kept_today ?? "—"}</b> kept today</span>
        <span><b className="font-mono text-foreground">{s?.skipped_today ?? "—"}</b> skipped</span>
      </p>
      <div className="mb-8 hidden grid-cols-2 border sm:grid sm:grid-cols-4 grid-lines">
        <StatCell label="Left to swipe" value={s?.remaining ?? "—"} accent />
        <StatCell label="Kept today" value={s?.kept_today ?? "—"} />
        <StatCell label="Skipped today" value={s?.skipped_today ?? "—"} />
        <StatCell label="Kept all time" value={s?.kept_total ?? "—"} />
      </div>

      {noResume && (
        <div className="rounded-xl mb-6 flex flex-wrap items-center justify-between gap-3 border border-primary/60 bg-primary/10 p-4 text-sm">
          <span className="flex items-center gap-2"><FileText className="h-4 w-4 text-primary" /> Upload your master resume before keeping jobs — every application is tailored from it.</span>
          <Link href="/dashboard/resume" className={buttonVariants({ size: "sm" })}>Upload resume</Link>
        </div>
      )}

      <div className="grid grid-cols-1 gap-8 xl:grid-cols-[minmax(0,1fr)_340px]">
        {/* ------------------------------------------------------------ deck */}
        <section aria-label="Job deck" className="flex flex-col items-center">
          <div className="relative mx-auto h-[min(600px,calc(100svh-19rem))] min-h-[400px] w-full max-w-[560px] sm:h-[640px] sm:max-h-none">
            {isLoading ? (
              <Skeleton className="absolute inset-0" />
            ) : error && !data ? (
              <div className="absolute inset-0 flex items-center">
                <ErrorState className="w-full" error={error} onRetry={() => mutate()} title="Couldn't load your deck" />
              </div>
            ) : !top ? (
              <div className="absolute inset-0 flex items-center">
                <div className="w-full">
                  <EmptyState icon={Layers} title={filters.q || filters.remote || filters.jobType !== "all" ? "No jobs match these filters" : "You're all caught up"}
                    description={s?.remaining ? "Clear the filters to see the rest of your deck." : "Run a scan to pull in fresh internships. Tip: apply the Internships preset in Settings to add the curated internship lists and 107 startup boards."}
                    action={
                      <div className="flex flex-wrap justify-center gap-2">
                        <Button onClick={scan} loading={scanning}><Play /> Scan now</Button>
                        <Link href="/dashboard/settings?tab=mass-apply" className={buttonVariants({ variant: "outline" })}>Mass-apply settings</Link>
                      </div>
                    } />
                </div>
              </div>
            ) : (
              <>
                {deck.slice(1, 3).reverse().map((c, i, arr) => <DeckShadowCard key={c.application_id} card={c} depth={arr.length - i} />)}
                <SwipeCard key={top.application_id} ref={topRef} card={top} disabled={busy}
                  onDecide={(d) => void decide(top, d)} onLoadDetails={() => loadDetails(top)} />
              </>
            )}
          </div>

          <div className="mt-4 flex w-full max-w-[560px] items-center justify-center gap-3 sm:mt-10 sm:gap-4">
            <Button variant="ghost" size="icon" className="h-12 w-12 shrink-0 rounded-full border border-line" onClick={undo}
              disabled={!history.length} aria-label="Undo last swipe (Z)" title="Undo (Z)">
              <RotateCcw />
            </Button>
            <Button variant="outline" size="xl" className="min-w-0 flex-1 px-4 sm:max-w-44" onClick={() => swipe("skip")} disabled={!top || busy} aria-label="Skip (left arrow)">
              <X /> Skip
            </Button>
            <Button size="xl" className="min-w-0 flex-1 px-4 sm:max-w-44" onClick={() => swipe("keep")} disabled={!top || busy || !!noResume} aria-label="Keep (right arrow)">
              <Check /> Keep
            </Button>
          </div>
          <Button variant="ghost" size="sm" className="mt-3" onClick={iApplied} disabled={!top || busy}
            title="Already applied to this one yourself? It moves to Applied and the agent tracks it (A)">
            <CheckCheck /> I Applied to this myself
          </Button>
          <p className="mt-3 text-center text-xs text-muted-foreground sm:hidden">Swipe the card right to keep it, left to skip it.</p>
          <p className="mt-3 hidden text-center text-xs text-muted-foreground sm:block">
            Drag the card, or use <kbd className="rounded border px-1.5 py-0.5 font-mono">←</kbd> skip · <kbd className="rounded border px-1.5 py-0.5 font-mono">→</kbd> keep · <kbd className="rounded border px-1.5 py-0.5 font-mono">A</kbd> I applied · <kbd className="rounded border px-1.5 py-0.5 font-mono">Z</kbd> undo
          </p>
        </section>

        {/* ------------------------------------------------------------ controls */}
        <aside className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>When I keep a job</CardTitle>
              <CardDescription>Kept jobs are tailored and filled in a real browser right away.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex items-start justify-between gap-4">
                <Label htmlFor="auto-submit" className="text-sm leading-snug">
                  <span className="font-semibold">Apply automatically</span>
                  <span className="mt-1 block text-xs font-normal text-muted-foreground">
                    Sent once the form is filled, after 10 minutes in Sending soon (so you can stop it). Anything the agent can&apos;t answer (visa, work authorization) still waits for you in Ready to submit.
                  </span>
                </Label>
                <Switch id="auto-submit" checked={autoSubmit} onCheckedChange={setAutoSubmit} />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Keep in bulk</CardTitle>
              <CardDescription>For mass applying: keep every job in the deck at or above a score.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div>
                <div className="mb-3 flex items-baseline justify-between">
                  <span className="label-caps text-[10px] text-muted-foreground">Minimum score</span>
                  <span className="font-mono font-bold tracking-tight text-2xl tabular-nums">{bulkScore}</span>
                </div>
                <Slider value={[bulkScore]} min={0} max={100} step={5} onValueChange={(v) => setBulkScore(v[0])}
                  onValueCommit={(v) => setBulkPreviewScore(v[0])} aria-label="Minimum match score" />
              </div>
              <Button className="w-full" disabled={!preview?.matching || !!noResume} onClick={() => bulk("keep", bulkScore)}>
                <Check /> Keep {preview?.matching ?? 0} jobs ≥ {bulkPreviewScore}
              </Button>
              <Button variant="outline" className="w-full" disabled={!s?.remaining} onClick={() => bulk("skip", undefined)}>
                <X /> Skip everything filtered
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Filter the deck</CardTitle></CardHeader>
            <CardContent className="space-y-5">
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Company, role or city" className="pl-9" aria-label="Search the deck" />
              </div>
              <ToggleGroup type="single" value={filters.jobType} onValueChange={(v) => v && setFilters((f) => ({ ...f, jobType: v }))}
                className="grid grid-cols-3 gap-1 rounded-lg border p-1" aria-label="Job type">
                <ToggleGroupItem value="all" className="h-8 rounded-md text-xs font-semibold uppercase tracking-wider data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">All</ToggleGroupItem>
                <ToggleGroupItem value="internship" className="h-8 rounded-md text-xs font-semibold uppercase tracking-wider data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">Intern</ToggleGroupItem>
                <ToggleGroupItem value="full-time" className="h-8 rounded-md text-xs font-semibold uppercase tracking-wider data-[state=on]:bg-primary data-[state=on]:text-primary-foreground">Full-time</ToggleGroupItem>
              </ToggleGroup>
              <div className="flex items-center justify-between">
                <Label htmlFor="remote-only" className="text-sm">Remote only</Label>
                <Switch id="remote-only" checked={filters.remote} onCheckedChange={(v) => setFilters((f) => ({ ...f, remote: v }))} />
              </div>
              <p className="text-xs text-muted-foreground">{data?.matching ?? 0} jobs match these filters.</p>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  );
}
