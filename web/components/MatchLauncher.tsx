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
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end gap-3 rounded-xl bg-surface p-4">
        <label className="flex flex-col gap-1 text-xs text-muted">
          Watch as
          <select value={fan} onChange={(e) => setFan(e.target.value)} className="rounded-md bg-surface-2 px-3 py-2 text-sm text-foreground">
            {profiles.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.display_name ?? p.profile_id} · {p.persona} · {p.language}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted">
          New match storyline
          <select value={storyline} onChange={(e) => setStoryline(e.target.value)} className="rounded-md bg-surface-2 px-3 py-2 text-sm text-foreground">
            {storylines.map((s) => <option key={s}>{s}</option>)}
          </select>
        </label>
        <button type="button" onClick={generate} disabled={busy !== null} className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-background disabled:opacity-50">
          {busy === "generate" ? "Simulating…" : "Simulate match"}
        </button>
      </div>
      {error && <p className="rounded-md bg-danger/15 p-3 text-sm text-danger">{error}</p>}
      <ul className="grid gap-3 sm:grid-cols-2">
        {matches.map((m) => (
          <li key={m.match_id} className="flex flex-col gap-3 rounded-xl border border-surface-2 bg-surface p-4">
            <div>
              <p className="font-semibold">{m.home.name} v {m.away.name}</p>
              <p className="text-xs text-muted">
                {m.storyline ?? "organic"} · {m.source} · {m.has_tracking ? "tracking" : "events only"} · replay {m.replay.state}
              </p>
            </div>
            <div className="flex gap-2">
              <Link
                href={`${basePath}?match=${encodeURIComponent(m.match_id)}${fan ? `&profile=${fan}` : ""}`}
                className="rounded-md bg-surface-2 px-3 py-1.5 text-sm hover:bg-accent hover:text-background"
              >
                Open
              </Link>
              {[10, 60].map((speed) => (
                <button key={speed} type="button" disabled={busy !== null} onClick={() => start(m.match_id, speed)} className="rounded-md bg-surface-2 px-3 py-1.5 text-sm disabled:opacity-50">
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
