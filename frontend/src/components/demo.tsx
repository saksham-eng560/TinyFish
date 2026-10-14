"use client";

import useSWR from "swr";
import { FlaskConical } from "lucide-react";
import { fetcher } from "@/lib/api-client";

export interface AuthConfig {
  google_enabled: boolean;
  registration_enabled: boolean;
  demo_mode?: boolean;
}

export function useAuthConfig() {
  return useSWR<AuthConfig>("/auth/config", fetcher, { revalidateOnFocus: false });
}

/** On every dashboard page in the demo, so nobody mistakes it for the real thing. */
export function DemoBanner() {
  const { data: config } = useAuthConfig();
  if (!config?.demo_mode) return null;
  return (
    <div role="note" className="flex items-center gap-2 border-b border-primary/20 bg-secondary px-4 py-2 text-xs text-secondary-foreground sm:text-sm lg:px-8">
      <FlaskConical className="h-4 w-4 shrink-0 text-primary" aria-hidden />
      <p><span className="font-semibold">Demo — nothing is really sent.</span> Applications go to a sample careers site with
        fictional companies, and everything resets every night.</p>
    </div>
  );
}

/** For settings that can't work in the demo (Google, the extension, job-site logins, passwords). */
export function DemoNotice({ children }: { children: React.ReactNode }) {
  const { data: config } = useAuthConfig();
  if (!config?.demo_mode) return null;
  return (
    <p role="note" className="mb-4 flex items-start gap-2 rounded-lg bg-secondary px-3 py-2 text-sm text-secondary-foreground">
      <FlaskConical className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden /> <span>{children}</span>
    </p>
  );
}
