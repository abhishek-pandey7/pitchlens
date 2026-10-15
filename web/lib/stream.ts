// Folds the engine's SSE messages into a match view. Pure: the React hook in useMatchStream.ts drives it.
import type { EnrichedEvent, Futures, Insight, MatchMeta, Narration, OverlayCue, PhysicalAlert, Profile, TrackingFrame } from "./types";

export type TeamView = {
  club_id: string;
  score: number;
  possession: number;
  shots: number;
  shots_on_target: number;
  xg: number;
  passes: number;
  pass_accuracy: number | null;
  corners: number;
  fouls: number;
  yellow_cards: number;
  red_cards: number;
};

export type Dynamics = {
  match_ms: number;
  clock_ms: number;
  period: number;
  score: [number, number];
  wp: [number, number, number];
  xg: [number, number];
  momentum: number;
  possession_5m: number;
  field_tilt: number;
  ppda: [number, number];
  press_rate: [number, number];
  control: number | null;
  control_components: Record<string, number> | null;
  tempo: number;
  tempo_z: number | null;
  tempo_baseline: number | null;
  stoppage_share: number;
  futures?: Futures | null; // the latest futures run (engine interpret/futures.py), on state messages only
};

export type StateSnapshot = {
  match_id: string;
  period: number;
  match_ms: number;
  clock_ms: number;
  finished: boolean;
  possession_team: string | null;
  home: TeamView;
  away: TeamView;
  dynamics: Dynamics;
};

export type FeedNarration = { text: string; headline: string; body: string; lang: string; source: "template" | "llm" };

export type FeedItem = {
  insight: Insight;
  personal_salience: number;
  reasons: string[];
  spoiler: boolean;
  narration: FeedNarration;
};

export type SyncPayload = { meta: MatchMeta; profile: Profile | null; state: StateSnapshot; insights: FeedItem[] };

export type Status = "connecting" | "live" | "reconnecting" | "finished";

export type MatchView = {
  status: Status;
  meta: MatchMeta | null;
  profile: Profile | null;
  state: StateSnapshot | null;
  feed: FeedItem[]; // newest first
  cues: ActiveCue[]; // cues currently on air
  frame: TrackingFrame | null;
  lastEvent: EnrichedEvent | null;
  alerts: PhysicalAlert[]; // most recent first, bounded
  matchMs: number;
  lastEventId: number | null;
};

export const initialView: MatchView = {
  status: "connecting",
  meta: null,
  profile: null,
  state: null,
  feed: [],
  cues: [],
  frame: null,
  lastEvent: null,
  alerts: [],
  matchMs: 0,
  lastEventId: null,
};

export type StreamMessage = { event: string; data: unknown; id?: number | null; at?: number };

/** A cue on air, with the wall-clock time it arrived. */
export type ActiveCue = OverlayCue & { shown_at: number };

// Cue durations are in match time (right for a renderer synced to video). In an accelerated replay
// that can be a fraction of a second, so every cue also stays up for at least this long in real time.
export const MIN_ON_AIR_MS = 4000;

const MAX_FEED = 200;
const MAX_ALERTS = 20;

function activeCues(cues: ActiveCue[], matchMs: number, now: number): ActiveCue[] {
  return cues.filter((c) => c.start_ms + c.duration_ms > matchMs || now - c.shown_at < MIN_ON_AIR_MS);
}

export function reduce(view: MatchView, msg: StreamMessage): MatchView {
  const next = msg.id != null ? { ...view, lastEventId: msg.id } : view;
  const now = msg.at ?? 0;
  switch (msg.event) {
    case "sync": {
      const data = msg.data as SyncPayload;
      return {
        ...next,
        status: data.state.finished ? "finished" : "live",
        meta: data.meta,
        profile: data.profile,
        state: data.state,
        feed: [...data.insights].reverse().slice(0, MAX_FEED),
        cues: [],
        matchMs: data.state.match_ms,
      };
    }
    case "reset":
      return { ...next, status: "live", state: null, feed: [], cues: [], frame: null, lastEvent: null, alerts: [], matchMs: 0 };
    case "state": {
      const state = msg.data as StateSnapshot;
      return {
        ...next,
        state,
        matchMs: Math.max(next.matchMs, state.match_ms),
        status: state.finished ? "finished" : "live",
        cues: activeCues(next.cues, state.match_ms, now),
      };
    }
    case "event": {
      const ev = msg.data as EnrichedEvent;
      return { ...next, lastEvent: ev, matchMs: Math.max(next.matchMs, ev.event.match_ms) };
    }
    case "insight": {
      const item = msg.data as FeedItem;
      const feed = [item, ...next.feed.filter((f) => f.insight.insight_id !== item.insight.insight_id)];
      return { ...next, feed: feed.slice(0, MAX_FEED) };
    }
    case "narration": {
      const n = msg.data as Narration;
      const upgrade = (text: string) => ({ text, headline: text, body: "", lang: n.lang, source: "llm" as const });
      return {
        ...next,
        feed: next.feed.map((f) => (f.insight.insight_id === n.insight_id ? { ...f, narration: upgrade(n.text) } : f)),
        cues: next.cues.map((c) =>
          c.insight_id === n.insight_id ? { ...c, text: { ...c.text, headline: n.text, body: "", source: "llm" } } : c,
        ),
      };
    }
    case "cue": {
      const cue: ActiveCue = { ...(msg.data as OverlayCue), shown_at: now };
      const others = next.cues.filter((c) => c.region !== cue.region || c.region === "player_anchor");
      return { ...next, cues: activeCues([...others, cue], Math.max(next.matchMs, cue.start_ms), now) };
    }
    case "frame": {
      const frame = msg.data as TrackingFrame;
      return {
        ...next,
        frame,
        matchMs: Math.max(next.matchMs, frame.match_ms),
        cues: activeCues(next.cues, frame.match_ms, now),
      };
    }
    case "tick":
      return { ...next, cues: activeCues(next.cues, next.matchMs, now) };
    case "alert":
      return { ...next, alerts: [msg.data as PhysicalAlert, ...next.alerts].slice(0, MAX_ALERTS) };
    case "control": {
      const action = (msg.data as { action?: string }).action;
      return action === "finished" ? { ...next, status: "finished" } : next;
    }
    default:
      return next;
  }
}

/** Short display names for players, keyed by id. */
export function playerNames(meta: MatchMeta | null): Record<string, string> {
  const out: Record<string, string> = {};
  if (!meta) return out;
  for (const lineup of Object.values(meta.lineups)) {
    for (const p of lineup) out[p.player_id] = p.name.split(" ").slice(-1)[0];
  }
  return out;
}
