// REST helpers for the PitchLens engine API (see docs/api.md).
import type { MatchMeta, Profile } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

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

export type AskStep = {
  agent: string;
  task?: string;
  lang?: string;
  round?: number;
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
  streamUrl: (id: string, opts: { profileId?: string | null; frames?: boolean; events?: boolean }) => {
    const params = new URLSearchParams();
    if (opts.profileId) params.set("profile_id", opts.profileId);
    if (opts.frames) params.set("frames", "true");
    if (opts.events === false) params.set("events", "false");
    return `${API_BASE}/matches/${id}/stream?${params}`;
  },
};
