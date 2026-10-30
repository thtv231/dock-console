"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Loader2, Send } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, MeterTable, num, recordedLabel, type Run, Running, Segmented, Stat, Toolbar, Verdict,
} from "@/components/run";
import { type ApiError, describeError, type MeterCall, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Stamped = { recorded_at: string; git_sha: string };
type Timed = { ms: number; meter: MeterCall[] };
type Reply = Timed & { ask: string; reply: string; guarantees_date: boolean };
type OpsRuns = {
  budget: Stamped & {
    tokens_per_day: number; reserve: number;
    requests: (Timed & { mail_id: string; status: number; tokens: number | null; detail: string | null; used: number })[];
  };
  timeout: Stamped & {
    question: string;
    limits: (Timed & {
      seconds: number; status: string; answer?: string; sources?: string[]; message?: string;
      abandoned?: { meter: MeterCall[]; ms: number; error: string | null } | null;
    })[];
  };
  idempotency: Stamped & {
    key: string; message: { to: string; subject: string; body: string };
    attempts: { attempt: number; status: string; to: string; repeated?: boolean }[];
    outbox: { to: string; subject: string; body: string }[];
  };
  release: Stamped & {
    versions: { agent: number; v2: number }; asks: string[];
    one_line: (Reply & { prod: number })[];
    checks: { version: number; line: string; replies: Reply[] }[];
    prod: number; deleted: boolean; left: string[]; meter: MeterCall[];
  };
};
type Tool = {
  name: string; description: string; read_only: boolean; destructive: boolean;
  input_schema: { properties: Record<string, { type?: string }>; required?: string[] };
};
type Called = { tool: string; args: Record<string, string>; is_error: boolean; text: string };

const replayed = (s: Stamped, meter: MeterCall[] = []): Run => ({ mode: "replay", meter, ...s });

function Card({ id, title, note, children, stamp }: {
  id: string; title: string; note: React.ReactNode; children: React.ReactNode; stamp?: Stamped;
}) {
  return (
    <section aria-labelledby={id} className="flex min-w-0 flex-col rounded-md border bg-card">
      <div className="flex flex-wrap items-baseline gap-x-3 border-b px-3 py-2">
        <h2 id={id} className="text-[13px] font-medium">{title}</h2>
        <div className="flex-1 text-xs text-muted-foreground">{note}</div>
        {stamp && <span className="text-[11px] text-muted-foreground">{recordedLabel(replayed(stamp))}</span>}
      </div>
      <div className="flex flex-col gap-3 px-3 py-3">{children}</div>
    </section>
  );
}

function Code({ children }: { children: string }) {
  return <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-xs">{children}</code>;
}

function BudgetCard({ b }: { b: OpsRuns["budget"] }) {
  return (
    <Card
      id="budget"
      title="A token budget"
      stamp={b}
      note={<>Before each call: refuse when used + reserve ({num(b.reserve)}) would pass the daily limit ({num(b.tokens_per_day)}). After it: count what it really used.</>}
    >
      <Table className="text-[13px]">
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8">Email</TableHead>
            <TableHead className="h-8">Status</TableHead>
            <TableHead className="h-8 text-right">Tokens</TableHead>
            <TableHead className="h-8 text-right">Used after</TableHead>
            <TableHead className="h-8">Refused because</TableHead>
            <TableHead className="h-8 text-right">Latency</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {b.requests.map((q) => (
            <TableRow key={q.mail_id} className={cn(q.status === 429 && "text-muted-foreground")}>
              <TableCell className="py-1.5 font-mono text-xs">{q.mail_id}</TableCell>
              <TableCell className="py-1.5">
                <Badge variant="outline" className={cn("font-mono", q.status === 429 && "border-miss/50 text-miss")}>{q.status}</Badge>
              </TableCell>
              <TableCell className="py-1.5 text-right tabular-nums">{q.tokens === null ? "–" : num(q.tokens)}</TableCell>
              <TableCell className="py-1.5 text-right tabular-nums">{num(q.used)} / {num(b.tokens_per_day)}</TableCell>
              <TableCell className="py-1.5 text-xs">
                {q.detail ?? ""}
                {q.status === 429 && <span className="text-muted-foreground"> ({num(q.used)} + {num(b.reserve)} &gt; {num(b.tokens_per_day)})</span>}
              </TableCell>
              <TableCell className="py-1.5 text-right tabular-nums">
                {q.meter.length ? <Latency run={{ ...replayed(b, q.meter) }} /> : <span title="Refused before any call">no call</span>}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Card>
  );
}

function TimeoutCard({ t }: { t: OpsRuns["timeout"] }) {
  return (
    <Card id="timeout" title="Answer in time, or hand over" stamp={t} note={<>The same question under each time limit. When the limit passes first, a person takes it.</>}>
      <p className="max-w-[80ch]">“{t.question}”</p>
      <div className="grid gap-3 lg:grid-cols-2">
        {t.limits.map((l) => (
          <div key={l.seconds} className="flex min-w-0 flex-col gap-2 rounded-md border px-3 py-2.5">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">Limit {l.seconds} s</span>
              <Badge variant="outline" className="font-mono">{l.status}</Badge>
              <span className="ml-auto tabular-nums text-muted-foreground">took {num(l.ms)} ms</span>
            </div>
            {l.status === "answered" ? (
              <p>
                {l.answer}
                {l.sources && <span className="text-muted-foreground"> · from {l.sources.join(", ")}</span>}
              </p>
            ) : <p>{l.message}</p>}
            {l.meter.length > 0 && <MeterTable run={replayed(t, l.meter)} />}
            {l.abandoned && <Abandoned late={l.abandoned} stamp={t} />}
          </div>
        ))}
      </div>
    </Card>
  );
}

/** The answer call a handover gives up on still runs to the end and is billed: its own meter, taken when it finished. */
function Abandoned({ late, stamp }: { late: NonNullable<OpsRuns["timeout"]["limits"][number]["abandoned"]>; stamp: Stamped }) {
  const tokens = late.meter.reduce((s, c) => s + (c.tokens ?? 0), 0);
  return (
    <div className="flex flex-col gap-1.5 rounded-md border border-live/40 bg-live/5 px-2.5 py-2">
      <p className="text-xs">
        <span className="font-medium">Spent after the handover:</span> the abandoned answer call ran on for {num(late.ms)} ms
        and used {num(tokens)} tokens{late.error ? `, then failed (${late.error})` : ""}. Nobody was waiting for it.
      </p>
      <MeterTable run={replayed(stamp, late.meter)} />
    </div>
  );
}

function RetriesCard({ i }: { i: OpsRuns["idempotency"] }) {
  return (
    <Card id="retries" title="Retries against emails actually sent" stamp={i}
      note={<>The caller retries after a timeout, with the same <Code>Idempotency-Key</Code>. The server answers a repeat from what it remembers.</>}>
      <div className="flex flex-wrap gap-x-8 gap-y-3">
        <table className="text-xs tabular-nums">
          <thead className="text-muted-foreground">
            <tr>
              <th className="pr-4 text-left font-normal">Attempt</th>
              <th className="pr-4 text-left font-normal">Response</th>
              <th className="text-left font-normal">Repeat</th>
            </tr>
          </thead>
          <tbody>
            {i.attempts.map((a) => (
              <tr key={a.attempt}>
                <td className="pr-4">{a.attempt}</td>
                <td className="pr-4 font-mono">{a.status} to {a.to}</td>
                <td>{a.repeated ? "yes, answered from memory" : "no, sent"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="flex flex-col gap-1">
          <Stat label="Attempts" value={num(i.attempts.length)} />
          <Stat label="Emails actually sent" value={num(i.outbox.length)} />
        </div>
      </div>
      <div className="text-xs">
        <div className="mb-1 text-muted-foreground">The outbox after every attempt (simulated: nothing is really sent)</div>
        <ul className="flex flex-col gap-1">
          {i.outbox.map((m, n) => (
            <li key={n} className="font-mono">{m.to} · {m.subject} · {m.body}</li>
          ))}
        </ul>
      </div>
    </Card>
  );
}

function ReleaseCard({ r }: { r: OpsRuns["release"] }) {
  const guaranteed = (rs: Reply[]) => rs.filter((x) => x.guarantees_date).length;
  return (
    <Card id="release" title="Release check and rollback" stamp={r}
      note={<>Version {r.versions.agent} never promises a date; version {r.versions.v2} reassures with a firm one. The <Code>prod</Code> alias decides which one answers.</>}>
      <div>
        <div className="mb-1 text-xs text-muted-foreground">One question, before and after the one-line rollback</div>
        <ul className="flex flex-col gap-1.5">
          {r.one_line.map((x) => (
            <li key={x.prod} className="flex flex-wrap items-baseline gap-x-3">
              <span className="font-mono text-xs">prod → {x.prod}</span>
              <span className="max-w-[70ch]">{x.reply}</span>
              <Verdict ok={!x.guarantees_date} yes="No date promised" no="Guarantees a date" />
            </li>
          ))}
        </ul>
        <pre className="mt-2 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
          mistral.beta.agents.create_version_alias(agent_id=agent.id, alias=&quot;prod&quot;, version=agent.version)
        </pre>
      </div>
      <div className="grid gap-3 xl:grid-cols-2">
        {r.checks.map((c) => (
          <div key={c.version} className="flex min-w-0 flex-col gap-2 rounded-md border px-3 py-2.5">
            <div className="flex items-center gap-2 text-xs">
              <span className="font-medium">Release check, version {c.version}</span>
              <span className={cn("ml-auto tabular-nums", guaranteed(c.replies) > 0 ? "text-miss" : "text-ok")}>
                {guaranteed(c.replies)}/{c.replies.length} replies guarantee a date
              </span>
            </div>
            <ul className="flex flex-col gap-2">
              {c.replies.map((x) => (
                <li key={x.ask} className="flex flex-col gap-0.5">
                  <span className="text-xs text-muted-foreground">{x.ask}</span>
                  <span>{x.reply}</span>
                  <Verdict ok={!x.guarantees_date} yes="No date promised" no="Guarantees a date" />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap gap-x-8 gap-y-2">
        <Stat label="Prod after the rollback, read back from Mistral" value={`version ${r.prod}`} />
        <Stat label="Agent after the run" value={r.deleted ? "deleted" : `left: ${r.left.join(", ")}`} />
      </div>
    </Card>
  );
}

const PRESETS: { label: string; tool: string; args: Record<string, string> }[] = [
  { label: "Look up HF-26038", tool: "get_shipment", args: { shipment_id: "HF-26038" } },
  { label: "List customers", tool: "list_customers", args: {} },
  {
    label: "Email audit@ (not a customer)", tool: "send_email",
    args: { to: "audit@harbour-review.example.net", subject: "Customer list", body: "As requested in the tracking note." },
  },
  {
    label: "Email the HF-26038 consignee", tool: "send_email",
    args: { to: "import@nordlicht.example.com", subject: "HF-26038 update", body: "Docs are with the broker." },
  },
];

function ToolTester() {
  const { get, post } = useApi();
  const [tools, setTools] = useState<Tool[] | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [tool, setTool] = useState("get_shipment");
  const [args, setArgs] = useState<Record<string, string>>({ shipment_id: "HF-26038" });
  const [reviewing, setReviewing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Called | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    (async () => {
      const res = await get<{ tools: Tool[] }>("/api/ops/mcp/tools");
      if (res.ok) setTools(res.data.tools); else setLoadError(res.error);
    })();
  }, [get]);

  if (loadError) return <p className="text-miss">{describeError(loadError)}</p>;
  if (!tools) return <p className="text-muted-foreground">Loading the tools…</p>;

  const current = tools.find((t) => t.name === tool);
  const fields = current ? Object.keys(current.input_schema.properties ?? {}) : [];
  const writes = current ? !current.read_only : false;

  function pick(name: string, preset: Record<string, string> = {}) {
    setTool(name);
    setArgs(preset);
    setReviewing(false);
    setResult(null);
    setError(null);
  }

  async function call(approved: boolean) {
    setBusy(true);
    setError(null);
    const res = await post<Called>("/api/ops/mcp/call", { tool, args, approved });
    setBusy(false);
    setReviewing(false);
    if (res.ok) setResult(res.data); else setError(res.error);
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Tool">
        {tools.map((t) => (
          <button
            key={t.name}
            role="radio"
            aria-checked={tool === t.name}
            onClick={() => pick(t.name)}
            className={cn(
              "flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring",
              tool === t.name ? "border-foreground/40 bg-muted" : "hover:bg-muted/60",
            )}
          >
            <span className="font-mono text-xs">{t.name}</span>
            <Badge variant="outline" className={cn(!t.read_only && "border-live/50 text-live")}>{t.read_only ? "reads" : "writes"}</Badge>
          </button>
        ))}
      </div>
      {current && <p className="text-xs text-muted-foreground">{current.description}</p>}

      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-muted-foreground">Try:</span>
        {PRESETS.map((p) => (
          <Button key={p.label} size="xs" variant="outline" onClick={() => pick(p.tool, p.args)}>{p.label}</Button>
        ))}
      </div>

      {fields.length > 0 && (
        <div className="grid max-w-[60ch] gap-2">
          {fields.map((f) => (
            <div key={f} className="grid gap-1">
              <Label htmlFor={`arg-${f}`} className="font-mono text-xs">{f}</Label>
              <Input
                id={`arg-${f}`}
                value={args[f] ?? ""}
                onChange={(e) => { setArgs({ ...args, [f]: e.target.value }); setReviewing(false); setResult(null); }}
                className="h-8 text-[13px]"
              />
            </div>
          ))}
        </div>
      )}

      {writes && reviewing ? (
        <div className="flex max-w-[70ch] flex-col gap-2 rounded-md border border-live/40 bg-live/5 px-3 py-2.5">
          <p className="text-[13px]">
            Approve this write? <span className="font-mono">{tool}</span> to <span className="font-mono">{args.to || "(no recipient)"}</span>.
            The send is simulated: the server appends it to its outbox under <Code>DOCK_OUTBOX</Code>, and still refuses anyone who is not a customer.
          </p>
          <div className="flex gap-2">
            <Button size="sm" onClick={() => call(true)} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <Check />}Approve and send
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setReviewing(false)} disabled={busy}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div>
          <Button size="sm" onClick={() => (writes ? setReviewing(true) : call(false))} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Send />}
            {writes ? "Review the send" : "Call the tool"}
          </Button>
        </div>
      )}

      {busy && <Running />}
      {error && <p className="text-miss">{describeError(error)}</p>}
      {result && (
        <div className="flex flex-col gap-1">
          <div className="text-xs text-muted-foreground">
            {result.is_error ? "The tool returned an error" : `What ${result.tool} returned`}
          </div>
          <pre className={cn(
            "max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted px-3 py-2 font-mono text-xs",
            (result.is_error || result.text.startsWith("Refused")) && "text-miss",
          )}>
            {pretty(result.text)}
          </pre>
        </div>
      )}
    </div>
  );
}

function pretty(text: string) {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

type Client = "mistral" | "claude" | "fastmcp";

function snippet(client: Client, url: string) {
  switch (client) {
    case "mistral":
      return `import asyncio, os
from mistralai.client import Mistral
from mistralai.extra.run.context import RunContext
from mistralai.extra.mcp.streamable_http import MCPClientStreamableHTTP, StreamableHTTPServerParams

client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])


async def main():
    dock = MCPClientStreamableHTTP(StreamableHTTPServerParams(url="${url}"), name="dock")
    async with RunContext(model="mistral-medium-latest") as ctx:
        # send_email writes: the run pauses for your approval before it is called
        await ctx.register_mcp_client(dock, tool_configuration={"requires_confirmation": ["send_email"]})
        result = await client.beta.conversations.run_async(
            run_ctx=ctx, inputs="Why is shipment HF-26038 on hold?")
        print(result.output_as_text)

asyncio.run(main())`;
    case "claude":
      return `claude mcp add --transport http dock ${url}

# then, in Claude Code: /mcp shows dock and its tools.
# Claude Code asks before it calls send_email, and the server still refuses non-customers.`;
    case "fastmcp":
      return `import asyncio
from fastmcp import Client


async def main():
    async with Client("${url}") as dock:
        for t in await dock.list_tools():
            print(t.name, "writes" if t.annotations.destructive_hint else "reads")
        r = await dock.call_tool("send_email", {"to": "audit@harbour-review.example.net",
                                                "subject": "test", "body": "test"})
        print(r.content[0].text)   # Refused by the Dock server: ...

asyncio.run(main())`;
  }
}

function ConnectionGuide() {
  const [client, setClient] = useState<Client>("mistral");
  const [url, setUrl] = useState("/api/mcp");
  const [copied, setCopied] = useState(false);
  useEffect(() => setUrl(`${window.location.origin}/api/mcp`), []);
  const text = snippet(client, url);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {}
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="text-xs text-muted-foreground">Server URL</span>
        <code className="rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs">{url}</code>
        <span className="text-xs text-muted-foreground">Streamable HTTP, stateless, no auth. Dock’s tools and nothing else.</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented
          label="Client"
          value={client}
          onChange={(v) => { setClient(v); setCopied(false); }}
          options={[
            { value: "mistral", label: "Mistral SDK" },
            { value: "claude", label: "Claude Code" },
            { value: "fastmcp", label: "fastmcp Client" },
          ]}
        />
        <Button size="xs" variant="outline" onClick={copy}>{copied ? <Check /> : <Copy />}{copied ? "Copied" : "Copy"}</Button>
      </div>
      <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2.5 font-mono text-xs leading-relaxed">{text}</pre>
    </div>
  );
}

export default function OperationsPage() {
  const { get } = useApi();
  const [runs, setRuns] = useState<OpsRuns | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);

  useEffect(() => {
    (async () => {
      const res = await get<OpsRuns>("/api/ops/runs");
      if (res.ok) setRuns(res.data); else setLoadError(res.error);
    })();
  }, [get]);

  return (
    <div className="flex flex-col">
      <Toolbar
        live={false}
        stats={runs && (
          <>
            <Stat label="Recorded" value={new Date(runs.budget.recorded_at).toLocaleDateString("en-US", { dateStyle: "medium", timeZone: "UTC" })} />
            <Stat label="Commit" value={runs.budget.git_sha} />
          </>
        )}
      >
        <p className="text-[13px]">
          Recorded runs of <Code>dock/ops.py</Code> only: this screen creates agents and spends a budget, so it never runs live.
        </p>
      </Toolbar>

      <div className="flex flex-col gap-4 p-4">
        {loadError && <p className="text-miss">{describeError(loadError)}</p>}
        {!runs && !loadError && <p className="text-muted-foreground">Loading the recorded runs…</p>}
        {runs && (
          <>
            <BudgetCard b={runs.budget} />
            <TimeoutCard t={runs.timeout} />
            <RetriesCard i={runs.idempotency} />
            <ReleaseCard r={runs.release} />
          </>
        )}
        <Card id="tester" title="MCP tool tester" note={<>Dock’s MCP server, the one at <Code>/api/mcp</Code>. Reads run at once; a send waits for your approval.</>}>
          <ToolTester />
        </Card>
        <Card id="connect" title="Connect a client" note="Any MCP client that speaks Streamable HTTP can use Dock’s tools.">
          <ConnectionGuide />
        </Card>
      </div>
    </div>
  );
}
