import { clockText, minuteLabel } from "@/lib/clock";
import { t } from "@/lib/i18n";
import type { StateSnapshot, Status } from "@/lib/stream";
import type { MatchMeta } from "@/lib/types";

type Props = { meta: MatchMeta | null; state: StateSnapshot | null; status: Status; lang: string };

export function Scoreboard({ meta, state, status, lang }: Props) {
  if (!meta) return <div className="h-24 animate-pulse rounded-xl bg-surface" />;
  const home = state?.home.score ?? 0;
  const away = state?.away.score ?? 0;
  const label =
    status === "finished"
      ? t(lang, "fullTime")
      : state
        ? `${minuteLabel(state.clock_ms, state.period)} · ${clockText(state.clock_ms)}`
        : t(lang, "waiting");
  return (
    <div className="rounded-xl bg-surface p-4">
      <div className="flex items-center justify-between gap-3">
        <Team name={meta.home.short_name} color={meta.home.colors[0]} align="start" />
        <div className="text-center">
          <div className="font-mono text-4xl font-bold tabular-nums" aria-label={`${home} - ${away}`}>
            {home}<span className="mx-2 text-muted">–</span>{away}
          </div>
          <div className="mt-1 flex items-center justify-center gap-2 text-xs text-muted">
            {status === "live" && <span className="inline-block size-2 animate-pulse rounded-full bg-danger" />}
            <span>{label}</span>
          </div>
        </div>
        <Team name={meta.away.short_name} color={meta.away.colors[0]} align="end" />
      </div>
      {state && <WinProbBar wp={state.dynamics.wp} meta={meta} lang={lang} />}
    </div>
  );
}

function Team({ name, color, align }: { name: string; color: string; align: "start" | "end" }) {
  return (
    <div className={`flex min-w-0 flex-1 items-center gap-2 ${align === "end" ? "flex-row-reverse text-end" : ""}`}>
      <span className="size-3 shrink-0 rounded-full ring-2 ring-white/20" style={{ background: color }} />
      <span className="truncate text-lg font-semibold">{name}</span>
    </div>
  );
}

export function WinProbBar({ wp, meta, lang }: { wp: [number, number, number]; meta: MatchMeta; lang: string }) {
  const [h, d, a] = wp.map((v) => Math.round(v * 100));
  return (
    <div className="mt-4">
      <div className="mb-1 flex justify-between text-xs text-muted">
        <span>{h}%</span>
        <span>{t(lang, "winProb")} · {t(lang, "draw")} {d}%</span>
        <span>{a}%</span>
      </div>
      <div className="flex h-2 overflow-hidden rounded-full bg-surface-2" role="img" aria-label={`${t(lang, "winProb")}: ${h}% / ${d}% / ${a}%`}>
        <div className="transition-all duration-700" style={{ width: `${h}%`, background: meta.home.colors[0] }} />
        <div className="bg-muted/40 transition-all duration-700" style={{ width: `${d}%` }} />
        <div className="transition-all duration-700" style={{ width: `${a}%`, background: meta.away.colors[0] }} />
      </div>
    </div>
  );
}
