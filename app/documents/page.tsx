"use client";

import { Fragment, useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, Loader2, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Run, Running, runMs, runPages, Segmented, Stat, Toolbar, useRows, Verdict,
} from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Value = string | number | null | Line[] | string[];
type Line = { description: string; hs_code: string; quantity: number; unit_price: number; amount: number };
type Truth = Record<string, Value> & { kind: "invoice" | "bill_of_lading"; flags: string[] };
type Schema = "invoice" | "v1" | "bill_of_lading";
type Extract = Run & {
  name: string;
  schema: Schema;
  fields: Record<string, Value>;
  ocr: { avg: number; min: number; low_words: { text: string; confidence: number }[]; markdown: string };
  problems: string[] | null;
  against_database: string | null;
  check_digit_ok: boolean;
  truth: { wrong_fields: string[]; flags: string[]; flag_right: boolean };
};

const INVOICE_SCHEMAS: { value: "invoice" | "v1"; label: string }[] = [
  { value: "invoice", label: "Invoice schema" },
  { value: "v1", label: "First schema (v1)" },
];
// The fields each schema returns, in reading order. Lines are shown as their own table.
const SHOWN: Record<Schema, string[]> = {
  invoice: ["invoice_no", "shipment_id", "container_no", "incoterm", "incoterm_place", "seller", "buyer", "subtotal", "freight", "insurance", "total"],
  v1: ["invoice_no", "shipment_id", "container_no", "incoterm", "seller", "buyer", "subtotal", "freight", "insurance", "total"],
  bill_of_lading: ["bl_no", "shipment_id", "shipper", "consignee", "container_no", "packages", "gross_weight_kg"],
};
const MONEY = new Set(["subtotal", "freight", "insurance", "total", "unit_price", "amount"]);

const pct = (x: number) => `${(x * 100).toFixed(1)}%`;
const fieldLabel = (f: string) => f.replaceAll("_", " ").replace(/^bl no$/, "B/L no");

function show(field: string, v: Value | undefined) {
  if (v === null || v === undefined) return "none";
  if (typeof v === "number") return MONEY.has(field) ? v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : num(v);
  return String(v);
}

function pdfUrl(name: string) {
  return `/api/files/documents/${encodeURIComponent(name)}`;
}

/** Problems the checks found: the s02 invoice checks, the database comparison, and the container check digit. */
function Findings({ result }: { result: Extract }) {
  const items: { text: string; bad: boolean }[] = [];
  if (result.problems) {
    if (result.problems.length === 0) items.push({ text: "lines, subtotal, total and container all check out", bad: false });
    for (const p of result.problems) items.push({ text: p, bad: true });
  }
  if (result.against_database) items.push({ text: result.against_database, bad: result.against_database !== "matches database" });
  // problems() already reports a failed container on an invoice; a bill of lading has only this check.
  if (!result.problems) items.push({ text: result.check_digit_ok ? "container check digit ok" : `container ${result.fields.container_no} fails the check digit`, bad: !result.check_digit_ok });
  return (
    <ul className="flex flex-col gap-0.5">
      {items.map((it, i) => (
        <li key={i} className={cn("flex gap-1.5", it.bad ? "font-medium" : "text-muted-foreground")}>
          <span aria-hidden>{it.bad ? "▲" : "·"}</span>{it.text}
        </li>
      ))}
    </ul>
  );
}

function problemCount(r: Extract) {
  return (r.problems?.length ?? (r.check_digit_ok ? 0 : 1)) + (r.against_database && r.against_database !== "matches database" ? 1 : 0);
}

function DocumentDetail({ result, truth }: { result: Extract; truth: Truth }) {
  const lines = result.fields.lines as Line[] | undefined;
  return (
    <div className="grid gap-x-8 gap-y-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,22rem)_auto]">
      <div className="flex min-w-0 flex-col gap-3">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Extracted fields{result.truth.wrong_fields.length > 0 && ", wrong ones against the answer key in red"}</div>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-0.5">
            {SHOWN[result.schema].map((f) => {
              const wrong = result.truth.wrong_fields.includes(f);
              return (
                <Fragment key={f}>
                  <dt className="text-muted-foreground">{fieldLabel(f)}</dt>
                  <dd className={cn("truncate tabular-nums", wrong && "font-medium text-miss")}>
                    {show(f, result.fields[f])}
                    {wrong && <span className="font-normal text-muted-foreground"> · answer key: {show(f, truth[f])}</span>}
                  </dd>
                </Fragment>
              );
            })}
          </dl>
        </div>
        {lines && (
          <table className="w-fit text-xs tabular-nums">
            <thead className="text-muted-foreground">
              <tr>
                <th className="pr-4 text-left font-normal">Line</th>
                <th className="pr-4 text-left font-normal">HS code</th>
                <th className="pr-4 text-right font-normal">Qty</th>
                <th className="pr-4 text-right font-normal">Unit price</th>
                <th className="text-right font-normal">Amount</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((l, i) => (
                <tr key={i}>
                  <td className="pr-4">{l.description}</td>
                  <td className="pr-4 font-mono">{l.hs_code}</td>
                  <td className="pr-4 text-right">{num(l.quantity)}</td>
                  <td className="pr-4 text-right">{show("unit_price", l.unit_price)}</td>
                  <td className="text-right">{show("amount", l.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="flex flex-col gap-3">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">Checks</div>
          <Findings result={result} />
        </div>
        <div>
          <div className="mb-1 text-xs text-muted-foreground">OCR confidence</div>
          <div className="flex gap-6">
            <Stat label="Page average" value={pct(result.ocr.avg)} />
            <Stat label="Least sure word" value={pct(result.ocr.min)} />
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {result.ocr.low_words.map((w, i) => (
              <span key={i} className="rounded-sm border bg-card px-1.5 py-0.5 font-mono text-xs">
                {w.text.trim() || "␣"} <span className="text-muted-foreground">{pct(w.confidence)}</span>
              </span>
            ))}
          </div>
        </div>
        <a href={pdfUrl(result.name)} target="_blank" rel="noreferrer" className="inline-flex w-fit items-center gap-1 text-primary underline-offset-4 hover:underline">
          Open the PDF<ExternalLink className="size-3.5" />
        </a>
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">What OCR read (markdown)</summary>
          <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap rounded-md border bg-card p-2 font-mono text-[11px] leading-snug">{result.ocr.markdown}</pre>
        </details>
      </div>

      <MeterTable run={result} />
    </div>
  );
}

export default function DocumentsPage() {
  const { get, post, live } = useApi();
  const [truth, setTruth] = useState<Record<string, Truth> | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [invoiceSchema, setInvoiceSchema] = useState<"invoice" | "v1">("invoice");

  useEffect(() => {
    (async () => {
      const res = await get<{ data: Record<string, Truth> }>("/api/data/documents");
      if (!res.ok) return setLoadError(res.error);
      setTruth(res.data.data);
    })();
    // The dataset doesn't depend on the key, so load it once.
  }, []);

  // Rows are keyed schema:name, so switching the invoice schema keeps both sets of results.
  const run = useCallback((key: string) => {
    const [schema, name] = key.split(/:(.*)/s);
    return post<Extract>("/api/documents/extract", { name, schema });
  }, [post]);
  const { rows, runOne, runAll, runningAll, running, failed, clear } = useRows(run);

  if (loadError) return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  if (!truth) return <p className="p-6 text-muted-foreground">Loading the documents…</p>;

  const docs = Object.entries(truth).sort(([a, x], [b, y]) => x.kind.localeCompare(y.kind) * -1 || a.localeCompare(b));
  const keyFor = (name: string, t: Truth) => `${t.kind === "invoice" ? invoiceSchema : "bill_of_lading"}:${name}`;
  const keys = docs.map(([name, t]) => keyFor(name, t));
  const shown = keys.flatMap((k) => (rows[k]?.status === "done" ? [rows[k].result] : []));
  const correct = shown.filter((r) => r.truth.wrong_fields.length === 0 && r.truth.flag_right).length;

  return (
    <div className="flex flex-col">
      <Toolbar
        live={live}
        stats={shown.length > 0 && (
          <>
            <Stat label="Read" value={`${shown.length} of ${docs.length}`} />
            <Stat label="Match the answer key" value={`${correct} of ${shown.length}`} />
            <Stat label="With problems" value={String(shown.filter((r) => problemCount(r) > 0).length)} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Median latency" value={`${num(median(shown.map(runMs)))} ms`} />
            <Stat label="OCR pages" value={num(shown.reduce((s, r) => s + runPages(r), 0))} />
          </>
        )}
      >
        <Segmented label="Invoice schema" value={invoiceSchema} options={INVOICE_SCHEMAS} onChange={setInvoiceSchema} />
        <Button size="sm" onClick={() => runAll(keys)} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Reading, ${running} running` : `Read all ${docs.length}`}
        </Button>
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={2} className="h-7 text-xs text-muted-foreground">Document</TableHead>
            <TableHead className="h-7 border-l text-xs text-muted-foreground">Answer key</TableHead>
            <TableHead colSpan={5} className="h-7 border-l text-xs text-muted-foreground">Dock</TableHead>
            <TableHead colSpan={4} className="h-7 border-l text-xs text-muted-foreground">Run</TableHead>
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">File</TableHead>
            <TableHead className="h-8 border-l">Flags</TableHead>
            <TableHead className="h-8 border-l">Ref</TableHead>
            <TableHead className="h-8">Container</TableHead>
            <TableHead className="h-8 text-right">Total</TableHead>
            <TableHead className="h-8 text-right">OCR avg</TableHead>
            <TableHead className="h-8">Checks</TableHead>
            <TableHead className="h-8 border-l">Result</TableHead>
            <TableHead className="h-8 text-right">Latency</TableHead>
            <TableHead className="h-8 text-right">Pages</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {docs.map(([name, t]) => {
            const key = keyFor(name, t);
            const row = rows[key];
            const result = row?.status === "done" ? row.result : null;
            const isOpen = open[key] ?? false;
            const wrong = result ? result.truth.wrong_fields.length : 0;
            const found = result ? problemCount(result) : 0;
            const refField = t.kind === "invoice" ? "invoice_no" : "bl_no";
            return (
              <Fragment key={key}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={isOpen ? `Hide ${name}` : `Show ${name}`}
                      aria-expanded={isOpen}
                      onClick={() => setOpen((o) => ({ ...o, [key]: !isOpen }))}
                    >
                      {isOpen ? <ChevronDown /> : <ChevronRight />}
                    </Button>
                  </TableCell>
                  <TableCell className="py-1.5">
                    <a href={pdfUrl(name)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs hover:underline" title="Open the PDF">
                      {name}<ExternalLink className="size-3 text-muted-foreground" />
                    </a>
                  </TableCell>
                  <TableCell className="border-l bg-muted/30 py-1.5 text-muted-foreground">
                    {t.flags.length ? t.flags.map((f) => f.replaceAll("_", " ")).join(", ") : "none"}
                  </TableCell>

                  {row?.status === "error" ? (
                    <TableCell colSpan={8} className="max-w-0 border-l py-1.5">
                      <span className="block truncate text-miss" title={describeError(row.error)}>{describeError(row.error)}</span>
                    </TableCell>
                  ) : (
                    <>
                      <TableCell className={cn("border-l py-1.5 font-mono text-xs", result?.truth.wrong_fields.includes(refField) && "text-miss")}>
                        {result && show(refField, result.fields[refField])}
                      </TableCell>
                      <TableCell className={cn("py-1.5 font-mono text-xs", result?.truth.wrong_fields.includes("container_no") && "text-miss")}>
                        {result && show("container_no", result.fields.container_no)}
                      </TableCell>
                      <TableCell className={cn("py-1.5 text-right tabular-nums", result?.truth.wrong_fields.includes("total") && "text-miss")}>
                        {result && "total" in result.fields ? show("total", result.fields.total) : ""}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result && pct(result.ocr.avg)}</TableCell>
                      <TableCell className="max-w-64 truncate py-1.5" title={result ? undefined : ""}>
                        {result && (found === 0 ? <span className="text-muted-foreground">no problems</span> : (
                          <span className="font-medium" title={[...(result.problems ?? []), result.against_database ?? ""].filter(Boolean).join("; ")}>
                            {found} {found === 1 ? "problem" : "problems"}
                          </span>
                        ))}
                      </TableCell>
                      <TableCell className="border-l py-1.5">
                        {row?.status === "running" && <Running />}
                        {result && (
                          <Verdict
                            ok={wrong === 0 && result.truth.flag_right}
                            no={wrong > 0 ? `${wrong} wrong` : "Flag wrong"}
                          />
                        )}
                      </TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result && <Latency run={result} />}</TableCell>
                      <TableCell className="py-1.5 text-right tabular-nums">{result ? num(runPages(result)) : ""}</TableCell>
                    </>
                  )}
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => runOne(key)} disabled={row?.status === "running"}>
                      {row ? "Run again" : "Read"}
                    </Button>
                  </TableCell>
                </TableRow>

                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={11} className="whitespace-normal pb-3 pt-0">
                      {result ? <DocumentDetail result={result} truth={t} /> : (
                        <p className="text-muted-foreground">
                          Not read yet. Press Read to extract the fields and run the checks, or{" "}
                          <a href={pdfUrl(name)} target="_blank" rel="noreferrer" className="text-primary hover:underline">open the PDF</a>.
                        </p>
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
