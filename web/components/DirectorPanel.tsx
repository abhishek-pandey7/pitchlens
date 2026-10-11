"use client";

import { useState } from "react";
import { api, type DirectorMode, type PendingCue, type ProgrammeLogEntry, type ProgrammeState } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const MODES: { mode: DirectorMode; label: string; hint: string }[] = [
  { mode: "off", label: "Off", hint: "Cues air by the scheduler's rules, as on any fan feed" },
  { mode: "auto", label: "Auto", hint: "The Director agent decides what goes on air" },
  { mode: "approve", label: "Approve", hint: "The Director proposes; you decide" },
];

const BY_TONE: Record<string, string> = { director: "text-away", producer: "text-accent", rules: "text-muted" };

/** The Director (T-125): makes the previewed fan feed the programme and curates its cues. In approve
 *  mode every non-urgent cue waits here with the Director's proposal; goals and red cards never wait. */
export function DirectorPanel({ matchId, profileId }: { matchId: string; profileId: string }) {
  const [tick, setTick] = useState(0);
  const [fresh, setFresh] = useState<{ tick: number; state: ProgrammeState } | null>(null);
  const polled = usePoll(`${matchId}|${tick}`, () => api.director(matchId), 1000);
  const state = polled ?? (fresh?.tick === tick ? fresh.state : null);
  const [error, setError] = useState<string | null>(null);

  async function act(run: () => Promise<ProgrammeState>) {
    setError(null);
    try {
      const next = await run();
      setFresh({ tick: tick + 1, state: next });
      setTick((t) => t + 1);
    } catch (err) {
      setError(String(err).replace(/^Error: /, ""));
    }
  }

  const mode = state?.mode ?? "off";
  const elsewhere = state && state.mode !== "off" && state.profile_id !== profileId;
  return (
    <section aria-label="Director" className="rounded-lg bg-surface p-3 text-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="me-auto text-sm font-semibold uppercase tracking-widest text-muted">Director</h2>
        <div role="radiogroup" aria-label="Director mode" className="flex rounded-md bg-surface-2 p-0.5">
          {MODES.map((m) => (
            <button
              key={m.mode}
              type="button"
              role="radio"
              aria-checked={mode === m.mode && !elsewhere}
              title={m.hint}
              data-testid={`director-${m.mode}`}
              onClick={() => void act(() => api.setDirector(matchId, { mode: m.mode, profile_id: profileId }))}
              className={`rounded px-2.5 py-1 text-xs font-semibold ${mode === m.mode && !elsewhere ? "bg-accent text-background" : "text-muted hover:text-foreground"}`}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>
      {elsewhere && (
        <p className="mb-2 text-xs text-warning">
          The programme is {state.profile_id} ({state.mode}). Pick a mode to direct this feed instead.
        </p>
      )}
      {state?.on_air && mode !== "off" && (
        <p className="mb-2 font-mono text-xs text-muted">
          on air {state.on_air.cues_last_minute}/{state.on_air.max_per_minute} a minute
          {Object.entries(state.on_air.busy_regions_s).map(([region, s]) => ` · ${region} ${s}s`)}
        </p>
      )}
      {error && <p className="mb-2 text-xs text-warning">{error}</p>}
      {mode !== "off" && (
        <ol className="flex flex-col gap-2" aria-label="Pending cues">
          {!state?.pending.length && <li className="text-xs text-muted">No cues waiting. Goals and red cards always air at once.</li>}
          {state?.pending.map((cue) => (
            <Pending key={cue.cue_id} cue={cue} onAct={(action) => void act(() => api.produce(matchId, cue.cue_id, action))} />
          ))}
        </ol>
      )}
      {!!state?.log.length && (
        <details className="mt-2" open={mode !== "off"}>
          <summary className="cursor-pointer text-xs text-muted">Decisions</summary>
          <ol className="mt-1 flex max-h-56 flex-col gap-1 overflow-y-auto pe-1">
            {state.log.slice(0, 12).map((e) => (
              <LogLine key={`${e.at}-${e.cue_id}-${e.result}`} entry={e} />
            ))}
          </ol>
        </details>
      )}
    </section>
  );
}

function Pending({ cue, onAct }: { cue: PendingCue; onAct: (action: "air" | "hold" | "drop" | "approve") => void }) {
  return (
    <li className="rounded-md bg-surface-2 p-2" data-testid="pending-cue">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="font-mono">{cue.region}</span>
        <span>p{cue.priority}</span>
        <span>{cue.waiting_s}s</span>
        {cue.held && <span className="text-warning">held</span>}
      </div>
      <p className="font-semibold">{cue.headline}</p>
      {cue.proposal && (
        <p className="text-xs">
          <span className="font-semibold text-away">Director: {cue.proposal}</span>
          {cue.reason && <span className="text-muted"> · {cue.reason}</span>}
        </p>
      )}
      <div className="mt-1 flex flex-wrap gap-1.5">
        {cue.proposal && (
          <button type="button" onClick={() => onAct("approve")} className="rounded bg-accent px-2 py-0.5 text-xs font-semibold text-background">
            Approve
          </button>
        )}
        {(["air", "hold", "drop"] as const).map((a) => (
          <button key={a} type="button" onClick={() => onAct(a)} className="rounded bg-surface px-2 py-0.5 text-xs capitalize text-muted hover:text-foreground">
            {a}
          </button>
        ))}
      </div>
    </li>
  );
}

function LogLine({ entry: e }: { entry: ProgrammeLogEntry }) {
  return (
    <li className="text-xs">
      <span className="font-mono text-muted">{e.minute}</span>{" "}
      <span className={`font-semibold ${BY_TONE[e.by] ?? ""}`}>{e.by}</span>{" "}
      <span>
        {e.action} → <span className={e.ok ? "" : "text-warning"}>{e.result}</span>
      </span>{" "}
      <span className="text-foreground">{e.headline}</span>
      {(e.reason || e.detail) && <span className="text-muted"> · {[e.reason, e.detail].filter(Boolean).join(" · ")}</span>}
    </li>
  );
}
