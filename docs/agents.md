# Agent layer

PitchLens keeps a **deterministic hot path with an agentic warm path** (plan §10A). Overlay cues never
wait on an agent. Agents add reasoning, conversation and curation alongside the live pipeline, and
**every number they state comes from a tool** that wraps the deterministic engine.

Built on **Microsoft Agent Framework (Python)**, pinned at `agent-framework-core 1.21.0`,
`agent-framework-openai 1.16.0` and `agent-framework-orchestrations 1.3.1`. The **PitchLens MCP server**
(`mcp 2.3.0`) exposes the same tools to any Model Context Protocol client.

| Module | Role |
|---|---|
| `pitchlens/agents/tools.py` | Analyst tools: typed, read-only views over a live or stored match. A `Ledger` records every call |
| `pitchlens/agents/runtime.py` | Builds Agent Framework agents over the tools; `run_agent` runs one turn and checks the answer's numbers against the ledger |
| `pitchlens/agents/clients.py` | Chat clients per tier from `LLM_PROVIDER`; shared free-tier rate budget; `OfflineChatClient` for tests |
| `pitchlens/agents/factcheck.py` | Fact-Checker: rules first, then a model judge with a structured verdict |
| `pitchlens/agents/ask.py` | "Ask the Match": an Agent Framework workflow of guard → analyst → fact-checker → companion |
| `pitchlens/agents/notes.py` | Analyst tactical notes: agent-authored insights in the live feed |
| `pitchlens/agents/desk.py` | Shared match-view cache, concurrency limit, recent agent traces per match |
| `pitchlens/mcpserver/` | MCP server: mounted on the engine at `/mcp`, or over stdio |

## Analyst tools

| Tool | Answers |
|---|---|
| `get_match_state` | Score, minute, team stats, win probability, momentum, control index |
| `get_metric(metric)` | One team metric now: possession, field_tilt, ppda, press_rate, momentum, xg, win_probability, control, tempo |
| `get_window(metric, from_minute, to_minute)` | Start, end, min, max and mean of a metric over a stretch of the match |
| `compare_periods(metric, a_from, a_to, b_from, b_to)` | Mean per team in two periods and the change ("how did the press change after 60'?") |
| `win_prob_at(minute)` | Score and win probability at a past minute |
| `what_if(home_goals, away_goals)` | Counterfactual win probability with a different scoreline |
| `get_player_card(player)` | One player's match: involvement, passing, shooting, defending, running |
| `top_players(stat, team?)` | Leaders for a stat (goals, xg, key_passes, tackles, distance_km, top_speed_kmh, ...) |
| `get_insights(type?, team?, since_minute?)` | Detected key moments with compact evidence ("win_prob Southvale 0.052→0.963") |

The tools accept team names or club ids, and player full names, surnames or ids. They validate their
own arguments and report problems ("unknown metric 'shots'; use one of ...", "'Ogunleye' is ambiguous:
Tariq Ogunleye, Diego Ogunleye", "distance_km needs tracking data"). Schemas use plain strings rather
than enums because some providers (Groq) reject a whole request when a generated argument breaks an
enum. Inside an agent these problems come back as a normal `{"unavailable": ...}` result, so the model
explains them rather than retrying until the framework disables tools.

## Grounded answers

`run_agent` collects every number the tools returned in a turn: values, percentage forms of 0..1 values,
numbers inside strings ("2-3", "90+4'", "0.052→0.963"), and differences between sibling values in one
result ("press rate rose 0.66"). A difference between two supported numbers that the answer itself
states also passes ("from 1.18 to 1.0, a drop of 0.18"). Any number in the answer that isn't supported, at the precision it is
written, is flagged (`AgentRun.unsupported`). Unrelated numbers never combine, so an invented "41 shots"
is caught. A number check alone cannot catch a real number attached to the wrong fact ("Southvale
scored 7 goals" when 7 is Northbridge's shots); that is the Fact-Checker agent's job (T-124).

Failures are soft. A provider error, spent rate budget or malformed tool call returns an `AgentRun` with
`error` set and no text, and callers fall back. Each run produces a `trace()`: question, answer, tool
calls with arguments and timings, grounding verdict and tokens, for the Agent Trace panel.

## Fact-Checker (reflection)

Two stages, cheapest first:
1. **Rules.** Any number not backed by a tool result means `revise` with the numbers listed, and no
   model call is needed.
2. **Judge.** A model reads the draft beside FACTS (the tool results, compact JSON) and returns a
   structured verdict (`approve` / `revise` with problems / `reject`). It checks the right team, player,
   metric, direction and moment. If FACTS is empty it rejects any claim about the match, including
   "the data is missing", which a model can otherwise say without looking.

If the judge call fails, a draft whose numbers all check out is still approved, marked
`by: "rules (judge unavailable)"`. Live on Groq, each judgement costs about 800 tokens:

| Draft | Number check | Judge |
|---|---|---|
| "Southvale scored 7 goals and won." (7 is Northbridge's shots) | passes | **revise**: "FACTS show Southvale City scored 3 goals" |
| "Southvale won 3-2 with 2.07 xG against Northbridge's 1.5." | passes | approve |
| "Northbridge had the better xG, 2.07 to 1.5." | passes | **revise**: "the xG values are reversed" |

## Ask the Match

`POST /api/v1/matches/{id}/ask` `{question, profile_id}` runs an Agent Framework workflow
(`WorkflowBuilder`, conditional edges):

```
guard ──► analyst ──► fact_checker ──approve──► companion ──► answer
             ▲              │
             └───revise─────┤ (one revision round)
                            └──reject / failure──► fallback ──► honest "couldn't verify"
```

- **Guard:** fan text is data, never instructions. It must be at most 300 characters, control
  characters are stripped, and it only ever appears in a user message.
- **Analyst:** the tool-using agent above, answering in English.
- **Fact-Checker:** the reflection loop. A revision feeds the problems back, and the analyst keeps the
  facts it already gathered.
- **Companion:** rewrites the approved answer for the fan's persona and language (no tools, no new
  facts; probabilities as percentages). Its numbers are checked again. If they fail, or the rewrite
  fails, the fan gets the verified English answer. English analysts skip this step.
- **Fallback:** "Sorry, I couldn't verify an answer to that from the match data", in the fan's language.

Agents read the match **as the fan sees it**. If a live session is running, answers reflect the
replay's current minute, so asking at 60' never reveals the 90th-minute winner. Stored matches with no
live session are answered at full time.

If the analyst's tool loop fails after gathering facts (for example a provider rejecting a forced
text-only turn), one **closing call with no tools** answers from the facts gathered so far.

The response carries `answer`, `lang`, `source` (companion / analyst / fallback / guard), `verdict`,
`revisions`, `ms`, `tokens` and every `step` (tool calls, verdicts, grounding).
`GET /api/v1/matches/{id}/agents/trace` returns the most recent 25 runs for the Agent Trace panel. At most
`ASK_CONCURRENCY` (default 2) questions run at once; others queue for up to 2 minutes, then get a 503.

In `/studio`, the **Agent trace** panel polls `/agents/trace` and shows every run as a timeline: each agent, the tools it called with their arguments, the Fact-Checker's verdict and problems, and timings and tokens. The producer can ask questions from the same console.

In `/watch`, the **Ask the match** panel offers three suggested questions in the fan's language, marks
approved answers "✓ Verified", and shows "How we answered" (each agent, the tools it called, the
verdict and timings).

**Live check (Groq):**

| Fan | Question | Outcome |
|---|---|---|
| p-casual-es | ¿Cómo remontó Southvale el partido? | Analyst → approved → Spanish rewrite (9 s) |
| p-casual-hi | सबसे तेज़ शॉट किसका था? | `top_players(max_shot_speed_kmh)` → "Bilal Okafor, 108.3 km/h" in Hindi (3 s) |
| p-analyst-en | Did Northbridge press less after the 60th minute? | Round 1 invented "0.418" → **revise** → round 2 cited the tool's own change → approved |

## Tactical notes (Analyst in the live feed)

The rule detectors fire on clear signals. The Analyst looks for the pattern **behind** them and adds it to
the live feed as a `tactical_note` insight (`author: "agent:analyst"`, key `tactical_note.agent`). It is
then personalised, narrated in each fan's language and eligible for the lower third, like any insight.

```
trigger ──► analyst (tools) ──► number check ──► fact_checker ──approve──► live feed
                 ▲                                    │
                 └──────────── revise (once) ─────────┘   otherwise dropped, with the reason in the trace
```

- **Triggers:** half-time, and tactical moments (momentum, pressing, control, xG and win-probability
  swings) with salience ≥ 0.4.
- **Budget:**
  - at least `NOTE_MIN_MATCH_MIN` (10) match minutes and `NOTE_MIN_GAP_S` (45) real seconds apart;
  - at most `NOTE_MAX` (5) per match;
  - one at a time;
  - never during a seek's catch-up.
- **Switch:** `AGENT_NOTES=auto|on|off`. The default `auto` turns notes on when a real model is
  configured and off for the offline mock.
- **Reply format:** Groq does not combine tool calls with structured output, so the Analyst replies with
  JSON text (`{found, team, note}`). A plain-prose reply is taken as the note; it is still checked and
  judged.
- **Templates:** "Analysis: {note}" in all six languages. The LLM narrator rewrites the note in each
  fan's language. Numbers inside the note are allowed by narration grounding because the note is
  evidence.
- **Traces:** every attempt appears in the Agent Trace panel (`kind: "tactical_note"`), including
  dropped notes and why they were dropped.

**Live check (Groq, comeback replayed at 30×):**
- **22': published.** "Northbridge's press rate jumped from 0.56 in the first 15 minutes to 1.54
  between minutes 20–25", from one tool call, approved in 5.7 s.
- **Half-time: dropped by the Fact-Checker.** "The draft says 'in the second half', but the press_rate
  data compares two intervals within the first half." A number check alone could not have caught that.

**Second run (from 40' at 20×), after adding a revision round:**
- **Half-time: published.** "Northbridge have lifted their press_rate from 0.56 in the first 15 minutes
  to 1.40 in minutes 30–45, a rise of 0.84".
- **74' and 90+6': dropped,** over honest arithmetic: "1.18 to 1.0, a drop of 0.18"; "35.44 to 27.09,
  about 8.3 points".

That led to one more grounding rule, shared by all agents: a difference counts as supported when both of
its operands are supported numbers stated in the same text. A lone "0.18", or a wrong difference, is
still flagged.

## Models

`chat_client(tier)` follows the same `LLM_PROVIDER` switch as narration:

| `LLM_PROVIDER` | Client |
|---|---|
| `groq` (development) | `OpenAIChatCompletionClient` on Groq, `openai/gpt-oss-120b`, with the shared rate budget |
| `openai_compat` | Any OpenAI-compatible endpoint (`OPENAI_COMPAT_*`) |
| `azure_openai` (deployment) | `OpenAIChatCompletionClient` on Azure OpenAI / Microsoft Foundry, API key or Entra ID |
| `mock` or no credentials | `OfflineChatClient`: deterministic; calls `get_match_state` and answers from it |

Each turn is capped at 6 model round-trips and 8 tool calls. Narration and agents share **one** Groq
rate budget per process (30 requests and 8K tokens per minute on the free tier). An agent turn with
tools uses about 2–12K tokens, so on the free tier expect roughly one answer per minute. Agents wait up
to `AGENT_MAX_WAIT_S` (default 70 s) for budget. Azure deployments have no such limit.

**Live check (Groq, comeback match):** four analyst questions, every answer grounded.

| Question | Tools | Answer (abridged) |
|---|---|---|
| How did Southvale get back into this game after the hour? | 4 | Press rate 0.72 → 1.38; xG 1.38 → 2.04; momentum −0.01 → −0.17 |
| How did the pressing change after the 60th minute? | 2 | Northbridge's press rate fell from 1.0 to 0.7 while Southvale's rose from 0.5 to 2.5 |
| Who ran the furthest? | 1 | Distance data isn't available for this match (no tracking) |
| Win probability if Northbridge had scored once more? | 3 | 3-3: a certain draw (the match was over) |

## MCP server

The analyst tools plus `list_matches`, each taking a `match_id`. A running live session is read
directly. A stored match is analysed once (with tracking, about 7 s) and then cached.

**On the engine**, at `http://localhost:8000/mcp` (streamable HTTP, stateless). In Azure, set
`MCP_ALLOWED_HOSTS` to the public host name; DNS-rebinding protection stays on.

**Over stdio** for a local client: `python -m pitchlens.mcpserver` (serves `PITCHLENS_DATA_DIRS`).

VS Code / GitHub Copilot agent mode (`.vscode/mcp.json`):

```json
{
  "servers": {
    "pitchlens": { "type": "http", "url": "http://localhost:8000/mcp" }
  }
}
```

Then ask Copilot, for example, "Using pitchlens, who covered the most distance in SYN-NHR-SVC-7?".

## Tests

`tests/unit/test_ask.py`: the Fact-Checker's two stages, every workflow route (approve, revise then
approve, revise twice, reject, guard), the companion falling back when it invents a number, and the
closing call. `tests/integration/test_agents_api.py`: `/ask` and `/agents/trace` over HTTP.
`web/e2e/watch.spec.ts`: a Spanish fan asks a suggested question and gets a verified answer.
`tests/unit/test_agents.py`: tool correctness on the comeback match, argument errors logged in the
ledger, grounding of answers, offline and scripted agents, recovery from a bad tool call, and soft
failures. `tests/integration/test_mcp.py`: an in-memory MCP client, and a real client against the
engine's mounted `/mcp` endpoint.
