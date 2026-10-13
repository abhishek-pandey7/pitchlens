import { API_BASE } from "@/lib/api";

/** T-93: the match as files a streaming player or a broadcast partner can load (match-time stamps). */
export function ExportLinks({ matchId, profileId, lang }: { matchId: string; profileId: string; lang: string }) {
  const base = `${API_BASE}/matches/${encodeURIComponent(matchId)}/export`;
  const links: [string, string, string][] = [
    ["Chapters", `${base}/chapters?lang=${lang}`, "WebVTT: kick-off, goals, red cards, half-time, big moments"],
    ["Captions", `${base}/captions?profile_id=${profileId}`, "WebVTT: this fan's narration, in their language"],
    ["Overlay cues", `${base}/cues?profile_id=${profileId}`, "JSON: every cue this fan's feed puts on air"],
  ];
  return (
    <section aria-label="Exports" className="panel text-sm">
      <h2 className="mb-2 section-title">Exports</h2>
      <ul className="flex flex-col gap-1.5">
        {links.map(([label, href, hint]) => (
          <li key={label} className="flex flex-wrap items-baseline gap-2">
            <a href={href} target="_blank" rel="noreferrer" className="font-semibold text-accent hover:underline" data-testid={`export-${label.split(" ")[0].toLowerCase()}`}>
              {label}
            </a>
            <span className="text-xs text-muted">{hint}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
