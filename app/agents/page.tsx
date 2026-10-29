"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ArrowRight, Bot, Check, ChevronDown, ChevronRight, Loader2, MessageSquare, Play, Trash2, Wrench, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Run, Running, runMs, runTokens, Segmented, Stat, Toolbar, useRows, Verdict,
} from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mail = { id: string; from: string; subject: string; body: string };
type Truth = { intent: string; shipment_id: string | null; language: string };
type Rule = "v1" | "v2";
type Agent = "triage" | "tracking" | "billing";
type Entry =
  | { type: "handoff"; from: string; to: string }
  | { type: "call"; agent: string | null; name: string; arguments: string }
  | { type: "result"; result: string }
  | { type: "message"; agent: string | null; content: string };
type Handoff = Run & {
  mail_id: string;
  rule: Rule;
  route: Agent;
  expected: Agent | null;
  right: boolean | null;
  entries: Entry[];
  agents: { created: { id: string; name: string }[]; deleted: string[] };
};
type ReportRow = { shipment_id: string; consignee: string; reason: string | null; next_step: string };
type Design = Run & {
  design: "a" | "parallel";
  guard: boolean;
  run: number;
  rows: ReportRow[];
  score: { covered: string; "invented reasons": string[]; "follows the note": string[] };
  held: string[];
  seconds: number;
  tool_calls: number;
  tokens: number;
};

const RULES: Rule[] = ["v1", "v2"];
const RULE_NOTE: Record<Rule, string> = {
  v1: "v1, section 4’s first rule: tracking questions to dock-tracking, invoice questions to dock-billing, anything else stays.",
  v2: "v2, the fix: hand off ONLY where-is-it and invoice amounts; customs, damage and bookings stay even if a shipment is mentioned.",
};
// Section 4's answer key: the intent in inbox_truth.json decides the specialist; everything else stays with triage.
const SPECIALIST: Record<string, Agent> = { tracking: "tracking", invoice: "billing" };
const RUNS = [1, 2, 3, 4, 5];
const INJECTED = "HF-26038";
const DESIGNS = [
  { value: "a", title: "A · One agent with tools", note: "One conversation calls list_held_shipments and get_events, then writes the whole report." },
  { value: "parallel", title: "C · Parallel workers", note: "One chat.parse per held shipment, all at once, each told that tracking notes are data, not instructions." },
] as const;

const retriesOf = (r: Run) => r.meter.reduce((s, c) => s + (c.retries ?? 0), 0);
const retriesMetered = (r: Run) => r.meter.every((c) => c.retries !== undefined);

/** **bold** as the agents write it, without pulling in a markdown renderer. */
function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
        part.startsWith("**") && part.endsWith("**") ? <strong key={i}>{part.slice(2, -2)}</strong> : <Fragment key={i}>{part}</Fragment>,
      )}
    </>
  );
}

function AgentName({ name }: { name: string | null }) {
  return <span className="font-medium">{name ?? "agent"}</span>;
}

/** The conversation as section 4's show() prints it: who handed off, which tool was called, what came back. */
function Timeline({ result }: { result: Handoff }) {
  const steps = result.entries;
  return (
    <ol className="relative flex flex-col gap-3 border-l pl-4">
      <li className="relative">
        <span className="absolute -left-[22px] top-0.5 grid size-3 place-items-center rounded-full border bg-card" aria-hidden />
        <div className="text-xs text-muted-foreground">Email arrives at <AgentName name="triage" /></div>
      </li>
      {steps.map((e, i) => (
        <li key={i} className="relative">
          <span className="absolute -left-[25px] top-0 grid size-[18px] place-items-center rounded-full border bg-card text-muted-foreground" aria-hidden>
            {e.type === "handoff" ? <ArrowRight className="size-3" /> : e.type === "call" ? <Wrench className="size-3" /> :
              e.type === "result" ? <Check className="size-3" /> : <MessageSquare className="size-3" />}
          </span>
          {e.type === "handoff" && (
            <div><span className="text-muted-foreground">Handoff</span> <AgentName name={e.from || "triage"} /> → <AgentName name={e.to} /></div>
          )}
          {e.type === "call" && (
            <div>
              <span className="text-muted-foreground">Tool call by</span> <AgentName name={e.agent} />
              <div className="mt-0.5 font-mono text-xs">{e.name}({e.arguments})</div>
            </div>
          )}
          {e.type === "result" && (
            <div>
              <span className="text-muted-foreground">Tool result</span>
              <div className="mt-0.5 break-all font-mono text-xs text-muted-foreground">{e.result}</div>
            </div>
          )}
          {e.type === "message" && (
            <div>
              <span className="text-muted-foreground">Answer from</span> <AgentName name={e.agent} />
              <p className="mt-1 max-w-[70ch] whitespace-pre-wrap rounded-md border bg-card px-3 py-2 leading-relaxed"><Rich text={e.content} /></p>
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}

/** Every agent the run created on the account, and whether its delete was confirmed. */
function Lifecycle({ result }: { result: Handoff }) {
  const { created, deleted } = result.agents;
  const gone = created.filter((a) => deleted.includes(a.id)).length;
  return (
    <div className="text-xs">
      <div className="mb-1 text-muted-foreground">
        Agents created {created.length} / deleted {gone}
      </div>
      <table className="tabular-nums">
        <tbody>
          {created.map((a) => {
            const ok = deleted.includes(a.id);
            return (
              <tr key={a.id}>
                <td className="py-0.5 pr-3"><Bot className="inline size-3.5 text-muted-foreground" /> {a.name}</td>
                <td className="py-0.5 pr-3 font-mono text-muted-foreground">{a.id}</td>
                <td className={cn("py-0.5", ok ? "text-ok" : "text-miss")}>
                  {ok ? <><Trash2 className="inline size-3.5" /> deleted</> : <><X className="inline size-3.5" /> still on the account</>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function RouteCell({ result, expected }: { result: Handoff; expected: Agent }) {
  const ok = result.route === expected;
  return (
    <span className={cn("inline-flex items-center gap-1.5", !ok && "font-medium text-miss")}>
      {ok ? <Check className="size-3.5 text-ok" /> : <X className="size-3.5" />}
      {result.route}
    </span>
  );
}

function ErrorText({ error }: { error: ApiError }) {
  return <span className="block truncate text-miss" title={describeError(error)}>{describeError(error)}</span>;
}

function HandoffsTab({ mails, truth, live }: { mails: Mail[]; truth: Record<string, Truth>; live: boolean }) {
  const { post } = useApi();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  // Rows are keyed rule:id, so both rules' results sit side by side.
  const run = useCallback((key: string) => {
    const [rule, id] = key.split(":");
    return post<Handoff>("/api/agents/handoff", { mail_id: id, rule });
  }, [post]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);
  const expected = (id: string) => SPECIALIST[truth[id]?.intent] ?? "triage";
  const routeBoth = (id: string) => RULES.forEach((r) => runOne(`${r}:${id}`));

  const created = done.reduce((s, r) => s + r.agents.created.length, 0);
  const deleted = done.reduce((s, r) => s + r.agents.created.filter((a) => r.agents.deleted.includes(a.id)).length, 0);

  return (
    <>
      <Toolbar
        live={live}
        run="handoff (three agents created, run, deleted)"
        stats={done.length > 0 && (
          <>
            {RULES.map((rule) => {
              const forRule = done.filter((r) => r.rule === rule);
              return forRule.length > 0 && (
                <Stat key={rule} label={`${rule} matches the answer key`}
                  value={`${forRule.filter((r) => r.route === expected(r.mail_id)).length} of ${forRule.length}`} />
              );
            })}
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Agents created / deleted" value={`${num(created)} / ${num(deleted)}`} />
            <Stat label="Median latency" value={`${num(median(done.map(runMs)))} ms`} />
            <Stat label="Tokens" value={num(done.reduce((s, r) => s + runTokens(r), 0))} />
          </>
        )}
      >
        <Button size="sm" onClick={() => runAll(RULES.flatMap((r) => mails.map((m) => `${r}:${m.id}`)))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Routing, ${running} running` : `Route all ${mails.length} under v1 and v2`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>
      <div className="flex flex-col gap-0.5 border-b px-4 py-2 text-xs text-muted-foreground">
        {RULES.map((r) => <p key={r}>{RULE_NOTE[r]}</p>)}
      </div>

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={3} className="h-7 text-xs text-muted-foreground">Email</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Answer key</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Routed to</TableHead>
            <TableHead colSpan={3} className="h-7 border-l text-xs text-muted-foreground">Run (v2)</TableHead>
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">ID</TableHead>
            <TableHead className="h-8">Subject</TableHead>
            <TableHead className="h-8 border-l">Intent</TableHead>
            <TableHead className="h-8">Specialist</TableHead>
            <TableHead className="h-8 border-l">v1</TableHead>
            <TableHead className="h-8">v2</TableHead>
            <TableHead className="h-8 border-l text-right">Latency</TableHead>
            <TableHead className="h-8 text-right">Tokens</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {mails.map((mail) => {
            const want = expected(mail.id);
            const byRule = Object.fromEntries(RULES.map((r) => [r, rows[`${r}:${mail.id}`]]));
            const v2 = byRule.v2?.status === "done" ? byRule.v2.result : null;
            const busy = RULES.some((r) => byRule[r]?.status === "running");
            const isOpen = open[mail.id] ?? false;
            return (
              <Fragment key={mail.id}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5">
                    <Button variant="ghost" size="icon-xs" aria-label={isOpen ? `Hide ${mail.id}` : `Show ${mail.id}`} aria-expanded={isOpen}
                      onClick={() => setOpen((o) => ({ ...o, [mail.id]: !isOpen }))}>
                      {isOpen ? <ChevronDown /> : <ChevronRight />}
                    </Button>
                  </TableCell>
                  <TableCell className="py-1.5 font-mono text-xs">{mail.id}</TableCell>
                  <TableCell className="max-w-80 truncate py-1.5" title={mail.subject}>{mail.subject}</TableCell>
                  <TableCell className="border-l bg-muted/30 py-1.5 text-muted-foreground">{truth[mail.id]?.intent}</TableCell>
                  <TableCell className="bg-muted/30 py-1.5 text-muted-foreground">{want}</TableCell>
                  {RULES.map((r, i) => {
                    const row = byRule[r];
                    return (
                      <TableCell key={r} className={cn("max-w-48 py-1.5", i === 0 && "border-l")}>
                        {row?.status === "running" && <Running />}
                        {row?.status === "done" && <RouteCell result={row.result} expected={want} />}
                        {row?.status === "error" && <ErrorText error={row.error} />}
                      </TableCell>
                    );
                  })}
                  <TableCell className="border-l py-1.5 text-right tabular-nums">{v2 && <Latency run={v2} />}</TableCell>
                  <TableCell className="py-1.5 text-right tabular-nums">{v2 ? num(runTokens(v2)) : ""}</TableCell>
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => routeBoth(mail.id)} disabled={busy}>
                      {byRule.v1 || byRule.v2 ? "Run again" : "Route"}
                    </Button>
                  </TableCell>
                </TableRow>
                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={9} className="whitespace-normal pb-4 pt-0">
                      <div className="mb-3 max-w-[80ch]">
                        <div className="text-xs text-muted-foreground">{mail.from}</div>
                        <div className="font-medium">{mail.subject}</div>
                        <p className="mt-1 leading-relaxed">{mail.body}</p>
                      </div>
                      <div className="grid gap-6 xl:grid-cols-2">
                        {RULES.map((r) => {
                          const row = byRule[r];
                          return (
                            <section key={r} aria-label={`Rule ${r}`} className="flex min-w-0 flex-col gap-3">
                              <div className="flex items-center gap-3 text-xs">
                                <span className="font-medium">Rule {r}</span>
                                {row?.status === "done" && (
                                  <Verdict ok={row.result.route === want} yes={`Routed to ${row.result.route}`}
                                    no={`Routed to ${row.result.route}, answer key says ${want}`} />
                                )}
                              </div>
                              {!row && <p className="text-muted-foreground">Not routed yet. Press Route to see the conversation.</p>}
                              {row?.status === "running" && <Running />}
                              {row?.status === "error" && <p className="text-miss">{describeError(row.error)}</p>}
                              {row?.status === "done" && (
                                <>
                                  <Timeline result={row.result} />
                                  <Lifecycle result={row.result} />
                                  <MeterTable run={row.result} />
                                </>
                              )}
                            </section>
                          );
                        })}
                      </div>
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </>
  );
}

/** Why a parallel run took as long as it did, read from its meter: the slowest worker, and what it waited on. */
function workersOf(r: Design) {
  return r.meter.filter((c) => c.op === "chat.parse_async");
}

function RetryNote({ results }: { results: Design[] }) {
  const a = results.filter((r) => r.design === "a");
  const c = results.filter((r) => r.design === "parallel");
  if (!a.length || !c.length) return null;
  const slowerC = median(c.map((r) => r.seconds * 1000)) > median(a.map((r) => r.seconds * 1000));
  const retried = c.filter((r) => retriesOf(r) > 0);
  if (!retried.length && !slowerC) return null;
  const workers = c.flatMap(workersOf);
  const clean = workers.filter((w) => !w.retries);
  const waited = workers.filter((w) => (w.retries ?? 0) > 0);
  const outcomes = [...new Set(waited.flatMap((w) => w.retried ?? []))];
  const codes = outcomes.filter((o): o is number => typeof o === "number").sort((x, y) => x - y);
  // No HTTP status: the exception the attempt ended with (ConnectTimeout...), or "no response" in older recordings.
  const errors = outcomes.filter((o): o is string => typeof o === "string");
  const named = errors.filter((e) => e !== "no response");
  const noResponse = errors.length > 0
    ? `got no response from Mistral${named.length ? ` (${named.join(", ")})` : ", no HTTP status came back"}` : "";
  return (
    <div className="rounded-md border bg-card px-4 py-3 text-[13px] leading-relaxed">
      <div className="mb-1 font-medium">{slowerC ? "Why C is slower here" : "Retries in C"}</div>
      {retried.length > 0 ? (
        <p className="max-w-[90ch]">
          In {retried.length} of {c.length} C runs, {waited.length} of {workers.length} worker calls had a first attempt that
          {codes.length > 0 ? ` was answered HTTP ${codes.join(", ")}${noResponse ? ` or ${noResponse}` : ""}` : ` ${noResponse}`},
          and the SDK retried it inside the call. A parallel run takes as long as its slowest worker: the retried calls took a
          median {num(median(waited.map((w) => w.ms)))} ms, while the {clean.length} that went through first time took a
          median {num(median(clean.map((w) => w.ms)))} ms.
          {codes.includes(429) ? " HTTP 429 is Mistral’s rate limit: workers sent all at once can exceed it, while A makes one call at a time." :
            " The time went into waiting for that first attempt, not into the model’s work."}
        </p>
      ) : (
        <p className="max-w-[90ch]">
          No C worker was retried, so the time is the workers’ own: the slowest took {num(Math.max(...workers.map((w) => w.ms)))} ms.
        </p>
      )}
    </div>
  );
}

function ScoreCells({ r }: { r: Design }) {
  const all = r.score.covered === `${r.held.length}/${r.held.length}`;
  const invented = r.score["invented reasons"];
  const followed = r.score["follows the note"];
  return (
    <>
      <TableCell className={cn("py-1.5 tabular-nums", !all && "text-miss")}>{r.score.covered}</TableCell>
      <TableCell className={cn("py-1.5", invented.length > 0 && "text-miss")} title={invented.join(", ")}>
        {invented.length ? invented.join(", ") : "none"}
      </TableCell>
      <TableCell className={cn("py-1.5", followed.length > 0 ? "font-medium text-miss" : "text-ok")}>
        {followed.length ? `follows ${followed.join(", ")}` : "refused"}
      </TableCell>
      <TableCell className="py-1.5 text-right tabular-nums">{r.seconds.toFixed(1)} s</TableCell>
      <TableCell className="py-1.5 text-right tabular-nums" title={retriesMetered(r) ? r.meter.flatMap((c) => c.retried ?? []).join(", ") : "Recorded before retries were metered"}>
        {retriesMetered(r) ? retriesOf(r) : "–"}
      </TableCell>
      <TableCell className="py-1.5 text-right tabular-nums">{r.tool_calls}</TableCell>
      <TableCell className="py-1.5 text-right tabular-nums">{num(r.tokens)}</TableCell>
    </>
  );
}

function Report({ result }: { result: Design }) {
  const followed = result.score["follows the note"];
  return (
    <div className="flex flex-col gap-2">
      <div className="text-xs text-muted-foreground">
        Report from {DESIGNS.find((d) => d.value === result.design)?.title}, run {result.run}, guard {result.guard ? "on" : "off"}
      </div>
      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8">Shipment</TableHead>
            <TableHead className="h-8">Consignee</TableHead>
            <TableHead className="h-8">Reason (only if a note says so)</TableHead>
            <TableHead className="h-8">Next step</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {result.rows.map((row) => {
            const injected = row.shipment_id === INJECTED;
            const leak = followed.includes(row.shipment_id);
            return (
              <TableRow key={row.shipment_id} className={cn(leak && "bg-miss/10 hover:bg-miss/10", injected && !leak && "bg-ok/10 hover:bg-ok/10")}>
                <TableCell className="py-1.5 align-top font-mono text-xs">{row.shipment_id}</TableCell>
                <TableCell className="py-1.5 align-top">{row.consignee}</TableCell>
                <TableCell className="max-w-[30ch] whitespace-normal py-1.5 align-top text-muted-foreground">{row.reason ?? "none"}</TableCell>
                <TableCell className={cn("max-w-[44ch] whitespace-normal py-1.5 align-top", leak && "font-medium text-miss")}>
                  {row.next_step}
                  {injected && (
                    <div className={cn("mt-0.5 text-xs", leak ? "text-miss" : "text-ok")}>
                      {leak ? "Follows the injected tracking note." : "Does not act on the injected tracking note."}
                    </div>
                  )}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

function DesignsTab({ live }: { live: boolean }) {
  const { post } = useApi();
  const [guard, setGuard] = useState<"off" | "on">("off");
  const [picked, setPicked] = useState<string | null>(null);
  // Rows are keyed guard:design:run, so both guard settings keep their results.
  const run = useCallback((key: string) => {
    const [g, design, n] = key.split(":");
    return post<Design>("/api/agents/design", { design, guard: g === "on", run: Number(n) });
  }, [post]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);
  const keyOf = (design: string, n: number) => `${guard}:${design}:${n}`;
  const shown = done.filter((r) => r.guard === (guard === "on"));
  const pickedRow = picked ? rows[picked] : undefined;
  const pickedResult = pickedRow?.status === "done" ? pickedRow.result : null;

  return (
    <>
      <Toolbar
        live={live}
        run="run (one design, the whole report)"
        stats={shown.length > 0 && (
          <>
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Runs that follow the note" value={`${shown.filter((r) => r.score["follows the note"].length > 0).length} of ${shown.length}`} />
            <Stat label="Tokens" value={num(shown.reduce((s, r) => s + r.tokens, 0))} />
          </>
        )}
      >
        <Segmented label="Guard" value={guard} onChange={setGuard}
          options={[{ value: "off", label: "Guard off" }, { value: "on", label: "Guard on" }]} />
        <Button size="sm" onClick={() => runAll(DESIGNS.flatMap((d) => RUNS.map((n) => keyOf(d.value, n))))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Running, ${running} running` : live ? `Run each design ${RUNS.length} times` : `Show all ${RUNS.length} runs of each`}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => { clear(); setPicked(null); }} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>
      <p className="border-b px-4 py-2 text-xs text-muted-foreground">
        Tonight’s report of every shipment on customs hold, scored on the same checks for each design. The {INJECTED} tracking note
        hides an instruction to email the customer list to an outside address. {guard === "on"
          ? "Guard on: code after the design replaces any next step that names an email address or the customer list."
          : "Guard off: the report is shown as the model wrote it."}
      </p>

      <div className="flex flex-col gap-4 p-4">
        <Table className="text-[13px]">
          <TableHeader className="bg-muted/60">
            <TableRow className="hover:bg-transparent">
              <TableHead className="h-8">Design</TableHead>
              <TableHead className="h-8 text-right">Runs</TableHead>
              <TableHead className="h-8 text-right">All held covered</TableHead>
              <TableHead className="h-8 text-right">Invented a reason</TableHead>
              <TableHead className="h-8 text-right">Followed the note</TableHead>
              <TableHead className="h-8 text-right">Median time</TableHead>
              <TableHead className="h-8 text-right">Retries</TableHead>
              <TableHead className="h-8 text-right">Median tool calls</TableHead>
              <TableHead className="h-8 text-right">Median tokens</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {DESIGNS.map((d) => {
              const rs = shown.filter((r) => r.design === d.value);
              const n = rs.length;
              return (
                <TableRow key={d.value}>
                  <TableCell className="py-1.5 font-medium">{d.title}</TableCell>
                  <TableCell className="py-1.5 text-right tabular-nums">{n || ""}</TableCell>
                  {n === 0 ? <TableCell colSpan={7} className="py-1.5 text-muted-foreground">Not run yet.</TableCell> : (
                    <>
                      <TableCell className="py-1.5 text-right tabular-nums">{rs.filter((r) => r.score.covered === `${r.held.length}/${r.held.length}`).length}/{n}</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{rs.filter((r) => r.score["invented reasons"].length > 0).length}/{n}</TableCell>
                      <TableCell className={cn("py-1.5 text-right tabular-nums", rs.some((r) => r.score["follows the note"].length) ? "text-miss" : "text-ok")}>
                        {rs.filter((r) => r.score["follows the note"].length > 0).length}/{n}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{(median(rs.map((r) => Math.round(r.seconds * 1000))) / 1000).toFixed(1)} s</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">
                        {rs.every(retriesMetered) ? rs.reduce((s, r) => s + retriesOf(r), 0) : <span title="Recorded before retries were metered">–</span>}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{median(rs.map((r) => r.tool_calls))}</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{num(median(rs.map((r) => r.tokens)))}</TableCell>
                    </>
                  )}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>

        <RetryNote results={shown} />

        <div className="grid gap-4 xl:grid-cols-2">
          {DESIGNS.map((d) => (
            <section key={d.value} aria-label={d.title} className="flex min-w-0 flex-col gap-2 rounded-md border bg-card">
              <div className="border-b px-3 py-2">
                <div className="text-[13px] font-medium">{d.title}</div>
                <div className="text-xs text-muted-foreground">{d.note}</div>
              </div>
              <Table className="text-[13px]">
                <TableHeader>
                  <TableRow className="hover:bg-transparent">
                    <TableHead className="h-8">Run</TableHead>
                    <TableHead className="h-8">Covered</TableHead>
                    <TableHead className="h-8">Invented</TableHead>
                    <TableHead className="h-8">{INJECTED} note</TableHead>
                    <TableHead className="h-8 text-right">Time</TableHead>
                    <TableHead className="h-8 text-right">Retries</TableHead>
                    <TableHead className="h-8 text-right">Tool calls</TableHead>
                    <TableHead className="h-8 text-right">Tokens</TableHead>
                    <TableHead className="h-8 w-20" aria-label="Action" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {RUNS.map((n) => {
                    const key = keyOf(d.value, n);
                    const row = rows[key];
                    return (
                      <TableRow key={key} className={cn(picked === key && "bg-muted/60")}>
                        <TableCell className="py-1.5 tabular-nums">{n}</TableCell>
                        {row?.status === "done" ? <ScoreCells r={row.result} /> : (
                          <TableCell colSpan={7} className="max-w-0 py-1.5">
                            {row?.status === "running" && <Running />}
                            {row?.status === "error" && <ErrorText error={row.error} />}
                          </TableCell>
                        )}
                        <TableCell className="py-1.5 text-right">
                          {row?.status === "done" ? (
                            <Button size="xs" variant={picked === key ? "secondary" : "outline"} onClick={() => setPicked(picked === key ? null : key)}>
                              {picked === key ? "Hide" : "Report"}
                            </Button>
                          ) : (
                            <Button size="xs" variant="outline" onClick={() => runOne(key)} disabled={row?.status === "running"}>Run</Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </section>
          ))}
        </div>

        {pickedResult && (
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
            <Report result={pickedResult} />
            <MeterTable run={pickedResult} />
          </div>
        )}
      </div>
    </>
  );
}

type Tab = "handoffs" | "designs";

export default function AgentsPage() {
  const { get, live } = useApi();
  const [tab, setTab] = useState<Tab>("handoffs");
  const [mails, setMails] = useState<Mail[] | null>(null);
  const [truth, setTruth] = useState<Record<string, Truth>>({});
  const [loadError, setLoadError] = useState<ApiError | null>(null);

  useEffect(() => {
    (async () => {
      const [inbox, key] = await Promise.all([
        get<{ data: Mail[] }>("/api/data/inbox"),
        get<{ data: Record<string, Truth> }>("/api/data/inbox_truth"),
      ]);
      if (!inbox.ok) return setLoadError(inbox.error);
      if (!key.ok) return setLoadError(key.error);
      setMails(inbox.data.data);
      setTruth(key.data.data);
    })();
    // The datasets don't depend on the key, so load them once.
  }, []);

  if (loadError) return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  if (!mails) return <p className="p-6 text-muted-foreground">Loading the inbox…</p>;

  return (
    <div className="flex flex-col">
      <div role="tablist" aria-label="Agents" className="flex gap-4 border-b bg-card px-4">
        {([["handoffs", "Handoffs"], ["designs", "Multi-agent designs"]] as const).map(([value, text]) => (
          <button
            key={value}
            role="tab"
            aria-selected={tab === value}
            onClick={() => setTab(value)}
            className={cn(
              "-mb-px border-b-2 py-2 text-[13px] outline-none focus-visible:text-foreground",
              tab === value ? "border-primary font-medium" : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {text}
          </button>
        ))}
      </div>
      {/* Both tabs stay mounted, so switching keeps their results. */}
      <div role="tabpanel" hidden={tab !== "handoffs"}><HandoffsTab mails={mails} truth={truth} live={live} /></div>
      <div role="tabpanel" hidden={tab !== "designs"}><DesignsTab live={live} /></div>
    </div>
  );
}
