/** A small dependency-free SVG line chart over match minutes, sized to its container. */

export type ChartSeries = { label: string; color: string; values: (number | null)[]; dashed?: boolean };
export type ChartMarker = { x: number; label: string; color?: string };
export type ChartBand = { from: number; to: number; color: string; label?: string };

type Props = {
  title: string;
  subtitle?: string;
  x: number[]; // minutes
  series: ChartSeries[];
  yMin: number;
  yMax: number;
  yTicks?: number[];
  formatY?: (v: number) => string;
  markers?: ChartMarker[]; // e.g. goals
  bands?: ChartBand[]; // e.g. chaos / control zones
  reference?: number; // a horizontal reference line (0, 0.5 ...)
  highlight?: number | null; // a minute to mark (the inspected moment)
  testId?: string;
};

const W = 600;
const H = 170;
const PAD = { left: 40, right: 10, top: 10, bottom: 22 };

export function LineChart({
  title, subtitle, x, series, yMin, yMax, yTicks, formatY = (v) => `${v}`, markers = [], bands = [],
  reference, highlight, testId,
}: Props) {
  const xMax = Math.max(90, ...x);
  const sx = (m: number) => PAD.left + (m / xMax) * (W - PAD.left - PAD.right);
  const sy = (v: number) => {
    const clamped = Math.min(yMax, Math.max(yMin, v));
    return PAD.top + (1 - (clamped - yMin) / (yMax - yMin || 1)) * (H - PAD.top - PAD.bottom);
  };
  const ticks = yTicks ?? [yMin, (yMin + yMax) / 2, yMax];
  const xTicks = [0, 15, 30, 45, 60, 75, 90].filter((m) => m <= xMax);

  const path = (values: (number | null)[]) => {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (v == null || x[i] == null) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${sx(x[i]).toFixed(1)},${sy(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  const last = (values: (number | null)[]) => [...values].reverse().find((v) => v != null) ?? null;

  return (
    <figure className="panel" data-testid={testId}>
      <figcaption className="mb-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-sm font-semibold">{title}</span>
        {subtitle && <span className="text-xs text-muted">{subtitle}</span>}
        <span className="ms-auto flex flex-wrap gap-3 text-xs">
          {series.map((s) => {
            const v = last(s.values);
            return (
              <span key={s.label} className="flex items-center gap-1">
                <span className="inline-block h-0.5 w-3" style={{ background: s.color }} aria-hidden />
                <span className="text-muted">{s.label}</span>
                {v != null && <span className="font-mono tabular-nums">{formatY(v)}</span>}
              </span>
            );
          })}
        </span>
      </figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-label={title}>
        {bands.map((b) => (
          <g key={`${b.from}-${b.to}`}>
            <rect x={PAD.left} width={W - PAD.left - PAD.right} y={sy(b.to)} height={Math.max(0, sy(b.from) - sy(b.to))} fill={b.color} />
            {b.label && (
              <text x={W - PAD.right - 4} y={sy(b.to) + 11} textAnchor="end" className="fill-muted text-[10px]">
                {b.label}
              </text>
            )}
          </g>
        ))}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} stroke="currentColor" className="text-surface-2" />
            <text x={PAD.left - 6} y={sy(t) + 3} textAnchor="end" className="fill-muted font-mono text-[10px]">
              {formatY(t)}
            </text>
          </g>
        ))}
        {reference != null && (
          <line x1={PAD.left} x2={W - PAD.right} y1={sy(reference)} y2={sy(reference)} stroke="currentColor" strokeDasharray="3 3" className="text-muted" />
        )}
        {xTicks.map((m) => (
          <text key={m} x={sx(m)} y={H - 6} textAnchor="middle" className="fill-muted font-mono text-[10px]">
            {m}&apos;
          </text>
        ))}
        {markers.map((m, i) => (
          <g key={`${m.x}-${i}`}>
            <line x1={sx(m.x)} x2={sx(m.x)} y1={PAD.top} y2={H - PAD.bottom} stroke={m.color ?? "currentColor"} strokeWidth={1} className="text-muted" opacity={0.7} />
            <text x={sx(m.x) + 3} y={PAD.top + 9} className="fill-foreground text-[10px]">
              {m.label}
            </text>
          </g>
        ))}
        {series.map((s) => (
          <path key={s.label} d={path(s.values)} fill="none" stroke={s.color} strokeWidth={2} strokeDasharray={s.dashed ? "5 4" : undefined} strokeLinejoin="round" />
        ))}
        {highlight != null && (
          <line x1={sx(highlight)} x2={sx(highlight)} y1={PAD.top} y2={H - PAD.bottom} stroke="var(--accent)" strokeWidth={2} data-testid="chart-highlight" />
        )}
      </svg>
    </figure>
  );
}
