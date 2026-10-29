"use client";

import { useCallback, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { type ApiError, type MeterCall, type Mode, type Result } from "@/lib/api";
import { cn } from "@/lib/utils";

// What every capability response carries, recorded or live.
export type Run = { mode: Mode; meter: MeterCall[]; recorded_at?: string; git_sha?: string };
export type Row<T> = { status: "running" } | { status: "done"; result: T } | { status: "error"; error: ApiError };

const PARALLEL = 3;

export const runMs = (r: Run) => r.meter.reduce((sum, c) => sum + c.ms, 0);
export const runTokens = (r: Run) => r.meter.reduce((sum, c) => sum + (c.tokens ?? 0), 0);
export const runPages = (r: Run) => r.meter.reduce((sum, c) => sum + (c.pages ?? 0), 0);
export const num = (n: number) => n.toLocaleString("en-US");

export function median(values: number[]) {
  const s = [...values].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : Math.round((s[mid - 1] + s[mid]) / 2);
}

export function recordedLabel(r: Run) {
  if (r.mode === "live") return "Live run on your key, just now.";
  if (!r.recorded_at) return "Recorded run.";
  const when = new Date(r.recorded_at).toLocaleString("en-US", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
  return `Recorded ${when} UTC${r.git_sha ? `, commit ${r.git_sha}` : ""}.`;
}

/** One request per row id, a few at a time, as the Inbox does. */
export function useRows<T>(run: (id: string) => Promise<Result<T>>) {
  const [rows, setRows] = useState<Record<string, Row<T>>>({});
  const [runningAll, setRunningAll] = useState(false);

  const runOne = useCallback(
    async (id: string) => {
      setRows((r) => ({ ...r, [id]: { status: "running" } }));
      const res = await run(id);
      setRows((r) => ({ ...r, [id]: res.ok ? { status: "done", result: res.data } : { status: "error", error: res.error } }));
    },
    [run],
  );

  async function runAll(ids: string[]) {
    setRunningAll(true);
    const queue = [...ids];
    const worker = async () => {
      for (let id = queue.shift(); id; id = queue.shift()) await runOne(id);
    };
    await Promise.all(Array.from({ length: PARALLEL }, worker));
    setRunningAll(false);
  }

  const all = Object.values(rows);
  const done = all.flatMap((r) => (r.status === "done" ? [r.result] : []));
  return {
    rows, runOne, runAll, runningAll, done,
    clear: () => setRows({}),
    running: all.filter((r) => r.status === "running").length,
    failed: all.filter((r) => r.status === "error").length,
  };
}

export function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[11px] text-muted-foreground">{label}</span>
      <span className="text-sm font-medium tabular-nums">{value}</span>
    </div>
  );
}

export function Latency({ run }: { run: Run }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0} className={cn("cursor-help", run.mode === "live" && "text-live")}>{num(runMs(run))} ms</span>
      </TooltipTrigger>
      <TooltipContent>{recordedLabel(run)}</TooltipContent>
    </Tooltip>
  );
}

export function Running() {
  return <span className="inline-flex items-center gap-1.5 text-muted-foreground"><Loader2 className="size-3.5 animate-spin" />Running</span>;
}

/** Green and red mean "agrees with the answer key" and "doesn't", nothing else. */
export function Verdict({ ok, yes = "Match", no }: { ok: boolean; yes?: string; no: string }) {
  return ok ? (
    <span className="inline-flex items-center gap-1 text-ok"><Check className="size-3.5" />{yes}</span>
  ) : (
    <span className="inline-flex items-center gap-1 text-miss"><X className="size-3.5" />{no}</span>
  );
}

export function MeterTable({ run }: { run: Run }) {
  const pages = run.meter.some((c) => c.pages != null);
  return (
    <div className="text-xs">
      <div className="mb-1 text-muted-foreground">{recordedLabel(run)}</div>
      <table className="tabular-nums">
        <thead className="text-muted-foreground">
          <tr>
            <th className="pr-4 text-left font-normal">Call</th>
            <th className="pr-4 text-left font-normal">Model</th>
            <th className="pr-4 text-right font-normal">ms</th>
            {pages ? <th className="text-right font-normal">Pages</th> : (
              <>
                <th className="pr-4 text-right font-normal">In</th>
                <th className="text-right font-normal">Out</th>
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {run.meter.map((c, i) => (
            <tr key={i}>
              <td className="pr-4 font-mono">{c.op}</td>
              <td className="pr-4">{c.model}</td>
              <td className="pr-4 text-right">{num(c.ms)}</td>
              {pages ? <td className="text-right">{c.pages ?? ""}</td> : (
                <>
                  <td className="pr-4 text-right">{c.tokens_in ?? ""}</td>
                  <td className="text-right">{c.tokens_out ?? ""}</td>
                </>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The toolbar strip every screen starts with: actions on the left, the mode line, then totals on the right. */
export function Toolbar({ live, children, stats, run = "run" }: {
  live: boolean; children: React.ReactNode; stats?: React.ReactNode; run?: string;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-b bg-card px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">{children}</div>
      <p className="text-xs text-muted-foreground">
        {live ? `Live: each ${run} is a real call on your key.` : "Replay: results are recorded real runs."}
      </p>
      {stats && <div className="ml-auto flex flex-wrap gap-x-6 gap-y-2">{stats}</div>}
    </div>
  );
}

/** A segmented choice, e.g. which schema or which rule. */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex h-7 items-center rounded-md border bg-muted p-0.5 text-xs">
      {options.map((o) => (
        <button
          key={o.value}
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "h-full rounded-[4px] px-2.5 outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === o.value ? "bg-card font-medium shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
