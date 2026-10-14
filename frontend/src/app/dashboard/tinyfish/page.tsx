"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, Fish, Layers, Save, SearchX, TriangleAlert, X } from "lucide-react";
import { EmptyState } from "@/components/empty-state";
import { ErrorState } from "@/components/error-state";
import { PageHeader } from "@/components/page-header";
import { FinderForm, DEFAULT_REQUEST, buildPayload, requestFromPreferences } from "@/components/tinyfish/finder-form";
import { FinderProgress } from "@/components/tinyfish/finder-progress";
import { ListingCard } from "@/components/tinyfish/listing-card";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { useAgentStatus, useMe } from "@/hooks/use-applications";
import { useFinder, useRecentSearches, useTinyFishStatus } from "@/hooks/use-tinyfish";
import type { FinderListing, FinderRequest } from "@/lib/types";
import { PLATFORM_LABELS, plural, timeAgo, titleCase } from "@/lib/utils";

const STORAGE_KEY = "hireflow.tinyfish.finder.request";

type Sort = "score" | "newest";

function loadRequest(): FinderRequest | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<FinderRequest>;
    return { ...DEFAULT_REQUEST, ...parsed };
  } catch {
    return null;
  }
}

function sortListings(list: FinderListing[], sort: Sort) {
  return [...list].sort((a, b) => {
    if (sort === "newest") {
      const ta = a.posted_date ? Date.parse(a.posted_date) : 0;
      const tb = b.posted_date ? Date.parse(b.posted_date) : 0;
      if (tb !== ta) return tb - ta;
    }
    return b.score - a.score;
  });
}

export default function TinyFishFinderPage() {
  const { data: tfStatus, error: statusError, mutate: refreshStatus } = useTinyFishStatus();
  const { data: me } = useMe();
  const { data: agentStatus } = useAgentStatus();
  const { data: recent, mutate: refreshRecent } = useRecentSearches();
  const finder = useFinder();
  const { data: state, error: stateError, mutate: refreshState } = finder.search;

  const [request, setRequest] = useState<FinderRequest>(DEFAULT_REQUEST);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saved, setSaved] = useState<{ count: number; url: string } | null>(null);
  const [minScore, setMinScore] = useState(0);
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [ats, setAts] = useState("all");
  const [sort, setSort] = useState<Sort>("score");

  useEffect(() => {  // the last request you sent comes back after a reload
    const stored = loadRequest();
    if (stored) setRequest(stored);
  }, []);

  const { searchId, setSearchId } = finder;
  const runningId = agentStatus?.running_runs.find((r) => r.run_type === "tinyfish_search")?.id ?? null;
  useEffect(() => {  // a search already running (another tab, before a reload) is picked up again
    if (!searchId && runningId) setSearchId(runningId);
  }, [searchId, runningId, setSearchId]);

  useEffect(() => {  // a different search: start with a clean selection and filters
    setSelected(new Set());
    setSaved(null);
    setMinScore(0);
    setRemoteOnly(false);
    setAts("all");
  }, [searchId]);

  const status = state?.status;
  useEffect(() => {
    if (status && status !== "running") void refreshRecent();
  }, [status, refreshRecent]);

  const running = status === "running";
  const results = useMemo(() => state?.results ?? [], [state?.results]);
  const atsOptions = useMemo(() => Array.from(new Set(results.map((l) => l.ats).filter(Boolean))).sort(), [results]);
  const visible = useMemo(
    () => sortListings(results.filter((l) => l.score >= minScore && (!remoteOnly || l.remote) && (ats === "all" || l.ats === ats)), sort),
    [results, minScore, remoteOnly, ats, sort],
  );
  const selectable = visible.filter((l) => !l.saved);
  const allSelected = selectable.length > 0 && selectable.every((l) => selected.has(l.id));

  const submit = async () => {
    const payload = buildPayload(request, tfStatus);
    try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(request)); } catch { /* private mode: nothing to remember */ }
    await finder.start(payload);
  };

  const save = async () => {
    const result = await finder.save([...selected]);
    if (result) {
      setSelected(new Set());
      setSaved({ count: result.saved + result.already_in_deck, url: result.review_url || "/dashboard/review" });
    }
  };

  const toggle = (id: string, on: boolean) => setSelected((cur) => {
    const next = new Set(cur);
    if (on) next.add(id); else next.delete(id);
    return next;
  });

  const unconfigured = !!tfStatus && !tfStatus.configured;
  const disabledByAdmin = !!tfStatus && tfStatus.configured && !tfStatus.enabled;

  return (
    <div className="pb-24">
      <PageHeader eyebrow="Powered by TinyFish" title="TinyFish Finder"
        description="Tell it what you want. TinyFish Search finds careers pages and job boards, Fetch reads them, and the Agent opens the JavaScript-heavy ones. You get ranked, de-duplicated openings with apply links. No resume needed." />

      {statusError && <ErrorState error={statusError} onRetry={() => refreshStatus()} title="Couldn't check TinyFish" className="mb-6" />}

      {(unconfigured || disabledByAdmin) && (
        <Card className="mb-6 border-warning/60" role="status">
          <CardContent className="flex items-start gap-3 p-5 text-sm">
            <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
            <div>
              <p className="font-semibold">{unconfigured ? "TinyFish isn't configured" : "TinyFish is switched off on this server"}</p>
              <p className="mt-1 text-muted-foreground">
                {(unconfigured && tfStatus?.hint) || (unconfigured
                  ? "Add TINYFISH_API_KEY to your .env (key: https://agent.tinyfish.ai/api-keys) and restart."
                  : "Ask the person running this server to turn TinyFish back on (TINYFISH_ENABLED).")}
              </p>
              <p className="mt-2 text-muted-foreground">The rest of HireFlow keeps working without it. The form below is disabled until then.</p>
            </div>
          </CardContent>
        </Card>
      )}

      <FinderForm value={request} onChange={setRequest} onSubmit={() => void submit()} status={tfStatus}
        hasPreferences={!!me} running={running} submitting={finder.starting}
        onUsePreferences={() => me && setRequest((cur) => requestFromPreferences(me.preferences, cur))} />

      {stateError && !state && <ErrorState error={stateError} onRetry={() => refreshState()} title="Couldn't load this search" className="mt-6" />}

      {state && (
        <div className="mt-6">
          <FinderProgress state={state} onStop={() => void finder.stop()} stopping={finder.stopping} />
        </div>
      )}

      {state && status === "completed" && (
        <section aria-label="Results" className="mt-8">
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div>
              <h2 className="display text-2xl" aria-live="polite">
                {plural(results.length, "opening")}
              </h2>
              <p className="mt-1 text-xs text-muted-foreground" aria-live="polite">
                {visible.length !== results.length ? `Showing ${visible.length} after filters. ` : ""}
                {state.counts.after_dedupe != null && `${state.counts.after_dedupe} unique from ${state.counts.listings ?? state.counts.raw_hits ?? "?"} found.`}
                {!!state.counts.excluded_no_sponsorship && ` ${plural(state.counts.excluded_no_sponsorship, "posting")} left out: no visa sponsorship.`}
              </p>
            </div>
          </div>

          {results.length > 0 && (
            <div className="mt-4 grid gap-4 rounded-lg border border-line/70 p-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-2">
                <Label htmlFor="finder-min-score" className="text-xs font-semibold">Minimum score: <span className="tabular-nums">{minScore}</span></Label>
                <Slider id="finder-min-score" min={0} max={100} step={5} value={[minScore]} onValueChange={(v) => setMinScore(v[0] ?? 0)} aria-label="Minimum score" />
              </div>
              <div className="flex items-center gap-3">
                <Switch id="finder-remote" checked={remoteOnly} onCheckedChange={setRemoteOnly} />
                <Label htmlFor="finder-remote" className="text-xs font-semibold">Remote only</Label>
              </div>
              <div className="space-y-2">
                <Label htmlFor="finder-ats" className="text-xs font-semibold">Source</Label>
                <Select id="finder-ats" value={ats} onChange={(e) => setAts(e.target.value)}>
                  <option value="all">All sources</option>
                  {atsOptions.map((a) => <option key={a} value={a}>{PLATFORM_LABELS[a] ?? titleCase(a)}</option>)}
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="finder-sort" className="text-xs font-semibold">Sort by</Label>
                <Select id="finder-sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)}>
                  <option value="score">Best match</option>
                  <option value="newest">Newest</option>
                </Select>
              </div>
            </div>
          )}

          {saved && (
            <div role="status" className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-primary/50 bg-secondary p-4 text-sm">
              <Layers className="h-4 w-4 text-primary" aria-hidden />
              <span className="flex-1 font-medium">{plural(saved.count, "listing")} in Swipe Review.</span>
              <Link href={saved.url} className={buttonVariants({ size: "sm" })}>Open Swipe Review <ArrowRight /></Link>
              <Button variant="ghost" size="icon" onClick={() => setSaved(null)} aria-label="Dismiss"><X /></Button>
            </div>
          )}

          {results.length === 0 ? (
            <div className="mt-6">
              <EmptyState icon={SearchX} title="No openings matched"
                description="Try broader roles, more locations or a lower seniority filter, add a careers page URL, or turn the Agent on." />
            </div>
          ) : visible.length === 0 ? (
            <div className="mt-6"><EmptyState icon={SearchX} title="Nothing matches these filters" description="Lower the minimum score or clear the source filter." /></div>
          ) : (
            <>
              <div className="mt-4 flex items-center gap-2">
                <Checkbox id="finder-select-all" checked={allSelected} disabled={!selectable.length}
                  onCheckedChange={(v) => setSelected(v === true ? new Set([...selected, ...selectable.map((l) => l.id)]) : new Set([...selected].filter((id) => !selectable.some((l) => l.id === id))))} />
                <Label htmlFor="finder-select-all" className="text-xs font-semibold">Select all shown ({selectable.length})</Label>
              </div>
              <ul className="mt-3 space-y-3" aria-label="Ranked openings">
                {visible.map((l) => (
                  <ListingCard key={l.id} listing={l} selected={selected.has(l.id)} onSelect={(on) => toggle(l.id, on)}
                    visaRequested={state.request.visa_sponsorship} />
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {!state && !stateError && searchId && <Skeleton className="mt-6 h-40" aria-busy="true" />}

      {!!recent?.items.length && (
        <details className="mt-8 rounded-lg border border-line/70 p-4">
          <summary className="cursor-pointer select-none rounded text-sm font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            Recent searches ({recent.items.length})
          </summary>
          <ul className="mt-3 divide-y divide-line/60" aria-label="Recent searches">
            {recent.items.map((run) => {
              const roles = run.progress?.request?.roles ?? [];
              const label = roles.length ? roles.slice(0, 3).join(", ") : run.progress?.request?.companies?.slice(0, 3).join(", ") || "Search";
              return (
                <li key={run.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-sm">
                  <span className="min-w-0 flex-1 truncate font-medium">{label}</span>
                  <Badge tone={run.status === "completed" ? "success" : run.status === "running" ? "primary" : "muted"}>{run.status}</Badge>
                  <span className="text-xs text-muted-foreground">{timeAgo(run.started_at)}</span>
                  <Button size="sm" variant="outline" disabled={run.id === searchId} onClick={() => setSearchId(run.id)}>
                    {run.id === searchId ? "Showing" : "Open"}
                  </Button>
                </li>
              );
            })}
          </ul>
        </details>
      )}

      {!state && !searchId && !recent?.items.length && !unconfigured && (
        <div className="mt-8">
          <EmptyState icon={Fish} title="Nothing searched yet"
            description="Fill in a role or two, or press Use my preferences, then Find openings." />
        </div>
      )}

      {selected.size > 0 && (
        <div className="fixed inset-x-0 bottom-4 z-20 flex justify-center px-4 lg:left-[248px]">
          <Card className="flex w-full max-w-xl items-center gap-3 p-3 shadow-lg" role="region" aria-label="Selected listings">
            <span className="flex-1 pl-2 text-sm font-medium" aria-live="polite">{selected.size} selected</span>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>Clear</Button>
            <Button onClick={() => void save()} loading={finder.saving}>{!finder.saving && <Save />} Save {selected.size} to Swipe Review</Button>
          </Card>
        </div>
      )}
    </div>
  );
}
