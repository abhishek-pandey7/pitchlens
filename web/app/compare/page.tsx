import { Suspense } from "react";
import { CompareClient } from "./CompareClient";

export const metadata = { title: "Compare · PitchLens" };

// /compare?match=…&a=…&b=… — one match, two fans: the same moments as two different experiences.
export default function ComparePage() {
  return (
    <Suspense fallback={<div className="p-8 text-muted">Loading…</div>}>
      <CompareClient />
    </Suspense>
  );
}
