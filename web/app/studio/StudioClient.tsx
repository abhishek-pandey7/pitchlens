"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AgentTrace } from "@/components/AgentTrace";
import { AskPanel } from "@/components/AskPanel";
import { DirectorPanel } from "@/components/DirectorPanel";
import { ExportLinks } from "@/components/ExportLinks";
import { RecapPanel } from "@/components/RecapPanel";
import { InsightFeed } from "@/components/InsightFeed";
import { LatencyHud } from "@/components/LatencyHud";
import { MatchLauncher } from "@/components/MatchLauncher";
import { OverlayStage } from "@/components/overlay/OverlayStage";
import { ReplayControls } from "@/components/ReplayControls";
import { Scoreboard } from "@/components/Scoreboard";
import { useMatchStream } from "@/lib/useMatchStream";
import { useProfiles } from "@/lib/useProfiles";

/** /studio?match=…&profile=… — the producer's console: replay control, the overlay as a chosen fan
 *  sees it, the insight queue, latency against budget, and the live agent trace. */
export function StudioClient() {
  const params = useSearchParams();
  const router = useRouter();
  const matchId = params.get("match");
  const profileId = params.get("profile") ?? "p-analyst-en";
  const profiles = useProfiles();
  const view = useMatchStream(matchId, { profileId, frames: true });

  if (!matchId) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <Header />
        <p className="mb-6 text-sm text-muted">Pick or simulate a match to produce.</p>
        <MatchLauncher profiles={profiles} profileId={profileId} basePath="/studio" />
      </main>
    );
  }

  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 py-5">
      <Header>
        <label className="flex items-center gap-2 text-xs text-muted">
          Preview as
          <select
            aria-label="Preview profile"
            value={profileId}
            onChange={(e) => router.replace(`/studio?match=${encodeURIComponent(matchId)}&profile=${e.target.value}`)}
            className="rounded-md bg-surface-2 px-3 py-1.5 text-sm text-foreground"
          >
            {profiles.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.display_name ?? p.profile_id} · {p.persona} · {p.language}
              </option>
            ))}
          </select>
        </label>
      </Header>
      <ReplayControls matchId={matchId} />
      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,3fr)_minmax(340px,4fr)]">
        <section className="flex min-w-0 flex-col gap-4" aria-label="Programme">
          <Scoreboard meta={view.meta} state={view.state} status={view.status} lang="en" />
          <div className="overflow-hidden rounded-lg ring-1 ring-surface-2">
            <OverlayStage view={view} pitch />
          </div>
          <DirectorPanel matchId={matchId} profileId={profileId} />
          <ExportLinks matchId={matchId} profileId={profileId} lang={view.profile?.language ?? "en"} />
          <LatencyHud matchId={matchId} />
        </section>
        <section className="min-w-0" aria-label="Insight queue">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-widest text-muted">Insight queue</h2>
          <div className="max-h-[78vh] overflow-y-auto pe-1">
            <InsightFeed items={view.feed} lang={view.profile?.language ?? "en"} showEvidence />
          </div>
        </section>
        <section className="flex min-w-0 flex-col gap-4">
          <AskPanel matchId={matchId} profileId={profileId} lang={view.profile?.language ?? "en"} />
          <RecapPanel matchId={matchId} profileId={profileId} lang={view.profile?.language ?? "en"} />
          <div className="max-h-[70vh] overflow-y-auto pe-1">
            <AgentTrace matchId={matchId} />
          </div>
        </section>
      </div>
    </main>
  );
}

function Header({ children }: { children?: React.ReactNode }) {
  return (
    <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <Link href="/" className="text-sm font-semibold uppercase tracking-widest text-accent">
        PitchLens <span className="text-muted">· Studio</span>
      </Link>
      {children}
    </header>
  );
}
