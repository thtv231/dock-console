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
// `built` flips to true when that screen exists.
export type Screen = { href: string; title: string; summary: string; icon: LucideIcon; lesson: number; built: boolean };

export const SCREENS: Screen[] = [
  { href: "/", title: "Overview", summary: "Every capability with its recorded latency and tokens.", icon: LayoutDashboard, lesson: 6, built: false },
  { href: "/inbox", title: "Inbox", summary: "Dock reads each customer email and picks the intent, the shipment and the language.", icon: Inbox, lesson: 1, built: true },
  { href: "/documents", title: "Documents", summary: "Invoices and bills of lading read with OCR and checked against the database.", icon: FileText, lesson: 2, built: false },
  { href: "/handbook", title: "Handbook", summary: "Answers from the ops handbook, with the sections they come from.", icon: BookOpen, lesson: 2, built: false },
  { href: "/agents", title: "Agents", summary: "Triage hands off to tracking and billing agents.", icon: Workflow, lesson: 3, built: false },
  { href: "/approvals", title: "Approvals", summary: "Dock drafts emails; a person approves each one before it is sent.", icon: ListChecks, lesson: 4, built: false },
  { href: "/guardrails", title: "Guardrails", summary: "Inbound screening and the never-guarantee-a-date rule.", icon: Shield, lesson: 2, built: false },
  { href: "/voice", title: "Voice desk", summary: "Phone calls transcribed, noted and answered by voice.", icon: Phone, lesson: 5, built: false },
  { href: "/scorecard", title: "Scorecard", summary: "Every capability tested against its expected result.", icon: ClipboardCheck, lesson: 5, built: false },
  { href: "/operations", title: "Operations", summary: "Budgets, timeouts, retries and release checks from recorded runs.", icon: Activity, lesson: 6, built: false },
];

export function screenFor(pathname: string) {
  return SCREENS.find((s) => (s.href === "/" ? pathname === "/" : pathname.startsWith(s.href)));
}
