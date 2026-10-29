"use client";

import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, Play, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { type ApiError, describeError, type MeterCall, type Mode, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Mail = { id: string; from: string; subject: string; body: string };
type Fields = { intent: string; shipment_id: string | null; language: string };
type Triage = Fields & {
  mail_id: string;
  model: string;
  expected: Fields | null;
  wrong_fields: string[];
  mode: Mode;
  meter: MeterCall[];
  recorded_at?: string;
  git_sha?: string;
};
type Row = { status: "running" } | { status: "done"; result: Triage } | { status: "error"; error: ApiError };

const FIELDS: { key: keyof Fields; label: string }[] = [
  { key: "intent", label: "Intent" },
  { key: "shipment_id", label: "Shipment" },
  { key: "language", label: "Lang" },
];
const PARALLEL = 3;

const ms = (r: Triage) => r.meter.reduce((sum, c) => sum + c.ms, 0);
const tokens = (r: Triage) => r.meter.reduce((sum, c) => sum + (c.tokens ?? 0), 0);
const show = (v: string | null) => v ?? "none";

function median(values: number[]) {
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : Math.round((s[mid - 1] + s[mid]) / 2);
}

function recordedLabel(r: Triage) {
  if (r.mode === "live") return "Live run on your key, just now.";
  if (!r.recorded_at) return "Recorded run.";
  const when = new Date(r.recorded_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
  return `Recorded ${when} UTC${r.git_sha ? `, commit ${r.git_sha}` : ""}.`;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="text-sm font-medium tabular-nums">{value}</span>
    </div>
  );
}

export default function InboxPage() {
  const { get, post, live } = useApi();
  const [mails, setMails] = useState<Mail[] | null>(null);
  const [truth, setTruth] = useState<Record<string, Fields>>({});
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [rows, setRows] = useState<Record<string, Row>>({});
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [runningAll, setRunningAll] = useState(false);

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

  const triage = useCallback(
    async (id: string) => {
      setRows((r) => ({ ...r, [id]: { status: "running" } }));
      const res = await post<Triage>("/api/triage", { mail_id: id });
      setRows((r) => ({ ...r, [id]: res.ok ? { status: "done", result: res.data } : { status: "error", error: res.error } }));
    },
    [post],
  );

  async function triageAll() {
    if (!mails) return;
    setRunningAll(true);
    const queue = mails.map((m) => m.id);
    const worker = async () => {
      for (let id = queue.shift(); id; id = queue.shift()) await triage(id);
    };
    await Promise.all(Array.from({ length: PARALLEL }, worker));
    setRunningAll(false);
  }

  const done = useMemo(
    () => Object.values(rows).flatMap((r) => (r.status === "done" ? [r.result] : [])),
    [rows],
  );
  const failed = Object.values(rows).filter((r) => r.status === "error").length;
  const running = Object.values(rows).filter((r) => r.status === "running").length;

  if (loadError) {
    return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  }
  if (!mails) {
    return <p className="p-6 text-muted-foreground">Loading the inbox…</p>;
  }

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b bg-card px-4 py-3">
        <div className="flex items-center gap-2">
          <Button size="sm" onClick={triageAll} disabled={runningAll || running > 0}>
            {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
            {runningAll ? `Triaging, ${running} running` : `Triage all ${mails.length}`}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setRows({})} disabled={running > 0 || Object.keys(rows).length === 0}>
            Clear results
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          {live ? "Live: each triage is a real call on your key." : "Replay: results are recorded real runs."}
        </p>
        {done.length > 0 && (
          <div className="ml-auto flex flex-wrap gap-x-6 gap-y-2">
            <Stat label="Triaged" value={`${done.length} of ${mails.length}`} />
            <Stat label="Match the answer key" value={`${done.filter((r) => r.wrong_fields.length === 0).length} of ${done.length}`} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${median(done.map(ms)).toLocaleString("en-US")} ms`} />
            <Stat label="Tokens" value={done.reduce((s, r) => s + tokens(r), 0).toLocaleString("en-US")} />
          </div>
        )}
      </div>

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
                        {row?.status === "running" && (
                          <span className="inline-flex items-center gap-1.5 text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />Running</span>
                        )}
                        {result && (result.wrong_fields.length === 0 ? (
                          <span className="inline-flex items-center gap-1 text-ok"><Check className="size-3.5" />Match</span>
                        ) : (
                          <span className="inline-flex items-center gap-1 text-miss"><X className="size-3.5" />{result.wrong_fields.length} wrong</span>
                        ))}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">
                        {result && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span tabIndex={0} className={cn("cursor-help", result.mode === "live" && "text-live")}>
                                {ms(result).toLocaleString("en-US")} ms
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>{recordedLabel(result)}</TooltipContent>
                          </Tooltip>
                        )}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result ? tokens(result).toLocaleString("en-US") : ""}</TableCell>
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
                        {result && (
                          <div className="text-xs">
                            <div className="mb-1 text-muted-foreground">{recordedLabel(result)}</div>
                            <table className="tabular-nums">
                              <thead className="text-muted-foreground">
                                <tr>
                                  <th className="pr-4 text-left font-normal">Call</th>
                                  <th className="pr-4 text-left font-normal">Model</th>
                                  <th className="pr-4 text-right font-normal">ms</th>
                                  <th className="pr-4 text-right font-normal">In</th>
                                  <th className="text-right font-normal">Out</th>
                                </tr>
                              </thead>
                              <tbody>
                                {result.meter.map((c, i) => (
                                  <tr key={i}>
                                    <td className="pr-4 font-mono">{c.op}</td>
                                    <td className="pr-4">{c.model}</td>
                                    <td className="pr-4 text-right">{c.ms.toLocaleString("en-US")}</td>
                                    <td className="pr-4 text-right">{c.tokens_in ?? ""}</td>
                                    <td className="text-right">{c.tokens_out ?? ""}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
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
