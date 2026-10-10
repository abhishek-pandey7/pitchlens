// Football clock formatting, identical to engine/pitchlens/clock.py.

/** 1'..45', 45+2', 46'..90', 90+4' (minute n covers (n-1):00 to n:00). */
export function minuteLabel(clockMs: number, period: number): string {
  const minute = Math.floor(clockMs / 60_000);
  const regular = 45 * period;
  return minute >= regular ? `${regular}+${minute - regular + 1}'` : `${minute + 1}'`;
}

/** mm:ss match clock for scoreboards (counts up through stoppage time). */
export function clockText(clockMs: number): string {
  const total = Math.max(0, Math.floor(clockMs / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
