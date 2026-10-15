"use client";

// The pitch, plus a replay of the whole match once it has finished. The replay is built in the browser:
// ~160 KB of packed events go to the Rust kernel (WebAssembly, in a worker), which rebuilds every player's
// and the ball's 5 Hz positions exactly as the engine does. Dragging then needs no network at all.
// Only after the whistle: before it, the whole-match build would show what happens next.
import { useEffect, useMemo, useState } from "react";
import { PitchView } from "@/components/PitchView";
import { api } from "@/lib/api";
import { minuteLabel } from "@/lib/clock";
import { scrubStrings } from "@/lib/i18n";
import { buildTracking, type BuiltTracking } from "@/lib/kernel";
import type { Status } from "@/lib/stream";
import type { MatchMeta, TrackingFrame } from "@/lib/types";

type Props = {
  matchId: string;
  meta: MatchMeta | null;
  liveFrame: TrackingFrame | null;
  status: Status;
  focusPlayerId?: string | null;
  lang: string;
};

type Replay = BuiltTracking & { playerIds: string[]; matchId: string; period2Start: number };

const PLAY_STEP = 5; // frames per 40 ms tick: 25 s of match each second, a full half in under 2 minutes

export function MatchScrubber({ matchId, meta, liveFrame, status, focusPlayerId, lang }: Props) {
  const s = scrubStrings(lang);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [state, setState] = useState<"idle" | "building" | "failed">("idle");
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const finished = status === "finished";

  const open = async () => {
    setState("building");
    try {
      const input = await api.trackingInput(matchId);
      const built = await buildTracking(input);
      let period2Start = 45 * 60_000;
      for (let i = 0; i < built.frames; i++) {
        if (built.period[i] === 2) {
          period2Start = built.ms[i];
          break;
        }
      }
      setReplay({ ...built, playerIds: input.player_ids, matchId, period2Start });
      setIndex(0);
      setState("idle");
    } catch {
      setState("failed");
    }
  };

  useEffect(() => {
    if (!playing || !replay) return;
    const timer = setInterval(() => {
      setIndex((i) => {
        if (i + PLAY_STEP >= replay.frames - 1) {
          setPlaying(false);
          return replay.frames - 1;
        }
        return i + PLAY_STEP;
      });
    }, 40);
    return () => clearInterval(timer);
  }, [playing, replay]);

  const frame = useMemo<TrackingFrame | null>(() => {
    if (!replay) return null;
    const i = Math.min(index, replay.frames - 1);
    const players = [];
    for (let c = 0; c < replay.players; c++) {
      const at = (i * replay.players + c) * 2;
      const x = replay.xy[at];
      if (!Number.isNaN(x)) players.push({ player_id: replay.playerIds[c], x, y: replay.xy[at + 1] });
    }
    return {
      match_id: replay.matchId,
      match_ms: replay.ms[i],
      ball: { x: replay.ball[i * 3], y: replay.ball[i * 3 + 1], z: replay.ball[i * 3 + 2] },
      players,
    } as TrackingFrame;
  }, [replay, index]);

  const clock = (i: number) => {
    if (!replay) return "";
    const ms = replay.ms[i];
    const period = replay.period[i];
    const clockMs = period === 2 ? 45 * 60_000 + (ms - replay.period2Start) : ms;
    return minuteLabel(clockMs, period);
  };

  const close = () => {
    setPlaying(false);
    setReplay(null);
  };

  return (
    <div className="flex flex-col gap-2">
      <PitchView frame={replay ? frame : liveFrame} meta={meta} focusPlayerId={focusPlayerId} />
      {finished && !replay && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <button
            type="button"
            onClick={open}
            disabled={state === "building"}
            className="rounded-md border border-surface-2 px-3 py-1.5 font-semibold hover:border-accent disabled:opacity-60"
          >
            {s.open}
          </button>
          {state === "building" && <span className="text-muted">{s.building}</span>}
          {state === "failed" && <span className="text-danger">{s.failed}</span>}
        </div>
      )}
      {replay && (
        <div className="flex flex-col gap-2 text-sm" data-testid="match-scrubber">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setPlaying((p) => !p)}
              className="w-20 rounded-md bg-accent px-3 py-1.5 font-semibold text-white"
            >
              {playing ? s.pause : s.play}
            </button>
            <input
              type="range"
              min={0}
              max={replay.frames - 1}
              value={index}
              onChange={(e) => {
                setPlaying(false);
                setIndex(Number(e.target.value));
              }}
              aria-label={s.position}
              aria-valuetext={clock(index)}
              className="min-w-0 flex-1 accent-[var(--accent)]"
            />
            <span className="w-14 text-end font-mono font-semibold">{clock(index)}</span>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2 text-muted">
            <span data-testid="kernel-built">
              {s.built.replace("{frames}", replay.frames.toLocaleString()).replace("{ms}", replay.buildMs.toFixed(0))}
            </span>
            <button type="button" onClick={close} className="font-semibold text-accent hover:underline">
              {s.back}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
