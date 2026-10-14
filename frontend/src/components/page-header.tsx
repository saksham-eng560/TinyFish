import { cn } from "@/lib/utils";

/** ``compact``: on phones only the title and actions show, so the page's content starts higher up. */
export function PageHeader({ title, description, actions, eyebrow, compact = false }: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: string;
  compact?: boolean;
}) {
  return (
    <div className={cn("mb-8 flex flex-col gap-4 border-b border-line/60 pb-6 sm:flex-row sm:items-end sm:justify-between",
      compact && "mb-4 gap-3 pb-4 sm:mb-8 sm:gap-4 sm:pb-6")}>
      <div className="min-w-0">
        {eyebrow && <p className={cn("label-caps mb-3 text-primary", compact && "hidden sm:block")}>{eyebrow}</p>}
        <h1 className="display text-3xl sm:text-4xl">{title}</h1>
        {description && <p className={cn("mt-3 max-w-2xl text-sm text-muted-foreground", compact && "hidden sm:block")}>{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
