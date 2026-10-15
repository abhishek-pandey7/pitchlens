"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMemo, useState } from "react";
import { LineChart, type ChartMarker } from "@/components/charts/LineChart";
import { FuturesPanel } from "@/components/FuturesPanel";
import { cardKind } from "@/components/InsightFeed";
import { MatchLauncher } from "@/components/MatchLauncher";
import { ReplayControls } from "@/components/ReplayControls";
import { Scoreboard } from "@/components/Scoreboard";
import { api, type FuturesPoint, type SeriesRow } from "@/lib/api";
import { minuteLabel } from "@/lib/clock";
import { typeLabel } from "@/lib/i18n";
import type { FeedItem } from "@/lib/stream";
import type { MatchMeta } from "@/lib/types";
import { useMatchStream } from "@/lib/useMatchStream";
import { usePoll } from "@/lib/usePoll";
import { useProfiles } from "@/lib/useProfiles";

const HOME = "var(--home)";
const AWAY = "var(--away)";
const PROFILE = "p-analyst-en"; // the analyst persona sees every insight, with evidence
const pct = (v: number) => `${Math.round(v * 100)}%`;
const minute = (ms: number) => ms / 60_000;

/** /analyst?match=… — the analyst's desk (T-66): the match's dynamics as charts, and an evidence
 *  inspector for every detected moment. Charts follow the live replay. */
export function AnalystClient() {
  const params = useSearchParams();
  const matchId = params.get("match");
  const profiles = useProfiles();
  const view = useMatchStream(matchId, { profileId: PROFILE, frames: false });
  // Refresh the series as the replay advances (rows are every 30 s of match time).
  // Fresh series every 30 s of match time; the charts keep the last series on screen while it loads.
  const rows = usePoll(matchId, () => api.series(matchId ?? ""), 4000, Math.floor(view.matchMs / 30_000)) ?? [];
  // Futures runs land after key moments and every 5 match minutes; refetch the history when one does.
  const futures = view.state?.dynamics.futures ?? null;
  const history = usePoll(matchId, () => api.futuresHistory(matchId ?? ""), 8000, futures?.match_ms) ?? [];
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const selected = view.feed.find((f) => f.insight.insight_id === selectedId) ?? null;

  if (!matchId) {
    return (
      <main className="mx-auto w-full max-w-5xl px-4 py-8">
        <Header />
        <p className="mb-6 text-sm text-muted">Pick or simulate a match to analyse.</p>
        <MatchLauncher profiles={profiles} profileId={PROFILE} basePath="/analyst" />
      </main>
    );
  }

  const home = view.meta?.home.name ?? "Home";
  const away = view.meta?.away.name ?? "Away";
  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 py-5">
      <Header />
      <ReplayControls matchId={matchId} />
      <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,7fr)_minmax(340px,3fr)]">
        <section className="flex min-w-0 flex-col gap-4" aria-label="Charts">
          <Scoreboard meta={view.meta} state={view.state} status={view.status} lang="en" />
          <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
            <FuturesPanel futures={futures} meta={view.meta} lang="en" scores={5} />
            <FuturesChart history={history} feed={view.feed} home={home} away={away} highlight={selected ? minute(selected.insight.match_ms) : null} />
          </div>
          <Charts rows={rows} feed={view.feed} home={home} away={away} highlight={selected ? minute(selected.insight.match_ms) : null} />
        </section>
        <section className="min-w-0" aria-label="Evidence inspector">
          <Inspector feed={view.feed} meta={view.meta} selected={selected} onSelect={setSelectedId} />
        </section>
      </div>
    </main>
  );
}

function goalMarkers(feed: FeedItem[]): ChartMarker[] {
  return feed
    .filter((f) => f.insight.type === "goal")
    .map((f) => {
      const score = f.insight.evidence.find((e) => e.metric === "score")?.value;
      return { x: minute(f.insight.match_ms), label: typeof score === "string" ? score : "goal" };
    });
}

/** Futures over time: each run's share of simulated endings per result (engine /futures/history). */
function FuturesChart({ history, feed, home, away, highlight }: { history: FuturesPoint[]; feed: FeedItem[]; home: string; away: string; highlight: number | null }) {
  const x = useMemo(() => history.map((h) => minute(h.match_ms)), [history]);
  const goals = useMemo(() => goalMarkers(feed), [feed]);
  if (!history.length) {
    return <p className="panel text-sm text-muted">The futures chart fills in as the replay runs: a point after every key moment and every 5 minutes.</p>;
  }
  return (
    <LineChart
      testId="chart-futures"
      title="Futures over time"
      subtitle="share of simulated endings per result"
      x={x}
      series={[
        { label: home, color: HOME, values: history.map((h) => h.result[0]) },
        { label: "Draw", color: "var(--muted)", values: history.map((h) => h.result[1]), dashed: true },
        { label: away, color: AWAY, values: history.map((h) => h.result[2]) },
      ]}
      yMin={0}
      yMax={1}
      yTicks={[0, 0.5, 1]}
      formatY={pct}
      markers={goals}
      highlight={highlight}
    />
  );
}

function Charts({ rows, feed, home, away, highlight }: { rows: SeriesRow[]; feed: FeedItem[]; home: string; away: string; highlight: number | null }) {
  const x = useMemo(() => rows.map((r) => minute(r.match_ms)), [rows]);
  const goals = useMemo(() => goalMarkers(feed), [feed]);
  const col = <T,>(pick: (r: SeriesRow) => T) => rows.map(pick);
  const xgMax = Math.max(1, ...rows.map((r) => Math.max(r.xg[0], r.xg[1]))) + 0.25;
  const pressMax = Math.max(0.5, ...rows.map((r) => Math.max(r.press_rate[0], r.press_rate[1])));

  if (!rows.length) {
    return <p className="rounded-lg bg-surface p-3 text-sm text-muted">Charts appear once the replay starts (one point every 30 s of match time).</p>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <LineChart
        testId="chart-wp"
        title="Win probability"
        subtitle="after every event, sampled every 30 s"
        x={x}
        series={[
          { label: home, color: HOME, values: col((r) => r.wp[0]) },
          { label: "Draw", color: "var(--muted)", values: col((r) => r.wp[1]), dashed: true },
          { label: away, color: AWAY, values: col((r) => r.wp[2]) },
        ]}
        yMin={0}
        yMax={1}
        yTicks={[0, 0.5, 1]}
        formatY={pct}
        markers={goals}
        highlight={highlight}
      />
      <LineChart
        testId="chart-xg"
        title="Expected goals"
        subtitle="cumulative"
        x={x}
        series={[
          { label: home, color: HOME, values: col((r) => r.xg[0]) },
          { label: away, color: AWAY, values: col((r) => r.xg[1]) },
        ]}
        yMin={0}
        yMax={xgMax}
        formatY={(v) => v.toFixed(2)}
        markers={goals}
        highlight={highlight}
      />
      <LineChart
        testId="chart-momentum"
        title="Momentum"
        subtitle={`above 0: ${home} on top`}
        x={x}
        series={[{ label: "Home minus away", color: "var(--accent)", values: col((r) => r.momentum) }]}
        yMin={-1}
        yMax={1}
        yTicks={[-1, 0, 1]}
        formatY={(v) => v.toFixed(2)}
        reference={0}
        highlight={highlight}
      />
      <LineChart
        testId="chart-pressing"
        title="Pressing"
        subtitle="press rate: pressing actions per opponent pass, higher = more pressing"
        x={x}
        series={[
          { label: home, color: HOME, values: col((r) => r.press_rate[0]) },
          { label: away, color: AWAY, values: col((r) => r.press_rate[1]) },
        ]}
        yMin={0}
        yMax={pressMax}
        formatY={(v) => v.toFixed(2)}
        highlight={highlight}
      />
      <LineChart
        testId="chart-territory"
        title="Territory"
        subtitle={`${home}'s share: possession (last 5 min) and field tilt (last 10 min)`}
        x={x}
        series={[
          { label: "Possession", color: HOME, values: col((r) => r.possession_5m) },
          { label: "Field tilt", color: "var(--warning)", values: col((r) => r.field_tilt), dashed: true },
        ]}
        yMin={0}
        yMax={1}
        yTicks={[0, 0.5, 1]}
        formatY={pct}
        reference={0.5}
        highlight={highlight}
      />
      <LineChart
        testId="chart-control"
        title="Control vs chaos"
        subtitle="index from passing, turnovers, transitions and fouls"
        x={x}
        series={[{ label: "Control index", color: "var(--accent)", values: col((r) => r.control) }]}
        yMin={0}
        yMax={100}
        yTicks={[0, 40, 60, 100]}
        formatY={(v) => v.toFixed(0)}
        bands={[
          { from: 0, to: 40, color: "rgba(200, 50, 60, 0.07)", label: "chaos" },
          { from: 60, to: 100, color: "rgba(21, 122, 74, 0.08)", label: "control" },
        ]}
        highlight={highlight}
      />
    </div>
  );
}

function Inspector({
  feed, meta, selected, onSelect,
}: { feed: FeedItem[]; meta: MatchMeta | null; selected: FeedItem | null; onSelect: (id: string | null) => void }) {
  const [type, setType] = useState("all");
  const types = useMemo(() => Array.from(new Set(feed.map((f) => f.insight.type))).sort(), [feed]);
  const shown = feed.filter((f) => type === "all" || f.insight.type === type);
  const names = useMemo(() => {
    const out: Record<string, string> = {};
    if (meta) {
      out[meta.home.club_id] = meta.home.name;
      out[meta.away.club_id] = meta.away.name;
      Object.values(meta.lineups).flat().forEach((p) => (out[p.player_id] = p.name));
    }
    return out;
  }, [meta]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <h2 className="me-auto section-title">Evidence inspector</h2>
        <select aria-label="Moment type" value={type} onChange={(e) => setType(e.target.value)} className="rounded-md bg-surface-2 px-2 py-1 text-xs">
          <option value="all">All moments ({feed.length})</option>
          {types.map((t) => (
            <option key={t} value={t}>
              {typeLabel("en", t)}
            </option>
          ))}
        </select>
      </div>
      {selected && <Detail item={selected} names={names} onClose={() => onSelect(null)} />}
      <ol className="flex max-h-[70vh] flex-col gap-1.5 overflow-y-auto pe-1" aria-label="Moments">
        {!shown.length && <li className="text-sm text-muted">Moments appear here as the match is analysed.</li>}
        {shown.map((f) => {
          const active = selected?.insight.insight_id === f.insight.insight_id;
          return (
            <li key={f.insight.insight_id}>
              <button
                type="button"
                onClick={() => onSelect(active ? null : f.insight.insight_id)}
                aria-pressed={active}
                data-testid="moment"
                className={`w-full rounded-md p-2 text-start text-sm ${active ? "bg-accent/15 ring-1 ring-accent" : "bg-surface hover:bg-surface-2"}`}
              >
                <span className="me-2 font-mono text-xs text-muted">{clockLabel(f)}</span>
                <span className="me-2 text-xs text-muted">{typeLabel("en", cardKind(f.insight) ?? f.insight.type)}</span>
                <span>{f.narration.headline || f.narration.text}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function Detail({ item, names, onClose }: { item: FeedItem; names: Record<string, string>; onClose: () => void }) {
  const i = item.insight;
  const who = [...i.entities.players, ...i.entities.teams].map((id) => names[id] ?? id);
  return (
    <article className="rounded-lg bg-surface p-3 text-sm ring-1 ring-accent/40" data-testid="evidence-detail">
      <header className="mb-2 flex items-start gap-2">
        <div className="me-auto">
          <p className="font-mono text-xs text-muted">
            {clockLabel(item)} · {typeLabel("en", cardKind(i) ?? i.type)} · <code>{i.headline_key}</code>
          </p>
          <p className="font-semibold">{item.narration.text}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close" className="text-muted hover:text-foreground">
          ✕
        </button>
      </header>
      <dl className="mb-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted">Salience</dt>
        <dd className="font-mono">
          {i.salience.toFixed(2)} (this persona: {item.personal_salience.toFixed(2)})
        </dd>
        <dt className="text-muted">Who</dt>
        <dd>{who.join(", ") || "–"}</dd>
        <dt className="text-muted">Why it matters</dt>
        <dd>{i.why_tags.map((t) => t.replaceAll("_", " ")).join(", ") || "–"}</dd>
        {i.author && i.author !== "engine" && (
          <>
            <dt className="text-muted">Author</dt>
            <dd>{i.author}</dd>
          </>
        )}
      </dl>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-start text-muted">
            <th className="py-1 text-start font-normal">Metric</th>
            <th className="py-1 text-start font-normal">For</th>
            <th className="py-1 text-end font-normal">Value</th>
          </tr>
        </thead>
        <tbody>
          {i.evidence.map((e, n) => (
            <tr key={n} className="border-t border-surface-2">
              <td className="py-1 pe-3 font-code text-[11.5px]">{e.metric}</td>
              <td className="py-1">{[e.team, e.player].filter(Boolean).map((id) => names[id as string] ?? id).join(" · ") || "–"}</td>
              <td className="py-1 text-end font-mono tabular-nums">
                {e.from != null && e.to != null ? `${fmt(e.from)} → ${fmt(e.to)}` : fmt(e.value)}
                {e.unit ? ` ${e.unit}` : ""}
                {e.window_min ? ` (${e.window_min} min)` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-muted">Every number here is computed by the deterministic engine; the charts mark this moment.</p>
    </article>
  );
}

function fmt(v: unknown): string {
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  if (v == null) return "–";
  return String(v);
}

function clockLabel(f: FeedItem): string {
  return minuteLabel(f.insight.clock_ms, f.insight.period);
}

function Header() {
  return (
    <header className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <Link href="/" className="display text-xl text-foreground">
        PitchLens <span className="ms-1 text-muted">Analyst</span>
      </Link>
    </header>
  );
}
