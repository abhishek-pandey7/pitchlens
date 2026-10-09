# PitchLens web

Next.js front end for PitchLens: broadcast overlay (`/overlay`), fan second screen (`/watch`), side-by-side demo (`/compare`), analyst view (`/analyst`), producer console (`/studio`) and multi-match view (`/matchday`).

```bash
npm install
npm run dev          # http://localhost:3000
npm run gen:types    # regenerate lib/types.ts after running `python -m pitchlens.schema.export_jsonschema` in ../engine
```

Types in `lib/types.ts` are generated from the engine's Pydantic schemas. Do not edit them by hand.
