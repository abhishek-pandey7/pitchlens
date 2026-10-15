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

test("what if: the browser's futures equal the engine's, and the sandbox recalculates in the browser", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 13, storyline: "comeback", tracking: false } })).json();
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 600, from_ms: 55 * 60_000, frames: false } });
  // Both sides must start from the same match state (the seed comes from the latest event): wait for the
  // whistle, after which nothing changes.
  const inputNow = async () => (await request.get(`${API}/matches/${match.match_id}/futures/input`)).json();
  await expect.poll(async () => (await inputNow()).input?.minutes_left, { timeout: 60_000 }).toBe(0);
  const now = await inputNow();

  // The same hypothetical, the same seed and paths: single-threaded in Chromium, multi-threaded in the engine.
  const paths = 20_000;
  const engine = await (await request.post(`${API}/matches/${match.match_id}/futures/whatif`, {
    data: { home_goals: 1, away_goals: 1, minute: 70, paths },
  })).json();
  await page.goto("/");
  const browser = await page.evaluate(async ({ base, paths }) => {
    const glue = "/kernel/pitchlens_kernel.js";
    const kernel = await import(/* webpackIgnore: true */ glue);
    await kernel.default({ module_or_path: "/kernel/pitchlens_kernel_bg.wasm" });
    const input = { ...base, score: [1, 1], minute: 70, minutes_left: kernel.remainingMinutes(70, 2), paths };
    return kernel.simulateFutures(input);
  }, { base: now.input, paths });
  const r4 = (v: number) => Number(v.toFixed(4)); // Python's round(v, 4), as the engine reports them
  expect([r4(browser.home), r4(browser.draw), r4(browser.away)]).toEqual([engine.home, engine.draw, engine.away]);
  expect(browser.scores.slice(0, 5).map(([h, a, p]: [number, number, number]) => [h, a, r4(p)])).toEqual(
    engine.scores.slice(0, 5).map((s: { home: number; away: number; p: number }) => [s.home, s.away, s.p]),
  );

  // The sandbox: one more goal for the home side, recalculated in the browser.
  await page.goto(`/analyst?match=${match.match_id}`);
  await page.getByRole("button", { name: "What if…" }).click();
  const sandbox = page.getByTestId("whatif");
  await expect(sandbox.getByTestId("whatif-where")).toContainText("simulated in your browser by the Rust kernel", { timeout: 30_000 });
  const before = Number(await sandbox.getByTestId("whatif-score-0").textContent());
  await sandbox.getByRole("button", { name: /^One more: .* Score$/ }).first().click();
  await expect(sandbox.getByTestId("whatif-score-0")).toHaveText(String(before + 1));
  await expect(sandbox.getByTestId("futures-paths")).toContainText("50,000");
  await page.waitForTimeout(1000); // the result bar animates for 700 ms
  await sandbox.screenshot({ path: "test-results/whatif-sandbox.png" });
  await sandbox.getByRole("button", { name: "Back to the match" }).click();
  await expect(page.getByTestId("whatif")).toHaveCount(0);
});
