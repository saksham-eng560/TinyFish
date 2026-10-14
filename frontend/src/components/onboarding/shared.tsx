"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Info } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";

/** Props every step receives from the wizard. */
export interface StepProps<T> {
  initial: T;
  onSave: (data: T) => Promise<void>;
  onBack?: () => void;
  onSkip?: () => void;
  saving: boolean;
}

/** Title, plus why we ask ("Forms ask this; we never guess it for you"). */
export function StepHeader({ title, intro, why }: { title: string; intro?: React.ReactNode; why?: React.ReactNode }) {
  return (
    <header className="mb-6">
      <h1 className="display text-3xl sm:text-4xl">{title}</h1>
      {intro && <p className="mt-2 max-w-2xl text-sm text-muted-foreground sm:text-base">{intro}</p>}
      {why && (
        <p className="mt-3 flex max-w-2xl items-start gap-2 rounded-lg bg-secondary px-3 py-2 text-xs text-secondary-foreground">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden /> <span>{why}</span>
        </p>
      )}
    </header>
  );
}

/** A labelled form row with an optional hint and inline error. */
export function Field({ label, hint, error, required, children, className }: {
  label: string;
  hint?: React.ReactNode;
  error?: string | null;
  required?: boolean;
  children: (id: string, describedBy: string | undefined) => React.ReactNode;
  className?: string;
}) {
  const id = useId();
  const hintId = hint ? `${id}-hint` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label htmlFor={id} className="text-sm font-semibold">
        {label}{required && <span className="ml-0.5 text-primary" aria-hidden>*</span>}
        {required && <span className="sr-only"> (required)</span>}
      </Label>
      {children(id, describedBy)}
      {hint && <p id={hintId} className="text-xs text-muted-foreground">{hint}</p>}
      {error && <p id={errorId} role="alert" className="text-xs font-medium text-destructive">{error}</p>}
    </div>
  );
}

/** Radio buttons as cards (native inputs: keyboard and screen readers for free). */
export function RadioCards<V extends string>({ name, value, onChange, options, columns = 2, label }: {
  name: string;
  value: V | null | undefined;
  onChange: (value: V) => void;
  options: { value: V; label: string; description?: string }[];
  columns?: 1 | 2 | 3 | 4;
  label: string;
}) {
  const cols = { 1: "", 2: "sm:grid-cols-2", 3: "sm:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <div role="radiogroup" aria-label={label} className={cn("grid gap-2", cols)}>
      {options.map((o) => (
        <label key={o.value} className={cn(
          "flex cursor-pointer items-start gap-3 rounded-lg border bg-card p-3 text-sm transition-colors hover:border-primary/60",
          "has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/40",
          value === o.value && "border-primary bg-secondary",
        )}>
          <input type="radio" name={name} value={o.value} checked={value === o.value} onChange={() => onChange(o.value)}
            className="mt-0.5 h-4 w-4 shrink-0 accent-[hsl(var(--primary))]" />
          <span>
            <span className="font-semibold">{o.label}</span>
            {o.description && <span className="mt-0.5 block text-xs text-muted-foreground">{o.description}</span>}
          </span>
        </label>
      ))}
    </div>
  );
}

/** Back / Skip / Continue. Continue submits the step's form, so Enter works too. */
export function StepActions({ onBack, onSkip, saving, continueLabel = "Continue", disabled }: {
  onBack?: () => void;
  onSkip?: () => void;
  saving: boolean;
  continueLabel?: string;
  disabled?: boolean;
}) {
  return (
    <div className="sticky bottom-0 -mx-4 mt-8 flex items-center gap-2 border-t bg-background/95 px-4 py-4 backdrop-blur sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:px-0">
      {onBack && <Button type="button" variant="outline" onClick={onBack} disabled={saving}><ArrowLeft /> Back</Button>}
      <div className="flex-1" />
      {onSkip && <Button type="button" variant="ghost" onClick={onSkip} disabled={saving}>Skip for now</Button>}
      <Button type="submit" loading={saving} disabled={disabled}>{continueLabel} {!saving && <ArrowRight />}</Button>
    </div>
  );
}

/** A form-level error from the server, announced to screen readers. */
export function FormError({ message }: { message: string | null }) {
  if (!message) return null;
  return <p role="alert" className="mb-4 rounded-lg border border-destructive/40 bg-secondary px-3 py-2 text-sm font-medium text-destructive">{message}</p>;
}

const DRAFT_PREFIX = "hireflow:onboarding:";

/**
 * Form state that survives a refresh: kept in localStorage until the step is saved (then `clear()`).
 * Falls back to plain state when storage isn't available.
 */
export function useDraft<T>(key: string, initial: T): [T, (next: T | ((prev: T) => T)) => void, () => void] {
  const storageKey = DRAFT_PREFIX + key;
  const [value, setValue] = useState<T>(() => {
    if (typeof window === "undefined") return initial;
    try {
      const saved = window.localStorage.getItem(storageKey);
      return saved ? { ...initial, ...(JSON.parse(saved) as T) } : initial;
    } catch {
      return initial;
    }
  });
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    try { window.localStorage.setItem(storageKey, JSON.stringify(value)); } catch { /* private mode: no draft */ }
  }, [storageKey, value]);
  const clear = useCallback(() => {
    try { window.localStorage.removeItem(storageKey); } catch { /* nothing to clear */ }
  }, [storageKey]);
  return [value, setValue, clear];
}

export function clearAllDrafts() {
  try {
    Object.keys(window.localStorage).filter((k) => k.startsWith(DRAFT_PREFIX)).forEach((k) => window.localStorage.removeItem(k));
  } catch { /* nothing to clear */ }
}

/** Light client-side link check (the server validates again): http(s), a dot in the host, no spaces. */
export function linkError(value: string | null | undefined, host?: string): string | null {
  const v = (value || "").trim();
  if (!v) return null;
  try {
    const url = new URL(/^https?:\/\//i.test(v) ? v : `https://${v}`);
    if (!url.hostname.includes(".") || /\s/.test(v)) return "Enter a valid link, e.g. https://example.com/you";
    if (host && !(url.hostname === host || url.hostname.endsWith(`.${host}`))) return `Enter a ${host} link`;
    return null;
  } catch {
    return "Enter a valid link, e.g. https://example.com/you";
  }
}
