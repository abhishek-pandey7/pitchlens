import { Suspense } from "react";
import { OverlayClient } from "./OverlayClient";

export const metadata = { title: "Overlay · PitchLens" };

// /overlay?match=…&profile=…[&pitch=1] — transparent 16:9 graphics for OBS or a rendering partner.
export default function OverlayPage() {
  return (
    <Suspense fallback={null}>
      <OverlayClient />
    </Suspense>
  );
}
