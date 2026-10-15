"use client";

// What if…: the fan changes the score, the minute, red cards or momentum, and the futures recalculate
// instantly in the browser: the same Rust futures model the engine runs, compiled to WebAssembly and run in
// a worker. The engine's input for the match as it stands (`GET /futures/input`) is the starting point; the
// same input, seed and paths give exactly the engine's numbers. Without the kernel it asks the engine.
import { useEffect, useRef, useState } from "react";
import { FuturesPanel } from "@/components/FuturesPanel";
import { api, type FuturesNow } from "@/lib/api";
import { whatIfStrings } from "@/lib/i18n";
import { remainingMinutes, simulateFutures, type FuturesRaw } from "@/lib/kernel";
import type { Futures, FuturesInput, MatchMeta } from "@/lib/types";

const PATHS = 50_000; // enough for steady percentages; a few tens of ms in a browser

type Tweak = { score: [number, number]; minute: number; reds: [number, number]; momentum: number };

// Python's round(v, n) on the exact binary value: toFixed rounds that value too (Math.round(v * 1e4) would
// round the product instead, and miss by a digit at the many near-halfway values futures produce).
const round = (v: number, n: number) => Number(v.toFixed(n));
const r4 = (v: number) => round(v, 4);

/** The engine's `to_futures` for a browser run: the raw kernel output, labelled with the match's moment. */
export function toFutures(raw: FuturesRaw, input: FuturesInput, now: FuturesNow, ms: number): Futures {
  const [nh, na, nn, median] = raw.next_goal;
  return {
    schema_version: "1.0",
    match_id: now.match_id,
    match_ms: now.match_ms,
    clock_ms: now.clock_ms,
    period: now.period,
    score: input.score as [number, number],
    backend: "rust",
    hypothetical: true,
    paths: raw.paths,
    home: r4(raw.home),
    draw: r4(raw.draw),
    away: r4(raw.away),
    scores: raw.scores.map(([home, away, p]) => ({ home, away, p: r4(p) })),
    other: r4(raw.other),
    comeback: raw.comeback
      ? {
          team: raw.comeback[0] === "home" ? now.team_ids[0] : now.team_ids[1],
          side: raw.comeback[0],
          avoid_defeat: r4(raw.comeback[1]),
          win: r4(raw.comeback[2]),
        }
      : null,
    next_goal: { home: r4(nh), away: r4(na), none: r4(nn), median_minute: median == null ? null : round(median, 1) },
    goals_left: [round(raw.goals_left[0], 3), round(raw.goals_left[1], 3)],
    ms: round(ms, 2),
    input,
  };
}

/** The engine's what-if overrides (`interpret/futures.py: futures_input`), applied to the input of now. */
export async function applyTweak(base: FuturesInput, now: FuturesNow, tweak: Tweak, paths: number): Promise<FuturesInput> {
  const clockNow = now.clock_ms / 60_000;
  let minute = base.minute;
  let left = base.minutes_left;
  if (Math.abs(tweak.minute - clockNow) >= 0.5) {
    const period = tweak.minute < 45 ? 1 : 2;
    left = await remainingMinutes(tweak.minute, period);
    minute = Math.round((period === 1 ? Math.min(tweak.minute, 45) : tweak.minute) * 1e4) / 1e4;
  }
  return { ...base, score: tweak.score, reds: tweak.reds, momentum: tweak.momentum, minute, minutes_left: left, paths };
}

function fromNow(now: FuturesNow): Tweak {
  const i = now.input;
  return {
    score: [i.score[0], i.score[1]],
    minute: Math.round(now.clock_ms / 60_000),
    reds: [i.reds[0], i.reds[1]],
    momentum: Math.round(i.momentum * 10) / 10,
  };
}

export function WhatIfSandbox({ matchId, meta, lang }: { matchId: string; meta: MatchMeta | null; lang: string }) {
  const s = whatIfStrings(lang);
  const [now, setNow] = useState<FuturesNow | null>(null);
  const [tweak, setTweak] = useState<Tweak | null>(null);
  const [result, setResult] = useState<{ futures: Futures; where: "browser" | "engine"; ms: number } | null>(null);
  const run = useRef(0);

  const open = async () => {
    const n = await api.futuresInput(matchId);
    setNow(n);
    setTweak(fromNow(n));
  };

  useEffect(() => {
    if (!tweak || !now) return;
    const id = ++run.current;
    const timer = setTimeout(async () => {
      try {
        const input = await applyTweak(now.input, now, tweak, PATHS);
        const { raw, ms } = await simulateFutures(input);
        if (id === run.current) setResult({ futures: toFutures(raw, input, now, ms), where: "browser", ms });
      } catch {
        // No kernel in this browser: the engine runs the same what-if.
        const futures = await api.futuresWhatIf(matchId, {
          home_goals: tweak.score[0],
          away_goals: tweak.score[1],
          minute: tweak.minute,
          home_reds: tweak.reds[0],
          away_reds: tweak.reds[1],
          momentum: tweak.momentum,
          paths: PATHS,
        });
        if (id === run.current) setResult({ futures, where: "engine", ms: futures.ms });
      }
    }, 100);
    return () => clearTimeout(timer);
  }, [tweak, now, matchId]);

  if (!meta) return null;
  if (!now || !tweak) {
    return (
      <button type="button" onClick={open} className="self-start rounded-md border border-surface-2 px-3 py-1.5 text-sm font-semibold hover:border-accent">
        {s.open}
      </button>
    );
  }

  const teams = [meta.home.short_name, meta.away.short_name];
  const codes = [meta.home.club_id, meta.away.club_id]; // steppers are narrow: club codes, full names on hover
  const set = (patch: Partial<Tweak>) => setTweak({ ...tweak, ...patch });
  const step = (key: "score" | "reds", side: 0 | 1, d: number, max: number) => {
    const v = [...tweak[key]] as [number, number];
    v[side] = Math.max(0, Math.min(max, v[side] + d));
    set({ [key]: v });
  };
  const momentumLabel =
    Math.abs(tweak.momentum) < 0.05 ? s.level : s.onTop(tweak.momentum > 0 ? teams[0] : teams[1]);

  return (
    <section aria-label={s.title} className="panel flex flex-col gap-3" data-testid="whatif">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="section-title">{s.title}</h2>
        <span className="text-sm text-muted">{s.hypothetical}</span>
      </div>
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        {(["score", "reds"] as const).map((key) => (
          <div key={key} className="flex flex-col gap-1">
            <span className="font-semibold">{key === "score" ? s.score : s.reds}</span>
            <div className="flex items-center gap-3">
              {([0, 1] as const).map((side) => (
                <span key={side} className="flex items-center gap-1">
                  <span className="w-9 font-semibold text-muted" title={teams[side]}>{codes[side]}</span>
                  <button type="button" aria-label={s.fewer(`${teams[side]} ${key === "score" ? s.score : s.reds}`)} onClick={() => step(key, side, -1, key === "score" ? 9 : 3)} className="size-7 rounded-md border border-surface-2 hover:border-accent">−</button>
                  <span className="w-5 text-center font-mono font-semibold" data-testid={`whatif-${key}-${side}`}>{tweak[key][side]}</span>
                  <button type="button" aria-label={s.more(`${teams[side]} ${key === "score" ? s.score : s.reds}`)} onClick={() => step(key, side, 1, key === "score" ? 9 : 3)} className="size-7 rounded-md border border-surface-2 hover:border-accent">+</button>
                </span>
              ))}
            </div>
          </div>
        ))}
        <label className="flex flex-col gap-1">
          <span className="font-semibold">
            {s.minute} <span className="font-mono font-normal text-muted">{tweak.minute}&apos;</span>
          </span>
          <input type="range" min={0} max={95} value={tweak.minute} onChange={(e) => set({ minute: Number(e.target.value) })} className="accent-[var(--accent)]" />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-semibold">
            {s.momentum} <span className="font-normal text-muted">{momentumLabel}</span>
          </span>
          <input type="range" min={-1} max={1} step={0.1} value={tweak.momentum} onChange={(e) => set({ momentum: Number(e.target.value) })} className="accent-[var(--accent)]" />
        </label>
      </div>
      <FuturesPanel futures={result?.futures} meta={meta} lang={lang} />
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted">
        <span data-testid="whatif-where">
          {result?.where === "browser"
            ? s.inBrowser(new Intl.NumberFormat(lang).format(result.futures.paths), result.ms.toFixed(0))
            : result
              ? s.fromEngine
              : ""}
        </span>
        <span className="flex gap-3">
          <button type="button" onClick={() => setTweak(fromNow(now))} className="font-semibold text-accent hover:underline">{s.reset}</button>
          <button type="button" onClick={() => { setNow(null); setTweak(null); setResult(null); }} className="font-semibold text-accent hover:underline">{s.back}</button>
        </span>
      </div>
    </section>
  );
}
