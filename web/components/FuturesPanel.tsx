import { minuteLabel } from "@/lib/clock";
import { futuresStrings, t } from "@/lib/i18n";
import type { Futures, MatchMeta } from "@/lib/types";

const pct = (v: number) => `${Math.round(v * 100)}%`;

/** Futures: the rest of the match simulated many times by the engine (docs/rust-expansion-plan.md §3).
 *  A result bar in the clubs' kit colours, the most likely final scores, and the comeback odds (or, when
 *  level, who scores next) in one plain sentence. Every number is the engine's. */
export function FuturesPanel({ futures, meta, lang, scores = 4 }: { futures: Futures | null | undefined; meta: MatchMeta | null; lang: string; scores?: number }) {
  const s = futuresStrings(lang);
  if (!meta) return null;
  const home = meta.home.short_name;
  const away = meta.away.short_name;
  const homeColor = meta.home.colors[0];
  const awayColor = meta.away.colors[0];
  return (
    <section aria-label={s.title} className="panel" data-testid="futures">
      <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="section-title">{s.title}</h2>
        {futures && (
          <span className="ms-auto text-xs text-muted" data-testid="futures-paths">
            {s.simulated(new Intl.NumberFormat(lang).format(futures.paths), minuteLabel(futures.clock_ms, futures.period))}
          </span>
        )}
      </div>
      {!futures ? (
        <p className="text-sm text-muted">{s.waiting}</p>
      ) : (
        <>
          <ResultBar futures={futures} home={home} away={away} homeColor={homeColor} awayColor={awayColor} lang={lang} />
          <h3 className="mt-4 mb-1.5 text-sm text-muted">{s.likely}</h3>
          <ol className="flex flex-col gap-1" data-testid="futures-scores">
            {futures.scores.slice(0, scores).map((c, i) => {
              const color = c.home > c.away ? homeColor : c.home < c.away ? awayColor : "var(--muted)";
              const width = Math.max(4, (c.p / (futures.scores[0]?.p || 1)) * 100);
              return (
                <li key={`${c.home}-${c.away}`} className="grid grid-cols-[1.25rem_3.25rem_1fr_3rem] items-center gap-2 text-sm">
                  <span className="font-mono text-xs text-muted">{i + 1}</span>
                  <span className="display font-mono text-lg tabular-nums">
                    {c.home}–{c.away}
                  </span>
                  <span className="h-1.5 rounded-full bg-surface-2" aria-hidden>
                    <span className="block h-full rounded-full transition-all duration-700" style={{ width: `${width}%`, background: color }} />
                  </span>
                  <span className="text-end font-mono tabular-nums">{pct(c.p)}</span>
                </li>
              );
            })}
          </ol>
          <p className="mt-3 text-sm leading-relaxed" data-testid="futures-sentence">
            {futures.comeback
              ? s.comeback(futures.comeback.side === "home" ? home : away, pct(futures.comeback.avoid_defeat), pct(futures.comeback.win))
              : s.nextGoal(home, pct(futures.next_goal.home), away, pct(futures.next_goal.away), pct(futures.next_goal.none))}
          </p>
        </>
      )}
    </section>
  );
}

function ResultBar({
  futures, home, away, homeColor, awayColor, lang,
}: { futures: Futures; home: string; away: string; homeColor: string; awayColor: string; lang: string }) {
  const [h, d, a] = [futures.home, futures.draw, futures.away].map((v) => Math.round(v * 100));
  const draw = t(lang, "draw");
  return (
    <div data-testid="futures-result">
      <div className="mb-1 grid grid-cols-3 items-baseline text-sm">
        <span className="truncate">
          <span className="display font-mono text-2xl tabular-nums">{h}%</span> <span className="text-muted">{home}</span>
        </span>
        <span className="text-center">
          <span className="font-mono tabular-nums">{d}%</span> <span className="text-muted">{draw.toLowerCase()}</span>
        </span>
        <span className="truncate text-end">
          <span className="text-muted">{away}</span> <span className="display font-mono text-2xl tabular-nums">{a}%</span>
        </span>
      </div>
      <div className="flex h-2.5 gap-0.5 overflow-hidden rounded-full" role="img" aria-label={`${home} ${h}%, ${draw} ${d}%, ${away} ${a}%`}>
        <div className="rounded-s-full transition-all duration-700" style={{ width: `${h}%`, background: homeColor }} />
        <div className="bg-surface-2 transition-all duration-700" style={{ width: `${d}%` }} />
        <div className="rounded-e-full transition-all duration-700" style={{ width: `${a}%`, background: awayColor }} />
      </div>
    </div>
  );
}
