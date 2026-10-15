# Agent layer

PitchLens keeps a **deterministic hot path with an agentic warm path** (plan §10A). Overlay cues never
wait on an agent. Agents add reasoning, conversation and curation alongside the live pipeline, and
**every number they state comes from a tool** that wraps the deterministic engine.

Where the agents sit in the whole system: [architecture diagram](architecture.html) ([image](architecture.png)).

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
| `pitchlens/agents/editor.py` | Editor recaps: an Agent Framework GroupChat (Editor, Analyst, Fact-Checker), then the Narrator per fan |
| `pitchlens/agents/narration.py` | Narrator and Localization agents: one line per fan group, written concurrently (fan-out / fan-in); see [narration.md](narration.md) |
| `pitchlens/agents/director.py` | Director: curates the programme feed with cue tools, in auto or approve mode |
| `pitchlens/render/programme.py` | Programme gate: pending cues, decisions within the scheduler's rules, the decision log |
| `pitchlens/agents/views.py` | Match views: live session, stored match, or the match as it stood at a whistle (half-time, full time) |
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
| `get_futures()` | The rest of the match simulated 100,000 times from now: result split, most likely final scores, the trailing side's chances of avoiding defeat and of winning, next goal ("Can Southvale still win?") |
| `get_player_card(player)` | One player's match: involvement, passing, shooting, defending, running |
| `top_players(stat, team?)` | Leaders for a stat (goals, xg, key_passes, tackles, distance_km, top_speed_kmh, ...) |
| `get_insights(type?, team?, since_minute?)` | Detected key moments with compact evidence ("win_prob Southvale 0.052→0.963") |
| `get_goals()` | Every goal: minute, team, scorer, assist and the score after it |
| `player_of_the_match(limit?)` | Player-of-the-match ranking with the stats behind each score (see below) |

Metric descriptions in the tool schema carry their units ("press_rate: pressing actions per opponent
pass"). Without them, models read press_rate as "presses per minute".

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

## Editor recaps (group chat)

Half-time and full-time recaps, with the player of the match, are written by a **GroupChat**
(`GroupChatBuilder` from `agent-framework-orchestrations`) led by the Editor:

```
brief: fact sheet from the tools (score, goals, key moments, player-of-the-match ranking)
  ─► Editor: plans 2–3 storylines; ends with "ANALYST: <requests>" or "ANALYST: none"
  ─► Analyst: supporting facts with the analyst tools          (only if the Editor asked)
  ─► Editor: drafts {headline, body, player_line}
  ─► Fact-Checker: rules (every number backed by a tool result), then the model judge
       approve ─► done · revise ─► Editor redrafts (up to 2 rounds) · reject ─► fact recap
```

- **Group chat:** every participant hears the whole conversation. The selection function follows the
  Editor: after its plan, the Analyst speaks only if the Editor asked for something. Termination comes
  from the Fact-Checker's verdict.
- **Participants:** custom group-chat executors (`Seat`) wrapping Agent Framework agents. Each turn
  keeps our runner's tool caps, the shared fact ledger, the free-tier budget and the trace.
- **Fact sheet:** four deterministic tool calls are recorded in the ledger before the chat starts, so
  the Fact-Checker can trace every number back to them.
- **Narrator:** the approved English recap is written once per match and stage. The Narrator then
  adapts it to each fan's persona, language and club ("tell it from Southvale's side"). Two checks
  follow: its numbers must appear in the facts, and the Fact-Checker reads the adaptation beside the
  verified recap. One revision is allowed; otherwise the fan gets the verified English recap. The
  English analyst with no club reads the Editor's text as written.
- **Fallback:** if no recap is approved, or no model is configured, the fan gets a plain **fact recap**
  (score, scorers, xG, possession, player of the match) in their language. It is always correct.
- **Stages:** `ht` is analysed from the stored events up to the first half's whistle, so it stays a
  half-time recap while the second half plays on. During a live replay, both stages answer 409 until
  the replay reaches that whistle. That keeps recaps spoiler-free, even from the cache.
- **Caching:** approved recaps are cached per match and stage, and per fan group (language, persona,
  clubs, verbosity). A failed chat is retried on the next request.
- **Player of the match:** points = 4×goals + 3×assists + 2×xG + 0.75×key passes + 0.1×progressive
  passes + 0.1×lines broken + 0.25×(tackles + interceptions) + 0.1×recoveries + 0.15×(blocks +
  clearances) + 0.2×distance km + 1.5 for the winning (or, at half-time, leading) side − 0.5×yellow −
  3×red. The ranking shows the stats behind each score, so the Editor can cite them.

**Live check (Groq gpt-oss-120b, comeback seed 7):**
- **Full time: approved after one revision** (19.5K tokens).
  - The judge caught "Yilmaz's goal assisted by Leon Becker"; the assist was Jonah Mensah's.
  - The Editor fixed it, and the redraft was approved.
  - The Spanish fan's version tells the comeback from Southvale's side.
- **Half-time: approved first time** (20K tokens). Spanish, Hindi and Arabic adaptations each passed
  the number check and the judge.
- **Fixes from these runs:**
  - The Editor once wrote "control index 70.3%", and the Narrator once said the home side held the
    ball (it was the away side). That led to the adaptation judge, and to prompt rules: "say which
    team and period every number belongs to"; "the control index is not a percentage".
  - The press_rate misreading led to units in the metric schema.
- **Time:** on Groq's free tier (8K tokens a minute) a recap takes 2–10 minutes, mostly waiting for
  rate-limit budget. On Foundry the same chat is a handful of model calls with no waiting.

## Director (programme feed, human in the loop)

Fan feeds stay deterministic: a cue airs the moment its insight lands, so overlays never wait on an agent.
One fan channel per match can be made the **programme**, the feed the studio puts on air. Its cues pass
through a gate (`render/programme.py`), and the Director decides what goes on air.

| Mode | What happens to a programme cue |
|---|---|
| `off` | Airs by the scheduler's rules, as on any feed |
| `auto` | Waits for the Director; its decisions apply at once |
| `approve` | The Director proposes; the producer airs, holds or drops it in `/studio` |

- **Tools:** `list_pending_cues` (the queue, busy regions, the per-minute budget, recent stories),
  `get_match_phase`, and the levers `schedule_cue`, `hold_cue` and `drop_cue`, each with a short reason
  the producer reads. These are the only write tools any agent has, and they act within the scheduler's
  rules: airing into a busy region or past the rate limit is refused, and the Director is told why.
- **Never waits:** goals and red cards (priority 90 and up) bypass the gate.
- **Expiry:** a pending cue expires after 3 minutes of match time; a story that missed its moment is not
  aired late.
- **Proposals:** in approve mode a proposal stands until the producer acts. The Director cannot flip it.
- **Fallback:** if the Director fails in auto mode, fresh cues air by the rules; cues it left undecided
  do too. In approve mode they wait for the producer.
- **Batching:** one Director turn decides every cue waiting at that moment. There is one turn at a time
  per match, at least `DIRECTOR_MIN_GAP_S` (6) apart; cues that arrive in between join the next turn.
  Held cues come back when new cues arrive. A seek cancels the turn and clears the queue.
- **Visibility:** every turn appears in the Agent Trace (`kind: "director"`) with its tool calls. Every
  decision (Director, producer or rules) is in the programme log in `/studio`.

**Live check (Groq gpt-oss-120b, comeback from 60' at 15×):**
- **Turns:** each was 2–3 tool calls of about 2.5K tokens. A typical one was `list_pending_cues` →
  `schedule_cue("Fresh tactical update, screen free, adds variety")`.
- **Approve mode:** the Director proposed holding "What a pass from Mensah to find Yilmaz" right
  after Yilmaz's goal: "avoid back-to-back Yilmaz stories".
- **Expiry:** two turns waited 30–40 s for free-tier budget, and their cues expired instead of airing
  late.
- **Fix:** the Director once re-proposed a cue it had already proposed, so proposals are now fixed
  until the producer acts.

## Content safety

`agents/safety.py` screens fan input (questions, display names) and generated output (answers, recaps,
tactical notes) before anything acts on it or shows it.

- **Azure AI Content Safety** (`CONTENT_SAFETY_ENDPOINT`): text analysis (hate, sexual, violence,
  self-harm) and **Prompt Shields** for jailbreaks, with a key or managed identity. Fan text is blocked at
  medium severity and up; our own output at low and up.
- **Local screen**, always on: prompt injection, self-harm and violent threats against people. It is
  tuned for football talk, so "shoot", "attack the left side" and "kill the game off" pass, while "how do
  I hurt the referee" does not. It is the fallback when Azure is not configured or unreachable.
- **Outcomes:** a blocked question gets a friendly message in the fan's language and never reaches a
  model; an unsafe answer becomes "couldn't verify"; an unsafe recap becomes the fact recap; an unsafe
  tactical note is dropped. Each check appears in the trace as a `safety` step.

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

## Evaluation

`docs/eval.md` has the measured results (T-130): Fact-Checker precision and recall on 100 drafts with
seeded hallucinations, 30 Ask the Match questions with expected answers computed from the tools, and the
Director's pacing against the rules alone. Run with `python -m pitchlens.evals {factcheck|ask|director}`.

## Tests

`tests/unit/test_ask.py`: the Fact-Checker's two stages, every workflow route (approve, revise then
approve, revise twice, reject, guard), the companion falling back when it invents a number, and the
closing call. `tests/integration/test_agents_api.py`: `/ask` and `/agents/trace` over HTTP.
`web/e2e/watch.spec.ts`: a Spanish fan asks a suggested question and gets a verified answer.
`tests/unit/test_director.py`: the gate's rules (open gate, must-show cues, refusals with reasons,
hold, drop, expiry, fallback, fixed proposals) and Director turns on a live replay in auto mode,
approve mode and with a failing model. `web/e2e/studio.spec.ts`: the producer approves a proposal.
`tests/unit/test_editor.py`: the group chat's speaker order (with and without the Analyst), the
revision loop, invented numbers never approved, a failing Editor, the Narrator's checks and retry, the
fact recap in Hindi, and spoiler-free stage views. `tests/integration/test_agents_api.py` also covers
`/recap` (cache, 404, 409, 422), and `web/e2e/watch.spec.ts` covers a recap that waits for the whistle.
`tests/unit/test_agents.py`: tool correctness on the comeback match, argument errors logged in the
ledger, grounding of answers, offline and scripted agents, recovery from a bad tool call, and soft
failures. `tests/integration/test_mcp.py`: an in-memory MCP client, and a real client against the
engine's mounted `/mcp` endpoint.
