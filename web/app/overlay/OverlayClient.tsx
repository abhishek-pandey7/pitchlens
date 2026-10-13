"use client";

import { useSearchParams } from "next/navigation";
import { MatchLauncher } from "@/components/MatchLauncher";
import { OverlayStage } from "@/components/overlay/OverlayStage";
import { useMatchStream } from "@/lib/useMatchStream";
import { useProfiles } from "@/lib/useProfiles";

export function OverlayClient() {
  const params = useSearchParams();
  const matchId = params.get("match");
  const profileId = params.get("profile") ?? "p-analyst-en";
  const pitch = params.get("pitch") === "1";
  const profiles = useProfiles();
  const view = useMatchStream(matchId, { profileId, frames: true });

  if (!matchId) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <p className="mb-6 text-sm text-muted">
          Choose a match for the broadcast overlay. Add <code>&amp;pitch=1</code> for a pitch backdrop in place of video.
        </p>
        <MatchLauncher profiles={profiles} profileId={profileId} basePath="/overlay" />
      </main>
    );
  }
  return (
    <main className="overlay-page broadcast mx-auto w-full max-w-[1920px]">
      <OverlayStage view={view} pitch={pitch} />
    </main>
  );
}
