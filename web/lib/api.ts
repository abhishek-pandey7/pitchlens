// REST helpers for the PitchLens engine API (see docs/api.md).
import type { Futures, MatchMeta, Profile, TrackingFrame } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";
// Live streams can come from the Rust broadcast edge (edge/), which serves the same /stream URLs.
// Unset or empty: streams come from the engine, like everything else.
export const STREAM_BASE = process.env.NEXT_PUBLIC_STREAM_BASE || API_BASE;

export type MatchSummary = {
  match_id: string;
  home: { club_id: string; name: string };
  away: { club_id: string; name: string };
  storyline: string | null;
  seed: number | null;
  source: "stored" | "generated" | "external";
  events: number;
  has_tracking: boolean;
  final_score: string | null;
  replay: { state: string; speed?: number; match_ms?: number };
};

export type GoalClip = {
  match_id: string;
  moment: { type: "goal"; match_ms: number; team_id: string; player_id: string | null; goals: number };
  frames: TrackingFrame[];
};

/** A match packed for the tracking build, for the Rust kernel in the browser (lib/kernel.ts). */
export type TrackingInput = { match_id: string; player_ids: string[] } & Record<string, unknown>;

export type AskStep = {
  agent: string;
  task?: string;
  lang?: string;
  round?: number;
  stage?: "input" | "output";
  allowed?: boolean;
  categories?: string[];
  verdict?: string;
  by?: string;
  problems?: string[];
  grounded?: boolean;
  unchanged?: boolean;
  ms?: number;
  tool_calls?: { tool: string; args: Record<string, unknown>; ms: number; error: string | null }[];
  answer?: string;
};

export type AskResponse = {
  question: string;
  answer: string;
  lang: string;
  source: "companion" | "analyst" | "fallback" | "guard";
  verdict: string;
  revisions: number;
  ms: number;
  tokens: number;
  steps: AskStep[];
};

export type RecapResponse = {
  stage: "ht" | "ft";
  minute: string;
  headline: string;
  body: string;
  player_line: string;
  player_of_match: { player: string; team: string; points: number; stats: Record<string, number> } | null;
  lang: string;
  source: "narrator" | "editor" | "facts";
  verdict: string;
  revisions: number;
  ms: number;
  tokens: number;
  steps: AskStep[];
  question: string;
  answer: string;
  cached: boolean;
};

export type AgentRunTrace = Omit<AskResponse, "source"> & {
  kind: string;
  at: number;
  profile_id: string;
  source: string;
};

export type DirectorMode = "off" | "auto" | "approve";

export type PendingCue = {
  cue_id: string;
  insight_id: string | null;
  template: string;
  region: string;
  priority: number;
  headline: string;
  waiting_s: number;
  held: boolean;
  proposal: "air" | "hold" | "drop" | null;
  reason: string;
};

export type ProgrammeLogEntry = {
  at: number;
  minute: string;
  cue_id: string;
  headline: string;
  action: "air" | "hold" | "drop";
  by: "director" | "producer" | "rules";
  reason: string;
  ok: boolean;
  result: string;
  detail: string;
};

export type ProgrammeState = {
  match_id: string;
  profile_id: string | null;
  mode: DirectorMode;
  pending: PendingCue[];
  on_air?: { busy_regions_s: Record<string, number>; cues_last_minute: number; max_per_minute: number };
  log: ProgrammeLogEntry[];
};

/** One futures run of a live session (engine MatchState.set_futures): result split, comeback, top score. */
export type FuturesPoint = {
  match_ms: number;
  clock_ms: number;
  period: number;
  score: [number, number];
  result: [number, number, number]; // home, draw, away
  comeback: ["home" | "away", number, number] | null; // side behind, P(avoid defeat), P(win)
  top_score: [number, number] | null;
};

/** One row of the match's time series (every 30 s of match time; see engine MatchState.readings). */
export type SeriesRow = {
  match_ms: number;
  clock_ms: number;
  period: number;
  score: [number, number];
  wp: [number, number, number]; // home, draw, away
  xg: [number, number];
  momentum: number; // home minus away, -1..1
  possession_5m: number; // home share, last 5 minutes
  field_tilt: number; // home share of final-third touches, last 10 minutes
  ppda: [number, number];
  press_rate: [number, number];
  control: number | null; // 0 chaos .. 100 control
  tempo: number;
};

export type FocusMoment = { insight_id: string; minute: string; type: string; text: string };

/** Player-focus card (engine personalize/player_focus.py). */
export type FocusCard = {
  player_id: string;
  name: string;
  short_name: string;
  shirt: number | null;
  position: string;
  team: { club_id: string; name: string };
  on_pitch: boolean;
  minutes: number;
  involvement: {
    touches: number;
    passes: number;
    pass_accuracy: number | null;
    mean_pass_difficulty: number | null;
    hardest_completed_pass: number | null;
    key_passes: number;
    progressive_passes: number;
    lines_broken: number;
  };
  attack: { goals: number; assists: number; shots: number; shots_on_target: number; xg: number; max_shot_speed_kmh: number | null };
  defence: { tackles: number; interceptions: number; recoveries: number; pressures: number; blocks: number };
  physical: { distance_km: number; top_speed_kmh: number; speed_kmh: number; sprints: number } | null;
  zones: { cols: number; rows: number; counts: number[] };
  timeline: FocusMoment[];
  latest: FocusMoment | null;
  match_minute: string;
};

export type LatencyStage = { count: number; p50_ms: number; p95_ms: number; max_ms: number };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${init?.method ?? "GET"} ${path} failed: ${res.status} ${await res.text()}`);
  return (await res.json()) as T;
}

export const api = {
  matches: () => call<MatchSummary[]>("/matches"),
  storylines: () => call<string[]>("/matches/storylines"),
  match: (id: string) => call<MatchSummary & { meta: MatchMeta }>(`/matches/${id}`),
  goalClip: (id: string, index = 0) => call<GoalClip>(`/matches/${id}/clips/goal?index=${index}`),
  trackingInput: (id: string) => call<TrackingInput>(`/matches/${id}/tracking-input`),
  createMatch: (body: { seed: number; storyline?: string; home?: string; away?: string; tracking?: boolean }) =>
    call<MatchSummary>("/matches", { method: "POST", body: JSON.stringify(body) }),
  replay: (id: string, body: { speed: number; from_ms?: number; frames?: boolean }) =>
    call<MatchSummary["replay"]>(`/matches/${id}/replay`, { method: "POST", body: JSON.stringify(body) }),
  control: (id: string, body: { action: "pause" | "resume" | "seek" | "speed"; to_ms?: number; speed?: number }) =>
    call<MatchSummary["replay"]>(`/matches/${id}/control`, { method: "POST", body: JSON.stringify(body) }),
  profiles: () => call<Profile[]>("/profiles"),
  agentTrace: (id: string) => call<{ match_id: string; runs: AgentRunTrace[] }>(`/matches/${id}/agents/trace`),
  latency: (id: string) =>
    call<{ stages: Record<string, LatencyStage>; subscribers: number }>(`/matches/${id}/latency`),
  ask: (id: string, body: { question: string; profile_id: string }) =>
    call<AskResponse>(`/matches/${id}/ask`, { method: "POST", body: JSON.stringify(body) }),
  recap: (id: string, body: { stage: "ht" | "ft"; profile_id: string }) =>
    call<RecapResponse>(`/matches/${id}/recap`, { method: "POST", body: JSON.stringify(body) }),
  director: (id: string) => call<ProgrammeState>(`/matches/${id}/director`),
  setDirector: (id: string, body: { mode: DirectorMode; profile_id: string }) =>
    call<ProgrammeState>(`/matches/${id}/director`, { method: "PUT", body: JSON.stringify(body) }),
  produce: (id: string, cueId: string, action: "air" | "hold" | "drop" | "approve") =>
    call<ProgrammeState & { outcome: { ok: boolean; result: string; detail: string } }>(
      `/matches/${id}/director/cues/${encodeURIComponent(cueId)}`,
      { method: "POST", body: JSON.stringify({ action }) },
    ),
  series: (id: string) => call<SeriesRow[]>(`/matches/${id}/series`),
  futures: (id: string, paths?: number) => call<Futures>(`/matches/${id}/futures${paths ? `?paths=${paths}` : ""}`),
  futuresHistory: (id: string) => call<FuturesPoint[]>(`/matches/${id}/futures/history`),
  focus: (id: string, playerId: string, profileId?: string | null) =>
    call<FocusCard>(
      `/matches/${id}/players/${encodeURIComponent(playerId)}/focus${profileId ? `?profile_id=${encodeURIComponent(profileId)}` : ""}`,
    ),
  streamUrl: (id: string, opts: { profileId?: string | null; frames?: boolean; events?: boolean }) => {
    const params = new URLSearchParams();
    if (opts.profileId) params.set("profile_id", opts.profileId);
    if (opts.frames) params.set("frames", "true");
    if (opts.events === false) params.set("events", "false");
    return `${STREAM_BASE}/matches/${id}/stream?${params}`;
  },
};
