"use client";

import { useState } from "react";
import { CalendarCheck, CircleCheck, ExternalLink, Mail, Puzzle, TriangleAlert } from "lucide-react";
import { FormError, StepActions, StepHeader, useDraft } from "@/components/onboarding/shared";
import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api-client";
import type { OnboardingState } from "@/lib/types";

export interface ConnectData {
  linkedin_consent: boolean;
  internshala_consent: boolean;
}

const EXTENSION_URL = "https://github.com/saksham-eng560/HireFlow/tree/main/extension";

const CONSENTS: { key: keyof ConnectData; name: string; warning: string }[] = [
  { key: "linkedin_consent", name: "LinkedIn",
    warning: "LinkedIn's terms don't allow automated access. Searching and applying with your session may get your account restricted." },
  { key: "internshala_consent", name: "Internshala",
    warning: "Internshala's terms don't allow automated access. The bot applies with your own login, at most your daily limit." },
];

/** Step 7 (optional): Gmail + Calendar, the Chrome extension, and opt-in sources (each with a recorded consent). */
export function ConnectStep({ connect, demo, onSave, onBack, onSkip, saving }: {
  connect: OnboardingState["data"]["connect"];
  demo: boolean;
  onSave: (data: ConnectData) => Promise<void>;
  onBack: () => void;
  onSkip: () => void;
  saving: boolean;
}) {
  const [form, setForm, clear] = useDraft<ConnectData>("connect", { linkedin_consent: connect.linkedin_consent, internshala_consent: connect.internshala_consent });
  const [error, setError] = useState<string | null>(null);
  const [googleNote, setGoogleNote] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);

  const connectGoogle = async () => {
    setConnecting(true);
    setGoogleNote(null);
    try {
      const { url } = await api<{ url: string }>("/auth/google/connect?next=/onboarding");
      window.location.href = url;
    } catch (err) {
      setGoogleNote(err instanceof ApiError && err.status === 501 ? "Google sign-in isn't set up on this server yet (see the README)." :
        err instanceof ApiError ? err.message : String(err));
      setConnecting(false);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
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
      <StepHeader title="Connect (optional)"
        intro="Each of these adds something. You can connect them later from Settings → Integrations."
        why="Nothing here is needed to start. Sources that don't allow automation stay off unless you agree to their risks below." />
      <FormError message={error} />
      <div className="max-w-3xl space-y-4">
        <section className="flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center">
          <div className="flex gap-2 text-primary" aria-hidden><Mail className="h-5 w-5" /><CalendarCheck className="h-5 w-5" /></div>
          <div className="flex-1">
            <p className="text-sm font-semibold">Gmail & Google Calendar</p>
            <p className="text-xs text-muted-foreground">Tracks replies from recruiters, drafts answers for you to send, and puts interviews on your calendar.</p>
            {googleNote && <p role="status" className="mt-1 text-xs font-medium text-destructive">{googleNote}</p>}
          </div>
          {connect.google_connected ? (
            <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-success"><CircleCheck className="h-4 w-4" aria-hidden /> Connected</span>
          ) : demo ? (
            <span className="text-xs text-muted-foreground">Not available in the demo</span>
          ) : (
            <Button type="button" variant="outline" loading={connecting} onClick={connectGoogle}>Connect Google</Button>
          )}
        </section>

        <section className="flex flex-col gap-3 rounded-xl border p-4 sm:flex-row sm:items-center">
          <Puzzle className="h-5 w-5 text-primary" aria-hidden />
          <div className="flex-1">
            <p className="text-sm font-semibold">Chrome extension</p>
            <p className="text-xs text-muted-foreground">Syncs your LinkedIn and Internshala logins to HireFlow, encrypted, so the agent can apply as you.</p>
          </div>
          <a href={EXTENSION_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline">
            Install it <ExternalLink className="h-3.5 w-3.5" aria-hidden />
          </a>
        </section>

        {CONSENTS.map(({ key, name, warning }) => (
          <section key={key} className="rounded-xl border p-4">
            <p className="text-sm font-semibold">{name}</p>
            <p className="mt-1 flex items-start gap-2 text-xs text-muted-foreground"><TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" aria-hidden />{warning}</p>
            {demo ? (
              <p className="mt-3 text-xs text-muted-foreground">Not available in the demo.</p>
            ) : (
              <label className="mt-3 flex cursor-pointer items-start gap-2 text-sm">
                <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[hsl(var(--primary))]" checked={form[key]}
                  onChange={(e) => setForm({ ...form, [key]: e.target.checked })} />
                <span>I understand the risk and want HireFlow to use {name} for me.</span>
              </label>
            )}
          </section>
        ))}
      </div>
      <StepActions onBack={onBack} onSkip={onSkip} saving={saving} />
    </form>
  );
}
