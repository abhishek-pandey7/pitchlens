"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { API_BASE, api, type MatchSummary } from "@/lib/api";
import type { Profile } from "@/lib/types";

type Props = { profiles: Profile[]; profileId: string | null; basePath: string };

export function MatchLauncher({ profiles, profileId, basePath }: Props) {
  const [matches, setMatches] = useState<MatchSummary[]>([]);
  const [storylines, setStorylines] = useState<string[]>([]);
  const [storyline, setStoryline] = useState("comeback");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chosenFan, setFan] = useState(profileId ?? "");
  const fan = chosenFan || profiles[0]?.profile_id || "";
  const unreachable = (e: unknown) =>
    setError(`Engine not reachable at ${API_BASE} (${(e as Error).message})`);

  const refresh = useCallback(async () => {
    try {
      setMatches(await api.matches());
      setError(null);
    } catch (e) {
      unreachable(e);
    }
     
  }, []);

  useEffect(() => {
    api.matches().then(setMatches).catch(unreachable);
    api.storylines().then(setStorylines).catch(() => {});
     
  }, []);

  const generate = async () => {
    setBusy("generate");
    try {
      await api.createMatch({ seed: Math.floor(Math.random() * 10_000), storyline, tracking: true });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const start = async (id: string, speed: number) => {
    setBusy(id);
    try {
      await api.replay(id, { speed });
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="flex flex-col gap-8">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm font-semibold">
          Watch as
          <select value={fan} onChange={(e) => setFan(e.target.value)} className="rounded-md border border-surface-2 bg-background px-3 py-2 font-normal">
            {profiles.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {fanLabel(p)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-sm font-semibold">
          New match storyline
          <select value={storyline} onChange={(e) => setStoryline(e.target.value)} className="rounded-md border border-surface-2 bg-background px-3 py-2 font-normal">
            {storylines.map((s) => <option key={s} value={s}>{storylineName(s)}</option>)}
          </select>
        </label>
        <button type="button" onClick={generate} disabled={busy !== null} className="rounded-md bg-accent px-4 py-2 font-semibold text-white disabled:opacity-50">
          {busy === "generate" ? "Simulating…" : "Simulate match"}
        </button>
      </div>
      {error && <p className="rounded-md bg-danger/10 p-3 text-sm text-danger">{error}</p>}
      <ul aria-label="Matches">
        {matches.map((m) => (
          <li key={m.match_id} className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-t border-surface-2 py-3">
            <div className="min-w-0">
              <p className="font-semibold">
                {m.home.name} <span className="font-normal text-muted">v</span> {m.away.name}
              </p>
              <p className="text-sm text-muted">
                {storylineName(m.storyline)}
                {m.replay.state === "playing" ? `, replaying at ${m.replay.speed}×` : ""}
                {m.has_tracking ? "" : ", events only"}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Link
                href={`${basePath}?match=${encodeURIComponent(m.match_id)}${fan ? `&profile=${fan}` : ""}`}
                className="rounded-md bg-accent px-3 py-1.5 text-sm font-semibold text-white hover:bg-accent/90"
              >
                Open
              </Link>
              {[10, 60].map((speed) => (
                <button
                  key={speed}
                  type="button"
                  disabled={busy !== null}
                  onClick={() => start(m.match_id, speed)}
                  aria-label={`Replay at ${speed}×`}
                  className="rounded-md border border-surface-2 px-3 py-1.5 text-sm hover:border-accent disabled:opacity-50"
                >
                  ▶ {speed}×
                </button>
              ))}
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

const PERSONAS: Record<string, string> = { casual: "Casual fan", analyst: "Analyst", tactician: "Tactician" };
const LANGUAGES: Record<string, string> = { en: "English", es: "Spanish", fr: "French", pt: "Portuguese", ar: "Arabic", hi: "Hindi" };

export function fanLabel(p: Profile) {
  const name = p.display_name ?? p.profile_id;
  return `${name}: ${PERSONAS[p.persona] ?? p.persona}, ${LANGUAGES[p.language] ?? p.language}`;
}

// Storyline keys from the simulator, as a fan would name the match.
export function storylineName(key: string | null) {
  if (!key || key === "organic") return "Unscripted match";
  const name = key.replaceAll("_", " ");
  return name[0].toUpperCase() + name.slice(1);
}
