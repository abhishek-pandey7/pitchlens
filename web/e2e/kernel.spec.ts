import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8765/api/v1";

type Frame = { match_ms: number; ball: { x: number; y: number; z: number }; players: { player_id: string; x: number; y: number }[] };

// The Rust kernel compiled to WebAssembly builds a whole match's tracking in the browser from ~150 KB of
// packed events, and it matches the frames the engine (the same Rust, through Python) serves.
test("the browser builds a match's tracking with the Rust kernel, identical to the engine's", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 7, storyline: "comeback", tracking: true } })).json();
  const input = await (await request.get(`${API}/matches/${match.match_id}/tracking-input`)).json();
  const clip = (await (await request.get(`${API}/matches/${match.match_id}/clips/goal?index=-1`)).json()) as { frames: Frame[] };

  await page.goto("/");
  const built = await page.evaluate(async (packed) => {
    const glue = "/kernel/pitchlens_kernel.js";
    const kernel = await import(/* webpackIgnore: true */ glue);
    await kernel.default({ module_or_path: "/kernel/pitchlens_kernel_bg.wasm" });
    const started = performance.now();
    const t = kernel.buildTracking(packed);
    const ms = performance.now() - started;
    return { version: kernel.version(), ms: Array.from(t.ms as Float64Array), ball: Array.from(t.ball as Float32Array), xy: Array.from(t.xy as Float32Array), players: t.players as number, buildMs: ms };
  }, input);

  expect(built.version).toMatch(/^\d+\.\d+\.\d+$/);
  expect(built.ms.length).toBeGreaterThan(20_000);
  console.log(`kernel ${built.version}: ${built.ms.length} frames x ${built.players} players in ${built.buildMs.toFixed(0)} ms`);

  // Every frame of the engine's goal clip, rounded as the engine rounds (2 decimals), is the browser's.
  const round2 = (v: number) => Math.round(v * 100) / 100;
  const col = new Map<string, number>((input.player_ids as string[]).map((id, i) => [id, i]));
  for (const frame of clip.frames) {
    const i = built.ms.indexOf(frame.match_ms);
    expect(i, `frame at ${frame.match_ms} ms`).toBeGreaterThanOrEqual(0);
    expect(round2(built.ball[i * 3])).toBeCloseTo(frame.ball.x, 2);
    expect(round2(built.ball[i * 3 + 1])).toBeCloseTo(frame.ball.y, 2);
    for (const p of frame.players) {
      const c = col.get(p.player_id)!;
      const at = (i * built.players + c) * 2;
      expect(round2(built.xy[at])).toBeCloseTo(p.x, 2);
      expect(round2(built.xy[at + 1])).toBeCloseTo(p.y, 2);
    }
  }
});

test("after the whistle a fan scrubs through the whole match, built in their browser", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 11, storyline: "comeback", tracking: true } })).json();
  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-en-nhr`);
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 600, from_ms: 88 * 60_000 } });
  await expect(page.getByText("Full time", { exact: true })).toBeVisible({ timeout: 90_000 });

  await page.getByRole("button", { name: "Replay the whole match" }).click();
  await expect(page.getByTestId("kernel-built")).toContainText("Built in your browser by the Rust kernel", { timeout: 30_000 });
  const slider = page.getByRole("slider", { name: "Match position" });
  await expect(slider).toHaveAttribute("aria-valuetext", "1'");
  const max = Number(await slider.getAttribute("max"));
  await slider.fill(String(Math.round(max * 0.75)));
  await expect(slider).not.toHaveAttribute("aria-valuetext", "1'");
  await page.getByRole("button", { name: "Back to the match" }).click();
  await expect(page.getByTestId("match-scrubber")).toHaveCount(0);
});
