"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ArrowRight, Link2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { User } from "@/lib/types";

const DISMISS_KEY = "hireflow:dismiss:links-banner";

/** Shown after onboarding when a recommended link (LinkedIn, GitHub) was skipped. Dismissible. */
export function LinksBanner({ me }: { me: User }) {
  const [dismissed, setDismissed] = useState(true);
  const missing = [!me.linkedin_url && "LinkedIn", !me.github_url && "GitHub"].filter(Boolean) as string[];

  useEffect(() => {
    try {
      setDismissed(window.localStorage.getItem(DISMISS_KEY) === "1");
    } catch {
      setDismissed(false);
    }
  }, []);

  if (dismissed || !me.onboarding_completed_at || missing.length === 0) return null;

  const dismiss = () => {
    setDismissed(true);
    try { window.localStorage.setItem(DISMISS_KEY, "1"); } catch { /* storage unavailable: hide for this visit only */ }
  };

  return (
    <div role="region" aria-label="Recommended profile links"
      className="mb-6 flex flex-col gap-3 rounded-xl border border-primary/30 bg-secondary p-4 text-secondary-foreground sm:flex-row sm:items-center">
      <Link2 className="h-5 w-5 shrink-0 text-primary" aria-hidden />
      <p className="flex-1 text-sm">
        <span className="font-semibold">Add your {missing.join(" and ")}.</span>{" "}
        Many internship forms ask for {missing.length > 1 ? "them" : "it"}, and HireFlow can only fill in links you&apos;ve saved.
      </p>
      <div className="flex items-center gap-1">
        <Link href="/dashboard/settings?tab=profile" className="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-sm font-semibold text-primary hover:underline">
          Add {missing.length > 1 ? "links" : missing[0]} <ArrowRight className="h-4 w-4" aria-hidden />
        </Link>
        <Button variant="ghost" size="icon" onClick={dismiss} aria-label="Dismiss"><X /></Button>
      </div>
    </div>
  );
}
