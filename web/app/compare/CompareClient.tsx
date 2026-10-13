"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { InsightFeed } from "@/components/InsightFeed";
import { MatchLauncher, fanLabel } from "@/components/MatchLauncher";
import { OverlayStage } from "@/components/overlay/OverlayStage";
import { Scoreboard } from "@/components/Scoreboard";
import { dir } from "@/lib/i18n";
import { useMatchStream } from "@/lib/useMatchStream";
import { useProfiles } from "@/lib/useProfiles";
import type { Profile } from "@/lib/types";

const DEFAULT_A = "p-casual-es";
const DEFAULT_B = "p-analyst-en";

export function CompareClient() {
  const params = useSearchParams();
  const router = useRouter();
  const matchId = params.get("match");
  const a = params.get("a") ?? DEFAULT_A;
  const b = params.get("b") ?? DEFAULT_B;
  const profiles = useProfiles();
  const left = useMatchStream(matchId, { profileId: a });
  const right = useMatchStream(matchId, { profileId: b });

  if (!matchId) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <Header />
        <p className="mb-6 text-sm text-muted">Pick a match to watch through the eyes of two fans at once.</p>
        <MatchLauncher profiles={profiles} profileId={a} basePath="/compare" />
      </main>
    );
  }

  const choose = (side: "a" | "b", value: string) => {
    const next = new URLSearchParams(params);
    next.set(side, value);
    router.replace(`/compare?${next}`);
  };

  return (
    <main className="mx-auto w-full max-w-7xl px-4 py-6">
      <Header />
      <Scoreboard meta={left.meta} state={left.state} status={left.status} lang="en" />
      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        {[
          { side: "a" as const, id: a, view: left },
          { side: "b" as const, id: b, view: right },
        ].map(({ side, id, view }) => (
          <FanColumn key={side} profiles={profiles} profileId={id} view={view} onChoose={(v) => choose(side, v)} />
        ))}
      </div>
    </main>
  );
}

function FanColumn({
  profiles,
  profileId,
  view,
  onChoose,
}: {
  profiles: Profile[];
  profileId: string;
  view: ReturnType<typeof useMatchStream>;
  onChoose: (profileId: string) => void;
}) {
  const profile = view.profile ?? profiles.find((p) => p.profile_id === profileId) ?? null;
  const lang = profile?.language ?? "en";
  return (
    <section className="flex flex-col gap-4" data-testid={`fan-${profileId}`} aria-label={profile?.display_name ?? profileId}>
      <select
        aria-label="Fan profile"
        value={profileId}
        onChange={(e) => onChoose(e.target.value)}
        className="rounded-md bg-surface-2 px-3 py-2 text-sm"
      >
        {profiles.map((p) => (
          <option key={p.profile_id} value={p.profile_id}>
            {fanLabel(p)}
          </option>
        ))}
      </select>
      <div className="overflow-hidden rounded-xl border border-surface-2 bg-[repeating-conic-gradient(#121826_0_25%,#0e131d_0_50%)] bg-[length:24px_24px]">
        <OverlayStage view={view} pitch={false} />
      </div>
      <div dir={dir(lang)} lang={lang} className="max-h-[55vh] overflow-y-auto pe-1">
        <InsightFeed items={view.feed} lang={lang} showEvidence={profile?.persona === "analyst" || profile?.persona === "tactician"} />
      </div>
    </section>
  );
}

function Header() {
  return (
    <header className="mb-6">
      <Link href="/" className="display text-xl text-foreground">
        PitchLens <span className="ms-1 text-muted">Same match, two fans</span>
      </Link>
    </header>
  );
}
