"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowRight } from "lucide-react";
import { num, Stat, Toolbar } from "@/components/run";
import { type ApiError, describeError, useApi } from "@/lib/api";
import { SCREENS } from "@/lib/screens";

type Score = { label: string; value: string; n?: number; of?: number };
type ScreenSummary = {
  href: string; replays: string[]; scores: Score[]; requests: number;
  median_ms: number | null; median_tokens: number | null; recorded_at: string | null; git_shas: string[];
};
type Overview = { screens: ScreenSummary[] };

const day = (iso: string) => new Date(iso).toLocaleDateString("en-US", { dateStyle: "medium", timeZone: "UTC" });

function ScreenCard({ s }: { s: ScreenSummary }) {
  const screen = SCREENS.find((x) => x.href === s.href);
  if (!screen) return null;
  const Icon = screen.icon;
  return (
    <section aria-labelledby={`card-${s.href}`} className="flex min-w-0 flex-col rounded-md border bg-card">
      <Link
        href={s.href}
        className="group flex items-center gap-2 border-b px-3 py-2 outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <Icon className="size-4 shrink-0 text-muted-foreground" />
        <h2 id={`card-${s.href}`} className="flex-1 text-[13px] font-medium">{screen.title}</h2>
        <ArrowRight className="size-3.5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
      </Link>
      <div className="flex flex-1 flex-col gap-3 px-3 py-3">
        <p className="text-[13px] leading-relaxed">{screen.proves}</p>
        <table className="text-xs tabular-nums">
          <tbody>
            {s.scores.map((sc) => (
              <tr key={sc.label}>
                <td className="py-0.5 pr-3 text-muted-foreground">{sc.label}</td>
                <td className="py-0.5 text-right font-medium">{sc.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-auto flex flex-wrap gap-x-6 gap-y-2 border-t pt-2.5">
          <Stat label="Median latency" value={s.median_ms === null ? "–" : `${num(Math.round(s.median_ms))} ms`} />
          <Stat label="Median tokens" value={s.median_tokens === null ? "pages, not tokens" : num(Math.round(s.median_tokens))} />
          <Stat label="Recorded requests" value={num(s.requests)} />
        </div>
        <p className="text-[11px] text-muted-foreground">
          {s.recorded_at && `Last recorded ${day(s.recorded_at)} · `}
          {s.git_shas.length === 1 ? "commit" : "commits"} {s.git_shas.join(", ")} · replays/{s.replays.map((r) => `${r}.json`).join(", ")}
        </p>
      </div>
    </section>
  );
}

export default function OverviewPage() {
  const { get } = useApi();
  const [overview, setOverview] = useState<Overview | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);

  useEffect(() => {
    (async () => {
      const res = await get<Overview>("/api/overview");
      if (res.ok) setOverview(res.data); else setLoadError(res.error);
    })();
  }, [get]);

  const cards = overview?.screens.filter((s) => s.replays.length > 0) ?? [];
  const requests = cards.reduce((sum, s) => sum + s.requests, 0);
  const commits = new Set(cards.flatMap((s) => s.git_shas));

  return (
    <div className="flex flex-col">
      <Toolbar
        live={false}
        stats={overview && (
          <>
            <Stat label="Recorded requests" value={num(requests)} />
            <Stat label="From commits" value={num(commits.size)} />
          </>
        )}
      >
        <p className="text-[13px]">
          Each card is computed from the recorded runs in <code className="rounded-sm bg-muted px-1 py-0.5 font-mono text-xs">replays/</code>.
          Latency and tokens are per request, summed over its calls.
        </p>
      </Toolbar>
      <div className="p-4">
        {loadError && <p className="text-miss">{describeError(loadError)}</p>}
        {!overview && !loadError && <p className="text-muted-foreground">Loading the overview…</p>}
        <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">
          {cards.map((s) => <ScreenCard key={s.href} s={s} />)}
        </div>
      </div>
    </div>
  );
}
