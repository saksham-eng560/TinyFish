"use client";

import { useState } from "react";
import { CloudOff, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api-client";
import { cn } from "@/lib/utils";

/** What went wrong, in words a person can act on (no stack traces, no status codes on their own). */
export function friendlyError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return "Too many requests in a short time. Wait a few seconds and try again.";
    if (error.status === 403 || error.status === 404) return error.message;
    if (error.status >= 500) return "Something went wrong on the server. It's usually brief, so try again in a moment.";
    return error.message;
  }
  if (error instanceof TypeError) return "Can't reach HireFlow right now. Check your connection, then try again.";
  return "Something went wrong while loading this.";
}

/** Shown in place of a section whose data failed to load, with a retry. */
export function ErrorState({ error, onRetry, title = "Couldn't load this", className }: {
  error: unknown;
  onRetry?: () => unknown;
  title?: string;
  className?: string;
}) {
  const [retrying, setRetrying] = useState(false);
  const retry = async () => {
    if (!onRetry) return;
    setRetrying(true);
    try {
      await onRetry();
    } finally {
      setRetrying(false);
    }
  };
  return (
    <div role="alert" className={cn("flex flex-col items-center justify-center rounded-xl border border-dashed border-line/70 p-10 text-center", className)}>
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-secondary">
        <CloudOff className="h-5 w-5 text-primary" aria-hidden />
      </div>
      <h3 className="mt-4 font-semibold">{title}</h3>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{friendlyError(error)}</p>
      {onRetry && (
        <Button type="button" variant="outline" className="mt-5" loading={retrying} onClick={retry}>
          {!retrying && <RotateCw />} Try again
        </Button>
      )}
    </div>
  );
}
