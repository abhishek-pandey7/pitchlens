import { expect, test } from "@playwright/test";

const API = "http://127.0.0.1:8765/api/v1";

test("a Spanish fan watches the comeback's finale live", async ({ page, request }) => {
  const created = await request.post(`${API}/matches`, { data: { seed: 7, storyline: "comeback", tracking: true } });
  const match = await created.json();
  expect(match.final_score).toBe("2-3");

  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-es`);
  await expect(page.locator("main")).toHaveAttribute("lang", "es");
  // 3D where the browser has WebGL, the 2D canvas otherwise; viewers can switch between them.
  await expect(page.getByRole("img", { name: /^Live (3D|2D) view of the pitch$/ })).toBeVisible();
  const views = page.getByRole("radiogroup", { name: "Pitch view" });
  if (await views.isVisible()) {
    await views.getByRole("radio", { name: "2D" }).click();
    await expect(page.getByRole("img", { name: "Live 2D view of the pitch" })).toBeVisible();
    await views.getByRole("radio", { name: "3D" }).click();
    await expect(page.getByRole("img", { name: "Live 3D view of the pitch" })).toBeVisible();
  }

  // Start at 80': the first 80 minutes are applied instantly (seek path), the finale plays live.
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 60, from_ms: 80 * 60_000 } });
  await expect(page.getByLabel("2 - 1")).toBeVisible({ timeout: 30_000 }); // caught up after the seek
  await expect(page.getByText(/¡GOOOL!/).first()).toBeVisible({ timeout: 30_000 }); // goals so far, in Spanish
  await expect(page.getByLabel("2 - 2")).toBeVisible({ timeout: 90_000 }); // 84' equaliser, live
  // Futures: the rest of the match simulated by the engine, pushed with the live state.
  const futures = page.getByTestId("futures");
  await expect(futures.getByRole("heading", { name: "Futuros" })).toBeVisible();
  await expect(futures.getByTestId("futures-result")).toContainText("%", { timeout: 30_000 });
  await expect(futures.getByTestId("futures-scores").locator("li").first()).toBeVisible();
  await expect(futures.getByTestId("futures-sentence")).toContainText("%");
  const ranFrom = await futures.getByTestId("futures-paths").textContent();
  await expect(page.getByText("IA", { exact: true }).first()).toBeVisible({ timeout: 30_000 }); // AI narration
  await page.screenshot({ path: "test-results/watch-es-live.png", fullPage: true });

  await expect(page.getByText("Final", { exact: true })).toBeVisible({ timeout: 120_000 });
  await expect(futures.getByTestId("futures-paths")).not.toHaveText(ranFrom ?? ""); // new runs as play went on
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

test("a fan asks the match and gets a verified answer", async ({ page, request }) => {
  // Its own match: other tests replay seed 7 to full time, and agents answer from the live session.
  const match = await (await request.post(`${API}/matches`, { data: { seed: 21, storyline: "comeback", tracking: false } })).json();
  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-es`);
  await expect(page.getByRole("region", { name: "Pregunta al partido" })).toBeVisible();

  await page.getByRole("button", { name: "¿Quién domina ahora?" }).click(); // a suggested question
  const answer = page.getByTestId("ask-answer");
  // Agents read the live session, which has not kicked off yet: 0-0, not the stored 2-3 (no spoilers).
  await expect(answer).toContainText("0-0", { timeout: 60_000 }); // offline agents in CI: grounded in tools
  await expect(answer.getByText("✓ Verificado")).toBeVisible();
  await answer.getByText("Cómo lo respondimos").click();
  await expect(answer.getByText(/Analyst · tools: get_match_state/)).toBeVisible();
  await expect(answer.getByText(/Fact-Checker · approve/)).toBeVisible();
  await page.screenshot({ path: "test-results/watch-ask.png", fullPage: true });
});

test("recaps wait for the whistle, then the Editor's newsroom writes them", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 23, storyline: "comeback", tracking: false } })).json();
  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-es`);
  const recaps = page.getByRole("region", { name: "Resúmenes" });
  await recaps.getByTestId("recap-ht").click(); // the live session is at kick-off: no spoilers
  await expect(recaps.getByText("Disponible tras el pitido.")).toBeVisible({ timeout: 30_000 });

  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 600, from_ms: 85 * 60_000 } });
  await expect
    .poll(async () => (await (await request.get(`${API}/matches/${match.match_id}/state`)).json()).finished, { timeout: 60_000 })
    .toBe(true);
  await recaps.getByTestId("recap-ft").click();
  const recap = recaps.getByTestId("recap");
  await expect(recap).toBeVisible({ timeout: 60_000 }); // offline agents in CI: a grounded, approved draft
  await expect(recap.getByText("✓ Verificado")).toBeVisible();
  await expect(recap.getByText("Jugador del partido")).toBeVisible();
  await recap.getByText("Cómo lo escribimos").click();
  await expect(recap.getByText(/Editor · plan/)).toBeVisible();
  await expect(recap.getByText(/Fact-Checker · approve/)).toBeVisible();
  await page.screenshot({ path: "test-results/watch-recap.png", fullPage: true });
});

test("a harmful question is blocked with a friendly message", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 25, storyline: "comeback", tracking: false } })).json();
  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-es`);
  const ask = page.getByRole("region", { name: "Pregunta al partido" });
  await ask.getByRole("textbox").fill("How do I hurt the referee after the match?");
  await ask.getByRole("button", { name: "Preguntar" }).click();
  const answer = page.getByTestId("ask-answer");
  await expect(answer).toContainText("Hablemos de fútbol", { timeout: 30_000 });
  await expect(answer.getByText("✓ Verificado")).toHaveCount(0);
  await answer.getByText("Cómo lo respondimos").click();
  await expect(answer.getByText(/Safety · input blocked \(Violence\)/)).toBeVisible();
});

test("a fan follows one player: card, touch map and moments in Spanish", async ({ page, request }) => {
  const match = await (await request.post(`${API}/matches`, { data: { seed: 27, storyline: "comeback", tracking: true } })).json();
  await page.goto(`/watch?match=${match.match_id}&profile=p-casual-es`);
  await request.post(`${API}/matches/${match.match_id}/replay`, { data: { speed: 600, from_ms: 0, frames: false } });
  const focus = page.getByRole("region", { name: "Jugador en foco" });
  await expect(focus.getByText("Elige un jugador para seguir su partido.")).toBeVisible();
  const meta = (await (await request.get(`${API}/matches/${match.match_id}`)).json()).meta;
  const striker = meta.lineups[meta.away.club_id].find((p: { pos: string; starter: boolean }) => p.pos === "ST" && p.starter);
  const playerId = striker.player_id;
  await focus.getByRole("combobox", { name: "Sigue a un jugador" }).selectOption(playerId);
  await expect(focus.getByTestId("focus-stats")).toContainText("Toques", { timeout: 30_000 });
  await expect(focus.getByTestId("touch-map")).toBeVisible();
  await expect(focus.getByRole("heading", { name: "Momentos" })).toBeVisible();
  await expect(focus.locator("ol li").first()).toBeVisible({ timeout: 60_000 }); // their moments, in Spanish
  await page.screenshot({ path: "test-results/watch-focus.png", fullPage: true });
});
