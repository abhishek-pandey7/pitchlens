"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { AskPanel } from "@/components/AskPanel";
import { FuturesPanel } from "@/components/FuturesPanel";
import { WhatIfSandbox } from "@/components/WhatIfSandbox";
import { RecapPanel } from "@/components/RecapPanel";
import { InsightFeed } from "@/components/InsightFeed";
import { MatchLauncher, fanLabel } from "@/components/MatchLauncher";
import { MatchScrubber } from "@/components/MatchScrubber";
import { PlayerFocusPanel } from "@/components/PlayerFocusPanel";
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
  const profileFocus = profile?.player_focus_mode.enabled ? profile.player_focus_mode.player_id : null;
  // The fan's own pick (undefined until they choose) overrides the profile's focus player.
  const [picked, setPicked] = useState<string | null | undefined>(undefined);
  const focus = picked === undefined ? profileFocus : picked;

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
              {fanLabel(p)}
            </option>
          ))}
        </select>
      </Header>
      {view.status === "reconnecting" && <p className="mb-3 text-xs text-warning">{t(lang, "reconnecting")}</p>}
      <div className="grid gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(320px,2fr)]">
        <section className="flex flex-col gap-4">
          <Scoreboard meta={view.meta} state={view.state} status={view.status} lang={lang} />
          <MatchScrubber matchId={matchId} meta={view.meta} liveFrame={view.frame} status={view.status} focusPlayerId={focus} lang={lang} />
          {view.state && (
            <dl className="text-sm">
              <Stat label={t(lang, "possession")} home={`${Math.round(view.state.home.possession * 100)}%`} away={`${Math.round(view.state.away.possession * 100)}%`} />
              <Stat label={t(lang, "xg")} home={view.state.home.xg.toFixed(2)} away={view.state.away.xg.toFixed(2)} />
              <Stat label={t(lang, "shots")} home={String(view.state.home.shots)} away={String(view.state.away.shots)} />
            </dl>
          )}
          <FuturesPanel futures={view.state?.dynamics.futures} meta={view.meta} lang={lang} />
          <WhatIfSandbox matchId={matchId} meta={view.meta} lang={lang} />
          <PlayerFocusPanel
            matchId={matchId}
            meta={view.meta}
            playerId={focus}
            onPick={setPicked}
            profileId={profileId}
            lang={lang}
            matchMs={view.matchMs}
          />
        </section>
        <div className="flex flex-col gap-4">
          <AskPanel matchId={matchId} profileId={profileId} lang={lang} />
          <RecapPanel matchId={matchId} profileId={profileId} lang={lang} />
          <section aria-label={t(lang, "feed")}>
            <h2 className="mb-3 section-title">{t(lang, "feed")}</h2>
            <div className="max-h-[60vh] overflow-y-auto pe-1">
              <InsightFeed items={view.feed} lang={lang} showEvidence={persona === "analyst" || persona === "tactician"} />
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}

function Header({ children }: { children?: React.ReactNode }) {
  return (
    <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <Link href="/" className="display text-xl text-foreground">
        PitchLens <span className="ms-1 text-muted">Watch</span>
      </Link>
      {children}
    </header>
  );
}

function Stat({ label, home, away }: { label: string; home: string; away: string }) {
  return (
    <div className="grid grid-cols-[1fr_auto_1fr] items-baseline border-b border-surface-2 py-1.5 last:border-b-0">
      <dd className="font-mono text-base font-semibold">{home}</dd>
      <dt className="text-muted">{label}</dt>
      <dd className="text-end font-mono text-base font-semibold">{away}</dd>
    </div>
  );
}
