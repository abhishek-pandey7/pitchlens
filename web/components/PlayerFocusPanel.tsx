"use client";

import { api, type FocusCard } from "@/lib/api";
import { focusStrings } from "@/lib/i18n";
import type { MatchMeta } from "@/lib/types";
import { usePoll } from "@/lib/usePoll";

type Props = {
  matchId: string;
  meta: MatchMeta | null;
  playerId: string | null;
  onPick: (playerId: string | null) => void;
  profileId: string | null;
  lang: string;
  matchMs: number;
};

/** Player-focus mode (T-57/T-65): one player's match as it happens, in the fan's language. The pitch
 *  highlights the same player, and the fan's profile can boost their moments in the feed. */
export function PlayerFocusPanel({ matchId, meta, playerId, onPick, profileId, lang, matchMs }: Props) {
  const s = focusStrings(lang);
  // Refresh every few seconds of real time, and whenever the match moves on noticeably.
  const key = playerId ? `${matchId}|${playerId}|${profileId ?? ""}|${Math.floor(matchMs / 20_000)}` : null;
  const card = usePoll(key, () => api.focus(matchId, playerId ?? "", profileId), 3000);

  return (
    <section aria-label={s.title} className="rounded-lg bg-surface p-3" data-testid="player-focus">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="me-auto text-sm font-semibold uppercase tracking-widest text-muted">{s.title}</h2>
        <select
          aria-label={s.pick}
          value={playerId ?? ""}
          onChange={(e) => onPick(e.target.value || null)}
          className="max-w-[60%] rounded-md bg-surface-2 px-2 py-1 text-xs"
        >
          <option value="">{s.pick}</option>
          {meta &&
            [meta.home, meta.away].map((team) => (
              <optgroup key={team.club_id} label={team.name}>
                {(meta.lineups[team.club_id] ?? []).map((p) => (
                  <option key={p.player_id} value={p.player_id}>
                    {p.shirt} · {p.name} ({p.pos})
                  </option>
                ))}
              </optgroup>
            ))}
        </select>
      </div>
      {!playerId && <p className="text-sm text-muted">{s.none}</p>}
      {playerId && card && card.player_id === playerId && <Card card={card} lang={lang} />}
    </section>
  );
}

function Card({ card, lang }: { card: FocusCard; lang: string }) {
  const s = focusStrings(lang);
  const pct = (v: number | null) => (v == null ? "–" : `${Math.round(v * 100)}%`);
  const stats: [string, string][] = [
    [s.touches, String(card.involvement.touches)],
    [s.passes, String(card.involvement.passes)],
    [s.accuracy, pct(card.involvement.pass_accuracy)],
    [s.difficulty, card.involvement.mean_pass_difficulty == null ? "–" : card.involvement.mean_pass_difficulty.toFixed(0)],
    [s.keyPasses, String(card.involvement.key_passes)],
    [s.goals, String(card.attack.goals)],
    [s.assists, String(card.attack.assists)],
    [s.shots, String(card.attack.shots)],
    [s.xg, card.attack.xg.toFixed(2)],
    [s.tackles, String(card.defence.tackles)],
    [s.interceptions, String(card.defence.interceptions)],
    [s.recoveries, String(card.defence.recoveries)],
  ];
  if (card.physical) {
    stats.push(
      [s.distance, `${card.physical.distance_km.toFixed(2)} km`],
      [s.topSpeed, `${card.physical.top_speed_kmh.toFixed(1)} km/h`],
      [s.sprints, String(card.physical.sprints)],
    );
  }
  return (
    <div className="flex flex-col gap-3 text-sm">
      <p>
        <span className="me-2 rounded bg-surface-2 px-1.5 py-0.5 font-mono text-xs">{card.shirt ?? "–"}</span>
        <span className="font-semibold" dir="ltr">{card.name}</span>
        <span className="text-muted">
          {" "}· {card.team.name} · {card.position} · {card.minutes.toFixed(0)} {s.minutes} · {card.on_pitch ? s.onPitch : s.off}
        </span>
      </p>
      <dl className="grid grid-cols-3 gap-2 sm:grid-cols-5" data-testid="focus-stats">
        {stats.map(([label, value]) => (
          <div key={label} className="rounded-md bg-surface-2 p-1.5 text-center">
            <dt className="truncate text-[11px] text-muted" title={label}>
              {label}
            </dt>
            <dd className="font-mono tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {!card.physical && <p className="text-xs text-muted">{s.noTracking}</p>}
      <TouchMap card={card} lang={lang} />
      <div>
        <h3 className="mb-1 text-xs font-semibold uppercase tracking-widest text-muted">{s.moments}</h3>
        {!card.timeline.length && <p className="text-xs text-muted">{s.noMoments}</p>}
        <ol className="flex flex-col gap-1">
          {[...card.timeline].reverse().map((m) => (
            <li key={m.insight_id} className="text-xs" dir="auto">
              <span className="me-2 font-mono text-muted">{m.minute}</span>
              {m.text}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}

/** Touches per zone, drawn on a pitch in the player's attacking direction (own goal on the left). */
function TouchMap({ card, lang }: { card: FocusCard; lang: string }) {
  const s = focusStrings(lang);
  const { cols, rows, counts } = card.zones;
  const max = Math.max(1, ...counts);
  const W = 210;
  const H = 136;
  const cw = W / cols;
  const ch = H / rows;
  return (
    <figure className="max-w-xs">
      <figcaption className="mb-1 flex text-xs text-muted">
        <span className="me-auto">{s.touchMap}</span>
        <span dir="ltr">{s.attacking} →</span>
      </figcaption>
      {/* SVG content is not mirrored in right-to-left layouts: the direction of play stays a fact. */}
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={s.touchMap} data-testid="touch-map">
        <rect width={W} height={H} rx={4} fill="#1d5a36" />
        {counts.map((c, i) => {
          const col = i % cols;
          const row = Math.floor(i / cols);
          // Row 0 is the bottom touchline in engine coordinates; SVG y grows downwards.
          return (
            <rect
              key={i}
              x={col * cw + 1}
              y={(rows - 1 - row) * ch + 1}
              width={cw - 2}
              height={ch - 2}
              rx={3}
              fill="var(--accent)"
              opacity={c ? 0.12 + 0.75 * (c / max) : 0}
            >
              <title>{c}</title>
            </rect>
          );
        })}
        <g fill="none" stroke="rgba(255,255,255,0.55)" strokeWidth={1}>
          <rect x={0.5} y={0.5} width={W - 1} height={H - 1} rx={4} />
          <line x1={W / 2} x2={W / 2} y1={0} y2={H} />
          <circle cx={W / 2} cy={H / 2} r={18} />
          <rect x={0.5} y={H / 2 - 40} width={32} height={80} />
          <rect x={W - 32.5} y={H / 2 - 40} width={32} height={80} />
        </g>
      </svg>
    </figure>
  );
}
