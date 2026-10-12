import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8765/api/v1";

async function comeback(request: import("@playwright/test").APIRequestContext) {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 7, storyline: "comeback", tracking: true } })).json();
  expect(match.final_score).toBe("2-3");
  return match.match_id as string;
}

test("broadcast overlay puts the equaliser on air over the pitch", async ({ page, request }) => {
  const id = await comeback(request);
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto(`/overlay?match=${id}&profile=p-analyst-en&pitch=1`);
  // The /overlay page itself is transparent so OBS can composite it over video.
  await expect(page.locator("main.overlay-page")).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
    .toBe("rgba(0, 0, 0, 0)");

  await request.post(`${API}/matches/${id}/replay`, { data: { speed: 60, from_ms: 82 * 60_000 } });
  const bug = page.getByTestId("score-bug");
  await expect(bug).toContainText("2–1", { timeout: 30_000 });
  await expect(page.getByTestId("lower-third")).toContainText("GOAL!", { timeout: 90_000 });
  await page.screenshot({ path: "test-results/overlay-goal.png" }); // held on air >= 4 s, even at 60x
  await expect(bug).toContainText("2–2");
  await expect(bug).toContainText("FT", { timeout: 120_000 });
});

test("compare: the same match as two genuinely different experiences", async ({ page, request }) => {
  const id = await comeback(request);
  await page.setViewportSize({ width: 1500, height: 1000 });
  await page.goto(`/compare?match=${id}&a=p-casual-es&b=p-analyst-en`);
  await request.post(`${API}/matches/${id}/replay`, { data: { speed: 60, from_ms: 82 * 60_000 } });

  const casual = page.getByTestId("fan-p-casual-es");
  const analyst = page.getByTestId("fan-p-analyst-en");
  await expect(casual.getByText(/¡GOOOL! .* empata/).first()).toBeVisible({ timeout: 90_000 });
  await expect(analyst.getByText(/GOAL! .* levels it/).first()).toBeVisible({ timeout: 30_000 });
  await expect(analyst.getByText(/xg/i).first()).toBeVisible(); // evidence chips only for the analyst
  await expect(casual.locator("li").filter({ hasText: "xg " })).toHaveCount(0);
  // Different personalisation: the analyst's feed is larger than the casual fan's.
  const counts = await Promise.all([casual.locator("li").count(), analyst.locator("li").count()]);
  expect(counts[1]).toBeGreaterThan(counts[0]);
  await page.screenshot({ path: "test-results/compare.png", fullPage: true });
});

test("a red card's what-if strip goes on air, and the studio exports the match", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 4, storyline: "red_card_turn", tracking: false } })).json();
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto(`/overlay?match=${match.match_id}&profile=p-analyst-en`);
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 20, from_ms: 37 * 60_000, frames: false } });
  const whatIf = page.getByTestId("what-if");
  await expect(whatIf).toBeVisible({ timeout: 60_000 }); // the 38' red card: "with eleven players"
  await expect(whatIf).toContainText("What if");
  await expect(page.getByTestId("lower-third")).toContainText("Red card!");
  await page.screenshot({ path: "test-results/overlay-what-if.png" });

  await page.goto(`/studio?match=${match.match_id}&profile=p-casual-es`);
  const link = page.getByTestId("export-chapters");
  await expect(link).toHaveAttribute("href", /lang=es/, { timeout: 30_000 }); // the fan's language once synced
  const chapters = await link.getAttribute("href");
  const vtt = await (await request.get(chapters ?? "")).text();
  expect(vtt.startsWith("WEBVTT")).toBe(true);
  expect(vtt).toContain("Roja directa");
});
