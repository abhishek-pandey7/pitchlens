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
result ("press rate rose 0.66"). Any number in the answer that isn't supported, at the precision it is
written, is flagged (`AgentRun.unsupported`). Unrelated numbers never combine, so an invented "41 shots"
is caught. A number check alone cannot catch a real number attached to the wrong fact ("Southvale
scored 7 goals" when 7 is Northbridge's shots); that is the Fact-Checker agent's job (T-124).

Failures are soft. A provider error, spent rate budget or malformed tool call returns an `AgentRun` with
`error` set and no text, and callers fall back. Each run produces a `trace()`: question, answer, tool
calls with arguments and timings, grounding verdict and tokens, for the Agent Trace panel.

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

`tests/unit/test_agents.py`: tool correctness on the comeback match, argument errors logged in the
ledger, grounding of answers, offline and scripted agents, recovery from a bad tool call, and soft
failures. `tests/integration/test_mcp.py`: an in-memory MCP client, and a real client against the
engine's mounted `/mcp` endpoint.
