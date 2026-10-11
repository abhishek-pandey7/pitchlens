# Security

## Reporting a vulnerability

Please report security issues privately through GitHub's **private vulnerability reporting** (the
repository's *Security* tab, then *Report a vulnerability*), not in a public issue. Include steps to
reproduce and the impact you see. We aim to acknowledge reports within three working days.

## How PitchLens handles secrets

- **No secrets in code or in the repository.** Configuration comes from environment variables
  (`.env.example` lists them). The local `.env` is gitignored.
- **In Azure, managed identity first.** The engine reaches Microsoft Foundry / Azure OpenAI, Azure AI
  Content Safety, Event Hubs and Cosmos DB with its managed identity (Entra ID tokens), so most services
  need no key at all. The few remaining secrets (for example a third-party key) live in **Azure Key
  Vault** and reach the container as Container Apps secret references, never as plain values in the
  deployment templates.
- **Leak prevention.** gitleaks runs in CI on every push and pull request (with GitHub secret scanning and
  push protection on the repository). Engine and web dependencies are pinned.
- **Documented false positives only.** `.gitleaks.toml` allowlists exactly one value: the public ID of
  Azure's built-in *Key Vault Secrets User* role, which generic rules mistake for a key. The same finding
  was resolved as a false positive in GitGuardian.

## What the running service protects against

| Risk | Control | Where |
|---|---|---|
| Abuse of model-backed endpoints (cost, denial of service) | Per-client rate limits on `POST …/ask` and `…/recap` (20 a minute), writes (120), ingest (600), MCP (120); `429` with `Retry-After` | `api/ratelimit.py` |
| Runaway model spend | Agent token caps per match and per day; over a cap, agents stand down and the deterministic pipeline carries on | `agents/desk.py` (`CostCaps`) |
| Harmful fan input, prompt injection | Azure AI Content Safety (text analysis + Prompt Shields) and a local screen on every question and display name; fan text is only ever a user message, never a system prompt | `agents/safety.py`, `agents/ask.py` |
| Harmful or wrong generated output | Content safety on answers, recaps and tactical notes before display; every number checked against tool results; the Fact-Checker judges claims | `agents/safety.py`, `agents/factcheck.py` |
| Agents acting outside their remit | Agent tools are read-only, except the Director's cue tools, which act within the cue scheduler's rules; goals and red cards never wait for an agent | `agents/tools.py`, `render/programme.py` |
| Cross-origin misuse | CORS allow-list (`CORS_ORIGINS`), limited methods and headers; MCP host allow-list (`MCP_ALLOWED_HOSTS`) | `api/main.py` |
| Bad external events | Every ingested event is validated against the `events.v1` schema; rejects are reported, not applied | `ingest/validator.py` |

Health probes: `GET /api/v1/health` (liveness) and `GET /api/v1/health/ready` (readiness: the model
provider is configured; also reports content-safety mode and agent budget).

## Data

PitchLens uses **synthetic data only**: fictional clubs, players and matches. No real Premier League
match data, player data or trademarks are included or redistributed. Fan profiles are anonymous
(a persona, a language, favourite clubs and an optional display name) and are not linked to any personal
account. See [docs/responsible-ai.md](docs/responsible-ai.md).
