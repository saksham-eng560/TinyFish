"use client";

import { useState } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export function TagInput({ value, onChange, placeholder, className, id, "aria-label": ariaLabel }: {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  className?: string;
  id?: string;
  "aria-label"?: string;
}) {
  const [draft, setDraft] = useState("");
  const add = (raw: string) => {
    const items = raw.split(",").map((s) => s.trim()).filter(Boolean).filter((s) => !value.includes(s));
    if (items.length) onChange([...value, ...items]);
    setDraft("");
  };
  return (
    <div className={cn("flex min-h-10 w-full flex-wrap items-center gap-1.5 rounded-md border border-input bg-background px-2 py-1.5 text-sm transition-colors hover:border-primary/50 focus-within:border-primary focus-within:ring-2 focus-within:ring-ring/30", className)}>
      {value.map((tag) => (
        <span key={tag} className="inline-flex items-center gap-1 rounded-full border border-primary/30 bg-secondary px-2.5 py-0.5 text-xs text-secondary-foreground">
          {tag}
          <button type="button" onClick={() => onChange(value.filter((t) => t !== tag))} aria-label={`Remove ${tag}`}>
            <X className="h-3 w-3 text-muted-foreground hover:text-foreground" />
          </button>
        </span>
      ))}
      <input
        id={id}
        aria-label={ariaLabel}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add(draft);
          } else if (e.key === "Backspace" && !draft && value.length) {
            onChange(value.slice(0, -1));
          }
        }}
        onBlur={() => draft && add(draft)}
        placeholder={value.length ? "" : placeholder}
        className="min-w-[8rem] flex-1 bg-transparent px-1 outline-none placeholder:text-muted-foreground"
      />
    </div>
  );
}
