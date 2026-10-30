import {
  Activity,
  BookOpen,
  ClipboardCheck,
  FileText,
  Inbox,
  LayoutDashboard,
  ListChecks,
  type LucideIcon,
  Phone,
  Shield,
  Workflow,
} from "lucide-react";

// One screen per capability of Dock, in sidebar order. `lesson` is the PLAN.md lesson that builds it;
// `built` flips to true when that screen exists. `proves` describes what the Overview card counts, and claims nothing its counts can't show.
export type Screen = {
  href: string; title: string; summary: string; proves: string; icon: LucideIcon; lesson: number; built: boolean;
};

export const SCREENS: Screen[] = [
  { href: "/", title: "Overview", summary: "Every capability with its recorded latency and tokens.", proves: "Every capability at a glance, from recorded runs.", icon: LayoutDashboard, lesson: 6, built: true },
  { href: "/inbox", title: "Inbox", summary: "Dock reads each customer email and picks the intent, the shipment and the language.", proves: "Emails triaged into intent, shipment and language, counted against the answer key.", icon: Inbox, lesson: 1, built: true },
  { href: "/documents", title: "Documents", summary: "Invoices and bills of lading read with OCR and checked against the database.", proves: "Invoices and bills of lading read into fields and flags, counted against the truth for the final schema and the first one.", icon: FileText, lesson: 2, built: true },
  { href: "/handbook", title: "Handbook", summary: "Answers from the ops handbook, with the sections they come from.", proves: "Handbook answers counted for citing the expected sections and containing the expected words.", icon: BookOpen, lesson: 2, built: true },
  { href: "/agents", title: "Agents", summary: "Triage hands off to tracking and billing agents; one agent vs parallel workers on the customs hold report.", proves: "Emails routed right under each routing rule, and design runs that covered every held shipment, for one agent and for parallel workers.", icon: Workflow, lesson: 3, built: true },
  { href: "/approvals", title: "Approvals", summary: "Dock drafts emails; a person approves each one before it is sent.", proves: "Recorded approval runs counted for emailing a non-customer, reaching the consignee, and answers that contradict the outbox.", icon: ListChecks, lesson: 4, built: true },
  { href: "/guardrails", title: "Guardrails", summary: "Inbound screening and the never-guarantee-a-date rule.", proves: "Messages screened as the answer key expects, and replies that guarantee a date under Dock’s rule and under a plain prompt.", icon: Shield, lesson: 2, built: true },
  { href: "/voice", title: "Voice desk", summary: "Phone calls transcribed, noted and answered by voice.", proves: "Call notes counted against the truth with and without context bias, and C2 noted again on one unchanged transcript per source.", icon: Phone, lesson: 5, built: true },
  { href: "/scorecard", title: "Scorecard", summary: "Every case on the baseline and a cheaper candidate, with the regressions between them.", proves: "Cases passed by the baseline and by a cheaper candidate.", icon: ClipboardCheck, lesson: 5, built: true },
  { href: "/operations", title: "Operations", summary: "Budgets, timeouts, retries and release checks from recorded runs.", proves: "One recorded run of each step: budget refusals, emails sent over the retries, date guarantees per version, where prod ended, and tokens spent after a handover.", icon: Activity, lesson: 6, built: true },
];

export function screenFor(pathname: string) {
  return SCREENS.find((s) => (s.href === "/" ? pathname === "/" : pathname.startsWith(s.href)));
}
