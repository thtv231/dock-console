"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Play } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Row, type Run, Running, runMs, runTokens, Stat, Toolbar, useRows, Verdict,
} from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Kind = "triage" | "answer" | "screen" | "reply" | "agent";
type Version = "baseline" | "cheaper";
type Case = { id: string; kind: Kind; input: unknown; shipment?: string; expect: Record<string, unknown> };
type CaseResult = Run & {
  case_id: string;
  kind: Kind;
  versions: Version;
  models: Record<string, { model: string }>;
  ok: boolean;
  detail: string;
  trace?: string[];
  sent?: string[];
};
type Judged = Run & {
  case_id: string;
  versions: Version;
  question: string;
  grounded: boolean;
  unsupported: string;
  answer: string;
  judge: { grounded: boolean; unsupported: string; unsupported_in_answer: boolean };
};

const KINDS: { kind: Kind; label: string }[] = [
  { kind: "triage", label: "Triage" },
  { kind: "answer", label: "Handbook answers" },
  { kind: "screen", label: "Screening" },
  { kind: "reply", label: "Replies" },
  { kind: "agent", label: "Agent path" },
];
const VERSIONS: { v: Version; label: string }[] = [
  { v: "baseline", label: "Baseline" },
  { v: "cheaper", label: "Cheaper" },
];

const caseRow = (id: string, v: Version) => `case|${id}|${v}`;
const judgeRow = (id: string, v: Version) => `judge|${id}|${v}`;
const judged = (c: Case) => c.kind === "answer" && Array.isArray(c.expect.sources) && c.expect.sources.length > 0;

/** What one case sends to Dock, as one line. */
function inputText(c: Case) {
  if (typeof c.input === "string") return c.input;
  const mail = c.input as { subject?: string; body?: string };
  return [mail.subject, mail.body].filter(Boolean).join(": ");
}

function expectText(c: Case) {
  return Object.entries(c.expect)
    .map(([k, v]) => `${k} ${Array.isArray(v) ? (v.length ? v.join(", ") : "none") : String(v)}`)
    .join(" · ");
}

/** The judged answer with the judge's unsupported phrase marked where it occurs in it. */
function Quoted({ j }: { j: Judged }) {
  const phrase = j.judge.unsupported.trim();
  const at = phrase ? j.answer.indexOf(phrase) : -1;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>Judge, on the {j.versions === "baseline" ? "baseline’s" : "cheaper candidate’s"} answer</span>
        <Verdict ok={j.grounded} yes="Grounded in the sections" no="Unsupported" />
      </div>
      <blockquote className="border-l-2 pl-3 leading-relaxed">
        {at >= 0 ? (
          <>
            {j.answer.slice(0, at)}
            <mark className="rounded-sm bg-miss/15 px-0.5 text-foreground">{phrase}</mark>
            {j.answer.slice(at + phrase.length)}
          </>
        ) : j.answer}
      </blockquote>
      <p className="text-xs text-muted-foreground">
        The judge asks Dock the question again on this version, so this answer can differ from the one the case checked.{" "}
        {phrase === ""
          ? "The judge named no unsupported phrase."
          : at >= 0
            ? `The judge quoted “${phrase}” from the answer as unsupported.`
            : `The judge named “${phrase}”, which is not in the answer, so it does not count as evidence.`}
      </p>
    </div>
  );
}

function CaseDetail({ c, results, judges }: {
  c: Case; results: Partial<Record<Version, CaseResult>>; judges: Partial<Record<Version, Judged>>;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="max-w-[80ch]">
        <div className="mb-1 text-xs text-muted-foreground">Input{c.shipment ? `, about ${c.shipment}` : ""}</div>
        <p className="leading-relaxed">{inputText(c)}</p>
        <div className="mt-1 text-xs text-muted-foreground">Expected: {expectText(c)}</div>
      </div>
      <div className="grid gap-3 lg:grid-cols-2">
        {VERSIONS.map(({ v, label }) => {
          const r = results[v];
          return (
            <div key={v} className="flex min-w-0 flex-col gap-2 rounded-md border bg-card px-3 py-2.5">
              <div className="flex items-center gap-2 text-xs font-medium">
                {label}
                {r && Object.keys(r.models).length > 0 && (
                  <span className="font-normal text-muted-foreground">
                    {Object.entries(r.models).map(([k, m]) => `${k} on ${m.model}`).join(", ")}
                  </span>
                )}
              </div>
              {r ? (
                <>
                  <Verdict ok={r.ok} yes="Pass" no="Fail" />
                  {r.detail && <p className="font-mono text-xs">{r.detail}</p>}
                  {r.trace && (
                    <div>
                      <div className="mb-1 text-xs text-muted-foreground">The path the agent took</div>
                      <ol className="flex flex-wrap gap-1">
                        {r.trace.map((t, i) => (
                          <li key={i}>
                            <Badge variant="outline" className={cn("font-mono", t === "list_customers" && "border-miss/50 text-miss")}>{t}</Badge>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                  <MeterTable run={r} />
                </>
              ) : <p className="text-muted-foreground">Not run yet.</p>}
              {judges[v] && (
                <div className="mt-1 border-t pt-2">
                  <Quoted j={judges[v]} />
                  <div className="mt-2"><MeterTable run={judges[v]} /></div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function ScorecardPage() {
  const { get, post, live } = useApi();
  const [cases, setCases] = useState<Case[] | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    (async () => {
      const res = await get<{ data: Case[] }>("/api/data/scorecard");
      if (!res.ok) return setLoadError(res.error);
      setCases(res.data.data);
    })();
    // The dataset doesn't depend on the key, so load it once.
  }, []);

  const run = useCallback((rowId: string) => {
    const [what, id, v] = rowId.split("|");
    return what === "judge"
      ? post<CaseResult | Judged>("/api/scorecard/judge", { case_id: id, versions: v })
      : post<CaseResult | Judged>("/api/scorecard/case", { case_id: id, versions: v });
  }, [post]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);

  if (loadError) return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  if (!cases) return <p className="p-6 text-muted-foreground">Loading the cases…</p>;

  const result = (rowId: string) => {
    const r = rows[rowId];
    return r?.status === "done" ? r.result : null;
  };
  const caseResult = (id: string, v: Version) => result(caseRow(id, v)) as CaseResult | null;
  const judgeResult = (id: string, v: Version) => result(judgeRow(id, v)) as Judged | null;
  const allRows = (list: Case[]) => [
    ...list.flatMap((c) => VERSIONS.map(({ v }) => caseRow(c.id, v))),
    ...list.filter(judged).flatMap((c) => VERSIONS.map(({ v }) => judgeRow(c.id, v))),
  ];

  const scored = (v: Version, list = cases) => {
    const got = list.map((c) => caseResult(c.id, v)).filter((r): r is CaseResult => r !== null);
    return { run: got.length, passed: got.filter((r) => r.ok).length };
  };
  const both = cases.flatMap((c) => {
    const b = caseResult(c.id, "baseline");
    const ch = caseResult(c.id, "cheaper");
    return b && ch ? [{ c, b, ch }] : [];
  });
  const regressions = both.filter(({ b, ch }) => b.ok && !ch.ok);
  const fixes = both.filter(({ b, ch }) => !b.ok && ch.ok);
  const base = scored("baseline");
  const cheap = scored("cheaper");

  return (
    <div className="flex flex-col">
      <Toolbar
        live={live}
        run="case"
        stats={done.length > 0 && (
          <>
            <Stat label="Baseline" value={`${base.passed} of ${base.run} pass`} />
            <Stat label="Cheaper" value={`${cheap.passed} of ${cheap.run} pass`} />
            <Stat label="Regressions" value={both.length ? String(regressions.length) : "–"} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${num(median(done.map(runMs)))} ms`} />
            <Stat label="Tokens" value={num(done.reduce((s, r) => s + runTokens(r), 0))} />
          </>
        )}
      >
        <Button size="sm" onClick={() => runAll(allRows(cases))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Running, ${running} at a time` : `Run all ${cases.length} on both versions`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>

      {both.length > 0 && (
        <section aria-labelledby="regressions" className="border-b px-4 py-3">
          <h2 id="regressions" className="mb-1.5 text-xs font-medium">
            Baseline against cheaper: {regressions.length} {regressions.length === 1 ? "regression" : "regressions"}
            {fixes.length > 0 && `, ${fixes.length} fixed`}, over {both.length} cases run on both
          </h2>
          {regressions.length + fixes.length === 0 ? (
            <p className="text-muted-foreground">Every case run on both versions has the same result.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {[...regressions, ...fixes].map(({ c, b, ch }) => (
                <li key={c.id} className="flex flex-wrap items-baseline gap-x-3">
                  <button
                    className="font-mono text-xs underline-offset-2 hover:underline"
                    onClick={() => setOpen((o) => ({ ...o, [c.id]: true }))}
                  >
                    {c.id}
                  </button>
                  <span className="text-xs text-muted-foreground">{b.ok ? "pass" : "fail"} → {ch.ok ? "pass" : "fail"}</span>
                  <span className={cn("font-mono text-xs", !ch.ok && "text-miss")}>{(ch.ok ? b.detail : ch.detail) || ""}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">Case</TableHead>
            <TableHead className="h-8">Input</TableHead>
            <TableHead className="h-8 border-l">Baseline</TableHead>
            <TableHead className="h-8">Cheaper</TableHead>
            <TableHead className="h-8 border-l">Judge, baseline</TableHead>
            <TableHead className="h-8">Judge, cheaper</TableHead>
            <TableHead className="h-8 border-l text-right">Latency</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {KINDS.map(({ kind, label }) => {
            const list = cases.filter((c) => c.kind === kind);
            if (list.length === 0) return null;
            const b = scored("baseline", list);
            const ch = scored("cheaper", list);
            return (
              <Fragment key={kind}>
                <TableRow className="bg-muted/30 hover:bg-muted/30">
                  <TableCell colSpan={3} className="py-1.5 text-xs font-medium">{label} <span className="font-normal text-muted-foreground">({list.length})</span></TableCell>
                  <TableCell className="border-l py-1.5 text-xs tabular-nums text-muted-foreground">{b.run ? `${b.passed}/${b.run}` : ""}</TableCell>
                  <TableCell className="py-1.5 text-xs tabular-nums text-muted-foreground">{ch.run ? `${ch.passed}/${ch.run}` : ""}</TableCell>
                  <TableCell colSpan={4} className="border-l" />
                </TableRow>
                {list.map((c) => {
                  const res = { baseline: caseResult(c.id, "baseline") ?? undefined, cheaper: caseResult(c.id, "cheaper") ?? undefined };
                  const js = { baseline: judgeResult(c.id, "baseline") ?? undefined, cheaper: judgeResult(c.id, "cheaper") ?? undefined };
                  const ids = [caseRow(c.id, "baseline"), caseRow(c.id, "cheaper"),
                    ...(judged(c) ? VERSIONS.map(({ v }) => judgeRow(c.id, v)) : [])];
                  const busy = ids.some((i) => rows[i]?.status === "running");
                  const errors = ids.flatMap((i) => (rows[i]?.status === "error" ? [(rows[i] as { error: ApiError }).error] : []));
                  const isOpen = open[c.id] ?? false;
                  const ran = [res.baseline, res.cheaper, js.baseline, js.cheaper].filter((r): r is CaseResult | Judged => !!r);
                  const judgeCell = (v: Version, first: boolean) => {
                    const j = js[v];
                    return (
                      <TableCell className={cn("max-w-[12rem] py-1.5", first && "border-l")}>
                        {rows[judgeRow(c.id, v)]?.status === "running" && <Running />}
                        {j && <span className="block truncate" title={j.judge.unsupported || undefined}><Verdict ok={j.grounded} yes="Grounded" no={`“${j.unsupported.trim()}”`} /></span>}
                        {!judged(c) && first && <span className="text-muted-foreground" aria-label="Not judged">–</span>}
                      </TableCell>
                    );
                  };
                  const regressed = res.baseline?.ok && res.cheaper && !res.cheaper.ok;
                  const cell = (v: Version) => {
                    const r = res[v];
                    if (rows[caseRow(c.id, v)]?.status === "running") return <Running />;
                    if (!r) return null;
                    return <span title={r.detail || undefined}><Verdict ok={r.ok} yes="Pass" no={r.detail ? r.detail.slice(0, 32) + (r.detail.length > 32 ? "…" : "") : "Fail"} /></span>;
                  };
                  return (
                    <Fragment key={c.id}>
                      <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                        <TableCell className="py-1.5">
                          <Button
                            variant="ghost"
                            size="icon-xs"
                            aria-label={isOpen ? `Hide ${c.id}` : `Show ${c.id}`}
                            aria-expanded={isOpen}
                            onClick={() => setOpen((o) => ({ ...o, [c.id]: !isOpen }))}
                          >
                            {isOpen ? <ChevronDown /> : <ChevronRight />}
                          </Button>
                        </TableCell>
                        <TableCell className="py-1.5 font-mono text-xs">{c.id}</TableCell>
                        <TableCell className="max-w-[24rem] truncate py-1.5" title={inputText(c)}>{inputText(c)}</TableCell>
                        {errors.length > 0 && !busy ? (
                          <TableCell colSpan={5} className="max-w-0 border-l py-1.5">
                            <span className="block truncate text-miss" title={describeError(errors[0])}>{describeError(errors[0])}</span>
                          </TableCell>
                        ) : (
                          <>
                            <TableCell className="border-l py-1.5">{cell("baseline")}</TableCell>
                            <TableCell className={cn("py-1.5", regressed && "bg-miss/10")}>
                              {cell("cheaper")}
                              {regressed && <span className="sr-only"> (regression)</span>}
                            </TableCell>
                            {judgeCell("baseline", true)}
                            {judgeCell("cheaper", false)}
                            <TableCell className="border-l py-1.5 text-right tabular-nums">
                              {ran.length > 0 && <Latency run={{ mode: ran[0].mode, meter: ran.flatMap((r) => r.meter), recorded_at: ran[0].recorded_at, git_sha: ran[0].git_sha }} />}
                            </TableCell>
                          </>
                        )}
                        <TableCell className="py-1.5 text-right">
                          <Button size="xs" variant="outline" onClick={() => ids.forEach(runOne)} disabled={busy}>
                            {ran.length ? "Run again" : "Run"}
                          </Button>
                        </TableCell>
                      </TableRow>
                      {isOpen && (
                        <TableRow className="bg-muted/40 hover:bg-muted/40">
                          <TableCell />
                          <TableCell colSpan={8} className="whitespace-normal pb-3 pt-0">
                            <CaseDetail c={c} results={res} judges={js} />
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
