# PitchLens

**Explainable, personalized match intelligence from synthetic football events, built on Azure.**

PitchLens ingests synthetic match events as they happen and turns them into stats, key moments and the reasons those moments matter. It renders the results as timed broadcast overlays, personalized for each fan (persona, language, favorite club or player). A deterministic analytics pipeline computes every number. A team of AI agents (Microsoft Agent Framework on Microsoft Foundry) analyzes, narrates, localizes, fact-checks and directs what goes on screen.

> Built for the **Inside the Game: Developer Hackathon** (Microsoft × Premier League), challenge *Synthetic Match Insights Engine for Premier League Studio*.
> **All data is synthetic.** No real Premier League match data, players or trademarks are used or redistributed.

## Status

🚧 In active development. See [`PROJECT_PLAN.md`](PROJECT_PLAN.md) for the full design, task checklist and timeline.

## Pipeline

```
Ingest → Interpret → Explain → Render → Personalize
(events)  (metrics)  (insights) (overlay cues) (per-fan feeds, languages, voice)
```

## Repository layout

| Path | What |
|---|---|
| `engine/` | Python engine: simulator, metrics, insight detection, agents, API |
| `web/` | Next.js apps: `/overlay`, `/watch`, `/analyst`, `/studio`, `/compare` |
| `data/` | Synthetic rosters and generated matches |
| `docs/` | Schemas, metrics, overlay contract, dataset, agents |
| `infra/` | Bicep + `azd` deployment to Azure |

## Quickstart (local, fully offline)

```bash
# Engine
cd engine
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash; use .venv/bin/activate on macOS/Linux
pip install -e ".[dev]"
pytest
python scripts/e2e_smoke.py   # whole system, start to finish (33 checks)

# API (http://localhost:8000/docs) - see docs/api.md
uvicorn pitchlens.api.main:app --reload --port 8000

# Web
cd ../web
npm install
npm run dev
```

## License

[MIT](LICENSE)
