"use client";

import { api, type AgentRunTrace, type AskStep } from "@/lib/api";
import { usePoll } from "@/lib/usePoll";

const AGENT: Record<string, { label: string; tone: string }> = {
  guard: { label: "Guard", tone: "bg-muted" },
  analyst: { label: "Analyst", tone: "bg-home" },
  fact_checker: { label: "Fact-Checker", tone: "bg-warning" },
  companion: { label: "Companion", tone: "bg-accent" },
  editor: { label: "Editor", tone: "bg-away" },
  narrator: { label: "Narrator", tone: "bg-accent" },
};

const UNVERIFIED = new Set(["fallback", "guard", "facts", "dropped"]);

/** Agent Trace (T-129): every agent run for this match, newest first, with each step, the tools it
 *  called (and their arguments), the Fact-Checker's verdicts and timings. Polls the engine. */
export function AgentTrace({ matchId }: { matchId: string }) {
  const data = usePoll(matchId, () => api.agentTrace(matchId), 2000);
  const runs = data?.runs ?? [];
  return (
    <section aria-label="Agent trace" className="flex flex-col gap-3">
      <h2 className="text-sm font-semibold uppercase tracking-widest text-muted">Agent trace</h2>
      {!runs.length && (
        <p className="rounded-lg bg-surface p-3 text-sm text-muted">
          No agent runs yet. Ask a question below or from /watch, and each agent step appears here.
        </p>
      )}
      <ol className="flex flex-col gap-3">
        {runs.map((run) => (
          <Run key={`${run.at}-${run.question}`} run={run} />
        ))}
      </ol>
    </section>
  );
}

function Run({ run }: { run: AgentRunTrace }) {
  const verified = run.verdict === "approve" && !UNVERIFIED.has(run.source);
  return (
    <li className="rounded-lg bg-surface p-3 text-sm" data-testid="trace-run">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="font-mono">{new Date(run.at * 1000).toLocaleTimeString()}</span>
        <span>{run.profile_id}</span>
        <span className={`rounded px-1.5 py-0.5 font-semibold ${verified ? "bg-accent/15 text-accent" : "bg-warning/15 text-warning"}`}>
          {verified ? "verified" : run.source}
        </span>
        {run.revisions > 0 && <span className="text-warning">revised ×{run.revisions}</span>}
        <span className="ms-auto font-mono">
          {(run.ms / 1000).toFixed(1)} s · {run.tokens.toLocaleString()} tok
        </span>
      </div>
      <p className="font-semibold">{run.question}</p>
      <ol className="mt-2 flex flex-col gap-1.5 border-s border-surface-2 ps-3">
        {run.steps.map((step, i) => (
          <Step key={i} step={step} />
        ))}
      </ol>
      <p className="mt-2 rounded-md bg-surface-2 p-2" dir="auto">
        {run.answer}
      </p>
    </li>
  );
}

function Step({ step }: { step: AskStep }) {
  const agent = AGENT[step.agent] ?? { label: step.agent, tone: "bg-muted" };
  return (
    <li className="text-xs" data-testid={`trace-step-${step.agent}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`inline-block h-2 w-2 rounded-full ${agent.tone}`} aria-hidden />
        <span className="font-semibold text-foreground">{agent.label}</span>
        {step.task && <span className="text-muted">{step.task}</span>}
        {step.agent === "narrator" && step.lang && <span className="text-muted">{step.lang}</span>}
        {step.verdict && (
          <span className={step.verdict === "approve" ? "text-accent" : "text-warning"}>
            {step.verdict}
            {step.by ? ` · ${step.by}` : ""}
          </span>
        )}
        {step.unchanged ? (
          <span className="text-muted">unchanged</span>
        ) : typeof step.grounded === "boolean" ? (
          <span className={step.grounded ? "text-accent" : "text-warning"}>{step.grounded ? "grounded" : "not grounded"}</span>
        ) : null}
        {typeof step.ms === "number" && <span className="ms-auto font-mono text-muted">{step.ms} ms</span>}
      </div>
      {step.tool_calls?.map((call, i) => (
        <div key={i} className="ms-4 font-mono text-muted">
          ↳ {call.tool}({Object.entries(call.args).filter(([, v]) => v != null).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})
          {call.error ? <span className="text-warning"> · {call.error}</span> : null}
        </div>
      ))}
      {step.problems?.map((p, i) => (
        <div key={i} className="ms-4 text-warning">
          ⚠ {p}
        </div>
      ))}
    </li>
  );
}
