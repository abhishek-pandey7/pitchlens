"use client";

// Home page: a goal from a sample match, replayed in 3D on a loop from its tracking frames.
import dynamic from "next/dynamic";
import { useEffect, useState, useSyncExternalStore } from "react";
import { api, type GoalClip } from "@/lib/api";
import type { MatchMeta } from "@/lib/types";

const Pitch3D = dynamic(() => import("@/components/Pitch3D").then((m) => m.Pitch3D), { ssr: false });

const FRAME_MS = 200; // 5 Hz, real time
const HOLD_MS = 1800; // pause on the last frame before the loop restarts

type Clip = GoalClip & { meta: MatchMeta };

async function loadClip(): Promise<Clip> {
  const matches = await api.matches();
  const pick = matches.find((m) => m.source === "stored" && m.storyline === "comeback") ?? matches.find((m) => m.source === "stored");
  if (!pick) throw new Error("no sample matches");
  const [match, clip] = await Promise.all([api.match(pick.match_id), api.goalClip(pick.match_id, -1)]);
  return { ...clip, meta: match.meta };
}

const reducedMotionQuery = () => window.matchMedia("(prefers-reduced-motion: reduce)");
const subscribeMotion = (cb: () => void) => {
  const q = reducedMotionQuery();
  q.addEventListener("change", cb);
  return () => q.removeEventListener("change", cb);
};

export function GoalHero() {
  const [clip, setClip] = useState<Clip | null>(null);
  const [failed, setFailed] = useState(false);
  const [i, setI] = useState(0);
  const reduced = useSyncExternalStore(subscribeMotion, () => reducedMotionQuery().matches, () => false);

  useEffect(() => {
    let cancelled = false;
    let tries = 0;
    // A fresh engine rebuilds the samples' tracking in the background: ask again for a short while.
    const attempt = () =>
      loadClip()
        .then((c) => !cancelled && setClip(c))
        .catch(() => {
          if (cancelled) return;
          if (++tries < 6) setTimeout(attempt, 5000);
          else setFailed(true);
        });
    attempt();
    return () => {
      cancelled = true;
    };
  }, []);

  const frames = clip?.frames ?? [];
  const goalIndex = clip ? Math.max(0, frames.findIndex((f) => f.match_ms >= clip.moment.match_ms)) : 0;

  useEffect(() => {
    if (!frames.length || reduced) return;
    const last = frames.length - 1;
    const timer = setTimeout(() => setI((n) => (n >= last ? 0 : n + 1)), i >= last ? HOLD_MS : FRAME_MS);
    return () => clearTimeout(timer);
  }, [i, frames.length, reduced]);

  if (failed) return null; // the page still works without the engine
  const frame = frames.length ? frames[reduced ? goalIndex : Math.min(i, frames.length - 1)] : null;
  return (
    <figure className="flex flex-col gap-2">
      <Pitch3D frame={frame} meta={clip?.meta ?? null} camera="follow" label={clip ? caption(clip) : "A goal replayed in 3D"} />
      <figcaption className="text-sm text-muted">{clip ? caption(clip) : "Loading a goal from a sample match…"}</figcaption>
    </figure>
  );
}

function caption(clip: Clip): string {
  const team = clip.meta.home.club_id === clip.moment.team_id ? clip.meta.home : clip.meta.away;
  const scorer = (clip.meta.lineups[team.club_id] ?? []).find((p) => p.player_id === clip.moment.player_id);
  const who = scorer ? `${scorer.name} scores for ${team.name}` : `${team.name} score`;
  return `${who}, replayed from the tracking data of a synthetic sample match.`;
}
