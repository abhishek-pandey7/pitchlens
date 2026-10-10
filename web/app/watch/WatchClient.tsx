"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { InsightFeed } from "@/components/InsightFeed";
import { MatchLauncher } from "@/components/MatchLauncher";
import { PitchCanvas } from "@/components/PitchCanvas";
import { Scoreboard } from "@/components/Scoreboard";
import { api } from "@/lib/api";
import { dir, t } from "@/lib/i18n";
import { useMatchStream } from "@/lib/useMatchStream";
import type { Profile } from "@/lib/types";

export function WatchClient() {
  const params = useSearchParams();
  const router = useRouter();
  const matchId = params.get("match");
  const profileId = params.get("profile");
  const [profiles, setProfiles] = useState<Profile[]>([]);

  useEffect(() => {
    api.profiles().then(setProfiles).catch(() => setProfiles([]));
  }, []);

  const profile = useMemo(() => profiles.find((p) => p.profile_id === profileId) ?? null, [profiles, profileId]);
  const view = useMatchStream(matchId, { profileId, frames: true });
  const lang = view.profile?.language ?? profile?.language ?? "en";
  const persona = view.profile?.persona ?? profile?.persona ?? "analyst";
  const focus = profile?.player_focus_mode.enabled ? profile.player_focus_mode.player_id : null;

  if (!matchId) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <Header />
        <MatchLauncher profiles={profiles} profileId={profileId} basePath="/watch" />
      </main>
    );
  }

  return (
    <main dir={dir(lang)} lang={lang} className="mx-auto w-full max-w-6xl px-4 py-6">
      <Header>
        <select
          aria-label="Fan profile"
          value={profileId ?? ""}
          onChange={(e) => router.replace(`/watch?match=${encodeURIComponent(matchId)}&profile=${e.target.value}`)}
          className="rounded-md bg-surface-2 px-3 py-1.5 text-sm"
        >
          {profiles.map((p) => (
            <option key={p.profile_id} value={p.profile_id}>
              {p.display_name ?? p.profile_id} · {p.persona} · {p.language}
            </option>
          ))}
        </select>
      </Header>
      {view.status === "reconnecting" && <p className="mb-3 text-xs text-warning">{t(lang, "reconnecting")}</p>}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
        <section className="flex flex-col gap-4">
          <Scoreboard meta={view.meta} state={view.state} status={view.status} lang={lang} />
          <PitchCanvas frame={view.frame} meta={view.meta} focusPlayerId={focus} />
          {view.state && (
            <dl className="grid grid-cols-3 gap-3 text-center text-sm">
              <Stat label={t(lang, "possession")} home={`${Math.round(view.state.home.possession * 100)}%`} away={`${Math.round(view.state.away.possession * 100)}%`} />
              <Stat label={t(lang, "xg")} home={view.state.home.xg.toFixed(2)} away={view.state.away.xg.toFixed(2)} />
              <Stat label={t(lang, "shots")} home={String(view.state.home.shots)} away={String(view.state.away.shots)} />
            </dl>
          )}
        </section>
        <section aria-label={t(lang, "feed")}>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-widest text-muted">{t(lang, "feed")}</h2>
          <div className="max-h-[75vh] overflow-y-auto pe-1">
            <InsightFeed items={view.feed} lang={lang} showEvidence={persona === "analyst" || persona === "tactician"} />
          </div>
        </section>
      </div>
    </main>
  );
}

function Header({ children }: { children?: React.ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <Link href="/" className="text-sm font-semibold uppercase tracking-widest text-accent">
        PitchLens <span className="text-muted">· Watch</span>
      </Link>
      {children}
    </header>
  );
}

function Stat({ label, home, away }: { label: string; home: string; away: string }) {
  return (
    <div className="rounded-lg bg-surface p-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className="font-mono tabular-nums">
        {home} <span className="text-muted">·</span> {away}
      </dd>
    </div>
  );
}
