import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8765/api/v1";

test("the analyst reads the match in charts and inspects a goal's evidence", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 26, storyline: "comeback", tracking: false } })).json();
  await page.goto(`/analyst?match=${match.match_id}`);
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 600, from_ms: 0, frames: false } });

  for (const chart of ["wp", "xg", "momentum", "pressing", "territory", "control"]) {
    await expect(page.getByTestId(`chart-${chart}`).locator("path").first()).toBeVisible({ timeout: 60_000 });
  }
  const inspector = page.getByRole("region", { name: "Evidence inspector" });
  await inspector.getByRole("combobox", { name: "Moment type" }).selectOption("goal");
  await inspector.getByTestId("moment").first().click();
  const detail = inspector.getByTestId("evidence-detail");
  await expect(detail).toContainText("score");
  await expect(detail).toContainText("Salience");
  await expect(page.getByTestId("chart-highlight")).toHaveCount(6); // the moment is marked on every chart
  await page.screenshot({ path: "test-results/analyst.png", fullPage: true });
});
