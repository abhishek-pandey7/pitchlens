// The PitchLens kernel in the browser: the same Rust the engine runs, compiled to WebAssembly and run in
// a worker (lib/kernel.worker.ts). Every call is async and never blocks the page. When the kernel isn't
// built or WebAssembly is unavailable, calls reject and features fall back to the engine.

export type BuiltTracking = {
  ms: Float64Array; // frame times, match ms
  period: Int8Array;
  ball: Float32Array; // frames x 3
  xy: Float32Array; // frames x players x 2, NaN while off the pitch
  players: number;
  frames: number;
  buildMs: number;
};

type Pending = { resolve: (v: never) => void; reject: (e: Error) => void };

let worker: Worker | null = null;
let nextId = 0;
const pending = new Map<number, Pending>();

function call<T>(op: string, args?: unknown): Promise<T> {
  if (typeof window === "undefined" || typeof Worker === "undefined" || typeof WebAssembly === "undefined") {
    return Promise.reject(new Error("the kernel needs a browser with WebAssembly"));
  }
  if (!worker) {
    worker = new Worker(new URL("./kernel.worker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (e: MessageEvent<{ id: number; ok: boolean; result?: unknown; error?: string }>) => {
      const p = pending.get(e.data.id);
      if (!p) return;
      pending.delete(e.data.id);
      if (e.data.ok) p.resolve(e.data.result as never);
      else p.reject(new Error(e.data.error));
    };
    worker.onerror = (e) => {
      for (const p of pending.values()) p.reject(new Error(e.message || "kernel worker failed"));
      pending.clear();
      worker?.terminate();
      worker = null;
    };
  }
  const id = nextId++;
  return new Promise<T>((resolve, reject) => {
    pending.set(id, { resolve: resolve as (v: never) => void, reject });
    worker!.postMessage({ id, op, args });
  });
}

/** The loaded kernel's version (rejects when the kernel isn't available). */
export const kernelVersion = () => call<string>("version");

/** Build a match's 5 Hz tracking from its packed input (`GET /matches/{id}/tracking-input`). */
export const buildTracking = (input: unknown) => call<BuiltTracking>("buildTracking", input);

/** What the kernel's futures model returns (the same fields the engine's Python binding returns). */
export type FuturesRaw = {
  paths: number;
  home: number;
  draw: number;
  away: number;
  scores: [number, number, number][];
  other: number;
  comeback: ["home" | "away", number, number] | null;
  next_goal: [number, number, number, number | null];
  goals_left: [number, number];
};

/** Run the futures model on a `FuturesInput` (`GET /matches/{id}/futures/input`), in the browser. */
export const simulateFutures = (input: unknown) => call<{ raw: FuturesRaw; ms: number }>("simulateFutures", input);

/** Minutes left including expected added time, as the engine estimates them for a what-if minute. */
export const remainingMinutes = (clockMinute: number, period: number) =>
  call<number>("remainingMinutes", [clockMinute, period]);
