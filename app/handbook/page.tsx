"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Play, Send } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Row, type Run, Running, runMs, runTokens, Stat, Toolbar, useRows, Verdict,
} from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Question = { id: string; question: string; sources: string[]; must_contain: string[] };
type Section = { id: string; title: string; text: string };
type Checks = { sources_ok: boolean; found_ok: boolean; must_contain_ok: boolean };
type Answer = Run & {
  question_id: string | null;
  question: string;
  found: boolean;
  answer: string;
  sources: string[];
  retrieved: string[];
  scores: Record<string, number>;
  model: string;
  checks: Checks | null;
};

const CHECKS: { key: keyof Checks; miss: string }[] = [
  { key: "found_ok", miss: "found is wrong" },
  { key: "sources_ok", miss: "cites a section it didn't retrieve" },
  { key: "must_contain_ok", miss: "missing a required word" },
];

const misses = (c: Checks) => CHECKS.filter((k) => !c[k.key]).map((k) => k.miss);

function Sources({ ids, expected }: { ids: string[]; expected?: string[] }) {
  if (ids.length === 0) return <span className="text-muted-foreground">none</span>;
  return (
    <span className="inline-flex flex-wrap gap-1">
      {ids.map((id) => (
        <Badge
          key={id}
          variant="outline"
          className={cn("font-mono", expected && !expected.includes(id) && "border-miss/50 text-miss")}
          title={expected && !expected.includes(id) ? "Not in the answer key" : undefined}
        >
          {id}
        </Badge>
      ))}
    </span>
  );
}

/** The answer, then the three sections search() retrieved, best first, with the similarity it ranked them by. */
function AnswerDetail({ result, sections }: { result: Answer; sections: Record<string, Section> }) {
  const top = Math.max(...result.retrieved.map((id) => result.scores[id]));
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
      <div className="flex max-w-[80ch] flex-col gap-3">
        <div>
          <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
            <span>{result.found ? "Answer" : "Not found in the handbook"}</span>
            <span aria-hidden>·</span>
            <span>Sources</span>
            <Sources ids={result.sources} />
          </div>
          <p className="leading-relaxed">{result.answer}</p>
        </div>
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Retrieved sections, by similarity to the question</div>
          <ol className="flex flex-col gap-1.5">
            {result.retrieved.map((id) => {
              const s = sections[id];
              const cited = result.sources.includes(id);
              const score = result.scores[id];
              return (
                <li key={id} className={cn("rounded-md border bg-card px-3 py-2", cited && "border-primary/50")}>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs">{id}</span>
                    <span className="font-medium">{s?.title}</span>
                    {cited && <Badge variant="secondary" className="text-[11px]">cited</Badge>}
                    <span className="ml-auto flex items-center gap-2 text-xs tabular-nums text-muted-foreground">
                      <span className="h-1.5 w-24 overflow-hidden rounded-full bg-muted" aria-hidden>
                        <span className="block h-full bg-primary/70" style={{ width: `${(score / top) * 100}%` }} />
                      </span>
                      {score.toFixed(4)}
                    </span>
                  </div>
                  {s && <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{s.text}</p>}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
      <MeterTable run={result} />
    </div>
  );
}

function AskBox({ live, sections }: { live: boolean; sections: Record<string, Section> }) {
  const { post } = useApi();
  const [text, setText] = useState("");
  const [row, setRow] = useState<Row<Answer> | null>(null);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;
    setRow({ status: "running" });
    const res = await post<Answer>("/api/answer", { question: text.trim() });
    setRow(res.ok ? { status: "done", result: res.data } : { status: "error", error: res.error });
  }

  return (
    <div className="border-b px-4 py-3">
      <form onSubmit={ask} className="flex max-w-3xl items-center gap-2">
        <label htmlFor="ask" className="sr-only">Your question</label>
        <Input
          id="ask"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={2000}
          disabled={!live}
          placeholder={live ? "Ask the handbook anything, e.g. Who pays the freight under FOB?" : "Typing your own question runs Live only. Add your Mistral key to ask one."}
        />
        <Button type="submit" size="sm" disabled={!live || !text.trim() || row?.status === "running"}>
          {row?.status === "running" ? <Loader2 className="animate-spin" /> : <Send />}Ask
        </Button>
      </form>
      {row?.status === "error" && <p className="mt-2 text-miss">{describeError(row.error)}</p>}
      {row?.status === "done" && <div className="mt-3"><AnswerDetail result={row.result} sections={sections} /></div>}
    </div>
  );
}

export default function HandbookPage() {
  const { get, post, live } = useApi();
  const [questions, setQuestions] = useState<Question[] | null>(null);
  const [sections, setSections] = useState<Record<string, Section>>({});
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    (async () => {
      const [qs, hb] = await Promise.all([
        get<{ data: Question[] }>("/api/data/questions"),
        get<{ data: Section[] }>("/api/data/handbook"),
      ]);
      if (!qs.ok) return setLoadError(qs.error);
      if (!hb.ok) return setLoadError(hb.error);
      setQuestions(qs.data.data);
      setSections(Object.fromEntries(hb.data.data.map((s) => [s.id, s])));
    })();
    // The datasets don't depend on the key, so load them once.
  }, []);

  const run = useCallback((id: string) => post<Answer>("/api/answer", { question_id: id }), [post]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);

  if (loadError) return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  if (!questions) return <p className="p-6 text-muted-foreground">Loading the questions…</p>;

  const passed = done.filter((r) => r.checks && misses(r.checks).length === 0).length;

  return (
    <div className="flex flex-col">
      <Toolbar
        live={live}
        stats={done.length > 0 && (
          <>
            <Stat label="Answered" value={`${done.length} of ${questions.length}`} />
            <Stat label="Pass the answer key" value={`${passed} of ${done.length}`} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${num(median(done.map(runMs)))} ms`} />
            <Stat label="Tokens" value={num(done.reduce((s, r) => s + runTokens(r), 0))} />
          </>
        )}
      >
        <Button size="sm" onClick={() => runAll(questions.map((q) => q.id))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Answering, ${running} running` : `Answer all ${questions.length}`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>

      <AskBox live={live} sections={sections} />

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={3} className="h-7 text-xs text-muted-foreground">Question</TableHead>
            <TableHead className="h-7 border-l text-xs text-muted-foreground">Answer key</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Dock</TableHead>
            <TableHead colSpan={4} className="h-7 border-l text-xs text-muted-foreground">Run</TableHead>
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">ID</TableHead>
            <TableHead className="h-8">Question</TableHead>
            <TableHead className="h-8 border-l">Sources</TableHead>
            <TableHead className="h-8 border-l">Found</TableHead>
            <TableHead className="h-8">Sources</TableHead>
            <TableHead className="h-8 border-l">Result</TableHead>
            <TableHead className="h-8 text-right">Latency</TableHead>
            <TableHead className="h-8 text-right">Tokens</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {questions.map((q) => {
            const row = rows[q.id];
            const result = row?.status === "done" ? row.result : null;
            const isOpen = open[q.id] ?? false;
            const missed = result?.checks ? misses(result.checks) : [];
            return (
              <Fragment key={q.id}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={isOpen ? `Hide ${q.id}` : `Show ${q.id}`}
                      aria-expanded={isOpen}
                      onClick={() => setOpen((o) => ({ ...o, [q.id]: !isOpen }))}
                    >
                      {isOpen ? <ChevronDown /> : <ChevronRight />}
                    </Button>
                  </TableCell>
                  <TableCell className="py-1.5 font-mono text-xs">{q.id}</TableCell>
                  <TableCell className="max-w-[28rem] truncate py-1.5" title={q.question}>{q.question}</TableCell>
                  <TableCell className="border-l bg-muted/30 py-1.5 text-muted-foreground">
                    <Sources ids={q.sources} />
                  </TableCell>

                  {row?.status === "error" ? (
                    <TableCell colSpan={5} className="max-w-0 border-l py-1.5">
                      <span className="block truncate text-miss" title={describeError(row.error)}>{describeError(row.error)}</span>
                    </TableCell>
                  ) : (
                    <>
                      <TableCell className={cn("border-l py-1.5", result?.checks && !result.checks.found_ok && "bg-miss/10 font-medium text-miss")}>
                        {result ? (result.found ? "yes" : "no") : ""}
                      </TableCell>
                      <TableCell className="py-1.5">{result && <Sources ids={result.sources} expected={q.sources} />}</TableCell>
                      <TableCell className="border-l py-1.5">
                        {row?.status === "running" && <Running />}
                        {result && <span title={missed.join(", ") || undefined}><Verdict ok={missed.length === 0} yes="Pass" no={missed.length === 1 ? missed[0] : `${missed.length} checks fail`} /></span>}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result && <Latency run={result} />}</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result ? num(runTokens(result)) : ""}</TableCell>
                    </>
                  )}
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => runOne(q.id)} disabled={row?.status === "running"}>
                      {row ? "Run again" : "Answer"}
                    </Button>
                  </TableCell>
                </TableRow>

                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={9} className="whitespace-normal pb-3 pt-0">
                      <p className="mb-2 max-w-[80ch] font-medium">{q.question}</p>
                      {result ? <AnswerDetail result={result} sections={sections} /> : (
                        <p className="text-muted-foreground">Not answered yet. Press Answer to see Dock’s answer and the sections it retrieved.</p>
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
