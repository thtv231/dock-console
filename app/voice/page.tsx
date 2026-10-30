"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight, Loader2, Mic, Pause, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  Latency, median, MeterTable, num, type Row, type Run, Running, runTokens, Segmented, Stat, Toolbar, useRows,
} from "@/components/run";
import { type ApiError, describeError, type Result, useApi } from "@/lib/api";
import { cn } from "@/lib/utils";

type Field = "shipment_id" | "issue" | "callback";
type Stage = "transcribe" | "understand" | "reply" | "speech";
type Note = { shipment_id: string | null; issue: string; callback: boolean };
type Call = Note & { script: string; voice: string };
type VoiceRun = Run & {
  call_id: string | null;
  bias: boolean;
  transcript: string;
  note: Note;
  truth: Note | null;
  score_vs_truth: Record<Field, boolean> | null;
  reply_text: string;
  voice: string | null;
  audio_url?: string;
  audio_b64?: string | null;
  audio_bytes: number;
  stages: Record<Stage, number | null>;
};
type Pair = Run & { plain: VoiceRun; biased: VoiceRun };
type Counts = { runs: number; right: Record<Field, number>; issues?: Record<string, number> };
type Noted = Counts & { transcripts: string[] };
type RunsSummary = {
  calls: Record<string, { nobias: Counts; bias: Counts }>;
  totals: { nobias: Counts; bias: Counts };
  notes: Record<string, Partial<Record<"nobias" | "bias", Noted>>>;
};

const FIELDS: { key: Field; label: string }[] = [
  { key: "shipment_id", label: "Shipment" },
  { key: "issue", label: "Issue" },
  { key: "callback", label: "Callback" },
];
const STAGES: { key: Stage; label: string }[] = [
  { key: "transcribe", label: "Transcribe" },
  { key: "understand", label: "Understand" },
  { key: "reply", label: "Reply text" },
  { key: "speech", label: "Speech" },
];
const RUNS = ["1", "2", "3", "4", "5"] as const;
const MAX_UPLOAD = 4 * 1024 * 1024;
const MAX_SECONDS = 120;   // 16 kHz mono 16-bit WAV: about 32 kB a second, so two minutes stays under 4 MB

const show = (f: Field, n: Note) => (f === "callback" ? (n.callback ? "callback" : "no callback") : String(n[f] ?? "none"));
const audioSrc = (r: VoiceRun) => r.audio_url ?? (r.audio_b64 ? `data:audio/wav;base64,${r.audio_b64}` : null);

/** Words of a and b, each marked when it isn't part of their longest common run (case and punctuation ignored). */
function diffWords(a: string, b: string) {
  const A = a.split(/\s+/).filter(Boolean);
  const B = b.split(/\s+/).filter(Boolean);
  const norm = (w: string) => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const dp = Array.from({ length: A.length + 1 }, () => new Array<number>(B.length + 1).fill(0));
  for (let i = A.length - 1; i >= 0; i--)
    for (let j = B.length - 1; j >= 0; j--)
      dp[i][j] = norm(A[i]) === norm(B[j]) ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
  const aChanged = A.map(() => true);
  const bChanged = B.map(() => true);
  for (let i = 0, j = 0; i < A.length && j < B.length;) {
    if (norm(A[i]) === norm(B[j])) {
      aChanged[i++] = false;
      bChanged[j++] = false;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) i++;
    else j++;
  }
  return { a: A.map((w, i) => ({ w, changed: aChanged[i] })), b: B.map((w, i) => ({ w, changed: bChanged[i] })) };
}

function Words({ words }: { words: { w: string; changed: boolean }[] }) {
  return (
    <p className="leading-relaxed">
      {words.map((t, i) => (
        <Fragment key={i}>
          {t.changed ? <mark className="rounded-sm bg-primary/15 px-0.5 text-foreground">{t.w}</mark> : t.w}{" "}
        </Fragment>
      ))}
    </p>
  );
}

/** A play/pause button over a hidden <audio>; shows the file's length once its metadata is in. */
function PlayButton({ src, label, duration = false }: { src: string; label: string; duration?: boolean }) {
  const ref = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [seconds, setSeconds] = useState<number | null>(null);
  return (
    <span className="inline-flex items-center gap-1.5">
      <Button
        size="icon-xs"
        variant="outline"
        aria-label={`${playing ? "Pause" : "Play"} ${label}`}
        onClick={() => (playing ? ref.current?.pause() : ref.current?.play())}
      >
        {playing ? <Pause /> : <Play />}
      </Button>
      {duration && seconds !== null && <span className="text-xs tabular-nums text-muted-foreground">{seconds.toFixed(1)} s</span>}
      <audio
        ref={ref}
        src={src}
        preload={duration ? "metadata" : "none"}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onLoadedMetadata={(e) => setSeconds(e.currentTarget.duration)}
      />
    </span>
  );
}

/** The extracted note, each field marked against the truth when there is one. */
function NoteCell({ note, truth }: { note: Note; truth: Note | null }) {
  return (
    <span className="inline-flex flex-wrap gap-x-1.5">
      {FIELDS.map((f, i) => {
        const wrong = truth !== null && note[f.key] !== truth[f.key];
        return (
          <span key={f.key} className={cn(wrong && "font-medium text-miss")} title={wrong ? `Expected ${show(f.key, truth)}` : undefined}>
            {show(f.key, note)}{i < FIELDS.length - 1 && <span className="text-muted-foreground" aria-hidden> ·</span>}
          </span>
        );
      })}
    </span>
  );
}

function Stages({ run }: { run: VoiceRun }) {
  const total = STAGES.reduce((s, st) => s + (run.stages[st.key] ?? 0), 0);
  return (
    <span className="text-xs tabular-nums text-muted-foreground">
      {STAGES.map((st) => `${st.label.toLowerCase()} ${num(run.stages[st.key] ?? 0)}`).join(" · ")} · total {num(total)} ms
    </span>
  );
}

function RunDetail({ title, run, words }: { title: string; run: VoiceRun; words: { w: string; changed: boolean }[] }) {
  const src = audioSrc(run);
  return (
    <div className="flex min-w-0 flex-col gap-3 rounded-md border bg-card px-3 py-2.5">
      <div className="text-xs font-medium">{title}</div>
      <div>
        <div className="mb-1 text-xs text-muted-foreground">Transcript</div>
        <Words words={words} />
      </div>
      <div>
        <div className="mb-1 text-xs text-muted-foreground">Call note</div>
        <NoteCell note={run.note} truth={run.truth} />
      </div>
      <div>
        <div className="mb-1 flex items-center gap-2 text-xs text-muted-foreground">
          <span>Dock’s spoken reply{run.voice ? `, voice ${run.voice}` : ""}</span>
          {src && <PlayButton src={src} label={`Dock’s reply (${title})`} />}
        </div>
        <p className="leading-relaxed">{run.reply_text}</p>
      </div>
      <Stages run={run} />
      <MeterTable run={run} />
    </div>
  );
}

function Budget({ runs }: { runs: VoiceRun[] }) {
  const rows = STAGES.map((st) => {
    const ms = runs.map((r) => r.stages[st.key]).filter((v): v is number => v !== null);
    return { ...st, median: ms.length ? median(ms) : null, max: ms.length ? Math.max(...ms) : null };
  });
  const total = rows.reduce((s, r) => s + (r.median ?? 0), 0);
  return (
    <section aria-labelledby="budget" className="flex flex-col gap-1.5">
      <h2 id="budget" className="text-xs font-medium">Latency budget, with bias ({runs.length} {runs.length === 1 ? "call" : "calls"})</h2>
      <table className="text-xs tabular-nums">
        <thead className="text-muted-foreground">
          <tr>
            <th className="pr-6 text-left font-normal">Stage</th>
            <th className="pr-6 text-right font-normal">Median</th>
            <th className="text-right font-normal">Max</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key}>
              <td className="pr-6">{r.label}</td>
              <td className="pr-6 text-right">{r.median === null ? "" : `${num(r.median)} ms`}</td>
              <td className="text-right">{r.max === null ? "" : `${num(r.max)} ms`}</td>
            </tr>
          ))}
          <tr className="border-t font-medium">
            <td className="pr-6 pt-0.5">Total</td>
            <td className="pr-6 pt-0.5 text-right">{num(total)} ms</td>
            <td />
          </tr>
        </tbody>
      </table>
      <p className="max-w-[44ch] text-[11px] text-muted-foreground">
        Total is the sum of the stage medians. Listing the voices, once per run, is left out of the speech stage, as in s09.
      </p>
    </section>
  );
}

/** Every recorded run counted against the truth: what bias changes, which one run can't show. */
function Counted({ summary, calls }: { summary: RunsSummary; calls: string[] }) {
  const cell = (c: Counts, f: Field) => {
    const all = c.right[f] === c.runs;
    return <td key={f} className={cn("px-2 text-right", !all && "font-medium text-miss")}>{c.right[f]}/{c.runs}</td>;
  };
  return (
    <section aria-labelledby="counted" className="flex flex-col gap-1.5">
      <h2 id="counted" className="text-xs font-medium">Recorded runs right against the truth</h2>
      <table className="text-xs tabular-nums">
        <thead className="text-muted-foreground">
          <tr>
            <th />
            <th colSpan={3} className="border-l px-2 text-center font-normal">Without bias</th>
            <th colSpan={3} className="border-l px-2 text-center font-normal">With bias</th>
          </tr>
          <tr>
            <th className="pr-2 text-left font-normal">Call</th>
            {[0, 1].flatMap((g) => FIELDS.map((f, i) => (
              <th key={`${g}${f.key}`} className={cn("px-2 text-right font-normal", i === 0 && "border-l")}>{f.label}</th>
            )))}
          </tr>
        </thead>
        <tbody>
          {calls.map((id) => {
            const c = summary.calls[id];
            if (!c) return null;
            const wrongIssues = Object.entries(c.nobias.issues ?? {}).length > 1
              ? Object.entries(c.nobias.issues ?? {}).map(([k, v]) => `${k} ${v}`).join(", ") : undefined;
            return (
              <tr key={id} title={wrongIssues && `Issues heard without bias: ${wrongIssues}`}>
                <td className="pr-2 font-mono">{id}</td>
                {FIELDS.map((f) => cell(c.nobias, f.key))}
                {FIELDS.map((f) => cell(c.bias, f.key))}
              </tr>
            );
          })}
          <tr className="border-t font-medium">
            <td className="pr-2 pt-0.5">All</td>
            {FIELDS.map((f) => cell(summary.totals.nobias, f.key))}
            {FIELDS.map((f) => cell(summary.totals.bias, f.key))}
          </tr>
        </tbody>
      </table>
    </section>
  );
}

/** note() run again on one recorded transcript per source: on the same words, a changing issue is understanding. */
function HearingOrUnderstanding({ callId, noted, truth }: {
  callId: string; noted: RunsSummary["notes"][string]; truth: Note;
}) {
  const plain = noted.nobias;
  const biased = noted.bias;
  const diff = plain && biased ? diffWords(plain.transcripts[0] ?? "", biased.transcripts[0] ?? "") : null;
  const rows: { label: string; c: Noted | undefined; words: { w: string; changed: boolean }[] | null }[] = [
    { label: "Without bias", c: plain, words: diff?.a ?? null },
    { label: "With bias", c: biased, words: diff?.b ?? null },
  ];
  return (
    <section aria-labelledby={`noted-${callId}`} className="flex max-w-[64ch] flex-col gap-1.5">
      <h2 id={`noted-${callId}`} className="text-xs font-medium">
        Hearing or understanding: {callId}, the call note written again on one recorded transcript
      </h2>
      <p className="text-[11px] text-muted-foreground">
        Each transcript below is run 1 of its column, noted again with the same prompt and nothing else changed.
        If the issue changes on the same words, the miss is in understanding, not in hearing.
        {diff && " Marked words differ between the two transcripts."}
      </p>
      <table className="text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="pr-3 text-left font-normal">Transcript</th>
            <th className="pr-3 text-left font-normal">Issue, as noted</th>
            <th className="text-right font-normal">Right</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ label, c, words }) => c && (
            <tr key={label} className="align-top">
              <td className="py-1 pr-3">
                <div className="text-muted-foreground">{label}{c.transcripts.length > 1 ? `, ${c.transcripts.length} different transcripts` : ""}</div>
                {words ? <Words words={words} /> : <p className="leading-relaxed">{c.transcripts[0]}</p>}
              </td>
              <td className="whitespace-nowrap py-1 pr-3 tabular-nums">
                {Object.entries(c.issues ?? {}).sort((a, b) => b[1] - a[1]).map(([issue, n]) => (
                  <div key={issue} className={cn(issue !== truth.issue && "text-miss")}>{issue} {n}</div>
                ))}
              </td>
              <td className={cn("py-1 text-right tabular-nums", c.right.issue !== c.runs && "font-medium text-miss")}>
                {c.right.issue}/{c.runs}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-muted-foreground">The truth for {callId} is {truth.issue}.</p>
    </section>
  );
}

// ---------- the mic, Live only ----------
async function toWav(blob: Blob): Promise<Blob> {
  const ctx = new AudioContext();
  const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  await ctx.close();
  const rate = 16000;
  const off = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
  const source = off.createBufferSource();
  source.buffer = decoded;
  source.connect(off.destination);
  source.start();
  const pcm = (await off.startRendering()).getChannelData(0);
  const buffer = new ArrayBuffer(44 + pcm.length * 2);
  const v = new DataView(buffer);
  const text = (at: number, s: string) => [...s].forEach((ch, i) => v.setUint8(at + i, ch.charCodeAt(0)));
  text(0, "RIFF");
  v.setUint32(4, 36 + pcm.length * 2, true);
  text(8, "WAVEfmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  text(36, "data");
  v.setUint32(40, pcm.length * 2, true);
  pcm.forEach((s, i) => v.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s)) * 0x7fff, true));
  return new Blob([buffer], { type: "audio/wav" });
}

function MicDesk({ live }: { live: boolean }) {
  const { upload } = useApi();
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [bias, setBias] = useState<"bias" | "plain">("bias");
  const [row, setRow] = useState<Row<VoiceRun> | null>(null);
  const [micError, setMicError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearInterval(timer.current);
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
  }, []);

  async function start() {
    setMicError(null);
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return setMicError("The browser did not give access to the microphone.");
    }
    const chunks: Blob[] = [];
    const rec = new MediaRecorder(stream);
    rec.ondataavailable = (e) => chunks.push(e.data);
    rec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop());
      if (timer.current) clearInterval(timer.current);
      setRecording(false);
      setRow({ status: "running" });
      let wav: Blob;
      try {
        wav = await toWav(new Blob(chunks, { type: rec.mimeType }));
      } catch {
        setRow(null);
        return setMicError("The recording could not be read. Try again.");
      }
      if (wav.size > MAX_UPLOAD) return setRow({ status: "error", error: { error: "audio_too_large", status: 413 } });
      const form = new FormData();
      form.append("file", wav, "mic.wav");
      form.append("bias", bias === "bias" ? "true" : "false");
      const res = await upload<VoiceRun>("/api/voice/upload", form);
      setRow(res.ok ? { status: "done", result: res.data } : { status: "error", error: res.error });
    };
    recorder.current = rec;
    rec.start();
    setSeconds(0);
    setRecording(true);
    const began = Date.now();
    timer.current = setInterval(() => {
      const s = (Date.now() - began) / 1000;
      setSeconds(s);
      if (s >= MAX_SECONDS && rec.state === "recording") rec.stop();
    }, 200);
  }

  const result = row?.status === "done" ? row.result : null;
  return (
    <div className="border-b px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        {recording ? (
          <Button size="sm" variant="destructive" onClick={() => recorder.current?.stop()}>
            <Square />Stop, {seconds.toFixed(0)} s
          </Button>
        ) : (
          <Button size="sm" onClick={start} disabled={!live || row?.status === "running"}>
            {row?.status === "running" ? <Loader2 className="animate-spin" /> : <Mic />}
            {row?.status === "running" ? "Running Dock on it" : "Leave a voicemail"}
          </Button>
        )}
        <Segmented
          label="Context bias for the mic"
          value={bias}
          onChange={setBias}
          options={[{ value: "bias", label: "With bias" }, { value: "plain", label: "Without bias" }]}
        />
        <p className="text-xs text-muted-foreground">
          {live
            ? `Speak as a driver would, up to ${MAX_SECONDS / 60} minutes (4 MB). It runs on your key and is not recorded.`
            : "Recording from the mic runs Live only. Add your Mistral key to leave a voicemail."}
        </p>
      </div>
      {micError && <p className="mt-2 text-miss">{micError}</p>}
      {row?.status === "error" && <p className="mt-2 text-miss">{describeError(row.error)}</p>}
      {result && (
        <div className="mt-3 max-w-3xl">
          <RunDetail title={`Your voicemail, ${result.bias ? "with" : "without"} bias`} run={result}
                     words={diffWords(result.transcript, result.transcript).a} />
        </div>
      )}
    </div>
  );
}

export default function VoicePage() {
  const { get, post, live } = useApi();
  const [calls, setCalls] = useState<Record<string, Call> | null>(null);
  const [summary, setSummary] = useState<RunsSummary | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [recordedRun, setRecordedRun] = useState<(typeof RUNS)[number]>("1");

  useEffect(() => {
    (async () => {
      const [data, runs] = await Promise.all([
        get<{ data: Record<string, Call> }>("/api/data/calls"),
        get<RunsSummary>("/api/voice/runs"),
      ]);
      if (!data.ok) return setLoadError(data.error);
      setCalls(data.data.data);
      if (runs.ok) setSummary(runs.data);
    })();
    // The datasets and the recorded counts don't depend on the key, so load them once.
  }, []);

  const run = useCallback(async (id: string): Promise<Result<Pair>> => {
    // Live ignores the run number: every live call is a new run.
    const [plain, biased] = await Promise.all([
      post<VoiceRun>("/api/voice/run", { call_id: id, bias: false, run: Number(recordedRun) }),
      post<VoiceRun>("/api/voice/run", { call_id: id, bias: true, run: Number(recordedRun) }),
    ]);
    if (!plain.ok) return plain;
    if (!biased.ok) return biased;
    return { ok: true, data: { plain: plain.data, biased: biased.data, mode: biased.data.mode, meter: [...plain.data.meter, ...biased.data.meter] } };
  }, [post, recordedRun]);
  const { rows, runOne, runAll, runningAll, running, failed, done, clear } = useRows(run);

  if (loadError) return <p className="p-6 text-miss">{describeError(loadError)}</p>;
  if (!calls) return <p className="p-6 text-muted-foreground">Loading the voicemails…</p>;

  const ids = Object.keys(calls);
  const right = (r: VoiceRun) => r.score_vs_truth !== null && Object.values(r.score_vs_truth).every(Boolean);

  return (
    <div className="flex flex-col">
      <Toolbar
        live={live}
        run="call"
        stats={done.length > 0 && (
          <>
            <Stat label="Calls" value={`${done.length} of ${ids.length}`} />
            <Stat label="Notes right, without bias" value={`${done.filter((p) => right(p.plain)).length} of ${done.length}`} />
            <Stat label="Notes right, with bias" value={`${done.filter((p) => right(p.biased)).length} of ${done.length}`} />
            {failed > 0 && <Stat label="Not run" value={String(failed)} />}
            <Stat label="Tokens" value={num(done.reduce((s, p) => s + runTokens(p), 0))} />
          </>
        )}
      >
        <Button size="sm" onClick={() => runAll(ids)} disabled={runningAll || running > 0}>
          {runningAll ? <Loader2 className="animate-spin" /> : <Play />}
          {runningAll ? `Running, ${running} at a time` : `Run all ${ids.length}, with and without bias`}
        </Button>
        {!live && (
          <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
            Recorded run
            <Segmented
              label="Recorded run, with and without bias"
              value={recordedRun}
              onChange={(v) => { setRecordedRun(v); clear(); }}
              options={RUNS.map((n) => ({ value: n, label: n }))}
            />
          </span>
        )}
        <Button size="sm" variant="ghost" onClick={clear} disabled={running > 0 || Object.keys(rows).length === 0}>
          Clear results
        </Button>
      </Toolbar>

      <MicDesk live={live} />

      <Table className="text-[13px]">
        <TableHeader className="bg-muted/60">
          <TableRow className="hover:bg-transparent">
            <TableHead colSpan={3} className="h-7 text-xs text-muted-foreground">Voicemail</TableHead>
            <TableHead className="h-7 border-l text-xs text-muted-foreground">Truth</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Dock’s call note</TableHead>
            <TableHead colSpan={2} className="h-7 border-l text-xs text-muted-foreground">Run</TableHead>
          </TableRow>
          <TableRow className="hover:bg-transparent">
            <TableHead className="h-8 w-8" aria-label="Expand" />
            <TableHead className="h-8">Call</TableHead>
            <TableHead className="h-8">Listen</TableHead>
            <TableHead className="h-8 border-l">Shipment · issue · callback</TableHead>
            <TableHead className="h-8 border-l">Without bias{live ? "" : `, run ${recordedRun}`}</TableHead>
            <TableHead className="h-8">With bias{live ? "" : `, run ${recordedRun}`}</TableHead>
            <TableHead className="h-8 border-l text-right">Latency</TableHead>
            <TableHead className="h-8 w-24" aria-label="Action" />
          </TableRow>
        </TableHeader>
        <TableBody>
          {ids.map((id) => {
            const call = calls[id];
            const row = rows[id];
            const pair = row?.status === "done" ? row.result : null;
            const isOpen = open[id] ?? false;
            const diff = pair && diffWords(pair.plain.transcript, pair.biased.transcript);
            return (
              <Fragment key={id}>
                <TableRow className={cn(isOpen && "border-b-0 bg-muted/40")}>
                  <TableCell className="py-1.5">
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={isOpen ? `Hide ${id}` : `Show ${id}`}
                      aria-expanded={isOpen}
                      onClick={() => setOpen((o) => ({ ...o, [id]: !isOpen }))}
                    >
                      {isOpen ? <ChevronDown /> : <ChevronRight />}
                    </Button>
                  </TableCell>
                  <TableCell className="py-1.5 font-mono text-xs">{id}</TableCell>
                  <TableCell className="py-1.5"><PlayButton src={`/api/files/calls/${id}`} label={`voicemail ${id}`} duration /></TableCell>
                  <TableCell className="border-l bg-muted/30 py-1.5 text-muted-foreground">
                    {FIELDS.map((f) => show(f.key, call)).join(" · ")}
                  </TableCell>
                  {row?.status === "error" ? (
                    <TableCell colSpan={3} className="max-w-0 border-l py-1.5">
                      <span className="block truncate text-miss" title={describeError(row.error)}>{describeError(row.error)}</span>
                    </TableCell>
                  ) : (
                    <>
                      <TableCell className="border-l py-1.5">
                        {row?.status === "running" && <Running />}
                        {pair && <NoteCell note={pair.plain.note} truth={call} />}
                      </TableCell>
                      <TableCell className="py-1.5">{pair && <NoteCell note={pair.biased.note} truth={call} />}</TableCell>
                      <TableCell className="border-l py-1.5 text-right tabular-nums">{pair && <Latency run={pair.biased} />}</TableCell>
                    </>
                  )}
                  <TableCell className="py-1.5 text-right">
                    <Button size="xs" variant="outline" onClick={() => runOne(id)} disabled={row?.status === "running"}>
                      {row ? "Run again" : "Run"}
                    </Button>
                  </TableCell>
                </TableRow>

                {isOpen && (
                  <TableRow className="bg-muted/40 hover:bg-muted/40">
                    <TableCell />
                    <TableCell colSpan={7} className="whitespace-normal pb-3 pt-0">
                      <p className="mb-2 max-w-[80ch] text-muted-foreground">
                        Script, as {call.voice} read it: <span className="text-foreground">{call.script}</span>
                      </p>
                      {pair && diff ? (
                        <>
                          <p className="mb-2 text-xs text-muted-foreground">
                            <mark className="rounded-sm bg-primary/15 px-0.5 text-foreground">Marked</mark> words differ between the two transcripts.
                          </p>
                          <div className="grid gap-3 lg:grid-cols-2">
                            <RunDetail title={`Without bias${pair.plain.mode === "replay" ? `, run ${recordedRun}` : ""}`} run={pair.plain} words={diff.a} />
                            <RunDetail title={`With bias${pair.biased.mode === "replay" ? `, run ${recordedRun}` : ""}`} run={pair.biased} words={diff.b} />
                          </div>
                        </>
                      ) : (
                        <p className="text-muted-foreground">Not run yet. Press Run to transcribe it with and without bias.</p>
                      )}
                    </TableCell>
                  </TableRow>
                )}
              </Fragment>
            );
          })}
        </TableBody>
      </Table>

      <div className="flex flex-wrap gap-x-12 gap-y-6 px-4 py-4">
        {done.length > 0 ? <Budget runs={done.map((p) => p.biased)} /> : (
          <p className="text-xs text-muted-foreground">Run the calls to see the latency budget per stage.</p>
        )}
        {summary && !live && <Counted summary={summary} calls={ids} />}
        {summary && !live && Object.entries(summary.notes).map(([id, noted]) => (
          <HearingOrUnderstanding key={id} callId={id} noted={noted} truth={calls[id]} />
        ))}
      </div>
    </div>
  );
}
