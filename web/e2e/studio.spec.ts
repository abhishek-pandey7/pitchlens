import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8765/api/v1";

test("a producer runs the replay and watches the agents work", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 22, storyline: "comeback", tracking: false } })).json();
  await page.goto(`/studio?match=${match.match_id}`);
  await expect(page.getByRole("region", { name: "Replay controls" })).toBeVisible();
  await expect(page.getByText("No agent runs yet.")).toBeVisible();

  // Start from 80' at 60x with the producer controls; the pipeline reports latency against budget.
  await page.getByRole("combobox", { name: "Speed" }).selectOption("60");
  await page.getByRole("button", { name: "▶ 80'" }).click();
  await expect(page.getByTestId("replay-state")).toContainText("playing", { timeout: 15_000 });
  await expect(page.getByTestId("latency-event_to_broadcast")).not.toHaveText("–", { timeout: 30_000 });

  // A question from the producer's panel appears in the agent trace, step by step.
  await page.getByPlaceholder("Ask anything about this match…").fill("What's the score?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  const run = page.getByTestId("trace-run").first();
  await expect(run).toBeVisible({ timeout: 60_000 });
  await expect(run.getByText("What's the score?")).toBeVisible();
  await expect(run.getByTestId("trace-step-analyst")).toContainText("get_match_state");
  await expect(run.getByTestId("trace-step-fact_checker")).toContainText("approve");
  await expect(run.getByText("verified")).toBeVisible();
  await page.screenshot({ path: "test-results/studio.png", fullPage: true });
});
