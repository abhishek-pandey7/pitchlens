"use client";

// Broadcast graphics are laid out once on a fixed 1920x1080 canvas and scaled to whatever box they are
// shown in (OBS full screen, a studio preview, a phone): one layout, pixel-consistent at every size.
import { useEffect, useRef, useState } from "react";

export const STAGE_WIDTH = 1920;
export const STAGE_HEIGHT = 1080;

export function ScaledStage({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  const outer = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);

  useEffect(() => {
    const el = outer.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setScale(entry.contentRect.width / STAGE_WIDTH));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={outer} className={`relative w-full overflow-hidden ${className}`} style={{ aspectRatio: `${STAGE_WIDTH} / ${STAGE_HEIGHT}` }}>
      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{ width: STAGE_WIDTH, height: STAGE_HEIGHT, transform: `scale(${scale})` }}
      >
        {children}
      </div>
    </div>
  );
}
