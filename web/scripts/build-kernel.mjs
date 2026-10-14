// Builds the PitchLens kernel (engine/core/wasm) for the browser:
//   cargo build --target wasm32-unknown-unknown  →  wasm-bindgen --target web  →  public/kernel/
// The JS glue and the .wasm are static files the kernel worker loads at runtime (lib/kernel.worker.ts), so
// the web app builds without Rust; without them the kernel features fall back to the engine. Build output,
// git-ignored.
//
// Needs Rust with the wasm32-unknown-unknown target and the wasm-bindgen CLI at the exact version the
// crate pins (see engine/core/wasm/Cargo.toml): `rustup target add wasm32-unknown-unknown`, then a
// prebuilt wasm-bindgen from https://github.com/wasm-bindgen/wasm-bindgen/releases on PATH.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const core = path.resolve(web, "../engine/core");
const out = path.join(web, "public/kernel");
const BUDGET_GZ = 400 * 1024;

const run = (cmd, args, cwd) => execFileSync(cmd, args, { cwd, stdio: "inherit" });

run("cargo", ["build", "-p", "pitchlens-wasm", "--target", "wasm32-unknown-unknown", "--release"], core);
const raw = path.join(core, "target/wasm32-unknown-unknown/release/pitchlens_wasm.wasm");
rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });
run("wasm-bindgen", [raw, "--target", "web", "--out-dir", out, "--out-name", "pitchlens_kernel", "--omit-default-module-path", "--no-typescript"], web);
writeFileSync(path.join(out, "package.json"), JSON.stringify({ type: "module" })); // for Node (scripts/check-kernel.mjs)

const gz = gzipSync(readFileSync(path.join(out, "pitchlens_kernel_bg.wasm"))).length;
console.log(`kernel: ${(gz / 1024).toFixed(0)} KB gzipped (budget ${BUDGET_GZ / 1024} KB)`);
if (gz > BUDGET_GZ) {
  console.error("kernel is over its size budget");
  process.exit(1);
}
