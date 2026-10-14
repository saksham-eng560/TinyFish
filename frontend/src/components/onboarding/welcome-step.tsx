"use client";

import { useMemo, useState } from "react";
import { Field, FormError, StepActions, StepHeader, useDraft, type StepProps } from "@/components/onboarding/shared";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { ApiError } from "@/lib/api-client";

export interface WelcomeData {
  full_name: string;
  phone: string;
  location: string;
  timezone: string;
}

const PHONE = /^\+?[0-9 ()\-.]{7,25}$/;
const COMMON_ZONES = ["Asia/Kolkata", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "Europe/London", "Europe/Berlin", "Asia/Singapore", "Asia/Dubai", "Australia/Sydney", "UTC"];

function timeZones(): string[] {
  try {
    const list = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone");
    if (list?.length) return list;
  } catch { /* older browsers */ }
  return COMMON_ZONES;
}

export function WelcomeStep({ email, initial, onSave, saving }: StepProps<WelcomeData> & { email: string }) {
  const [form, setForm, clear] = useDraft<WelcomeData>("welcome", initial);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const zones = useMemo(() => {
    const all = timeZones();
    return form.timezone && !all.includes(form.timezone) ? [form.timezone, ...all] : all;
  }, [form.timezone]);
  const nameError = !form.full_name.trim() ? "Your name is required" : null;
  const phoneError = form.phone.trim() && (!PHONE.test(form.phone.trim()) || form.phone.replace(/\D/g, "").length < 7)
    ? "Enter a phone number with at least 7 digits, e.g. +91 98765 43210" : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (nameError || phoneError) return;
    setError(null);
    try {
      await onSave({ full_name: form.full_name.trim(), phone: form.phone.trim(), location: form.location.trim(), timezone: form.timezone });
      clear();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <StepHeader title="Welcome to HireFlow"
        intro="Let's set up your internship agent. It takes about three minutes, and you can change everything later in Settings."
        why="Application forms ask for these on every submission; we fill them from here so you never type them again." />
      <FormError message={error} />
      <div className="grid max-w-2xl gap-5 sm:grid-cols-2">
        <Field label="Full name" required error={submitted ? nameError : null} className="sm:col-span-2">
          {(id, d) => <Input id={id} aria-describedby={d} autoComplete="name" value={form.full_name} aria-invalid={submitted && !!nameError}
            onChange={(e) => setForm({ ...form, full_name: e.target.value })} />}
        </Field>
        <Field label="Email" hint="From your account">
          {(id, d) => <Input id={id} aria-describedby={d} value={email} readOnly disabled />}
        </Field>
        <Field label="Phone" error={submitted ? phoneError : null}>
          {(id, d) => <Input id={id} aria-describedby={d} type="tel" autoComplete="tel" placeholder="+91 98765 43210" value={form.phone}
            onChange={(e) => setForm({ ...form, phone: e.target.value })} />}
        </Field>
        <Field label="Current city" hint="e.g. Bengaluru, India">
          {(id, d) => <Input id={id} aria-describedby={d} autoComplete="address-level2" value={form.location}
            onChange={(e) => setForm({ ...form, location: e.target.value })} />}
        </Field>
        <Field label="Time zone" hint="For interview times and your daily digest">
          {(id, d) => (
            <Select id={id} aria-describedby={d} value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
              {zones.map((z) => <option key={z} value={z}>{z.replaceAll("_", " ")}</option>)}
            </Select>
          )}
        </Field>
      </div>
      <StepActions saving={saving} />
    </form>
  );
}
