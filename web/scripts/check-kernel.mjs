// Loads the browser build of the kernel in Node and checks it works:
//   node scripts/check-kernel.mjs                 version, and an empty match refused like the engine
//   node scripts/check-kernel.mjs DIR             also: build DIR/input.json and compare with the engine's
//                                                 arrays in DIR/{ms,period,ball,xy}.bin, byte for byte
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const kernel = await import(path.join(web, "public/kernel/pitchlens_kernel.js").replace(/\\/g, "/").replace(/^([A-Z]):/, "file:///$1:"));
kernel.initSync({ module: readFileSync(path.join(web, "public/kernel/pitchlens_kernel_bg.wasm")) });
console.log(`kernel ${kernel.version()}`);

const empty = { kind: [], flags: [], match_ms: [], period: [], team: [], player: [], related: [], recipient: [],
  duration_ms: [], start: [], end: [], top_speed_kmh: [], sides: [] };
// A match without events has no ball path: the kernel refuses it, as NumPy's interp does in the engine.
let refused = false;
try {
  kernel.buildTracking(empty);
} catch (e) {
  refused = /sample points is empty/.test(String(e));
}
if (!refused) throw new Error("an empty match should be refused with the engine's error");

const dir = process.argv[2];
if (dir) {
  const input = JSON.parse(readFileSync(path.join(dir, "input.json"), "utf8"));
  const t0 = performance.now();
  const built = kernel.buildTracking(input);
  const ms = performance.now() - t0;
  const same = (name, actual, Ctor) => {
    const want = new Uint8Array(readFileSync(path.join(dir, `${name}.bin`)));
    const got = new Uint8Array(new Ctor(actual).buffer);
    if (got.length !== want.length || got.some((b, i) => b !== want[i])) throw new Error(`${name} differs from the engine`);
  };
  same("ms", built.ms, Float64Array);
  same("period", built.period, Int8Array);
  same("ball", built.ball, Float32Array);
  same("xy", built.xy, Float32Array);
  console.log(`${input.match_id}: ${built.frames} frames x ${built.players} players, identical to the engine, built in ${ms.toFixed(0)} ms`);
}
