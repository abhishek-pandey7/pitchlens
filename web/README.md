# PitchLens web

Next.js front end for PitchLens: broadcast overlay (`/overlay`), fan second screen (`/watch`), side-by-side demo (`/compare`), analyst view (`/analyst`), producer console (`/studio`) and multi-match view (`/matchday`).

```bash
npm install
npm run dev          # http://localhost:3000
npm run gen:types    # regenerate lib/types.ts after running `python -m pitchlens.schema.export_jsonschema` in ../engine
```

Types in `lib/types.ts` are generated from the engine's Pydantic schemas. Do not edit them by hand.

## Environment

Both are read at build time (`next build`, or when `next dev` starts) and compiled into the browser bundle.

| Variable | Default | What |
|---|---|---|
| `NEXT_PUBLIC_API_BASE` | `http://localhost:8000/api/v1` | The engine's REST API |
| `NEXT_PUBLIC_STREAM_BASE` | the API base | Where live streams (`/matches/{id}/stream`) are opened: the Rust broadcast edge, e.g. `http://localhost:8080/api/v1` (see [../edge/README.md](../edge/README.md)). Leave it unset to stream from the engine directly |
