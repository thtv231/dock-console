"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Run, Running, runMs, runTokens, Stat, Toolbar, useRows, Verdict,
} from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mail = { id: string; from: string; subject: string; body: string };
type Fields = { intent: string; shipment_id: string | null; language: string };
type Triage = Run & Fields & {
  mail_id: string;
  model: string;
  expected: Fields | null;
  wrong_fields: string[];
};

const FIELDS: { key: keyof Fields; label: string }[] = [
  { key: "intent", label: "Intent" },
  { key: "shipment_id", label: "Shipment" },
  { key: "language", label: "Lang" },
];
const show = (v: string | null) => v ?? "none";

export default function InboxPage() {
  const { get, post, live } = useApi();
  const [mails, setMails] = useState<Mail[] | null>(null);
  const [truth, setTruth] = useState<Record<string, Fields>>({});
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});

  useEffect(() => {
    (async () => {
      const [inbox, key] = await Promise.all([
        get<{ data: Mail[] }>("/api/data/inbox"),
        get<{ data: Record<string, Fields> }>("/api/data/inbox_truth"),
      ]);
      if (!inbox.ok) return setLoadError(inbox.error);
      if (!key.ok) return setLoadError(key.error);
      setMails(inbox.data.data);
      setTruth(key.data.data);
    })();
    // The datasets don't depend on the key, so load them once.
  }, []);

  const run = useCallback((id: string) => post<Triage>("/api/triage", { mail_id: id }), [post]);
  const { rows, runOne: triage, runAll, runningAll, running, failed, done, clear } = useRows(run);

  if (loadError) {
    return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  }
  if (!mails) {
    return <p className="p-6 text-muted-foreground">Loading the inbox…</p>;
  }

  return (
    <div className="flex flex-col">
      <Toolbar
        live={live}
        run="triage"
        stats={done.length > 0 && (
          <>
            <Stat label="Triaged" value={`${done.length} of ${mails.length}`} />
            <Stat label="Match the answer key" value={`${done.filter((r) => r.wrong_fields.length === 0).length} of ${done.length}`} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${num(median(done.map(runMs)))} ms`} />
            <Stat label="Tokens" value={num(done.reduce((s, r) => s + runTokens(r), 0))} />
          </>
        )}
      >
        <Button size="sm" onClick={() => runAll(mails.map((m) => m.id))} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Triaging, ${running} running` : `Triage all ${mails.length}`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={4} className="h-7 text-xs text-muted-foreground">Email</TableHead>
            <TableHead colSpan={3} className="h-7 border-l text-xs text-muted-foreground">Answer key</TableHead>
            <TableHead colSpan={3} className="h-7 border-l text-xs text-muted-foreground">Dock</TableHead>
            <TableHead colSpan={4} className="h-7 border-l text-xs text-muted-foreground">Run</TableHead>
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">ID</TableHead>
            <TableHead className="h-8">From</TableHead>
            <TableHead className="h-8">Subject</TableHead>
            {FIELDS.map((f, i) => (
              <TableHead key={`k-${f.key}`} className={cn("h-8", i === 0 && "border-l")}>{f.label}</TableHead>
            ))}
            {FIELDS.map((f, i) => (
              <TableHead key={`d-${f.key}`} className={cn("h-8", i === 0 && "border-l")}>{f.label}</TableHead>
            ))}
            <TableHead className="h-8 border-l">Result</TableHead>
            <TableHead className="h-8 text-right">Latency</TableHead>
            <TableHead className="h-8 text-right">Tokens</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {mails.map((mail) => {
            const row = rows[mail.id];
            const key = truth[mail.id];
            const result = row?.status === "done" ? row.result : null;
            const isOpen = open[mail.id] ?? false;
            return (
              <Fragment key={mail.id}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={isOpen ? `Hide ${mail.id}` : `Show ${mail.id}`}
                      aria-expanded={isOpen}
                      onClick={() => setOpen((o) => ({ ...o, [mail.id]: !isOpen }))}
                    >
                      {isOpen ? <ChevronDown /> : <ChevronRight />}
                    </Button>
                  </TableCell>
                  <TableCell className="py-1.5 font-mono text-xs">{mail.id}</TableCell>
                  <TableCell className="max-w-44 truncate py-1.5 text-muted-foreground" title={mail.from}>{mail.from}</TableCell>
                  <TableCell className="max-w-80 truncate py-1.5" title={mail.subject}>{mail.subject}</TableCell>

                  {FIELDS.map((f, i) => (
                    <TableCell key={f.key} className={cn("bg-muted/30 py-1.5 text-muted-foreground", i === 0 && "border-l", f.key === "shipment_id" && "font-mono text-xs")}>
                      {key ? show(key[f.key]) : ""}
                    </TableCell>
                  ))}

                  {row?.status === "error" ? (
                    <TableCell colSpan={6} className="max-w-0 border-l py-1.5">
                      <span className="block truncate text-miss" title={describeError(row.error)}>{describeError(row.error)}</span>
                    </TableCell>
                  ) : (
                    <>
                      {FIELDS.map((f, i) => {
                        const wrong = result?.wrong_fields.includes(f.key);
                        return (
                          <TableCell
                            key={f.key}
                            className={cn("py-1.5", i === 0 && "border-l", f.key === "shipment_id" && "font-mono text-xs", wrong && "bg-miss/10 font-medium text-miss")}
                            title={wrong && key ? `Answer key: ${show(key[f.key])}` : undefined}
                          >
                            {result ? show(result[f.key]) : ""}
                          </TableCell>
                        );
                      })}
                      <TableCell className="border-l py-1.5">
                        {row?.status === "running" && <Running />}
                        {result && <Verdict ok={result.wrong_fields.length === 0} no={`${result.wrong_fields.length} wrong`} />}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">
                        {result && <Latency run={result} />}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result ? num(runTokens(result)) : ""}</TableCell>
                    </>
                  )}
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => triage(mail.id)} disabled={row?.status === "running"}>
                      {row ? "Run again" : "Triage"}
                    </Button>
                  </TableCell>
                </TableRow>

                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={13} className="whitespace-normal pb-3 pt-0">
                      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
                        <div className="max-w-[70ch]">
                          <div className="text-xs text-muted-foreground">{mail.from}</div>
                          <div className="font-medium">{mail.subject}</div>
                          <p className="mt-1 leading-relaxed">{mail.body}</p>
                        </div>
                        {result && <MeterTable run={result} />}
                      </div>
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
