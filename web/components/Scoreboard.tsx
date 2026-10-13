import { clockText, minuteLabel } from "@/lib/clock";
import { t } from "@/lib/i18n";
import type { StateSnapshot, Status } from "@/lib/stream";
import type { MatchMeta } from "@/lib/types";

type Props = { meta: MatchMeta | null; state: StateSnapshot | null; status: Status; lang: string };

// A stadium board: each side carries its kit colour, the score is set narrow and heavy.
export function Scoreboard({ meta, state, status, lang }: Props) {
  if (!meta) return <div className="h-28 animate-pulse rounded-lg bg-surface" />;
  const home = state?.home.score ?? 0;
  const away = state?.away.score ?? 0;
  return (
    <div>
      <div className="grid grid-cols-[1fr_auto_1fr] items-stretch overflow-hidden rounded-lg bg-surface">
        <Team code={meta.home.club_id} name={meta.home.short_name} full={meta.home.name} color={meta.home.colors[0]} align="start" />
        <div className="flex flex-col items-center justify-center px-4 py-3 sm:px-6">
          <div className="display text-5xl tabular-nums sm:text-6xl" aria-label={`${home} - ${away}`}>
            {home}
            <span className="mx-2 text-muted/50">–</span>
            {away}
          </div>
          <Clock state={state} status={status} lang={lang} />
        </div>
        <Team code={meta.away.club_id} name={meta.away.short_name} full={meta.away.name} color={meta.away.colors[0]} align="end" />
      </div>
      {state && <WinProbBar wp={state.dynamics.wp} meta={meta} lang={lang} />}
    </div>
  );
}

function Clock({ state, status, lang }: { state: StateSnapshot | null; status: Status; lang: string }) {
  if (status === "finished") return <div className="mt-1 text-sm font-semibold">{t(lang, "fullTime")}</div>;
  if (!state) return <div className="mt-1 text-sm text-muted">{t(lang, "waiting")}</div>;
  return (
    <div className="mt-1 flex items-baseline gap-2 text-sm">
      {status === "live" && <span className="inline-block size-2 self-center rounded-full bg-danger motion-safe:animate-pulse" />}
      <span className="font-semibold">{minuteLabel(state.clock_ms, state.period)}</span>
      <span className="font-mono text-muted">{clockText(state.clock_ms)}</span>
    </div>
  );
}

function Team({ code, name, full, color, align }: { code: string; name: string; full: string; color: string; align: "start" | "end" }) {
  const end = align === "end";
  return (
    <div className={`flex min-w-0 items-stretch gap-3 ${end ? "flex-row-reverse text-end" : ""}`}>
      <span className="w-2 shrink-0" style={{ background: color }} aria-hidden />
      <div className="flex min-w-0 flex-col justify-center py-3">
        <span className="display text-2xl sm:hidden" title={full}>{code}</span>
        <span className="display hidden truncate text-3xl sm:block">{name}</span>
        <span className="hidden truncate text-sm text-muted sm:block">{full}</span>
      </div>
    </div>
  );
}

export function WinProbBar({ wp, meta, lang }: { wp: [number, number, number]; meta: MatchMeta; lang: string }) {
  const [h, d, a] = wp.map((v) => Math.round(v * 100));
  return (
    <div className="mt-3">
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <span className="font-mono font-semibold">{h}%</span>
        <span className="text-muted">
          {t(lang, "winProb")}, {t(lang, "draw").toLowerCase()} <span className="font-mono">{d}%</span>
        </span>
        <span className="font-mono font-semibold">{a}%</span>
      </div>
      <div className="flex h-2 gap-0.5 overflow-hidden rounded-full" role="img" aria-label={`${t(lang, "winProb")}: ${h}% / ${d}% / ${a}%`}>
        <div className="rounded-s-full transition-all duration-700" style={{ width: `${h}%`, background: meta.home.colors[0] }} />
        <div className="bg-surface-2 transition-all duration-700" style={{ width: `${d}%` }} />
        <div className="rounded-e-full transition-all duration-700" style={{ width: `${a}%`, background: meta.away.colors[0] }} />
      </div>
    </div>
  );
}
