import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8765/api/v1";

test("a Spanish fan watches the comeback's finale live", async ({ page, request }) => {
  const created = await request.post(`${API}/matches`, { data: { seed: 7, storyline: "comeback", tracking: true } });
  const match = await created.json();
  expect(match.final_score).toBe("2-3");

  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-es`);
  await expect(page.locator("main")).toHaveAttribute("lang", "es");
  await expect(page.getByRole("img", { name: "Live 2D view of the pitch" })).toBeVisible();

  // Start at 80': the first 80 minutes are applied instantly (seek path), the finale plays live.
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 60, from_ms: 80 * 60_000 } });
  await expect(page.getByLabel("2 - 1")).toBeVisible({ timeout: 30_000 }); // caught up after the seek
  await expect(page.getByText(/¡GOOOL!/).first()).toBeVisible({ timeout: 30_000 }); // goals so far, in Spanish
  await expect(page.getByLabel("2 - 2")).toBeVisible({ timeout: 90_000 }); // 84' equaliser, live
  await expect(page.getByText("IA", { exact: true }).first()).toBeVisible({ timeout: 30_000 }); // AI narration
  await page.screenshot({ path: "test-results/watch-es-live.png", fullPage: true });

  await expect(page.getByText("Final", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(page.getByLabel("2 - 3")).toBeVisible();
  const goals = page.locator("li").filter({ hasText: "GOOOL" });
  await expect(page.locator("li").filter({ hasText: "Gol" }).first()).toBeVisible(); // localized type label
  await expect(goals).toHaveCount(5);
  await page.screenshot({ path: "test-results/watch-es-fulltime.png", fullPage: true });
});

test("an analyst sees evidence, an Arabic fan reads right to left", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 4, storyline: "red_card_turn", tracking: false } })).json();
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 600 } });

  await page.goto(`/watch?match=${match.match_id}&profile=p-analyst-en`);
  await expect(page.getByText("Full time", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(/win prob/).first()).toBeVisible(); // evidence chips
  await page.screenshot({ path: "test-results/watch-analyst.png", fullPage: true });

  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-ar`);
  await expect(page.locator("main")).toHaveAttribute("dir", "rtl");
  await expect(page.getByText("نهاية المباراة")).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "test-results/watch-ar.png", fullPage: true });
});

test("the launcher simulates a match and lists it", async ({ page }) => {
  // Count existing matches only once the launcher's initial list has arrived from the engine.
  const listed = page.waitForResponse((r) => r.url().endsWith("/matches") && r.request().method() === "GET");
  await page.goto("/watch");
  await listed;
  await expect(page.getByRole("button", { name: "Simulate match" })).toBeVisible();
  const before = await page.getByRole("link", { name: "Open" }).count();
  await page.getByRole("button", { name: "Simulate match" }).click();
  await expect(page.getByRole("link", { name: "Open" })).toHaveCount(before + 1, { timeout: 150_000 });
});
