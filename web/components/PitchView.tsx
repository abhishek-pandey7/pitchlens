"use client";

// The live pitch, in 3D or 2D. 3D needs WebGL and loads three.js only when it is shown; the 2D canvas
// is the fallback, and the viewer's choice is remembered on this device.
import dynamic from "next/dynamic";
import { useEffect, useState, useSyncExternalStore } from "react";
import { PitchCanvas } from "@/components/PitchCanvas";
import type { MatchMeta, TrackingFrame } from "@/lib/types";

const Pitch3D = dynamic(() => import("@/components/Pitch3D").then((m) => m.Pitch3D), {
  ssr: false,
  loading: () => <div className="aspect-video w-full animate-pulse rounded-lg bg-[#1c5631]" />,
});

type Mode = "3d" | "2d";
type Camera = "follow" | "wide";
const KEY = "pitchlens.pitch";

type Props = { frame: TrackingFrame | null; meta: MatchMeta | null; focusPlayerId?: string | null };

function readStored(): { mode?: Mode; camera?: Camera } {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

const noSubscribe = () => () => {};

let webglChecked: boolean | null = null;

function hasWebGL(): boolean {
  // Once per page: every probe opens a GL context, and browsers cap how many may be open.
  if (webglChecked === null) {
    try {
      const gl = document.createElement("canvas").getContext("webgl2");
      gl?.getExtension("WEBGL_lose_context")?.loseContext();
      webglChecked = Boolean(gl);
    } catch {
      webglChecked = false;
    }
  }
  return webglChecked;
}

export function PitchView({ frame, meta, focusPlayerId }: Props) {
  // Checked once in the browser; the server render assumes 2D.
  const webgl = useSyncExternalStore(noSubscribe, hasWebGL, () => false);
  const [mode, setMode] = useState<Mode>("3d");
  const [camera, setCamera] = useState<Camera>("follow");

  useEffect(() => {
    const stored = readStored();
    if (stored.mode) setMode(stored.mode); // eslint-disable-line react-hooks/set-state-in-effect
    if (stored.camera) setCamera(stored.camera);
  }, []);

  const remember = (next: { mode?: Mode; camera?: Camera }) => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ mode, camera, ...next }));
    } catch {
      // private windows: the choice lasts for this page only
    }
  };

  const shown: Mode = webgl ? mode : "2d";
  return (
    <figure className="flex flex-col gap-2">
      {shown === "3d" ? (
        <Pitch3D frame={frame} meta={meta} focusPlayerId={focusPlayerId} camera={camera} />
      ) : (
        <PitchCanvas frame={frame} meta={meta} focusPlayerId={focusPlayerId} />
      )}
      {webgl && (
        <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <Segmented
            label="Pitch view"
            value={mode}
            options={[["3d", "3D"], ["2d", "2D"]]}
            onChange={(v) => {
              setMode(v);
              remember({ mode: v });
            }}
          />
          {shown === "3d" && (
            <Segmented
              label="Camera"
              value={camera}
              options={[["follow", "Follow the ball"], ["wide", "Whole pitch"]]}
              onChange={(v) => {
                setCamera(v);
                remember({ camera: v });
              }}
            />
          )}
        </figcaption>
      )}
    </figure>
  );
}

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-md bg-surface p-0.5">
      {options.map(([v, text]) => (
        <button
          key={v}
          type="button"
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={`rounded px-2.5 py-1 ${value === v ? "bg-background font-semibold shadow-sm" : "text-muted hover:text-foreground"}`}
        >
          {text}
        </button>
      ))}
    </div>
  );
}
