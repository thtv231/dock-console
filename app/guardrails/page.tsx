"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Play, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Row, type Run, Running, runMs, runTokens, Segmented, Stat, Toolbar, useRows, Verdict,
} from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Bad = { id: string; text: string; kind: string };
type Mail = { id: string; from: string; subject: string; body: string };
type Ask = { id: string; text: string };
type Message = { id: string; kind: string; text: string; block: boolean };
type Category = { flagged: boolean; score: number };
type Screen = Run & {
  message_id: string | null;
  text: string;
  reasons: string[];
  block: boolean;
  ignored: string[];
  others_data: boolean;
  categories: Record<string, Category>;
  right: boolean | null;
};
type Rule = "dock" | "plain";
type Reply = Run & {
  ask_id: string | null;
  text: string;
  rule: Rule;
  reply: string;
  guarantees_date: boolean;
  judge: { guarantees_date: boolean; quote: string; quote_in_reply: boolean };
};

const RULES: { value: Rule; label: string }[] = [
  { value: "dock", label: "Dock’s rule" },
  { value: "plain", label: "Without the date rule" },
];
const RULE_NOTE: Record<Rule, string> = {
  dock: "Dock’s instructions say: never promise or guarantee a delivery or arrival date; an ETA is allowed if it is called an estimate.",
  plain: "The section 7 starting point: “be helpful and reassuring”, with no date rule. Shown for contrast.",
};

const label = (category: string) => category.replaceAll("_", " ");

function Decision({ block }: { block: boolean }) {
  return <span className={cn(block && "font-medium")}>{block ? "Block" : "Let through"}</span>;
}

/** Every moderation category with its score. pii is shown with its real score but never counts as a reason. */
function CategoryBars({ result }: { result: Screen }) {
  const cats = Object.entries(result.categories).sort((a, b) => b[1].score - a[1].score);
  return (
    <div className="flex flex-col gap-1">
      <div className="text-xs text-muted-foreground">Moderation categories (mistral-moderation-latest)</div>
      <table className="text-xs tabular-nums">
        <tbody>
          {cats.map(([name, c]) => {
            const ignored = result.ignored.includes(name);
            const counts = c.flagged && !ignored;
            return (
              <tr key={name} className={cn(!c.flagged && "text-muted-foreground")}>
                <td className="w-44 py-0.5 pr-3">{label(name)}</td>
                <td className="w-48 py-0.5 pr-3">
                  <span className="block h-2 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
                    <span
                      className={cn("block h-full", counts ? "bg-primary" : c.flagged ? "bg-muted-foreground/50" : "bg-muted-foreground/25")}
                      style={{ width: `${Math.max(c.score * 100, 0.5)}%` }}
                    />
                  </span>
                </td>
                <td className="w-14 py-0.5 pr-3 text-right">{c.score.toFixed(3)}</td>
                <td className="py-0.5">
                  {counts && <span className="font-medium">flagged: a reason to block</span>}
                  {c.flagged && ignored && <span>flagged, ignored: customers always write names and refs</span>}
                </td>
              </tr>
            );
          })}
          <tr className="border-t">
            <td className="py-1 pr-3">others’ data</td>
            <td className="py-1 pr-3 text-muted-foreground" colSpan={2}>mistral-small-latest, a yes/no check</td>
            <td className={cn("py-1", result.others_data ? "font-medium" : "text-muted-foreground")}>
              {result.others_data ? "yes: asks for other customers’ data" : "no"}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function ScreenDetail({ result }: { result: Screen }) {
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
      <div className="flex flex-col gap-3">
        <p className="max-w-[80ch] whitespace-pre-wrap leading-relaxed">{result.text}</p>
        <CategoryBars result={result} />
        <p>
          Dock’s decision: <Decision block={result.block} />
          {result.reasons.length > 0 && <span className="text-muted-foreground"> because of {result.reasons.map(label).join(", ")}</span>}
        </p>
      </div>
      <MeterTable run={result} />
    </div>
  );
}

/** The reply, with the judge's quote marked when it is the sentence that guarantees a date. */
function ReplyDetail({ result }: { result: Reply }) {
  const quote = result.judge.quote.trim();
  const at = result.guarantees_date ? result.reply.indexOf(quote) : -1;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
      <div className="flex max-w-[80ch] flex-col gap-3">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Customer asks</div>
          <p>{result.text}</p>
        </div>
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Dock’s reply</div>
          <p className="whitespace-pre-wrap rounded-md border bg-card px-3 py-2 leading-relaxed">
            {at < 0 ? result.reply : (
              <>
                {result.reply.slice(0, at)}
                <mark className="rounded-sm bg-miss/15 px-0.5 text-foreground ring-1 ring-miss/40">{quote}</mark>
                {result.reply.slice(at + quote.length)}
              </>
            )}
          </p>
        </div>
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Policy judge (mistral-small-latest)</div>
          {result.guarantees_date ? (
            <p className="text-miss">Guarantees a date. The judge quotes: “{quote}”</p>
          ) : result.judge.guarantees_date ? (
            <p>The judge said it guarantees a date but its quote is not in the reply, so it does not count{quote ? `: “${quote}”` : "."}</p>
          ) : (
            <p className="text-ok">No guaranteed date.</p>
          )}
        </div>
      </div>
      <MeterTable run={result} />
    </div>
  );
}

function FreeText({ live, placeholder, onSubmit, busy, children }: {
  live: boolean; placeholder: string; onSubmit: (text: string) => void; busy: boolean; children?: React.ReactNode;
}) {
  const [text, setText] = useState("");
  return (
    <div className="border-b px-4 py-3">
      <form
        onSubmit={(e) => { e.preventDefault(); if (text.trim()) onSubmit(text.trim()); }}
        className="flex max-w-3xl items-center gap-2"
      >
        <label htmlFor="free-text" className="sr-only">Your message</label>
        <Input
          id="free-text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          maxLength={10000}
          disabled={!live}
          placeholder={live ? placeholder : "Typing your own message runs Live only. Add your Mistral key to try one."}
        />
        <Button type="submit" size="sm" disabled={!live || !text.trim() || busy}>
          {busy ? <Loader2 className="animate-spin" /> : <Send />}Run
        </Button>
      </form>
      {children}
    </div>
  );
}

function Expand({ id, open, onToggle }: { id: string; open: boolean; onToggle: () => void }) {
  return (
    <Button variant="ghost" size="icon-xs" aria-label={open ? `Hide ${id}` : `Show ${id}`} aria-expanded={open} onClick={onToggle}>
      {open ? <ChevronDown /> : <ChevronRight />}
    </Button>
  );
}

function ErrorCell({ error, span }: { error: ApiError; span: number }) {
  return (
    <TableCell colSpan={span} className="max-w-0 border-l py-1.5">
      <span className="block truncate text-miss" title={describeError(error)}>{describeError(error)}</span>
    </TableCell>
  );
}

function ScreenTab({ messages, live }: { messages: Message[]; live: boolean }) {
  const { post } = useApi();
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [own, setOwn] = useState<Row<Screen> | null>(null);
  const run = useCallback((id: string) => post<Screen>("/api/guardrails/screen", { message_id: id }), [post]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);

  async function screenText(text: string) {
    setOwn({ status: "running" });
    const res = await post<Screen>("/api/guardrails/screen", { text });
    setOwn(res.ok ? { status: "done", result: res.data } : { status: "error", error: res.error });
  }

  const bad = done.filter((r) => messages.find((m) => m.id === r.message_id)?.block);
  const normal = done.filter((r) => !messages.find((m) => m.id === r.message_id)?.block);

  return (
    <>
      <Toolbar
        live={live}
        stats={done.length > 0 && (
          <>
            <Stat label="Bad messages blocked" value={`${bad.filter((r) => r.block).length} of ${bad.length}`} />
            <Stat label="Normal emails let through" value={`${normal.filter((r) => !r.block).length} of ${normal.length}`} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${num(median(done.map(runMs)))} ms`} />
            <Stat label="Tokens" value={num(done.reduce((s, r) => s + runTokens(r), 0))} />
          </>
        )}
      >
        <Button size="sm" onClick={() => runAll(messages.map((m) => m.id))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Screening, ${running} running` : `Screen all ${messages.length}`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>

      <FreeText live={live} placeholder="Paste an inbound message to screen it" onSubmit={screenText} busy={own?.status === "running"}>
        {own?.status === "error" && <p className="mt-2 text-miss">{describeError(own.error)}</p>}
        {own?.status === "done" && <div className="mt-3"><ScreenDetail result={own.result} /></div>}
      </FreeText>

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={3} className="h-7 text-xs text-muted-foreground">Message</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Answer key</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Dock</TableHead>
            <TableHead colSpan={4} className="h-7 border-l text-xs text-muted-foreground">Run</TableHead>
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">ID</TableHead>
            <TableHead className="h-8">Text</TableHead>
            <TableHead className="h-8 border-l">Kind</TableHead>
            <TableHead className="h-8">Expect</TableHead>
            <TableHead className="h-8 border-l">Decision</TableHead>
            <TableHead className="h-8">Reasons</TableHead>
            <TableHead className="h-8 border-l">Result</TableHead>
            <TableHead className="h-8 text-right">Latency</TableHead>
            <TableHead className="h-8 text-right">Tokens</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {messages.map((m) => {
            const row = rows[m.id];
            const result = row?.status === "done" ? row.result : null;
            const isOpen = open[m.id] ?? false;
            return (
              <Fragment key={m.id}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5"><Expand id={m.id} open={isOpen} onToggle={() => setOpen((o) => ({ ...o, [m.id]: !isOpen }))} /></TableCell>
                  <TableCell className="py-1.5 font-mono text-xs">{m.id}</TableCell>
                  <TableCell className="max-w-[26rem] truncate py-1.5" title={m.text}>{m.text}</TableCell>
                  <TableCell className="border-l bg-muted/30 py-1.5 text-muted-foreground">{m.kind}</TableCell>
                  <TableCell className="bg-muted/30 py-1.5 text-muted-foreground"><Decision block={m.block} /></TableCell>
                  {row?.status === "error" ? <ErrorCell error={row.error} span={5} /> : (
                    <>
                      <TableCell className={cn("border-l py-1.5", result && result.block !== m.block && "bg-miss/10 text-miss")}>
                        {result && <Decision block={result.block} />}
                      </TableCell>
                      <TableCell className="max-w-56 truncate py-1.5 text-muted-foreground" title={result?.reasons.map(label).join(", ")}>
                        {result && (result.reasons.map(label).join(", ") || "none")}
                      </TableCell>
                      <TableCell className="border-l py-1.5">
                        {row?.status === "running" && <Running />}
                        {result && <Verdict ok={result.block === m.block} no={m.block ? "Let through" : "Blocked"} />}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result && <Latency run={result} />}</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result ? num(runTokens(result)) : ""}</TableCell>
                    </>
                  )}
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => runOne(m.id)} disabled={row?.status === "running"}>
                      {row ? "Run again" : "Screen"}
                    </Button>
                  </TableCell>
                </TableRow>
                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={10} className="whitespace-normal pb-3 pt-0">
                      {result ? <ScreenDetail result={result} /> : (
                        <>
                          <p className="max-w-[80ch] whitespace-pre-wrap leading-relaxed">{m.text}</p>
                          <p className="mt-2 text-muted-foreground">Not screened yet. Press Screen to see the moderation scores.</p>
                        </>
                      )}
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

function ReplyTab({ asks, live }: { asks: Ask[]; live: boolean }) {
  const { post } = useApi();
  const [rule, setRule] = useState<Rule>("dock");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [own, setOwn] = useState<Row<Reply> | null>(null);
  // Rows are keyed rule:id, so switching the rule keeps both sets of results.
  const run = useCallback((key: string) => {
    const [r, id] = key.split(":");
    return post<Reply>("/api/guardrails/reply", { ask_id: id, rule: r });
  }, [post]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);
  const forRule = done.filter((r) => r.rule === rule);

  async function replyTo(text: string) {
    setOwn({ status: "running" });
    const res = await post<Reply>("/api/guardrails/reply", { text, rule });
    setOwn(res.ok ? { status: "done", result: res.data } : { status: "error", error: res.error });
  }

  return (
    <>
      <Toolbar
        live={live}
        stats={forRule.length > 0 && (
          <>
            <Stat label="Replies with no guaranteed date" value={`${forRule.filter((r) => !r.guarantees_date).length} of ${forRule.length}`} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${num(median(forRule.map(runMs)))} ms`} />
            <Stat label="Tokens" value={num(forRule.reduce((s, r) => s + runTokens(r), 0))} />
          </>
        )}
      >
        <Segmented label="Instructions" value={rule} options={RULES} onChange={setRule} />
        <Button size="sm" onClick={() => runAll(asks.map((a) => `${rule}:${a.id}`))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Drafting, ${running} running` : `Draft all ${asks.length}`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>
      <p className="border-b px-4 py-2 text-xs text-muted-foreground">{RULE_NOTE[rule]}</p>

      <FreeText live={live} placeholder="Write a customer message and see Dock’s reply under these instructions" onSubmit={replyTo} busy={own?.status === "running"}>
        {own?.status === "error" && <p className="mt-2 text-miss">{describeError(own.error)}</p>}
        {own?.status === "done" && <div className="mt-3"><ReplyDetail result={own.result} /></div>}
      </FreeText>

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">ID</TableHead>
            <TableHead className="h-8">Customer asks</TableHead>
            <TableHead className="h-8 border-l">Dock’s reply</TableHead>
            <TableHead className="h-8 border-l">Result</TableHead>
            <TableHead className="h-8 text-right">Latency</TableHead>
            <TableHead className="h-8 text-right">Tokens</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {asks.map((a) => {
            const key = `${rule}:${a.id}`;
            const row = rows[key];
            const result = row?.status === "done" ? row.result : null;
            const isOpen = open[key] ?? false;
            return (
              <Fragment key={key}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5"><Expand id={a.id} open={isOpen} onToggle={() => setOpen((o) => ({ ...o, [key]: !isOpen }))} /></TableCell>
                  <TableCell className="py-1.5 font-mono text-xs">{a.id}</TableCell>
                  <TableCell className="max-w-[22rem] truncate py-1.5" title={a.text}>{a.text}</TableCell>
                  {row?.status === "error" ? <ErrorCell error={row.error} span={4} /> : (
                    <>
                      <TableCell className="max-w-[24rem] truncate border-l py-1.5 text-muted-foreground" title={result?.reply}>{result?.reply}</TableCell>
                      <TableCell className="border-l py-1.5">
                        {row?.status === "running" && <Running />}
                        {result && <Verdict ok={!result.guarantees_date} yes="No date promised" no="Guarantees a date" />}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result && <Latency run={result} />}</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result ? num(runTokens(result)) : ""}</TableCell>
                    </>
                  )}
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => runOne(key)} disabled={row?.status === "running"}>
                      {row ? "Run again" : "Draft"}
                    </Button>
                  </TableCell>
                </TableRow>
                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={7} className="whitespace-normal pb-3 pt-0">
                      {result ? <ReplyDetail result={result} /> : (
                        <p className="text-muted-foreground">{a.text} Not drafted yet. Press Draft to see the reply and the judge’s verdict.</p>
                      )}
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

type Tab = "screen" | "reply";

export default function GuardrailsPage() {
  const { get, live } = useApi();
  const [tab, setTab] = useState<Tab>("screen");
  const [messages, setMessages] = useState<Message[] | null>(null);
  const [asks, setAsks] = useState<Ask[]>([]);
  const [loadError, setLoadError] = useState<ApiError | null>(null);

  useEffect(() => {
    (async () => {
      const [bad, inbox, ask] = await Promise.all([
        get<{ data: Bad[] }>("/api/data/bad"),
        get<{ data: Mail[] }>("/api/data/inbox"),
        get<{ data: Ask[] }>("/api/data/asks"),
      ]);
      if (!bad.ok) return setLoadError(bad.error);
      if (!inbox.ok) return setLoadError(inbox.error);
      if (!ask.ok) return setLoadError(ask.error);
      setMessages([
        ...bad.data.data.map((b) => ({ id: b.id, kind: b.kind, text: b.text, block: true })),
        // Screened as section 7 did: subject, blank line, body.
        ...inbox.data.data.map((m) => ({ id: m.id, kind: "normal", text: `${m.subject}\n\n${m.body}`, block: false })),
      ]);
      setAsks(ask.data.data);
    })();
    // The datasets don't depend on the key, so load them once.
  }, []);

  if (loadError) return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  if (!messages) return <p className="p-6 text-muted-foreground">Loading the messages…</p>;

  return (
    <div className="flex flex-col">
      <div role="tablist" aria-label="Guardrails" className="flex gap-4 border-b bg-card px-4">
        {([["screen", "Screen inbound messages"], ["reply", "Never promise a date"]] as const).map(([value, text]) => (
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
      <div role="tabpanel" hidden={tab !== "screen"}><ScreenTab messages={messages} live={live} /></div>
      <div role="tabpanel" hidden={tab !== "reply"}><ReplyTab asks={asks} live={live} /></div>
    </div>
  );
}
