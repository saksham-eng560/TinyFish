"use client";

import { useState } from "react";
import { Field, FormError, RadioCards, StepActions, StepHeader, useDraft, type StepProps } from "@/components/onboarding/shared";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ApiError } from "@/lib/api-client";
import type { AnswerKey } from "@/lib/types";

export type AnswersData = Record<AnswerKey, string | null>;

const YES_NO = [{ value: "Yes", label: "Yes" }, { value: "No", label: "No" }] as const;
const YES_NO_PREFER = [...YES_NO, { value: "Prefer not to say", label: "Prefer not to say" }] as const;
const PREFER = "Prefer not to say";
const EEO: { key: AnswerKey; label: string; options: string[] }[] = [
  { key: "gender", label: "Gender", options: [PREFER, "Woman", "Man", "Non-binary"] },
  { key: "race_ethnicity", label: "Race / ethnicity", options: [PREFER, "Asian", "Black or African American", "Hispanic or Latino", "White", "Two or more races", "Other"] },
  { key: "hispanic_latino", label: "Hispanic or Latino?", options: [PREFER, "Yes", "No"] },
  { key: "veteran_status", label: "Veteran status", options: [PREFER, "I am not a protected veteran", "I am a protected veteran"] },
  { key: "disability_status", label: "Disability status", options: [PREFER, "No, I don't have a disability", "Yes, I have a disability"] },
];

/** Step 5: the questions only you can answer. Each becomes a saved answer; none is ever guessed. */
export function AnswersStep({ initial, onSave, onBack, saving }: StepProps<AnswersData>) {
  const [form, setForm, clear] = useDraft<AnswersData>("answers", initial);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const set = (key: AnswerKey, value: string | null) => setForm({ ...form, [key]: value || null });
  const months = form.availability_months ? Number(form.availability_months) : null;
  const errors = {
    work_authorization: form.work_authorization ? null : "Choose Yes or No",
    availability_months: months !== null && !(Number.isInteger(months) && months >= 1 && months <= 24) ? "Enter 1 to 24 months" : null,
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.values(errors).some(Boolean)) return;
    setError(null);
    try {
      await onSave(form);
      clear();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <StepHeader title="Your answers"
        intro="Application forms ask these, and only you can answer them."
        why="Forms ask this; we never guess it for you. A form that asks something you haven't answered waits for you instead." />
      <FormError message={error} />
      <div className="max-w-3xl space-y-6">
        <Field label="Are you legally authorized to work in the countries you're applying to?" required error={submitted ? errors.work_authorization : null}>
          {() => <RadioCards name="work_authorization" label="Work authorization" columns={2} value={form.work_authorization as "Yes" | "No" | null}
            onChange={(v) => set("work_authorization", v)} options={[...YES_NO]} />}
        </Field>
        <Field label="Are you 18 or older?">
          {() => <RadioCards name="over_18" label="18 or older" columns={2} value={form.over_18 as "Yes" | "No" | null}
            onChange={(v) => set("over_18", v)} options={[...YES_NO]} />}
        </Field>
        <Field label="Will you now or in the future need visa sponsorship?">
          {() => <RadioCards name="requires_sponsorship" label="Visa sponsorship" columns={3} value={form.requires_sponsorship as "Yes" | "No" | typeof PREFER | null}
            onChange={(v) => set("requires_sponsorship", v)} options={[...YES_NO_PREFER]} />}
        </Field>
        <Field label="Are you willing to relocate?">
          {() => <RadioCards name="willing_to_relocate" label="Relocation" columns={3} value={form.willing_to_relocate as "Yes" | "No" | typeof PREFER | null}
            onChange={(v) => set("willing_to_relocate", v)} options={[...YES_NO_PREFER]} />}
        </Field>
        <div className="grid gap-5 sm:grid-cols-3">
          <Field label="Earliest start date">
            {(id, d) => <Input id={id} aria-describedby={d} type="date" value={form.earliest_start_date || ""} onChange={(e) => set("earliest_start_date", e.target.value)} />}
          </Field>
          <Field label="Months available" hint="Internship length you can do" error={submitted ? errors.availability_months : null}>
            {(id, d) => <Input id={id} aria-describedby={d} type="number" min={1} max={24} value={form.availability_months || ""}
              onChange={(e) => set("availability_months", e.target.value)} />}
          </Field>
          <Field label="Notice period" hint='e.g. "2 weeks"'>
            {(id, d) => <Input id={id} aria-describedby={d} value={form.notice_period || ""} onChange={(e) => set("notice_period", e.target.value)} />}
          </Field>
        </div>
        <fieldset className="rounded-xl border p-4">
          <legend className="px-1 text-sm font-semibold">Voluntary self-identification</legend>
          <p className="mb-4 text-xs text-muted-foreground">US employers ask these (EEO). Answering is always optional, and “Prefer not to say” is a complete answer.</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Pronouns">
              {(id, d) => (
                <Select id={id} aria-describedby={d} value={form.pronouns || ""} onChange={(e) => set("pronouns", e.target.value)}>
                  <option value="">Not set</option>
                  {[PREFER, "she/her", "he/him", "they/them"].map((o) => <option key={o} value={o}>{o}</option>)}
                </Select>
              )}
            </Field>
            {EEO.map(({ key, label, options }) => (
              <Field key={key} label={label}>
                {(id, d) => (
                  <Select id={id} aria-describedby={d} value={form[key] || ""} onChange={(e) => set(key, e.target.value)}>
                    <option value="">Not set</option>
                    {options.map((o) => <option key={o} value={o}>{o}</option>)}
                  </Select>
                )}
              </Field>
            ))}
          </div>
        </fieldset>
      </div>
      <StepActions onBack={onBack} saving={saving} />
    </form>
  );
}
