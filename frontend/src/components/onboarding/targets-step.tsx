"use client";

import { useState } from "react";
import { Sparkles } from "lucide-react";
import { Field, FormError, RadioCards, StepActions, StepHeader, useDraft, type StepProps } from "@/components/onboarding/shared";
import { TagInput } from "@/components/tag-input";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api-client";
import type { LocationFocus, OnboardingTargets, RemotePreference } from "@/lib/types";
import { cn } from "@/lib/utils";

export interface TargetsData extends OnboardingTargets {
  preset: string | null;
}

/** What each preset fills in here (mirrors backend/app/services/presets.py; the server applies the rest). */
const AI_ROLES = ["AI Engineer Intern", "Generative AI Intern", "Python Developer Intern", "Machine Learning Engineer Intern",
  "Backend Developer Intern", "Software Engineer Intern"];
const AI_FOCUS = ["AI", "LLMs", "Generative AI", "AI agents", "RAG", "Machine Learning", "Python", "FastAPI", "Django", "Flask",
  "PyTorch", "Hugging Face", "LangChain"];
const DEFAULT_INTERN_ROLES = ["Software Engineer Intern", "Software Developer Intern", "Backend Engineer Intern",
  "Frontend Engineer Intern", "Machine Learning Intern"];
const INDIA_FOCUS: LocationFocus = { enabled: true, country: "India", country_share: 90,
  prime_cities: ["Delhi", "New Delhi", "Delhi NCR", "Gurugram", "Gurgaon", "Noida", "Greater Noida", "Faridabad", "Ghaziabad"] };

const internify = (roles: string[]) =>
  roles.length ? roles.map((r) => (/\b(intern|internship|co-?op)\b/i.test(r) ? r : `${r} Intern`)) : DEFAULT_INTERN_ROLES;

const PRESETS: { id: string; label: string; description: string; apply: (f: TargetsData) => TargetsData }[] = [
  { id: "ai-engineer", label: "AI engineer · Python", description: "AI, LLM and Python internships; Java and data-science roles left out",
    apply: (f) => ({ ...f, target_roles: AI_ROLES, focus_skills: AI_FOCUS, avoid_skills: ["Java", "Spring Boot"], internships_only: true }) },
  { id: "internships", label: "Internships", description: "Intern roles from curated lists plus 110 startup boards",
    apply: (f) => ({ ...f, target_roles: internify(f.target_roles), internships_only: true }) },
  { id: "startups", label: "Startups", description: "Adds 110 startup Greenhouse, Ashby and Lever boards", apply: (f) => f },
  { id: "new-grad", label: "New grad", description: "Entry-level full-time roles", apply: (f) => ({ ...f, internships_only: false }) },
  { id: "india-internships", label: "India · Summer 2027", description: "~90% in India, Delhi NCR first",
    apply: (f) => ({ ...f, target_roles: internify(f.target_roles), target_locations: ["Delhi, India", "India"],
      internship_season: "Summer 2027", internships_only: true, location_focus: INDIA_FOCUS }) },
];

const REMOTE: { value: RemotePreference; label: string }[] = [
  { value: "any", label: "Any" }, { value: "remote", label: "Remote" }, { value: "hybrid", label: "Hybrid" }, { value: "onsite", label: "On-site" },
];

export function TargetsStep({ initial, onSave, onBack, saving }: StepProps<TargetsData>) {
  const [form, setForm, clear] = useDraft<TargetsData>("targets", initial);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const focus: LocationFocus = form.location_focus ?? { enabled: false, country: "", prime_cities: [], country_share: 90 };
  const set = <K extends keyof TargetsData>(key: K, value: TargetsData[K]) => setForm({ ...form, [key]: value });
  const setFocus = (patch: Partial<LocationFocus>) => set("location_focus", { ...focus, ...patch });

  const errors = {
    roles: form.target_roles.length ? null : "Add at least one role, e.g. Software Engineer Intern",
    locations: form.target_locations.length ? null : "Add at least one location, e.g. Bengaluru, India or Remote",
    share: focus.enabled && !(focus.country_share >= 0 && focus.country_share <= 100) ? "Use a number from 0 to 100" : null,
    country: focus.enabled && !focus.country.trim() ? "Which country?" : null,
  };

  const choosePreset = (id: string) => {
    if (form.preset === id) return set("preset", null);
    const preset = PRESETS.find((p) => p.id === id);
    if (preset) setForm({ ...preset.apply(form), preset: id });
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.values(errors).some(Boolean)) return;
    setError(null);
    try {
      await onSave({ ...form, location_focus: { ...focus, country: focus.country.trim() } });
      clear();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <StepHeader title="What you're looking for"
        intro="Pick a starting point, then make it yours. Scans only bring back jobs that match these."
        why="These are hard filters: a posting outside your roles, locations or season never reaches your deck." />
      <FormError message={error} />
      <div className="max-w-3xl space-y-6">
        <div>
          <p className="mb-2 text-sm font-semibold">Start from a preset <span className="font-normal text-muted-foreground">(optional)</span></p>
          <div className="flex flex-wrap gap-2" role="group" aria-label="Presets">
            {PRESETS.map((p) => (
              <button key={p.id} type="button" aria-pressed={form.preset === p.id} title={p.description} onClick={() => choosePreset(p.id)}
                className={cn("inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors hover:border-primary/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  form.preset === p.id ? "border-primary bg-primary text-primary-foreground" : "bg-card")}>
                {p.id === "ai-engineer" && <Sparkles className="h-3.5 w-3.5" aria-hidden />}{p.label}
              </button>
            ))}
          </div>
        </div>

        <Field label="Target roles" required error={submitted ? errors.roles : null} hint="Press Enter or comma after each role">
          {(id) => <TagInput id={id} aria-label="Target roles" value={form.target_roles} onChange={(v) => set("target_roles", v)} placeholder="Software Engineer Intern" />}
        </Field>
        <Field label="Locations" required error={submitted ? errors.locations : null} hint="Cities, countries or Remote">
          {(id) => <TagInput id={id} aria-label="Locations" value={form.target_locations} onChange={(v) => set("target_locations", v)} placeholder="Bengaluru, India" />}
        </Field>
        <div>
          <p className="mb-2 text-sm font-semibold">Remote or on-site</p>
          <RadioCards name="remote" label="Remote preference" columns={4} value={form.remote_preference}
            onChange={(v) => set("remote_preference", v)} options={REMOTE} />
        </div>

        <div className="rounded-xl border p-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="text-sm font-semibold">Mostly one country</p>
              <p className="text-xs text-muted-foreground">Keep most new postings in one country, with your favourite cities first.</p>
            </div>
            <Switch checked={!!focus.enabled} onCheckedChange={(v) => setFocus({ enabled: v })} label="Mostly one country" />
          </div>
          {focus.enabled && (
            <div className="mt-4 grid gap-4 sm:grid-cols-[1fr_8rem]">
              <Field label="Country" error={submitted ? errors.country : null}>
                {(id, d) => <Input id={id} aria-describedby={d} value={focus.country} onChange={(e) => setFocus({ country: e.target.value })} />}
              </Field>
              <Field label="Share %" error={submitted ? errors.share : null}>
                {(id, d) => <Input id={id} aria-describedby={d} type="number" min={0} max={100} value={focus.country_share}
                  onChange={(e) => setFocus({ country_share: Number(e.target.value) })} />}
              </Field>
              <Field label="Cities to show first" className="sm:col-span-2">
                {(id) => <TagInput id={id} aria-label="Cities to show first" value={focus.prime_cities} onChange={(v) => setFocus({ prime_cities: v })} placeholder="Bengaluru" />}
              </Field>
            </div>
          )}
        </div>

        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Internship season" hint='e.g. "Summer 2027"; empty = any season'>
            {(id, d) => <Input id={id} aria-describedby={d} value={form.internship_season || ""} placeholder="Summer 2027"
              onChange={(e) => set("internship_season", e.target.value || null)} />}
          </Field>
          <div className="flex items-end justify-between gap-4 rounded-lg border px-3 py-2.5">
            <div><p className="text-sm font-semibold">Internships only</p><p className="text-xs text-muted-foreground">No full-time or contract roles</p></div>
            <Switch checked={form.internships_only ?? true} onCheckedChange={(v) => set("internships_only", v)} label="Internships only" />
          </div>
          <Field label="Year of study">
            {(id, d) => (
              <Select id={id} aria-describedby={d} value={form.year_of_study ?? ""} onChange={(e) => set("year_of_study", e.target.value ? Number(e.target.value) : null)}>
                <option value="">Any year</option>
                {[1, 2, 3, 4, 5].map((y) => <option key={y} value={y}>{["First", "Second", "Third", "Fourth", "Fifth"][y - 1]} year</option>)}
              </Select>
            )}
          </Field>
          <Field label="Graduation year" hint="Empty = read from your resume">
            {(id, d) => <Input id={id} aria-describedby={d} type="number" min={2000} max={2100} value={form.graduation_year ?? ""}
              onChange={(e) => set("graduation_year", e.target.value ? Number(e.target.value) : null)} />}
          </Field>
          <Field label="Skills to focus on" hint="A posting must mention at least one">
            {(id) => <TagInput id={id} aria-label="Skills to focus on" value={form.focus_skills} onChange={(v) => set("focus_skills", v)} placeholder="Python" />}
          </Field>
          <Field label="Skills to avoid" hint="Roles built on these are skipped">
            {(id) => <TagInput id={id} aria-label="Skills to avoid" value={form.avoid_skills} onChange={(v) => set("avoid_skills", v)} placeholder="Java" />}
          </Field>
          <Field label="Expected stipend (per month)" hint="Only used when a form asks">
            {(id, d) => <Input id={id} aria-describedby={d} type="number" min={0} value={form.expected_stipend ?? ""} placeholder="15000"
              onChange={(e) => set("expected_stipend", e.target.value ? Number(e.target.value) : null)} />}
          </Field>
        </div>
      </div>
      <StepActions onBack={onBack} saving={saving} />
    </form>
  );
}
