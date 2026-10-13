"use client";

import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const STAGES: { key: string; label: string; budget: number }[] = [
  { key: "event_to_broadcast", label: "Event → screen", budget: 300 },
  { key: "interpret", label: "Interpret", budget: 20 },
  { key: "explain", label: "Explain", budget: 30 },
  { key: "personalize", label: "Personalise", budget: 20 },
  { key: "narration", label: "AI narration", budget: 3000 },
];

/** Per-stage latency (p95) against the budget in plan §17, polled from the engine. */
export function LatencyHud({ matchId }: { matchId: string }) {
  const data = usePoll(matchId, () => api.latency(matchId), 2000);
  return (
    <section aria-label="Latency" className="panel">
      <h2 className="mb-2 section-title">
        Latency <span className="font-normal text-muted">(95th percentile against budget)</span>
      </h2>
      <dl className="grid grid-cols-5 gap-2 text-center">
        {STAGES.map(({ key, label, budget }) => {
          const stage = data?.stages[key];
          const ok = stage ? stage.p95_ms <= budget : true;
          return (
            <div key={key} className="rounded-md bg-surface-2 p-2">
              <dt className="text-[11px] text-muted">{label}</dt>
              <dd className={`font-mono text-sm tabular-nums ${ok ? "text-foreground" : "text-warning"}`} data-testid={`latency-${key}`}>
                {stage ? fmt(stage.p95_ms) : "–"}
              </dd>
              <dd className="font-mono text-[10px] text-muted">≤ {fmt(budget)}</dd>
            </div>
          );
        })}
      </dl>
      {data && <p className="mt-2 text-xs text-muted">{data.subscribers} viewers connected</p>}
    </section>
  );
}

function fmt(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)} s`;
  if (ms >= 10) return `${Math.round(ms)} ms`;
  return `${ms.toFixed(1)} ms`;
}
