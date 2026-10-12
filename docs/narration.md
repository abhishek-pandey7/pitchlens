# PitchLens Narration: facts first, words second

Insights carry evidence ([insights.md](insights.md)). Narration turns one insight into one line for one fan, in their language and persona, **without adding a single fact the evidence doesn't contain**.

Source: `engine/pitchlens/narrate/`.

```
Insight ─► template text (instant, 6 languages, grounded by construction)
        └► LLM rewrite for persona and language ─► grounding validator ─► pass: use it
                                                                       └► fail: one retry with the violations
                                                                                 └► fail again: template text
```

A narration never reaches a viewer without passing grounding.

## Narrator and Localization agents (T-123)

Live lines are written by agents (`engine/pitchlens/agents/narration.py`). When an insight lands, the
session hands **every fan it reaches** to the narration worker at once. The worker keeps its cache,
in-flight de-duplication and load shedding per (persona, language, verbosity) group, and the groups
still to write go to one **Agent Framework concurrent workflow** (`ConcurrentBuilder`):

```
insight ──► Narrator (casual · es) ─────────────────────────────┐
       ├──► Narrator (analyst · en) ─────────────────────────────┤──► one line per group (fan-in)
       └──► Narrator (casual · hi, pivot en) ──► Localization ───┘
```

- **Narrator:** the same prompt as below (persona, glossary, ground rules, the template as a
  reference). One retry with the validator's violations.
- **Localization:** for pivot languages (`NARRATION_PIVOT_LANGS`, default Arabic and Hindi), the
  Narrator writes in English and the Localization agent transcreates the line: idiomatic phrasing,
  glossary terms, numbers kept as digits, names in Latin script. One retry, as above.
- Every line is validated **in its own language**; a group that fails keeps its template line.
- **Switch:** `NARRATION_AGENTS=auto|on|off`. `auto` uses the agents with Groq, Azure OpenAI / Foundry,
  OpenAI-compatible endpoints and the offline mock (which returns the reference line), and the direct
  provider path below for Claude on Foundry.

**Live check (Groq, the comeback's last ten minutes at 10×, four fans):**
- **The fan-out:** for the 85' equaliser one workflow wrote the Spanish, Hindi, Arabic and English
  analyst lines in parallel. Hindi and Arabic went through the Localization agent, read naturally,
  and kept the names in Latin script. Every line passed grounding.
- **Free-tier waits:** 7 of 13 group lines fell back to the template. Live lines wait at most 2 s for
  free-tier rate budget, the same bound as the direct path. Foundry has no such limit.

## Template text (`templates/*.yaml`)

Every headline key the detectors emit has a template in **English, Spanish, French, Portuguese, Arabic and Hindi** (30+ keys, kept in lockstep by a test).

- `headline` is shown to everyone. `detail` sentences are added for analysts and tacticians, or anyone with long verbosity.
- Placeholders are filled only from evidence and entity names: `{player}`, `{team}`, `{score}`, `{xg}`, `{win_prob_from_pct}`, `{ppda_to}`, and so on.
- A detail sentence whose evidence is missing is **dropped**, never guessed.
- Numbers are formatted per locale: decimal comma in es, fr and pt, `34 %` in French, and a sensible precision per metric.

Rendering takes microseconds, so overlays never wait for an LLM.

## Persona prompts (`prompts.py`)

- **System prompt:** persona + language + verbosity + football glossary (`personalize/glossary/*.yaml`) + ground rules. It is **byte-identical for every moment** with those settings, so providers can cache it.
- **User message:** JSON with `MINUTE`, `SCORE` (with team names, e.g. `Northbridge 1-0 Southvale`), `LEADER` (a team or `level`), `ENTITIES`, `EVIDENCE`, `why_it_matters` and a `REFERENCE` line (the template text). The model may restyle the reference, but may not add facts it lacks. The ground rules forbid claiming a lead change unless SCORE and LEADER show it (live tests showed small models getting the leader wrong from a bare `1-0`) and require names in Latin script, as given, in every language.
- **Output:** `{"text", "emphasis_entities"}`, requested as structured output where the provider supports it and parsed tolerantly otherwise (code fences and stray prose are handled).

Personas:

| Persona | Style |
|---|---|
| `casual` | Vivid, one key number at most |
| `analyst` | Numbers first, precise |
| `tactician` | The pattern behind the moment |
| `kids` | Simple, one emoji allowed |
| `fantasy` | What it means for the players involved |
| `accessibility` | Plain who / what / score, no symbols |

## Grounding validator (`grounding.py`)

**Allowed numbers:**
- every evidence value, from/to and window (probabilities may also appear as percentages);
- from/to differences;
- the score and the minute label;
- numbers written literally into that moment's template ("/100", "last 5 minutes").

**Matching:** each number in the text must equal an allowed value **rounded to the precision it is written with**. "5" may stand for 4.6; "0.03" may not stand for 0.05.

**Scripts:** Arabic-Indic and Devanagari digits are normalised. Ordinals are caught too ("12th", "2e", "12.º", "12वां"), which was a hole the test suite found.

**Players:** any player named in the text must be one of the insight's entities. Surnames are unique within a club but can repeat across squads, so a name passes if any player carrying it is involved.

**Verified:** every insight from five storylines, rendered in 6 languages × 2 personas (about 2,900 lines), passes the validator. Invented statistics, foreign players and invented ordinals are rejected.

## Providers

| `LLM_PROVIDER` | Module | Notes |
|---|---|---|
| `mock` (default) | `mock.py` | Offline and deterministic. Echoes the reference line; `faulty_first=N` invents a statistic to exercise retry and fallback |
| `foundry_claude` | `foundry_claude.py` | **Claude on Microsoft Foundry** via `anthropic.AsyncAnthropicFoundry`. Both tiers default to `claude-opus-5-5`; with thinking always on, tiers differ by **effort** (`low` for live lines, `medium` for recaps), and `max_tokens` leaves room for thinking. JSON via structured outputs (`output_config.format`). Safety-classifier refusals are retried on `claude-opus-5` by the SDK's `BetaRefusalFallbackMiddleware` (server-side fallbacks are not offered on Foundry) |
| `azure_openai` | `azure_openai.py` | `openai.AsyncAzureOpenAI` with a strict JSON-schema response format; fast and quality deployments |
| `groq` | `openai_compat.py` | **Free development tier.** Groq's `openai/gpt-oss-120b` for both tiers (in a 24-line live test, `20b` needed retries and wrote names in Devanagari; `120b` made no slips at ~0.9 s median) with strict JSON-schema output, `reasoning_effort` low/medium and the reasoning text hidden. Needs `GROQ_API_KEY` |
| `openai_compat` | `openai_compat.py` | Any OpenAI-compatible endpoint (Ollama, OpenRouter, GitHub Models) set by `OPENAI_COMPAT_*`; `json_schema`, `json_object` or no structured output |

The interface is `LLMProvider.generate(LLMRequest) -> LLMResponse` with `fast` and `quality` tiers. There are no sampling parameters: current Claude models reject them, and grounding is enforced by the validator anyway.

**Auth:** an API key if set (`ANTHROPIC_FOUNDRY_API_KEY` / `AZURE_OPENAI_API_KEY`), otherwise **Microsoft Entra ID** through `azure-identity` (managed identity in Azure, `az login` locally), so the deployment carries no secrets.

**Development vs deployment:** we develop on `groq`, which is free, and keep Azure credits for the final deployment on Foundry. Switching is configuration only (`LLM_PROVIDER`). The prompts, grounding validator and template fallback are identical across providers, so lines that pass on Groq pass on Foundry.

**Free-tier rate limits:** Groq allows 30 requests and 8K tokens per minute. One narration is about 600 input tokens. Calls pass through a client-side `RateBudget`, a sliding one-minute window settled with real usage. A live line that would wait more than 2 s for budget gets its grounded template text immediately; a recap waits up to 30 s. `llm_smoke.py` raises the wait so every line reaches the model.

**Failures:** refusals, truncation, HTTP errors and timeouts become `LLMError`. The narrator then serves the grounded template text, so a provider outage never blanks the broadcast. Unit tests cover the request shape and every failure path with fake SDK clients.

**Live check** (needs credentials): `python engine/scripts/llm_smoke.py` narrates the comeback's top moments for three fans. It reports how many lines came from the model, retries, template fallbacks, provider errors and latency.
