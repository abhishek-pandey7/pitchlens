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

test("in approve mode the producer airs what the Director proposes", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 24, storyline: "comeback", tracking: false } })).json();
  await page.goto(`/studio?match=${match.match_id}&profile=p-analyst-en`);
  const director = page.getByRole("region", { name: "Director" });
  await director.getByTestId("director-approve").click();
  await expect(director.getByTestId("director-approve")).toHaveAttribute("aria-checked", "true");
  await expect(director.getByText(/No cues waiting/)).toBeVisible();

  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 10, from_ms: 66 * 60_000, frames: false } });
  const proposal = director.getByTestId("pending-cue").filter({ hasText: "Director: air" }).first();
  await expect(proposal).toBeVisible({ timeout: 60_000 }); // the offline Director proposes airing it
  await proposal.getByRole("button", { name: "Approve" }).click();
  await expect(director.getByText("producer").first()).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId("trace-step-director").first()).toContainText("list_pending_cues");
  await page.screenshot({ path: "test-results/studio-director.png", fullPage: true });
});
