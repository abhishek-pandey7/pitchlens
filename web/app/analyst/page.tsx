import { Suspense } from "react";
import { AnalystClient } from "./AnalystClient";

export const metadata = { title: "Analyst · PitchLens" };

// /analyst?match=… reads the match with useSearchParams, which needs a Suspense boundary.
export default function AnalystPage() {
  return (
    <Suspense fallback={<div className="p-8 text-muted">Loading…</div>}>
      <AnalystClient />
    </Suspense>
  );
}
