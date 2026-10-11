# Deploying PitchLens to Azure

One command with the Azure Developer CLI: `azd up` provisions everything in `infra/` (Bicep) and
deploys both containers. Without Azure, `docker compose up --build` runs the same images locally.

## What gets created

One resource group, `rg-<environment>`:

| Resource | Purpose |
|---|---|
| **Microsoft Foundry** (Azure AI Services account) | Model deployments `pitchlens-fast` (and `pitchlens-quality` if a different model is chosen), and **Azure AI Content Safety** on the same account. Key auth is disabled: Entra ID only. |
| **Azure Container Apps** (environment + 2 apps) | `engine`: FastAPI, the pipeline, agents and the MCP server (1 vCPU / 2 GiB, exactly 1 replica, since live sessions are in memory). `web`: Next.js (0.5 vCPU / 1 GiB, scales 0–3). |
| **Azure Container Registry** (Basic) | The two images, pulled with the managed identity (no admin user). |
| **Key Vault** (RBAC) | Third-party secrets (an optional Groq key), referenced by the engine as a Container Apps secret. |
| **Log Analytics + Application Insights** | Container logs and the engine's traces. |
| **User-assigned managed identity** | Runs both apps. Roles: AcrPull, Cognitive Services OpenAI User, Cognitive Services User (Content Safety), Key Vault Secrets User. |

The engine is configured entirely by environment variables set in `infra/resources.bicep`:
`LLM_PROVIDER=azure_openai`, the model endpoint and deployments, `CONTENT_SAFETY_ENDPOINT`,
`AZURE_CLIENT_ID` (so `DefaultAzureCredential` uses the managed identity), `CORS_ORIGINS` (the web app's
URL), `MCP_ALLOWED_HOSTS`, `TRUST_PROXY_HEADERS=1` and the Application Insights connection string.

## Before you start

1. Install the [Azure Developer CLI](https://aka.ms/azd-install) and Docker.
2. An Azure subscription. *Azure for Students* works; no credit card needed.
3. **Check model quota** in the region you pick: Azure AI Foundry portal → *Quota*, or the Azure portal →
   your subscription → *Usage + quotas*. The defaults need **50K tokens a minute of `gpt-4.1-mini`
   (Global Standard)**. Regions that usually have it include `eastus2`, `swedencentral` and `westus3`.
   If quota is short, lower the `modelCapacity` default in `infra/main.bicep`, or choose another model
   (below).

## Deploy

```bash
azd auth login
azd env new pitchlens-dev
azd env set AZURE_LOCATION swedencentral          # a region with quota
azd up                                            # about 10 minutes the first time
```

`azd up` runs `provision` (Bicep) and then `deploy` (build, push and roll out both images). The web image
is built with `NEXT_PUBLIC_API_BASE=${API_BASE_URL}`, the engine URL that provisioning outputs.

At the end azd prints the endpoints. They are also in `azd env get-values`:

| Output | What |
|---|---|
| `WEB_URL` | The app: `/watch`, `/studio`, `/overlay`, `/compare` |
| `API_BASE_URL` | The engine API (`/health`, `/health/ready`, `/docs`) |
| `MCP_URL` | The MCP endpoint for GitHub Copilot and other agents ([agents.md](agents.md#mcp-server)) |

Run `azd up` (or `azd provision`) a second time after the first deploy. That provision knows the real
engine image and adds the HTTP readiness probe on `/api/v1/health/ready`. The very first provision
creates the apps with a placeholder image and TCP probes only.

### Check it

```bash
curl "$(azd env get-value API_BASE_URL)/health/ready"
# {"status":"ready","checks":{"llm":{"provider":"azure_openai","configured":true},"content_safety":"azure",...}}
python engine/scripts/e2e_smoke.py --base-url "$(azd env get-value API_BASE_URL)"
```

Then re-run the agent evaluation against the deployed models (it reads the same environment):

```bash
export LLM_PROVIDER=azure_openai AZURE_OPENAI_ENDPOINT=$(azd env get-value AZURE_OPENAI_ENDPOINT) \
       AZURE_OPENAI_FAST_DEPLOYMENT=pitchlens-fast AZURE_OPENAI_QUALITY_DEPLOYMENT=pitchlens-fast
cd engine && python -m pitchlens.evals factcheck --fresh   # also: ask, director, then report
```

Your own `az login` identity has model and Content Safety access (granted to `AZURE_PRINCIPAL_ID`).

## Options

Set with `azd env set NAME value` before `azd up`:

| Variable | Default | Effect |
|---|---|---|
| `PITCHLENS_FAST_MODEL` / `PITCHLENS_FAST_MODEL_VERSION` | `gpt-4.1-mini` / `2025-04-14` | Fast-tier model |
| `PITCHLENS_QUALITY_MODEL` / `PITCHLENS_QUALITY_MODEL_VERSION` | same as fast | A second deployment for recaps if different |
| `PITCHLENS_LLM_PROVIDER` | `azure_openai` | `groq` or `mock` instead (no model calls with `mock`) |
| `GROQ_API_KEY` | – | Stored in Key Vault and referenced by the engine, only for `groq` |

## Cost

On Azure for Students credit, the main costs are the always-on engine and model usage:

- **Engine:** 1 vCPU / 2 GiB, always on. Roughly US$1.5–2 a day at Consumption prices.
- **Web:** scales to zero when idle.
- **Models:** pay per token. A full live match with agents is a few hundred thousand tokens, well under
  a dollar on `gpt-4.1-mini`. The engine's agent token caps (`AGENT_MATCH_TOKEN_CAP`,
  `AGENT_DAILY_TOKEN_CAP`) bound it.
- **Other:** Log Analytics, the registry and Key Vault cost cents a day.

Stop paying when you are done:

```bash
azd down --purge      # deletes the resource group and purges the Key Vault and AI account
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `InsufficientQuota` or a model deployment fails | Another region, a smaller `modelCapacity`, or another model/version available to your subscription |
| `/health/ready` reports `configured: false` | The engine is missing `AZURE_OPENAI_ENDPOINT`; check the app's environment in the portal |
| `401`/`403` from the models or Content Safety | Role assignments can take a few minutes to apply; restart the engine revision |
| The web app shows no matches | `API_BASE_URL` was empty when the web image was built: run `azd deploy web` again |
| CORS errors in the browser | `CORS_ORIGINS` must be the web app's URL (set by Bicep); custom domains need adding there |

## Local run with containers

```bash
docker compose up --build      # web on http://localhost:3000, engine on http://localhost:8000
```

`compose.yaml` reads `.env` if present (for example `LLM_PROVIDER=groq` and `GROQ_API_KEY`). Without it,
everything runs offline with the mock model and the local safety screen.

## Not yet in the templates

The plan's Event Hubs bus (T-71) and Cosmos DB store (T-72) are not implemented in the engine yet, so the
templates do not create them; the engine uses its in-memory bus and store.
