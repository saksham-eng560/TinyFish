"use client";

import { useState } from "react";
import { cleanProfiles, ProfileLinksEditor, profileErrors } from "@/components/onboarding/profile-links";
import { FormError, StepActions, StepHeader, useDraft, type StepProps } from "@/components/onboarding/shared";
import { ApiError } from "@/lib/api-client";
import type { OnboardingProfiles } from "@/lib/types";

/** Step 3 (optional): LinkedIn, GitHub, portfolio and other profiles, prefilled from the resume when found. */
export function ProfilesStep({ initial, onSave, onBack, onSkip, saving }: StepProps<OnboardingProfiles>) {
  const [form, setForm, clear] = useDraft<OnboardingProfiles>("profiles", initial);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.values(profileErrors(form)).some(Boolean)) return;
    setError(null);
    try {
      await onSave(cleanProfiles(form));
      clear();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <StepHeader title="Profiles & links"
        intro="Add the profiles recruiters look at. We filled in what we found on your resume."
        why="Forms ask for LinkedIn and GitHub constantly; with them saved, HireFlow fills those fields instead of stopping to ask you." />
      <FormError message={error} />
      <div className="max-w-2xl"><ProfileLinksEditor value={form} onChange={setForm} showErrors={submitted} /></div>
      <StepActions onBack={onBack} onSkip={onSkip} saving={saving} />
    </form>
  );
}
