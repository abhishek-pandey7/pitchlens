# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security problems.
Report them privately through GitHub's **"Report a vulnerability"** button (Security tab → Advisories) on this repository.
We aim to acknowledge reports within 72 hours.

## Secrets

- No credentials are stored in this repository. Azure services are accessed with **managed identity**; any remaining secrets live in **Azure Key Vault**.
- Local development uses `.env` files, which are git-ignored. See `.env.example` for the variable names.
- CI runs secret scanning (gitleaks) on every push.

## Data

PitchLens uses **synthetic, football-realistic data only**. No real Premier League match data, player data or trademarks are included or redistributed.
