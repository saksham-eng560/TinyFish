"use client";

import { useState } from "react";
import { FormError, RadioCards, StepActions, StepHeader, useDraft, type StepProps } from "@/components/onboarding/shared";
import { Slider } from "@/components/ui/slider";
import { Switch } from "@/components/ui/switch";
import { ApiError } from "@/lib/api-client";
import type { OnboardingApply } from "@/lib/types";

const KEEP_STEPS = [
  "We tailor your resume for the job (only rewording and reordering what's already on it).",
  "We write a cover letter, if they're on.",
  "We open the application form in a real browser and fill every field we're sure about.",
  "Anything we're not sure about (eligibility, visa, a question you haven't answered) waits for you in Ready to submit.",
  "Then it's submitted, by you, or automatically if you turn that on.",
];

/** Step 6: review mode, auto-submit (off by default), daily cap, resume strategy, cover letters. */
export function ApplyStep({ initial, ceiling, onSave, onBack, saving }: StepProps<OnboardingApply> & { ceiling: number }) {
  const [form, setForm, clear] = useDraft<OnboardingApply>("apply", initial);
  const [error, setError] = useState<string | null>(null);
  const set = <K extends keyof OnboardingApply>(key: K, value: OnboardingApply[K]) => setForm({ ...form, [key]: value });
  const cap = Math.min(Math.max(form.max_applications_per_day || 10, 1), ceiling);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await onSave({ ...form, max_applications_per_day: cap });
      clear();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <StepHeader title="How HireFlow applies for you"
        intro="Safe defaults are already picked. Nothing is ever sent for a job you didn't keep."
        why="You're in control: start with Swipe Review and manual submits, and turn on more automation once you trust it." />
      <FormError message={error} />
      <div className="max-w-3xl space-y-7">
        <div>
          <p className="mb-2 text-sm font-semibold">Review mode</p>
          <RadioCards name="review_mode" label="Review mode" value={form.review_mode} onChange={(v) => set("review_mode", v)} options={[
            { value: "swipe", label: "Swipe Review (recommended)", description: "Every matching job waits for your keep or skip." },
            { value: "auto", label: "Automatic threshold", description: "Jobs under your match threshold are skipped for you." },
          ]} />
        </div>

        <div className="flex items-start justify-between gap-6 rounded-xl border p-4">
          <div>
            <p className="text-sm font-semibold">Submit automatically</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Off: filled forms wait in Ready to submit for your click. On: kept jobs are sent as soon as the form is filled
              (questions only you can answer still wait for you).
            </p>
          </div>
          <Switch checked={form.auto_submit_kept} onCheckedChange={(v) => set("auto_submit_kept", v)} label="Submit automatically" />
        </div>

        <div>
          <div className="mb-3 flex items-baseline justify-between">
            <p className="text-sm font-semibold">Applications per day</p>
            <span className="font-mono text-2xl font-bold tabular-nums text-primary" aria-live="polite">{cap}</span>
          </div>
          <Slider aria-label="Applications per day" min={1} max={ceiling} step={1} value={[cap]} onValueChange={([v]) => set("max_applications_per_day", v)} />
          <p className="mt-2 text-xs text-muted-foreground">At most {ceiling} a day, however you set it.</p>
        </div>

        <div>
          <p className="mb-2 text-sm font-semibold">Resume for each job</p>
          <RadioCards name="resume_strategy" label="Resume strategy" columns={3} value={form.resume_strategy} onChange={(v) => set("resume_strategy", v)} options={[
            { value: "original", label: "Original", description: "Your file, untouched" },
            { value: "light", label: "Light", description: "Reordered to put the most relevant first" },
            { value: "full", label: "Full AI rewrite", description: "Reworded for the job, facts unchanged" },
          ]} />
        </div>

        <div className="flex items-center justify-between gap-6 rounded-xl border p-4">
          <div>
            <p className="text-sm font-semibold">Cover letters</p>
            <p className="mt-1 text-xs text-muted-foreground">A short letter for forms that take one.</p>
          </div>
          <Switch checked={form.cover_letter_enabled} onCheckedChange={(v) => set("cover_letter_enabled", v)} label="Cover letters" />
        </div>

        <div className="rounded-xl bg-secondary p-4">
          <p className="text-sm font-semibold text-secondary-foreground">What happens when you keep a job</p>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-sm text-secondary-foreground">
            {KEEP_STEPS.map((s) => <li key={s}>{s}</li>)}
          </ol>
        </div>
      </div>
      <StepActions onBack={onBack} saving={saving} />
    </form>
  );
}
