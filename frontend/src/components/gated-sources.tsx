"use client";

import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { CircleCheck, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/components/ui/toast";
import { ApiError, fetcher, put } from "@/lib/api-client";

interface GatedSource {
  platform: string;
  name: string;
  warning: string;
  consented: boolean;
  enabled: boolean;
  available: boolean;
}

function SourceRow({ source, onChange }: { source: GatedSource; onChange: () => Promise<void> }) {
  const toast = useToast();
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const setEnabled = async (enabled: boolean) => {
    setBusy(true);
    try {
      await put(`/users/me/sources/${source.platform}`, { enabled, agree: enabled && agree });
      await onChange();
      setAgree(false);
      toast({ title: enabled ? `${source.name} is on` : `${source.name} is off`, tone: "success",
        description: enabled ? "It's searched in your next scan. You can turn it off here any time." : "Your OK to its risks was withdrawn." });
    } catch (err) {
      toast({ title: `Couldn't change ${source.name}`, description: err instanceof ApiError ? err.message : String(err), tone: "error" });
    } finally {
      setBusy(false);
    }
  };
  const id = `consent-${source.platform}`;
  return (
    <li className="rounded-xl border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-semibold">{source.name}</p>
        {source.enabled && <span className="inline-flex items-center gap-1.5 text-sm font-semibold text-success"><CircleCheck className="h-4 w-4" aria-hidden /> On</span>}
      </div>
      <p className="mt-1 flex items-start gap-2 text-sm text-muted-foreground">
        <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden /> {source.warning}
      </p>
      {!source.available ? (
        <p className="mt-3 text-xs text-muted-foreground">Not available in the demo.</p>
      ) : source.enabled ? (
        <Button className="mt-3" size="sm" variant="outline" loading={busy} onClick={() => setEnabled(false)}>Turn off</Button>
      ) : (
        <div className="mt-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <label htmlFor={id} className="flex cursor-pointer items-start gap-2 text-sm">
            <input id={id} type="checkbox" className="mt-0.5 h-4 w-4 accent-[hsl(var(--primary))]" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
            <span>I understand the risk and want HireFlow to use {source.name} for me.</span>
          </label>
          <Button size="sm" disabled={!agree} loading={busy} onClick={() => setEnabled(true)}>Turn on</Button>
        </div>
      )}
    </li>
  );
}

/** Sites whose terms forbid automation: off by default, on only with your recorded OK to the risks. */
export function GatedSources() {
  const { data, mutate } = useSWR<{ sources: GatedSource[] }>("/users/me/sources", fetcher);
  const { mutate: mutateGlobal } = useSWRConfig();
  if (!data) return null;
  const refresh = async () => {
    await Promise.all([mutate(), mutateGlobal("/auth/me")]);
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sites that don&apos;t allow automation</CardTitle>
        <CardDescription>
          These sites forbid automated access in their terms. They stay off unless you turn them on here, after reading the
          risk; your OK is recorded and you can withdraw it any time. See <a href="/responsible-use" className="font-semibold text-primary hover:underline">responsible use</a>.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-3 md:grid-cols-2">
          {data.sources.map((s) => <SourceRow key={s.platform} source={s} onChange={refresh} />)}
        </ul>
      </CardContent>
    </Card>
  );
}
