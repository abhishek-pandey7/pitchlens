"use client";

import { useState } from "react";
import { api, type AskResponse } from "@/lib/api";
import { askStrings } from "@/lib/i18n";

type Entry = { id: number; question: string; result?: AskResponse; error?: string };

const AGENT_LABEL: Record<string, string> = {
  guard: "Guard",
  analyst: "Analyst",
  fact_checker: "Fact-Checker",
  companion: "Companion",
};

/** "Ask the Match": a fan's question answered by the Analyst, checked by the Fact-Checker and
 *  personalised by the Companion (see docs/agents.md). */
export function AskPanel({ matchId, profileId, lang }: { matchId: string; profileId: string | null; lang: string }) {
  const s = askStrings(lang);
  const [question, setQuestion] = useState("");
  const [entries, setEntries] = useState<Entry[]>([]);
  const [busy, setBusy] = useState(false);

  async function ask(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const id = Date.now();
    setEntries((prev) => [{ id, question: q }, ...prev]);
    setQuestion("");
    setBusy(true);
    try {
      const result = await api.ask(matchId, { question: q, profile_id: profileId ?? "p-analyst-en" });
      setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, result } : e)));
    } catch (err) {
      setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, error: String(err) } : e)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label={s.title} className="rounded-lg bg-surface p-3">
      <h2 className="mb-2 text-sm font-semibold uppercase tracking-widest text-muted">{s.title}</h2>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void ask(question);
        }}
        className="flex gap-2"
      >
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={300}
          placeholder={s.placeholder}
          aria-label={s.placeholder}
          className="min-w-0 flex-1 rounded-md bg-surface-2 px-3 py-2 text-sm placeholder:text-muted"
        />
        <button
          type="submit"
          disabled={busy || !question.trim()}
          className="rounded-md bg-accent px-3 py-2 text-sm font-semibold text-background disabled:opacity-40"
        >
          {s.send}
        </button>
      </form>
      {!entries.length && (
        <div className="mt-2 flex flex-wrap gap-2">
          {s.suggestions.map((q) => (
            <button
              key={q}
              type="button"
              onClick={() => void ask(q)}
              className="rounded-full bg-surface-2 px-3 py-1 text-xs text-muted hover:text-foreground"
            >
              {q}
            </button>
          ))}
        </div>
      )}
      <ol className="mt-3 flex flex-col gap-3" aria-live="polite">
        {entries.map((e) => (
          <li key={e.id} className="text-sm">
            <p className="mb-1 font-semibold">{e.question}</p>
            {!e.result && !e.error && <p className="animate-pulse text-muted">{s.thinking}</p>}
            {e.error && <p className="text-warning">{s.failed}</p>}
            {e.result && <Answer result={e.result} lang={lang} />}
          </li>
        ))}
      </ol>
    </section>
  );
}

function Answer({ result, lang }: { result: AskResponse; lang: string }) {
  const s = askStrings(lang);
  const verified = result.verdict === "approve" && (result.source === "companion" || result.source === "analyst");
  return (
    <div data-testid="ask-answer" dir={result.lang === "ar" ? "rtl" : undefined}>
      <p className="rounded-md bg-surface-2 p-2 leading-relaxed">{result.answer}</p>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
        {verified && (
          <span className="rounded bg-accent/15 px-1.5 py-0.5 font-semibold text-accent" title={s.verifiedHint}>
            ✓ {s.verified}
          </span>
        )}
        <details>
          <summary className="cursor-pointer">{s.how}</summary>
          <ol className="mt-1 flex flex-col gap-1 ps-3" dir="ltr">
            {result.steps.map((step, i) => (
              <li key={i} className="font-mono">
                <span className="text-foreground">{AGENT_LABEL[step.agent] ?? step.agent}</span>
                {step.tool_calls?.length ? ` · tools: ${step.tool_calls.map((c) => c.tool).join(", ")}` : ""}
                {step.verdict ? ` · ${step.verdict}${step.by ? ` (${step.by})` : ""}` : ""}
                {step.problems?.length ? ` · ${step.problems.join("; ")}` : ""}
                {step.unchanged ? " · unchanged" : typeof step.grounded === "boolean" ? ` · ${step.grounded ? "grounded" : "not grounded"}` : ""}
                {typeof step.ms === "number" ? ` · ${step.ms} ms` : ""}
              </li>
            ))}
          </ol>
        </details>
      </div>
    </div>
  );
}
