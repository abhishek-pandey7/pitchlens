"use client";

import { useState } from "react";
import { minuteLabel } from "@/lib/clock";
import { t, typeLabel } from "@/lib/i18n";
import type { FeedItem } from "@/lib/stream";
import type { Evidence } from "@/lib/types";

const TONE: Record<string, string> = {
  goal: "border-accent",
  card: "border-warning",
  big_chance: "border-warning",
  career_milestone: "border-accent",
  pressure_change: "border-home",
  momentum_shift: "border-home",
  control_chaos_flip: "border-home",
};

/** A sending-off (straight red or second yellow) is a red card, whatever the event type says. */
export function cardKind(insight: Pick<FeedItem["insight"], "type" | "headline_key">): "red_card" | "yellow_card" | null {
  if (insight.type !== "card") return null;
  return insight.headline_key === "card.yellow" ? "yellow_card" : "red_card";
}

type Props = { items: FeedItem[]; lang: string; showEvidence: boolean };

export function InsightFeed({ items, lang, showEvidence }: Props) {
  if (!items.length) return <p className="py-8 text-center text-sm text-muted">{t(lang, "waiting")}</p>;
  return (
    <ol className="flex flex-col gap-3" aria-live="polite">
      {items.map((item) => (
        <FeedCard key={item.insight.insight_id} item={item} lang={lang} showEvidence={showEvidence} />
      ))}
    </ol>
  );
}

function FeedCard({ item, lang, showEvidence }: { item: FeedItem; lang: string; showEvidence: boolean }) {
  const [revealed, setRevealed] = useState(!item.spoiler);
  const { insight, narration } = item;
  const goal = insight.type === "goal";
  const card = cardKind(insight);
  const tone = card === "red_card" ? "border-danger" : (TONE[insight.type] ?? "border-surface-2");
  return (
    <li
      className={`rounded-lg border-s-4 bg-surface p-3 ${tone} ${goal ? "ring-1 ring-accent/40" : ""}`}
      style={{ opacity: 0.55 + 0.45 * item.personal_salience }}
    >
      <div className="mb-1 flex items-center gap-2 text-xs text-muted">
        <span className="font-mono font-semibold text-foreground">{minuteLabel(insight.clock_ms, insight.period)}</span>
        <span className={card === "red_card" ? "font-semibold text-danger" : undefined}>{typeLabel(lang, card ?? insight.type)}</span>
        {narration.source === "llm" && (
          <span className="rounded bg-accent/15 px-1.5 py-0.5 font-semibold text-accent" title="Rewritten by the AI narrator, checked against the evidence">
            {t(lang, "ai")}
          </span>
        )}
        {item.reasons.includes("favorite_club") && <span aria-label="favourite club">★</span>}
      </div>
      {revealed ? (
        <p className={goal ? "text-base font-semibold" : "text-sm"}>{narration.text}</p>
      ) : (
        <button type="button" onClick={() => setRevealed(true)} className="text-sm text-muted underline">
          {t(lang, "reveal")}
        </button>
      )}
      {showEvidence && revealed && <EvidenceChips evidence={insight.evidence} />}
    </li>
  );
}

function fmt(v: number | null | undefined): string {
  if (v == null) return "–";
  return Math.abs(v) < 1 && v !== 0 ? v.toFixed(2) : Number.isInteger(v) ? String(v) : v.toFixed(1);
}

function EvidenceChips({ evidence }: { evidence: Evidence[] }) {
  return (
    <ul className="mt-2 flex flex-wrap gap-1.5">
      {evidence.map((e, i) => (
        <li key={`${e.metric}-${i}`} className="rounded bg-surface-2 px-2 py-0.5 font-mono text-[11px] text-muted">
          {e.metric.replaceAll("_", " ")}{" "}
          <span className="text-foreground">
            {e.from != null || e.to != null ? `${fmt(e.from)} → ${fmt(e.to)}` : String(e.value ?? "")}
          </span>
        </li>
      ))}
    </ul>
  );
}
