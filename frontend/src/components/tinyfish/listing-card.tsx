"use client";

import { Building2, Check, ExternalLink, MapPin } from "lucide-react";
import { JobMatchBadge } from "@/components/job-match-badge";
import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { FinderListing } from "@/lib/types";
import { PLATFORM_LABELS, cn, formatSalary, safeUrl, timeAgo, titleCase } from "@/lib/utils";

const VIA: Record<string, string> = { search: "Search", fetch: "Fetch", agent: "Agent", ats_api: "ATS API" };

/** One ranked opening: score, where it came from, why it ranks, and the apply link. */
export function ListingCard({ listing, selected, onSelect, visaRequested }: {
  listing: FinderListing;
  selected: boolean;
  onSelect: (checked: boolean) => void;
  visaRequested: boolean;
}) {
  const apply = safeUrl(listing.apply_url, ["http:", "https:"]);
  const salary = formatSalary(listing.salary?.min ?? null, listing.salary?.max ?? null, listing.salary?.currency ?? null);
  const titleId = `listing-${listing.id}`;
  const { visa } = listing;
  return (
    <li className={cn("rounded-lg border bg-card p-4 shadow-sm transition-colors sm:p-5", selected ? "border-primary" : "border-line/70", listing.saved && "opacity-80")}>
      <div className="flex items-start gap-3 sm:gap-4">
        <Checkbox checked={listing.saved || selected} disabled={listing.saved} onCheckedChange={(v) => onSelect(v === true)}
          aria-labelledby={titleId} className="mt-1" />
        <JobMatchBadge score={listing.score} />
        <div className="min-w-0 flex-1">
          <p id={titleId} className="break-words font-display text-lg font-extrabold leading-tight">{listing.title}</p>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1.5 font-medium text-foreground"><Building2 className="h-4 w-4" aria-hidden />{listing.company}</span>
            {listing.location && <span className="inline-flex items-center gap-1"><MapPin className="h-3.5 w-3.5" aria-hidden />{listing.location}</span>}
            {listing.posted_date && <span>posted {timeAgo(listing.posted_date)}</span>}
            {salary && <span>{salary}</span>}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {listing.remote && <Badge tone="info">Remote</Badge>}
            {listing.job_type && <Badge tone="muted">{titleCase(listing.job_type)}</Badge>}
            {listing.seniority && <Badge tone="muted">{titleCase(listing.seniority)}</Badge>}
            {listing.source_host && <Badge tone="muted" title={listing.source_url}>{PLATFORM_LABELS[listing.ats] ?? listing.source_host}</Badge>}
            {visa.status === "sponsors" && <Badge tone="success">Sponsors visas</Badge>}
            {visa.status === "no_sponsorship" && <Badge tone="warning">No sponsorship</Badge>}
            {visa.status === "unknown" && visaRequested && <Badge tone="muted">Visa unknown</Badge>}
            {listing.discovered_by.map((v) => <Badge key={v} tone="outline">via {VIA[v] ?? titleCase(v)}</Badge>)}
          </div>
          {listing.snippet && <p className="mt-3 line-clamp-2 break-words text-sm text-muted-foreground">{listing.snippet}</p>}

          <details className="group mt-3">
            <summary className="inline-flex cursor-pointer select-none items-center gap-1 rounded text-xs font-semibold text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
              Why this ranks <span className="tabular-nums text-muted-foreground">({listing.score}/100)</span>
            </summary>
            <ul className="mt-2 space-y-1.5 border-l-2 border-line pl-3 text-xs" aria-label="Score breakdown">
              {listing.reasons.map((r) => (
                <li key={r.factor} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="w-20 shrink-0 font-semibold capitalize">{r.factor}</span>
                  <span className="shrink-0 font-mono tabular-nums">{r.points}/{r.max}</span>
                  <span className="min-w-0 break-words text-muted-foreground">{r.note}</span>
                </li>
              ))}
              {visa.evidence && <li className="break-words text-muted-foreground">Visa: &ldquo;{visa.evidence}&rdquo;</li>}
            </ul>
          </details>
        </div>

        <div className="flex shrink-0 flex-col items-end gap-2">
          {apply ? (
            <a href={apply} target="_blank" rel="noopener noreferrer" className={buttonVariants({ size: "sm" })}
              aria-label={`Apply to ${listing.title} at ${listing.company} (opens in a new tab)`}>
              Apply <ExternalLink />
            </a>
          ) : <Badge tone="muted">No apply link</Badge>}
          {listing.saved && <Badge tone="success"><Check className="h-3 w-3" aria-hidden /> In your deck</Badge>}
        </div>
      </div>
    </li>
  );
}
