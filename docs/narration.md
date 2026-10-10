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

## Providers (`provider.py`)

`LLMProvider.generate(LLMRequest) -> LLMResponse`, with two tiers: `fast` for live lines and `quality` for recaps. There are no sampling parameters, because current Claude models reject them. The `mock` provider echoes the reference line deterministically, so everything runs offline and in CI. Its `faulty_first=N` mode invents a statistic to exercise retry and fallback.

Azure OpenAI and Claude on Microsoft Foundry arrive in T-51. The Foundry default is `claude-opus-5-5` for both tiers, with lower effort for live lines, and the model is configurable.
