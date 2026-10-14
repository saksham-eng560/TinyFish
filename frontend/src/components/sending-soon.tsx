"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useSWRConfig } from "swr";
import { Clock, Send, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useApplications } from "@/hooks/use-applications";
import { ApiError, post } from "@/lib/api-client";
import type { ApplicationSummary } from "@/lib/types";

/** "in 7 min", "at 14:05", "tomorrow at 00:05" */
function when(sendAfter: string, now: number) {
  const at = new Date(sendAfter);
  const minutes = Math.ceil((at.getTime() - now) / 60000);
  if (minutes <= 0) return "any moment now";
  if (minutes < 60) return `in ${minutes} min`;
  const time = at.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  const days = Math.round((new Date(at).setHours(0, 0, 0, 0) - new Date(now).setHours(0, 0, 0, 0)) / 86400000);
  if (days === 0) return `at ${time}`;
  if (days === 1) return `tomorrow at ${time}`;
  return `on ${at.toLocaleDateString(undefined, { day: "numeric", month: "short" })} at ${time}`;
}

function Row({ app, now, onDone }: { app: ApplicationSummary; now: number; onDone: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<"stop" | "send" | null>(null);
  const undoWindow = (app.hold_reason || "").startsWith("Sending soon");
  const act = async (kind: "stop" | "send") => {
    setBusy(kind);
    try {
      await post(`/applications/${app.id}/${kind === "stop" ? "cancel-send" : "send-now"}`);
      toast(kind === "stop"
        ? { title: "Stopped", description: "It's back in Ready to submit. Nothing was sent.", tone: "success" }
        : { title: "Sending now", tone: "success" });
      onDone();
    } catch (err) {
      toast({ title: kind === "stop" ? "Couldn't stop it" : "Couldn't send it", description: err instanceof ApiError ? err.message : String(err), tone: "error" });
      onDone();
    } finally {
      setBusy(null);
    }
  };
  return (
    <li className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
      <div className="min-w-0 flex-1">
        <Link href={`/dashboard/applications/${app.id}`} className="font-semibold hover:underline">
          {app.job?.role_title} <span className="font-normal text-muted-foreground">at</span> {app.job?.company_name}
        </Link>
        <p className="mt-0.5 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Clock className="h-3.5 w-3.5 text-primary" aria-hidden />
          <span>Goes out <span className="font-semibold text-foreground">{app.send_after ? when(app.send_after, now) : "soon"}</span>
            {!undoWindow && app.hold_reason ? ` · ${app.hold_reason.split(":")[0]}` : ""}</span>
        </p>
      </div>
      <div className="flex gap-2">
        <Button size="sm" variant="outline" onClick={() => act("stop")} loading={busy === "stop"} disabled={!!busy}>
          {busy !== "stop" && <Undo2 />} Stop
        </Button>
        {undoWindow && (
          <Button size="sm" variant="ghost" onClick={() => act("send")} loading={busy === "send"} disabled={!!busy}>
            {busy !== "send" && <Send />} Send now
          </Button>
        )}
      </div>
    </li>
  );
}

/** Approved applications that haven't gone out yet: the 10-minute undo window of automatic submits,
 * or ones waiting on the daily / per-company limit. Stop puts one back in Ready to submit. */
export function SendingSoon() {
  const { data, mutate } = useApplications({ status: "approved", page_size: 100, sort: "updated" });
  const { mutate: mutateGlobal } = useSWRConfig();
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15000);
    return () => clearInterval(t);
  }, []);
  const held = (data?.items || []).filter((a) => a.send_after)
    .sort((a, b) => new Date(a.send_after!).getTime() - new Date(b.send_after!).getTime());
  if (!held.length) return null;
  const refresh = () => {
    void mutate();
    void mutateGlobal((key) => typeof key === "string" && (key.startsWith("/applications") || key === "/agent/status"));
  };
  return (
    <section aria-labelledby="sending-soon" className="mx-auto mb-8 max-w-[880px] rounded-xl border bg-card shadow-sm">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <h2 id="sending-soon" className="flex items-center gap-2 font-semibold"><Send className="h-4 w-4 text-primary" aria-hidden /> Sending soon</h2>
        <p className="text-xs text-muted-foreground">Automatic submits wait 10 minutes, so you can stop them.</p>
      </header>
      <ul className="divide-y">
        {held.map((app) => <Row key={app.id} app={app} now={now} onDone={refresh} />)}
      </ul>
    </section>
  );
}
