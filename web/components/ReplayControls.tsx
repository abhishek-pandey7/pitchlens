"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const STARTS = [
  { label: "Kick-off", ms: 0 },
  { label: "60'", ms: 60 * 60_000 },
  { label: "80'", ms: 80 * 60_000 },
];
const SPEEDS = [1, 10, 30, 60];

/** Producer replay controls: start from a minute, change speed, pause and resume. */
export function ReplayControls({ matchId }: { matchId: string }) {
  const [speed, setSpeed] = useState(10);
  const [error, setError] = useState<string | null>(null);
  const match = usePoll(matchId, () => api.match(matchId), 1500);
  const state = match?.replay.state ?? "idle";
  // While a replay runs, the select shows its real speed: otherwise picking the speed the select already
  // shows (but the replay is not running at) would not register as a change.
  const live = (state === "playing" || state === "paused") && match?.replay.speed ? match.replay.speed : null;
  const shown = live ?? speed;
  const options = SPEEDS.includes(shown) ? SPEEDS : [...SPEEDS, shown].sort((a, b) => a - b);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(String(err));
    }
  }

  return (
    <section aria-label="Replay controls" className="flex flex-wrap items-center gap-2 rounded-lg bg-surface p-3 text-sm">
      <span className="me-1 text-xs uppercase tracking-widest text-muted">Replay</span>
      {STARTS.map((s) => (
        <button
          key={s.label}
          type="button"
          onClick={() => run(() => api.replay(matchId, { speed, from_ms: s.ms }))}
          className="rounded-md bg-surface-2 px-2.5 py-1 hover:bg-accent hover:text-background"
        >
          ▶ {s.label}
        </button>
      ))}
      <label className="flex items-center gap-1 text-xs text-muted">
        Speed
        <select
          value={shown}
          aria-label="Speed"
          onChange={(e) => {
            const next = Number(e.target.value);
            setSpeed(next);
            if (state === "playing" || state === "paused") void run(() => api.control(matchId, { action: "speed", speed: next }));
          }}
          className="rounded bg-surface-2 px-1.5 py-1 text-foreground"
        >
          {options.map((s) => (
            <option key={s} value={s}>
              {s}×
            </option>
          ))}
        </select>
      </label>
      {state === "playing" && (
        <button type="button" onClick={() => run(() => api.control(matchId, { action: "pause" }))} className="rounded-md bg-surface-2 px-2.5 py-1">
          ❚❚ Pause
        </button>
      )}
      {state === "paused" && (
        <button type="button" onClick={() => run(() => api.control(matchId, { action: "resume" }))} className="rounded-md bg-surface-2 px-2.5 py-1">
          ▶ Resume
        </button>
      )}
      <span className="ms-auto font-mono text-xs text-muted" data-testid="replay-state">
        {state}
        {match?.replay.speed ? ` · ${match.replay.speed}×` : ""}
      </span>
      {error && <p className="w-full text-xs text-warning">{error}</p>}
    </section>
  );
}
