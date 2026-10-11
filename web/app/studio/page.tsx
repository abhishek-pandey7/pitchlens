import { Suspense } from "react";
import { StudioClient } from "./StudioClient";

export const metadata = { title: "Studio · PitchLens" };

// /studio?match=…&profile=… — the producer's console.
export default function StudioPage() {
  return (
    <Suspense fallback={<div className="p-8 text-muted">Loading…</div>}>
      <StudioClient />
    </Suspense>
  );
}
