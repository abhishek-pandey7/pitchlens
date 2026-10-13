import type { AskStep } from "@/lib/api";

const AGENT_LABEL: Record<string, string> = {
  guard: "Guard",
  analyst: "Analyst",
  fact_checker: "Fact-Checker",
  companion: "Companion",
  editor: "Editor",
  narrator: "Narrator",
  director: "Director",
  safety: "Safety",
};

/** The steps of one agent run, compact, for a fan who asks "how did you get this?". */
export function AgentSteps({ steps }: { steps: AskStep[] }) {
  return (
    <ol className="mt-1 flex flex-col gap-1 ps-3" dir="ltr">
      {steps.map((step, i) => (
        <li key={i} className="font-code text-[11.5px]">
          <span className="text-foreground">{AGENT_LABEL[step.agent] ?? step.agent}</span>
          {step.task ? ` · ${step.task}` : ""}
          {step.agent === "safety" ? ` · ${step.stage} ${step.allowed ? "passed" : `blocked (${(step.categories ?? []).join(", ")})`}` : ""}
          {step.lang && step.agent === "narrator" ? ` · ${step.lang}` : ""}
          {step.tool_calls?.length ? ` · tools: ${step.tool_calls.map((c) => c.tool).join(", ")}` : ""}
          {step.verdict ? ` · ${step.verdict}${step.by ? ` (${step.by})` : ""}` : ""}
          {step.problems?.length ? ` · ${step.problems.join("; ")}` : ""}
          {step.unchanged ? " · unchanged" : typeof step.grounded === "boolean" ? ` · ${step.grounded ? "grounded" : "not grounded"}` : ""}
          {typeof step.ms === "number" ? ` · ${step.ms} ms` : ""}
        </li>
      ))}
    </ol>
  );
}
