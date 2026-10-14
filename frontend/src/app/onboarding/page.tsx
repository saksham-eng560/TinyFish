"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import useSWR, { useSWRConfig } from "swr";
import { Check, LogOut, Sparkles } from "lucide-react";
import { Logo } from "@/components/brand";
import { AnswersStep } from "@/components/onboarding/answers-step";
import { ApplyStep } from "@/components/onboarding/apply-step";
import { ConnectStep } from "@/components/onboarding/connect-step";
import { DoneStep } from "@/components/onboarding/done-step";
import { ProfilesStep } from "@/components/onboarding/profiles-step";
import { ResumeStep } from "@/components/onboarding/resume-step";
import { clearAllDrafts } from "@/components/onboarding/shared";
import { TargetsStep } from "@/components/onboarding/targets-step";
import { WelcomeStep } from "@/components/onboarding/welcome-step";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";
import { ApiError, fetcher, patch, post } from "@/lib/api-client";
import type { OnboardingState } from "@/lib/types";
import { cn } from "@/lib/utils";

const LAST = 8;

function browserTimeZone() {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; } catch { return "UTC"; }
}

export default function OnboardingPage() {
  const router = useRouter();
  const toast = useToast();
  const { mutate: mutateGlobal } = useSWRConfig();
  const { data: state, error, mutate, isLoading } = useSWR<OnboardingState>("/users/me/onboarding", fetcher, { revalidateOnFocus: false });
  const [current, setCurrent] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [loadingSample, setLoadingSample] = useState(false);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (state && current === null) setCurrent(state.completed ? LAST : state.step);
  }, [state, current]);

  // Each step starts at the top, with focus on its content (keyboard and screen-reader friendly)
  useEffect(() => {
    if (current === null) return;
    window.scrollTo({ top: 0 });
    mainRef.current?.focus({ preventScroll: true });
  }, [current]);

  const go = (step: number) => setCurrent(Math.min(Math.max(step, 1), LAST));

  /** Save one step; errors propagate to the step, which shows them next to the form. */
  const save = async (step: number, data: unknown = {}, skip = false) => {
    setSaving(true);
    try {
      const next = await patch<OnboardingState>("/users/me/onboarding", { step, data, skip });
      await mutate(next, { revalidate: false });
      mutateGlobal("/auth/me");
      go(step + 1);
    } finally {
      setSaving(false);
    }
  };

  const skip = (step: number) => async () => {
    try {
      await save(step, {}, true);
    } catch (err) {
      toast({ title: "Couldn't skip this step", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    }
  };

  const loadSample = async () => {
    setLoadingSample(true);
    try {
      const next = await post<OnboardingState>("/users/me/onboarding/sample");
      clearAllDrafts();
      await mutate(next, { revalidate: false });
      mutateGlobal((key) => typeof key === "string" && (key.startsWith("/resumes") || key === "/auth/me"));
      go(LAST);
    } catch (err) {
      toast({ title: "Couldn't load the sample profile", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setLoadingSample(false);
    }
  };

  const logout = async () => {
    await post("/auth/logout");
    router.replace("/login");
  };

  const step = current ?? 1;
  const reached = state ? (state.completed ? LAST : Math.max(state.step, step)) : step;

  return (
    <div className="min-h-screen bg-background">
      <header className="sticky top-0 z-20 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-5xl items-center gap-3 px-4">
          <Logo href="/onboarding" />
          <div className="flex-1" />
          {state?.demo_mode && step < LAST && (
            <Button variant="outline" size="sm" onClick={loadSample} loading={loadingSample}><Sparkles /> Load sample profile</Button>
          )}
          <ThemeToggle />
          <Button variant="ghost" size="icon" onClick={logout} aria-label="Sign out"><LogOut /></Button>
        </div>
        <div className="mx-auto max-w-5xl px-4 pb-3">
          <div className="mb-2 flex items-baseline justify-between text-xs">
            <span className="font-semibold text-primary">Step {step} of {LAST}</span>
            <span className="text-muted-foreground">{state?.steps[step - 1]?.title}</span>
          </div>
          <Progress value={(step / LAST) * 100} aria-label={`Step ${step} of ${LAST}`} />
          {state && (
            <ol className="mt-3 hidden gap-1 md:flex" aria-label="Steps">
              {state.steps.map((s) => {
                const enabled = s.id <= reached && !saving;
                return (
                  <li key={s.id} className="flex-1">
                    <button type="button" disabled={!enabled} onClick={() => go(s.id)} aria-current={s.id === step ? "step" : undefined}
                      className={cn("flex w-full items-center gap-1.5 rounded-md px-2 py-1 text-left text-[11px] transition-colors",
                        s.id === step ? "bg-secondary font-semibold text-primary" : enabled ? "text-foreground hover:bg-accent" : "text-muted-foreground")}>
                      <span className={cn("flex h-4 w-4 shrink-0 items-center justify-center rounded-full border text-[9px]",
                        s.done ? "border-primary bg-primary text-primary-foreground" : "border-input")}>
                        {s.done ? <Check className="h-2.5 w-2.5" aria-hidden /> : s.id}
                      </span>
                      <span className="truncate">{s.title}</span>
                      {!s.required && <span className="sr-only">(optional)</span>}
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </header>

      <main ref={mainRef} tabIndex={-1} className="mx-auto max-w-5xl px-4 py-8 outline-none sm:py-10">
        {error && !state ? (
          <div role="alert" className="rounded-xl border p-6">
            <p className="font-semibold">We couldn’t load your setup.</p>
            <p className="mt-1 text-sm text-muted-foreground">{error instanceof ApiError ? error.message : "Check your connection and try again."}</p>
            <Button className="mt-4" onClick={() => mutate()}>Try again</Button>
          </div>
        ) : isLoading || !state || current === null ? (
          <div className="space-y-4" aria-busy="true" aria-label="Loading">
            <Skeleton className="h-10 w-72 rounded-lg" /><Skeleton className="h-5 w-full max-w-xl rounded" />
            <Skeleton className="h-40 w-full max-w-3xl rounded-xl" />
          </div>
        ) : (
          <div key={step} className="animate-fade-in">
            {step === 1 && (
              <WelcomeStep email={state.data.welcome.email} saving={saving} onSave={(d) => save(1, d)}
                initial={{ full_name: state.data.welcome.full_name || "", phone: state.data.welcome.phone || "",
                  location: state.data.welcome.location || "", timezone: state.data.welcome.timezone || browserTimeZone() }} />
            )}
            {step === 2 && <ResumeStep saving={saving} onSave={() => save(2)} onBack={() => go(1)} />}
            {step === 3 && (
              <ProfilesStep initial={{ ...state.data.profiles, profile_links: state.data.profiles.profile_links || [] }}
                saving={saving} onSave={(d) => save(3, d)} onBack={() => go(2)} onSkip={skip(3)} />
            )}
            {step === 4 && (
              <TargetsStep saving={saving} onSave={(d) => save(4, d)} onBack={() => go(3)}
                initial={{ ...state.data.targets, preset: null, target_roles: state.data.targets.target_roles || [],
                  target_locations: state.data.targets.target_locations || [], remote_preference: state.data.targets.remote_preference || "any",
                  focus_skills: state.data.targets.focus_skills || [], avoid_skills: state.data.targets.avoid_skills || [] }} />
            )}
            {step === 5 && <AnswersStep initial={state.data.answers} saving={saving} onSave={(d) => save(5, d)} onBack={() => go(4)} />}
            {step === 6 && (
              <ApplyStep initial={state.data.apply} ceiling={state.daily_cap_ceiling} saving={saving}
                onSave={(d) => save(6, d)} onBack={() => go(5)} />
            )}
            {step === 7 && (
              <ConnectStep connect={state.data.connect} demo={state.demo_mode} saving={saving}
                onSave={(d) => save(7, d)} onBack={() => go(6)} onSkip={skip(7)} />
            )}
            {step === 8 && <DoneStep state={state} onBack={() => go(7)} />}
          </div>
        )}
      </main>
    </div>
  );
}
