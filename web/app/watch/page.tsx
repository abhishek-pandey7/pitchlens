import { Suspense } from "react";
import { WatchClient } from "./WatchClient";

export const metadata = { title: "Watch · PitchLens" };

// The client reads ?match= and ?profile= with useSearchParams, which needs a Suspense boundary.
export default function WatchPage() {
  return (
    <Suspense fallback={<div className="p-8 text-muted">Loading…</div>}>
      <WatchClient />
    </Suspense>
  );
}
