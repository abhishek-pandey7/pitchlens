// Runs the PitchLens kernel (Rust, compiled to WebAssembly) off the main thread. The JS glue and the
// .wasm are static files under /kernel/ (npm run build:kernel), loaded at runtime so the app builds
// without Rust. Requests: { id, op, args }; replies: { id, ok, result } or { id, ok: false, error }.

type KernelModule = {
  default: (opts: { module_or_path: string }) => Promise<unknown>;
  version: () => string;
  simulateFutures: (input: unknown) => unknown;
  remainingMinutes: (clockMinute: number, period: number) => number;
  buildTracking: (input: unknown) => {
    ms: Float64Array;
    period: Int8Array;
    ball: Float32Array;
    xy: Float32Array;
    players: number;
    frames: number;
    free: () => void;
  };
};

let kernel: Promise<KernelModule> | null = null;

// Static files, fetched by the browser at runtime: not part of the bundle.
const GLUE = "/kernel/pitchlens_kernel.js";
const WASM = "/kernel/pitchlens_kernel_bg.wasm";

function load(): Promise<KernelModule> {
  kernel ??= (async () => {
    const mod = (await import(/* webpackIgnore: true */ /* turbopackIgnore: true */ GLUE)) as KernelModule;
    await mod.default({ module_or_path: WASM });
    return mod;
  })();
  return kernel;
}

self.onmessage = async (e: MessageEvent<{ id: number; op: string; args: unknown }>) => {
  const { id, op, args } = e.data;
  try {
    const k = await load();
    if (op === "version") {
      postMessage({ id, ok: true, result: k.version() });
    } else if (op === "buildTracking") {
      const started = performance.now();
      const t = k.buildTracking(args);
      const result = {
        ms: t.ms,
        period: t.period,
        ball: t.ball,
        xy: t.xy,
        players: t.players,
        frames: t.frames,
        buildMs: performance.now() - started,
      };
      t.free();
      // The arrays are copies out of WebAssembly memory: hand them over without another copy.
      postMessage({ id, ok: true, result }, { transfer: [result.ms.buffer, result.period.buffer, result.ball.buffer, result.xy.buffer] });
    } else if (op === "simulateFutures") {
      const started = performance.now();
      const raw = k.simulateFutures(args);
      postMessage({ id, ok: true, result: { raw, ms: performance.now() - started } });
    } else if (op === "remainingMinutes") {
      const [clock, period] = args as [number, number];
      postMessage({ id, ok: true, result: k.remainingMinutes(clock, period) });
    } else {
      postMessage({ id, ok: false, error: `unknown kernel op ${op}` });
    }
  } catch (err) {
    postMessage({ id, ok: false, error: String(err) });
  }
};
