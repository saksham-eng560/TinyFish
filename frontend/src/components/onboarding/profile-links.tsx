"use client";

import { BriefcaseBusiness, CodeXml, Globe, Plus, Trash2 } from "lucide-react";
import { Field, linkError } from "@/components/onboarding/shared";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import type { OnboardingProfiles, ProfileLink } from "@/lib/types";

export const PROFILE_LABELS = ["LeetCode", "Codeforces", "Kaggle", "HackerRank", "CodeChef", "Other"] as const;
const MAX_LINKS = 10;

/** Errors for every link (client-side check; the server validates again). */
export function profileErrors(value: OnboardingProfiles): Record<string, string | null> {
  const errors: Record<string, string | null> = {
    linkedin_url: linkError(value.linkedin_url, "linkedin.com"),
    github_url: linkError(value.github_url, "github.com"),
    portfolio_url: linkError(value.portfolio_url),
  };
  value.profile_links.forEach((link, i) => {
    errors[`link-${i}`] = !link.label.trim() ? "Give this link a name" : !link.url.trim() ? "Add the link" : linkError(link.url);
  });
  return errors;
}

/** LinkedIn, GitHub, portfolio and any other profiles (onboarding step 3 and Settings → Profile & links). */
export function ProfileLinksEditor({ value, onChange, showErrors }: {
  value: OnboardingProfiles;
  onChange: (next: OnboardingProfiles) => void;
  showErrors: boolean;
}) {
  const errors = showErrors ? profileErrors(value) : {};
  const setLink = (i: number, patch: Partial<ProfileLink>) =>
    onChange({ ...value, profile_links: value.profile_links.map((l, j) => (j === i ? { ...l, ...patch } : l)) });
  const icon = (Icon: typeof Globe) => <Icon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />;
  return (
    <div className="space-y-5">
      <div className="grid gap-5 sm:grid-cols-2">
        <Field label="LinkedIn" hint="Recommended: most forms ask for it" error={errors.linkedin_url}>
          {(id, describedBy) => (
            <div className="relative">{icon(BriefcaseBusiness)}
              <Input id={id} aria-describedby={describedBy} className="pl-9" inputMode="url" autoComplete="url" placeholder="linkedin.com/in/you"
                value={value.linkedin_url || ""} onChange={(e) => onChange({ ...value, linkedin_url: e.target.value })} />
            </div>
          )}
        </Field>
        <Field label="GitHub" hint="Recommended for engineering roles" error={errors.github_url}>
          {(id, describedBy) => (
            <div className="relative">{icon(CodeXml)}
              <Input id={id} aria-describedby={describedBy} className="pl-9" inputMode="url" placeholder="github.com/you"
                value={value.github_url || ""} onChange={(e) => onChange({ ...value, github_url: e.target.value })} />
            </div>
          )}
        </Field>
        <Field label="Portfolio or website" error={errors.portfolio_url} className="sm:col-span-2">
          {(id, describedBy) => (
            <div className="relative">{icon(Globe)}
              <Input id={id} aria-describedby={describedBy} className="pl-9" inputMode="url" placeholder="you.dev"
                value={value.portfolio_url || ""} onChange={(e) => onChange({ ...value, portfolio_url: e.target.value })} />
            </div>
          )}
        </Field>
      </div>

      <fieldset className="space-y-3">
        <legend className="text-sm font-semibold">Other profiles</legend>
        <p className="text-xs text-muted-foreground">LeetCode, Codeforces, Kaggle… used when a form asks for them.</p>
        {value.profile_links.map((link, i) => {
          const preset = (PROFILE_LABELS as readonly string[]).includes(link.label) ? link.label : "Other";
          return (
            <div key={i} className="grid gap-2 rounded-lg border p-3 sm:grid-cols-[10rem_minmax(0,1fr)_auto] sm:items-start sm:border-0 sm:p-0">
              <div className="space-y-2">
                <Select aria-label={`Profile ${i + 1} site`} value={preset}
                  onChange={(e) => setLink(i, { label: e.target.value === "Other" ? "" : e.target.value })}>
                  {PROFILE_LABELS.map((l) => <option key={l} value={l}>{l}</option>)}
                </Select>
                {preset === "Other" && (
                  <Input aria-label={`Profile ${i + 1} name`} placeholder="Name, e.g. Blog" maxLength={40}
                    value={link.label} onChange={(e) => setLink(i, { label: e.target.value })} />
                )}
              </div>
              <div>
                <Input aria-label={`Profile ${i + 1} link`} inputMode="url" placeholder="https://…" value={link.url}
                  aria-invalid={!!errors[`link-${i}`]} onChange={(e) => setLink(i, { url: e.target.value })} />
                {errors[`link-${i}`] && <p role="alert" className="mt-1 text-xs font-medium text-destructive">{errors[`link-${i}`]}</p>}
              </div>
              <Button type="button" variant="ghost" size="icon" aria-label={`Remove profile ${i + 1}`}
                onClick={() => onChange({ ...value, profile_links: value.profile_links.filter((_, j) => j !== i) })}><Trash2 /></Button>
            </div>
          );
        })}
        {value.profile_links.length < MAX_LINKS && (
          <Button type="button" variant="outline" size="sm"
            onClick={() => onChange({ ...value, profile_links: [...value.profile_links, { label: "LeetCode", url: "" }] })}>
            <Plus /> Add a profile
          </Button>
        )}
      </fieldset>
    </div>
  );
}

/** Drop empty rows and trim before saving. */
export function cleanProfiles(value: OnboardingProfiles): OnboardingProfiles {
  return {
    linkedin_url: value.linkedin_url?.trim() || null,
    github_url: value.github_url?.trim() || null,
    portfolio_url: value.portfolio_url?.trim() || null,
    profile_links: value.profile_links.filter((l) => l.label.trim() || l.url.trim()).map((l) => ({ label: l.label.trim(), url: l.url.trim() })),
  };
}
