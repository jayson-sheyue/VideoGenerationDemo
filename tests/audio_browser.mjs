// Run against a local app. All generation requests are intercepted; no paid API calls.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/audio_browser.mjs
import assert from "node:assert/strict";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({ headless: true, channel: "chrome" });
const context = await browser.newContext({ viewport: { width: 1440, height: 1100 } });
const page = await context.newPage();
const errors = [];
page.on("pageerror", (err) => errors.push(err.message));
let records = [], captured;
await page.route("**/api/jobs*", async route => {
  const path = new URL(route.request().url()).pathname;
  const body = path === "/api/jobs" ? { jobs: records } : records.find(j => path === "/api/jobs/" + j.id);
  await route.fulfill({ json: body || {} });
});
await page.route("**/api/generate", async route => {
  captured = route.request().postDataJSON();
  const stamp = new Date().toISOString();
  const record = { id: "audio-ui-test", mode: captured.mode, status: "failed", error: "Controlled browser fixture — no generation was sent", created_at: stamp, updated_at: stamp, request: captured, meta: {}, library: {} };
  records = [record];
  await route.fulfill({ json: record });
});
const cap = i => page.locator("#capabilities button").nth(i);
const flags = async () => Promise.all(["audioSpeech", "audioMusic", "audioEffects", "captions"].map(id => page.locator("#" + id).isChecked()));
try {
  await page.goto(process.env.TEST_URL || "http://127.0.0.1:8087");
  await page.waitForFunction(() => document.querySelectorAll("#capabilities button").length === 8);
  assert.deepEqual(await flags(), [true, true, true, false]);
  await page.locator("#prompt").fill('The host says: Welcome to the garden. Soft piano music.');
  await page.locator("#audioSpeech").uncheck();
  await page.locator("#audioEffects").uncheck();
  await page.locator("#captions").check();
  // A → B → A, refresh, and language changes must preserve per-mode preferences.
  await cap(1).click();
  assert.deepEqual(await flags(), [true, true, true, false]);
  await cap(0).click();
  assert.deepEqual(await flags(), [false, true, false, true]);
  await page.reload();
  await page.waitForFunction(() => document.querySelectorAll("#capabilities button").length === 8);
  assert.deepEqual(await flags(), [false, true, false, true]);
  await page.locator("#settingsButton").click();
  await page.locator("#language").selectOption("zh");
  await page.keyboard.press("Escape");
  assert.deepEqual(await flags(), [false, true, false, true]);
  assert.match(await page.locator("#audioHelp").textContent(), /原始提示词/);
  for (let i = 0; i < 8; i++) {
    await cap(i).click();
    // Capabilities: text, image, frames, references, continue, edit, extend, upscale.
    assert.equal(await page.locator("#audioOptions").isVisible(), [0, 1, 2, 3, 6].includes(i));
  }
  await cap(0).click();
  await page.locator("#generate").click();
  await page.waitForFunction(() => document.querySelector("#detailContent")?.textContent.includes("Welcome to the garden"));
  assert.deepEqual(captured.audio_preferences, { speech: false, music: true, effects: false });
  assert.equal(captured.no_subtitles, false);
  assert.equal(captured.enhance_prompt, false);
  assert.match(await page.locator("#detailContent").textContent(), /人声.*未勾选/);
  await page.locator("#audioSpeech").check();
  await page.getByRole("button", { name: "复用此任务设置", exact: true }).click();
  assert.deepEqual(await flags(), [false, true, false, true]);
  // Keyboard activation and narrow-screen wrapping.
  await page.locator("#audioMusic").focus();
  await page.keyboard.press("Space");
  assert.equal(await page.locator("#audioMusic").isChecked(), false);
  await page.locator("#toast").waitFor({ state: "hidden" });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: "/tmp/omni-audio-desktop.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  for (const id of ["audioSpeech", "audioMusic", "audioEffects", "captions"]) {
    const box = await page.locator("#" + id).boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= 390);
  }
  await page.screenshot({ path: "/tmp/omni-audio-mobile.png", fullPage: true });
  assert.deepEqual(errors, []);
  console.log("PASS: controls, mode isolation, refresh, localization, request, task restore, keyboard, desktop/mobile");
} finally {
  await browser.close();
}
