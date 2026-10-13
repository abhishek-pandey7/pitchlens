"use client";

// A 16:9 broadcast stage: score bug plus the fan's live cues, laid out by region. With `pitch`, a 2D
// pitch stands in for the video feed and player tags follow the players; without it the background
// is transparent for compositing over real video (OBS browser source or a rendering partner).
import { PitchCanvas } from "@/components/PitchCanvas";
import { clockText, minuteLabel } from "@/lib/clock";
import { dir } from "@/lib/i18n";
import type { MatchView } from "@/lib/stream";
import type { OverlayCue } from "@/lib/types";
import { ScaledStage } from "./ScaledStage";
import { ChaosMeter, LowerThird, MomentumBar, PassCard, PlayerTag, ShotSpeed, WinProbSwing, cueAccent } from "./cues";

const LENGTH = 105;
const WIDTH = 68;

function CueView({ cue, view }: { cue: OverlayCue; view: MatchView }) {
  switch (cue.template) {
    case "pass_card":
      return <PassCard cue={cue} />;
    case "shot_speed":
      return <ShotSpeed cue={cue} />;
    case "wp_swing":
      return <WinProbSwing cue={cue} />;
    case "chaos_meter":
      return <ChaosMeter cue={cue} />;
    case "momentum_bar":
      return <MomentumBar cue={cue} meta={view.meta} />;
    case "player_tag":
      return <PlayerTag cue={cue} meta={view.meta} />;
    default:
      return <LowerThird cue={cue} accent={cueAccent(view.meta, cue)} />;
  }
}

function ScoreBug({ view }: { view: MatchView }) {
  const { meta, state } = view;
  if (!meta) return null;
  const clock = state ? `${minuteLabel(state.clock_ms, state.period)}` : "";
  return (
    <div className="flex items-stretch overflow-hidden rounded-lg bg-[#0b0f17]/95 text-xl font-bold shadow-2xl" data-testid="score-bug">
      <span className="flex items-center gap-2 px-4 py-2">
        <span className="h-6 w-1.5 rounded" style={{ background: meta.home.colors[0] }} />
        {meta.home.club_id}
      </span>
      <span className="flex items-center bg-white/10 px-4 font-mono tabular-nums">
        {state?.home.score ?? 0}–{state?.away.score ?? 0}
      </span>
      <span className="flex items-center gap-2 px-4">
        {meta.away.club_id}
        <span className="h-6 w-1.5 rounded" style={{ background: meta.away.colors[0] }} />
      </span>
      <span className="flex items-center bg-accent px-3 font-mono text-base text-background" title={state ? clockText(state.clock_ms) : ""}>
        {view.status === "finished" ? "FT" : clock}
      </span>
    </div>
  );
}

export function OverlayStage({ view, pitch }: { view: MatchView; pitch: boolean }) {
  const lang = view.profile?.language ?? "en";
  const region = (r: OverlayCue["region"]) => view.cues.filter((c) => c.region === r);
  const tags = region("player_anchor");
  const position = (cue: OverlayCue) => view.frame?.players.find((p) => p.player_id === cue.anchor?.player_id);

  return (
    <ScaledStage>
      <div dir={dir(lang)} lang={lang} className="overlay-root broadcast relative h-full w-full text-foreground">
      {pitch && (
        <div className="absolute left-1/2 top-1/2 w-[78%] -translate-x-1/2 -translate-y-1/2">
          <div className="relative">
            <PitchCanvas frame={view.frame} meta={view.meta} />
            {tags.map((cue) => {
              const p = position(cue);
              if (!p) return null;
              return (
                <div
                  key={cue.cue_id}
                  className="absolute -translate-x-1/2 -translate-y-[160%] transition-all duration-200"
                  style={{ left: `${(p.x / LENGTH) * 100}%`, top: `${(p.y / WIDTH) * 100}%` }}
                >
                  <PlayerTag cue={cue} meta={view.meta} />
                </div>
              );
            })}
          </div>
        </div>
      )}
      <div className="absolute inset-[3%] flex flex-col justify-between">
        <div className="flex items-start justify-between gap-6">
          <div className="flex flex-col gap-3">
            <ScoreBug view={view} />
            {region("top_left").slice(-1).map((c) => <CueView key={c.cue_id} cue={c} view={view} />)}
          </div>
          <div className="flex-1">{region("full_bar").slice(-1).map((c) => <CueView key={c.cue_id} cue={c} view={view} />)}</div>
          <div className="flex flex-col items-end gap-3">
            {region("top_right").slice(-1).map((c) => <CueView key={c.cue_id} cue={c} view={view} />)}
          </div>
        </div>
        <div className="flex items-end justify-between gap-6">
          <div data-testid="lower-third">
            {region("lower_third").slice(-1).map((c) => <CueView key={c.cue_id} cue={c} view={view} />)}
          </div>
          {!pitch && tags.length > 0 && (
            <div className="flex flex-col items-end gap-2">{tags.map((c) => <PlayerTag key={c.cue_id} cue={c} meta={view.meta} />)}</div>
          )}
        </div>
      </div>
      </div>
    </ScaledStage>
  );
}
