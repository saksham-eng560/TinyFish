import {
  BadgeCheck,
  CalendarCheck,
  Check,
  ClipboardList,
  Flag,
  Gift,
  Hourglass,
  LoaderCircle,
  MailCheck,
  PhoneCall,
  Search,
  Send,
  SkipForward,
  Target,
  TriangleAlert,
  Undo2,
  X,
  type LucideIcon,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { ApplicationStatus } from "@/lib/types";
import { STATUS_LABELS, STATUS_TONE } from "@/lib/utils";

/** Every status has an icon as well as its label, so it never depends on colour alone (all statuses are shades of blue). */
export const STATUS_ICON: Record<ApplicationStatus, LucideIcon> = {
  discovered: Search,
  matched: Target,
  skipped: SkipForward,
  preparing: LoaderCircle,
  pending_approval: Hourglass,
  approved: Send,
  applied: Check,
  acknowledged: MailCheck,
  screening: PhoneCall,
  interview: CalendarCheck,
  assessment: ClipboardList,
  final_round: Flag,
  offer: Gift,
  accepted: BadgeCheck,
  rejected: X,
  withdrawn: Undo2,
  failed: TriangleAlert,
};

export function StatusBadge({ status }: { status: ApplicationStatus }) {
  const Icon = STATUS_ICON[status];
  return (
    <Badge tone={STATUS_TONE[status] || "default"}>
      {Icon && <Icon className="h-3 w-3" aria-hidden />}
      {STATUS_LABELS[status] || status}
    </Badge>
  );
}
