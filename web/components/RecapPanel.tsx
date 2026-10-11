"use client";

import { useState } from "react";
import { api, type RecapResponse } from "@/lib/api";
import { recapStrings } from "@/lib/i18n";
import { AgentSteps } from "./AgentSteps";

type Stage = "ht" | "ft";
type Slot = { result?: RecapResponse; error?: "notYet" | "failed"; busy?: boolean };

/** Half-time and full-time recaps for one fan: written by the Editor's group chat (Editor, Analyst,
 *  Fact-Checker) and adapted by the Narrator to the fan's persona, language and club (docs/agents.md).
 *  The engine answers 409 before the whistle, which shows as "available after the whistle". */
export function RecapPanel({ matchId, profileId, lang }: { matchId: string; profileId: string | null; lang: string }) {
  const s = recapStrings(lang);
  const [stage, setStage] = useState<Stage | null>(null);
  const [slots, setSlots] = useState<Record<string, Slot>>({});
  const key = (st: Stage) => `${matchId}|${profileId ?? ""}|${st}`;

  async function load(st: Stage) {
    setStage(st);
    const k = key(st);
    if (slots[k]?.result || slots[k]?.busy) return;
    setSlots((prev) => ({ ...prev, [k]: { busy: true } }));
    try {
      const result = await api.recap(matchId, { stage: st, profile_id: profileId ?? "p-analyst-en" });
      setSlots((prev) => ({ ...prev, [k]: { result } }));
    } catch (err) {
      const notYet = String(err).includes(": 409");
      setSlots((prev) => ({ ...prev, [k]: { error: notYet ? "notYet" : "failed" } }));
    }
  }

  const slot = stage ? slots[key(stage)] : undefined;
  return (
    <section aria-label={s.title} className="rounded-lg bg-surface p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h2 className="me-auto text-sm font-semibold uppercase tracking-widest text-muted">{s.title}</h2>
        {(["ht", "ft"] as const).map((st) => (
          <button
            key={st}
            type="button"
            onClick={() => void load(st)}
            aria-pressed={stage === st}
            data-testid={`recap-${st}`}
            className={`rounded-md px-3 py-1 text-xs font-semibold ${stage === st ? "bg-accent text-background" : "bg-surface-2 text-muted hover:text-foreground"}`}
          >
            {s[st]}
          </button>
        ))}
      </div>
      {slot?.busy && <p className="animate-pulse text-sm text-muted">{s.writing}</p>}
      {slot?.error && <p className="text-sm text-warning">{slot.error === "notYet" ? s.notYet : s.failed}</p>}
      {slot?.result && <Recap result={slot.result} lang={lang} />}
    </section>
  );
}

function Recap({ result, lang }: { result: RecapResponse; lang: string }) {
  const s = recapStrings(lang);
  const checked = result.verdict === "approve" && result.source !== "facts";
  return (
    <article data-testid="recap" dir={result.lang === "ar" ? "rtl" : undefined} className="text-sm">
      <h3 className="mb-1 text-base font-semibold">{result.headline}</h3>
      {result.body.split(/\n+/).filter(Boolean).map((para, i) => (
        <p key={i} className="mb-2 leading-relaxed">
          {para}
        </p>
      ))}
      {result.player_line && (
        <p className="rounded-md bg-surface-2 p-2">
          <span className="me-1 text-xs font-semibold uppercase tracking-wide text-accent">
            {result.stage === "ft" ? s.potm : s.standout}
          </span>
          {result.player_line}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span
          className={`rounded px-1.5 py-0.5 font-semibold ${checked ? "bg-accent/15 text-accent" : "bg-warning/15 text-warning"}`}
          title={checked ? s.verifiedHint : undefined}
        >
          {checked ? `✓ ${s.verified}` : s.facts}
        </span>
        <span className="font-mono">{result.minute}</span>
        <details>
          <summary className="cursor-pointer">{s.how}</summary>
          <AgentSteps steps={result.steps} />
        </details>
      </div>
    </article>
  );
}
