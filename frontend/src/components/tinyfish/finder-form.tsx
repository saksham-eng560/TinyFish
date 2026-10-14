"use client";

import { useId } from "react";
import { Search, Wand2 } from "lucide-react";
import { TagInput } from "@/components/tag-input";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { FinderRequest, Preferences, TinyFishStatus } from "@/lib/types";
import { cn, safeUrl } from "@/lib/utils";

export const SENIORITY_LEVELS = ["internship", "entry", "mid", "senior", "lead", "executive"] as const;
export const POSTED_WITHIN_OPTIONS = [7, 14, 30, 90] as const;

export const DEFAULT_REQUEST: FinderRequest = {
  roles: [], locations: [], keywords: [], seniority: [], visa_sponsorship: false, remote: false, companies: [], career_pages: [],
  posted_within_days: 30, limit: 30, min_score: 20, use_agent: true, agent_runs: 2,
};

// Server limits (POST /tinyfish/search validation): checked here so the form explains instead of answering 422.
const MAX_ITEMS = { roles: 10, locations: 10, keywords: 15, companies: 10, career_pages: 5 } as const;
const MAX_TEXT = 120;
const MAX_URL = 500;

const pill = (on: boolean) => cn("rounded-full border px-3 py-1 text-sm capitalize transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50",
  on ? "border-primary bg-primary text-primary-foreground" : "border-foreground/30 hover:border-foreground");

/** What is wrong with the request, in words, or null when it can be sent. */
export function validateRequest(req: FinderRequest): string | null {
  if (!req.roles.length && !req.keywords.length && !req.companies.length && !req.career_pages.length) {
    return "Add at least one role, keyword, company or careers page.";
  }
  for (const key of Object.keys(MAX_ITEMS) as (keyof typeof MAX_ITEMS)[]) {
    if (req[key].length > MAX_ITEMS[key]) return `Use at most ${MAX_ITEMS[key]} entries for ${key.replace("_", " ")}.`;
  }
  const texts = [...req.roles, ...req.locations, ...req.keywords, ...req.companies];
  if (texts.some((t) => t.length > MAX_TEXT)) return `Each entry can be at most ${MAX_TEXT} characters.`;
  const badPage = req.career_pages.find((u) => u.length > MAX_URL || !safeUrl(u, ["http:", "https:"]));
  if (badPage) return `"${badPage.slice(0, 60)}" isn't a valid http(s) careers page URL.`;
  return null;
}

/** Fill the form from the saved job preferences, trimmed to what the API accepts. */
export function requestFromPreferences(prefs: Preferences, current: FinderRequest): FinderRequest {
  const clean = (list: string[] | undefined, max: number) => (list ?? []).map((s) => s.trim().slice(0, MAX_TEXT)).filter(Boolean).slice(0, max);
  const days = prefs.posted_within_days;
  const posted = days ? POSTED_WITHIN_OPTIONS.reduce((best, o) => (Math.abs(o - days) < Math.abs(best - days) ? o : best)) : current.posted_within_days;
  return {
    ...current,
    roles: clean(prefs.target_roles, MAX_ITEMS.roles),
    locations: clean(prefs.target_locations, MAX_ITEMS.locations),
    keywords: clean(prefs.focus_skills, MAX_ITEMS.keywords),
    seniority: (prefs.experience_level ?? []).filter((l) => (SENIORITY_LEVELS as readonly string[]).includes(l)),
    visa_sponsorship: !!prefs.exclude_no_sponsorship,
    remote: prefs.remote_preference === "remote",
    companies: clean(prefs.companies_to_target, MAX_ITEMS.companies),
    career_pages: (prefs.sources?.career_pages ?? []).filter((u) => u.length <= MAX_URL && safeUrl(u, ["http:", "https:"])).slice(0, MAX_ITEMS.career_pages),
    posted_within_days: posted,
  };
}

/** The body sent to POST /tinyfish/search: the Agent is switched off when the server won't run it. */
export function buildPayload(req: FinderRequest, status: TinyFishStatus | undefined): FinderRequest {
  const agentOn = req.use_agent && !!status?.agent_enabled && !status.demo_mode;
  const max = status?.agent_runs_max ?? 5;
  return { ...req, use_agent: agentOn, agent_runs: agentOn ? Math.min(Math.max(0, req.agent_runs), max, 5) : 0 };
}

function Field({ label, hint, htmlFor, children }: { label: string; hint?: string; htmlFor?: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={htmlFor} className="text-sm font-semibold">{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

function SwitchRow({ id, label, hint, checked, onChange, disabled }: {
  id: string; label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean;
}) {
  return (
    <div className="flex items-start gap-3">
      <Switch id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} />
      <div className="min-w-0">
        <Label htmlFor={id} className="text-sm font-semibold">{label}</Label>
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </div>
    </div>
  );
}

export function FinderForm({ value, onChange, onSubmit, onUsePreferences, status, hasPreferences, running, submitting }: {
  value: FinderRequest;
  onChange: (next: FinderRequest) => void;
  onSubmit: () => void;
  onUsePreferences: () => void;
  status: TinyFishStatus | undefined;
  hasPreferences: boolean;
  running: boolean;
  submitting: boolean;
}) {
  const uid = useId();
  const id = (name: string) => `${uid}-${name}`;
  const set = <K extends keyof FinderRequest>(key: K, v: FinderRequest[K]) => onChange({ ...value, [key]: v });
  const unconfigured = status ? !status.configured : false;
  const off = unconfigured || (status ? !status.enabled : false);
  const agentBlocked = !status?.agent_enabled || !!status?.demo_mode;
  const maxRuns = Math.min(5, status?.agent_runs_max ?? 5);
  const problem = validateRequest(value);
  const empty = !value.roles.length && !value.keywords.length && !value.companies.length && !value.career_pages.length;
  const disabled = off || running;

  return (
    <Card>
      <CardHeader>
        <CardTitle>What are you looking for?</CardTitle>
        <CardDescription>
          TinyFish Search finds careers pages and boards, Fetch reads them, and the Agent opens the JavaScript-heavy ones.
          No resume needed.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); if (!problem && !disabled) onSubmit(); }} aria-describedby={problem ? id("problem") : undefined}>
          <fieldset disabled={disabled} className="min-w-0 space-y-5 border-0 p-0 disabled:opacity-60">
            <div className="grid gap-5 md:grid-cols-2">
              <Field label="Roles" htmlFor={id("roles")} hint="Press Enter after each one, e.g. Backend Engineer, Software Engineer Intern.">
                <TagInput id={id("roles")} value={value.roles} onChange={(v) => set("roles", v)} placeholder="Backend Engineer" />
              </Field>
              <Field label="Locations" htmlFor={id("locations")}>
                <TagInput id={id("locations")} value={value.locations} onChange={(v) => set("locations", v)} placeholder="Bengaluru, Remote" />
              </Field>
              <Field label="Keywords" htmlFor={id("keywords")} hint="Skills a posting should mention.">
                <TagInput id={id("keywords")} value={value.keywords} onChange={(v) => set("keywords", v)} placeholder="python, fastapi" />
              </Field>
              <Field label="Companies" htmlFor={id("companies")} hint="Look at these companies' careers pages specifically.">
                <TagInput id={id("companies")} value={value.companies} onChange={(v) => set("companies", v)} placeholder="Razorpay, Stripe" />
              </Field>
            </div>

            <div role="group" aria-labelledby={id("seniority")} className="space-y-1.5">
              <p id={id("seniority")} className="text-sm font-semibold">Seniority</p>
              <div className="flex flex-wrap gap-2">
                {SENIORITY_LEVELS.map((level) => {
                  const on = value.seniority.includes(level);
                  return (
                    <button key={level} type="button" className={pill(on)} aria-pressed={on}
                      onClick={() => set("seniority", on ? value.seniority.filter((l) => l !== level) : [...value.seniority, level])}>
                      {level}
                    </button>
                  );
                })}
              </div>
            </div>

            <Field label="Career pages" htmlFor={id("pages")} hint="Your own list of careers page URLs (up to 5). They are read first.">
              <TagInput id={id("pages")} value={value.career_pages} onChange={(v) => set("career_pages", v)} placeholder="https://acme.com/careers" />
            </Field>

            <div className="grid gap-5 md:grid-cols-2">
              <SwitchRow id={id("visa")} label="I need visa sponsorship" hint="Adds it to the search and drops postings that say they don't sponsor."
                checked={value.visa_sponsorship} onChange={(v) => set("visa_sponsorship", v)} />
              <SwitchRow id={id("remote")} label="Remote only" hint="Ranks remote-friendly openings first."
                checked={value.remote} onChange={(v) => set("remote", v)} />
              <Field label="Posted within" htmlFor={id("posted")}>
                <Select id={id("posted")} className="max-w-xs" value={value.posted_within_days}
                  onChange={(e) => set("posted_within_days", Number(e.target.value))}>
                  {POSTED_WITHIN_OPTIONS.map((d) => <option key={d} value={d}>{d} days</option>)}
                </Select>
              </Field>
              <div className="space-y-3">
                <SwitchRow id={id("agent")} label="Use the TinyFish Agent for JavaScript-heavy pages"
                  hint={status?.demo_mode ? "The Agent is off in the demo." : !status?.agent_enabled && status ? "The Agent isn't available on this server." : "It opens pages that plain fetching can't read. Each step costs credits."}
                  checked={value.use_agent && !agentBlocked} disabled={agentBlocked} onChange={(v) => set("use_agent", v)} />
                {!status?.demo_mode && value.use_agent && !agentBlocked && (
                  <Field label="Agent runs" htmlFor={id("runs")}>
                    <Select id={id("runs")} className="max-w-[10rem]" value={Math.min(value.agent_runs, maxRuns)}
                      onChange={(e) => set("agent_runs", Number(e.target.value))}>
                      {Array.from({ length: maxRuns + 1 }, (_, n) => <option key={n} value={n}>{n} {n === 1 ? "run" : "runs"}</option>)}
                    </Select>
                  </Field>
                )}
              </div>
            </div>
          </fieldset>

          {problem && (
            <p id={id("problem")} role={empty ? undefined : "alert"} className={cn("text-sm", empty ? "text-muted-foreground" : "text-warning")}>{problem}</p>
          )}

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" loading={submitting} disabled={disabled || !!problem}>
              {!submitting && <Search />} {running ? "Searching…" : "Find openings"}
            </Button>
            <Button type="button" variant="outline" onClick={onUsePreferences} disabled={disabled || !hasPreferences}>
              <Wand2 /> Use my preferences
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}
