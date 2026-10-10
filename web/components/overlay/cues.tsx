// Broadcast graphics for overlay cues. Every number shown comes from the cue's `data` (the insight's
// evidence); every sentence from `text` (template or grounded AI narration).
import type { MatchMeta, OverlayCue } from "@/lib/types";

type Change = { from: number | null; to: number | null };

function value(cue: OverlayCue, key: string): number | null {
  const v = cue.data[key];
  return typeof v === "number" ? v : null;
}

function change(cue: OverlayCue, key: string): Change | null {
  const v = cue.data[key];
  return v && typeof v === "object" && "to" in (v as object) ? (v as Change) : null;
}

const pct = (v: number | null | undefined) => (v == null ? "–" : `${Math.round(v * 100)}%`);
const fixed = (v: number | null | undefined, d = 1) => (v == null ? "–" : Number.isInteger(v) ? String(v) : v.toFixed(d));

function teamColor(meta: MatchMeta | null, cue: OverlayCue): string {
  if (!meta) return "var(--accent)";
  const team = cue.anchor?.player_id?.split("-")[0];
  return team === meta.away.club_id ? meta.away.colors[0] : meta.home.colors[0];
}

function Panel({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <div className={`animate-cue-in rounded-xl border border-white/10 bg-[#0b0f17]/90 shadow-2xl backdrop-blur ${className}`}>
      {children}
    </div>
  );
}

export function LowerThird({ cue, accent }: { cue: OverlayCue; accent: string }) {
  const goal = cue.priority >= 100;
  return (
    <Panel className="flex max-w-[70%] overflow-hidden">
      <div className="w-2 shrink-0" style={{ background: accent }} />
      <div className="px-6 py-4">
        <p className={`${goal ? "text-4xl" : "text-2xl"} font-bold leading-tight`}>{cue.text.headline}</p>
        {cue.text.body && <p className="mt-1 text-lg text-muted">{cue.text.body}</p>}
      </div>
      {cue.text.source === "llm" && <span className="self-start px-3 pt-3 text-xs font-semibold text-accent">AI</span>}
    </Panel>
  );
}

function StatCard({ label, big, unit, rows, caption }: { label: string; big: string; unit?: string; rows: [string, string][]; caption: string }) {
  return (
    <Panel className="w-[340px] p-5">
      <p className="text-xs font-semibold uppercase tracking-widest text-accent">{label}</p>
      <p className="mt-1 font-mono text-6xl font-bold tabular-nums">
        {big}
        {unit && <span className="ms-1 text-2xl text-muted">{unit}</span>}
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="text-end font-mono tabular-nums">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm leading-snug">{caption}</p>
    </Panel>
  );
}

export function PassCard({ cue }: { cue: OverlayCue }) {
  const rows: [string, string][] = [["Distance", `${fixed(value(cue, "pass_distance_m"), 0)} m`]];
  const lines = value(cue, "lines_broken");
  if (lines != null) rows.push(["Lines broken", String(lines)]);
  return <StatCard label="Pass difficulty" big={fixed(value(cue, "pass_difficulty"), 0)} unit="/100" rows={rows} caption={cue.text.headline} />;
}

export function ShotSpeed({ cue }: { cue: OverlayCue }) {
  const prev = value(cue, "previous_fastest_kmh");
  return (
    <StatCard
      label="Shot speed"
      big={fixed(value(cue, "shot_speed_kmh"), 0)}
      unit="km/h"
      rows={prev != null ? [["Previous best", `${fixed(prev, 0)} km/h`]] : []}
      caption={cue.text.headline}
    />
  );
}

export function WinProbSwing({ cue }: { cue: OverlayCue }) {
  const wp = change(cue, "win_prob");
  return (
    <Panel className="w-[360px] p-5">
      <p className="text-xs font-semibold uppercase tracking-widest text-accent">Win probability</p>
      <p className="mt-1 font-mono text-5xl font-bold tabular-nums">
        {pct(wp?.from)} <span className="text-muted">→</span> {pct(wp?.to)}
      </p>
      <p className="mt-2 text-sm leading-snug">{cue.text.headline}</p>
    </Panel>
  );
}

export function ChaosMeter({ cue }: { cue: OverlayCue }) {
  const index = change(cue, "control_index");
  const to = index?.to ?? 50;
  return (
    <Panel className="w-[380px] p-5">
      <div className="flex justify-between text-xs font-semibold uppercase tracking-widest">
        <span className="text-danger">Chaos</span>
        <span className="text-accent">Control</span>
      </div>
      <div className="relative mt-2 h-3 rounded-full bg-gradient-to-r from-danger via-warning to-accent">
        <div className="absolute -top-1.5 size-6 -translate-x-1/2 rounded-full border-4 border-white bg-[#0b0f17] transition-all duration-1000" style={{ left: `${to}%` }} />
      </div>
      <p className="mt-3 text-sm leading-snug">{cue.text.headline}</p>
    </Panel>
  );
}

export function MomentumBar({ cue, meta }: { cue: OverlayCue; meta: MatchMeta | null }) {
  const m = change(cue, "momentum")?.to ?? 0;
  const tilt = change(cue, "field_tilt")?.to;
  const width = Math.min(50, Math.abs(m) * 50);
  return (
    <Panel className="mx-auto w-[60%] px-6 py-3">
      <div className="flex items-center gap-4 text-sm">
        <span className="font-semibold">{meta?.home.short_name}</span>
        <div className="relative h-2 flex-1 rounded-full bg-surface-2">
          <div className="absolute inset-y-0 left-1/2 w-px bg-white/40" />
          <div
            className="absolute inset-y-0 rounded-full transition-all duration-1000"
            style={{ width: `${width}%`, left: m >= 0 ? `${50 - width}%` : "50%", background: m >= 0 ? meta?.home.colors[0] : meta?.away.colors[0] }}
          />
        </div>
        <span className="font-semibold">{meta?.away.short_name}</span>
      </div>
      <p className="mt-1 text-center text-sm">
        {cue.text.headline}
        {tilt != null && <span className="ms-2 text-muted">field tilt {pct(tilt)}</span>}
      </p>
    </Panel>
  );
}

export function PlayerTag({ cue, meta }: { cue: OverlayCue; meta: MatchMeta | null }) {
  const stat =
    value(cue, "top_speed") != null
      ? `${fixed(value(cue, "top_speed"))} km/h`
      : value(cue, "distance") != null
        ? `${fixed(value(cue, "distance"))} km`
        : value(cue, "sprint_count") != null
          ? `${fixed(value(cue, "sprint_count"), 0)} sprints`
          : value(cue, "consecutive_completed_passes") != null
            ? `${value(cue, "consecutive_completed_passes")} passes`
            : null;
  return (
    <div className="animate-cue-in flex items-center gap-2 whitespace-nowrap rounded-full border border-white/20 bg-[#0b0f17]/90 py-1 ps-1 pe-3 text-sm shadow-xl">
      <span className="size-3 rounded-full" style={{ background: teamColor(meta, cue) }} />
      <span className="font-semibold">{cue.text.headline}</span>
      {stat && <span className="font-mono text-accent">{stat}</span>}
    </div>
  );
}

export function cueAccent(meta: MatchMeta | null, cue: OverlayCue): string {
  return teamColor(meta, cue);
}
