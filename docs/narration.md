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

## Template text (`templates/*.yaml`)

Every headline key the detectors emit has a template in **English, Spanish, French, Portuguese, Arabic and Hindi** (30+ keys, kept in lockstep by a test).

- `headline` is shown to everyone. `detail` sentences are added for analysts and tacticians, or anyone with long verbosity.
- Placeholders are filled only from evidence and entity names: `{player}`, `{team}`, `{score}`, `{xg}`, `{win_prob_from_pct}`, `{ppda_to}`, and so on.
- A detail sentence whose evidence is missing is **dropped**, never guessed.
- Numbers are formatted per locale: decimal comma in es, fr and pt, `34 %` in French, and a sensible precision per metric.

Rendering takes microseconds, so overlays never wait for an LLM.

## Persona prompts (`prompts.py`)

- **System prompt:** persona + language + verbosity + football glossary (`personalize/glossary/*.yaml`) + ground rules. It is **byte-identical for every moment** with those settings, so providers can cache it.
- **User message:** JSON with `MINUTE`, `SCORE`, `ENTITIES`, `EVIDENCE`, `why_it_matters` and a `REFERENCE` line (the template text). The model may restyle the reference, but may not add facts it lacks.
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

The interface is `LLMProvider.generate(LLMRequest) -> LLMResponse` with `fast` and `quality` tiers. There are no sampling parameters: current Claude models reject them, and grounding is enforced by the validator anyway.

**Auth:** an API key if set (`ANTHROPIC_FOUNDRY_API_KEY` / `AZURE_OPENAI_API_KEY`), otherwise **Microsoft Entra ID** through `azure-identity` (managed identity in Azure, `az login` locally), so the deployment carries no secrets.

**Failures:** refusals, truncation, HTTP errors and timeouts become `LLMError`. The narrator then serves the grounded template text, so a provider outage never blanks the broadcast. Unit tests cover the request shape and every failure path with fake SDK clients.

**Live check** (needs credentials): `python engine/scripts/llm_smoke.py` narrates the comeback's top moments for three fans. It reports how many lines came from the model, retries, template fallbacks, provider errors and latency.
