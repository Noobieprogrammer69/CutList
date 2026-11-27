// Run against the ordinary app: npm run dev, then npm run test:browser.
// No mocked requests, injected results, or alternate sample pipeline.
import assert from "node:assert/strict";
import { chromium } from "playwright";
import { existsSync, mkdirSync, readFileSync } from "node:fs";
import path from "node:path";

const base = process.env.PLAYWRIGHT_BASE_URL || "http://127.0.0.1:3000";
try {
  const response = await fetch(base);
  assert.equal(response.ok, true);
} catch {
  console.error(`Cannot reach ${base}. Start npm run dev in a separate terminal first.`);
  process.exit(1);
}
const executable = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || (existsSync("/usr/bin/chromium") ? "/usr/bin/chromium" : undefined);
const browser = await chromium.launch({ executablePath: executable, headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
const page = await browser.newPage({ viewport: { width: 1360, height: 1100 } });
const errors = [];
page.on("pageerror", error => errors.push(error.message));
mkdirSync("test-results", { recursive: true });

async function analyze() {
  const completed = page.waitForResponse(async response => {
    if (!/\/api\/jobs\/[a-f0-9]{32}(?:\?compact=true(?:&since_sections=-?\d+)?)?$/.test(response.url()) || response.status() !== 200) return false;
    try { return (await response.json()).status === "complete"; } catch { return false; }
  }, { timeout: 180000 });
  await page.getByRole("button", { name: /^Find clips/ }).click();
  const result = (await (await completed).json()).result;
  await page.locator(".analysis-summary").waitFor();
  assert.ok(Array.isArray(result.clips));
  for (const clip of result.clips) {
    assert.ok(clip.start >= 0 && clip.end <= result.duration);
    assert.ok(clip.end - clip.start <= result.maximum + .001);
    for (const other of result.clips) {
      if (clip.id !== other.id) assert.ok(!(clip.start < other.end && clip.end > other.start));
    }
  }
  return result;
}
function animatedWav() {
  const rate = 16000, seconds = 42, count = rate * seconds;
  const wav = Buffer.alloc(44 + count * 2);
  wav.write("RIFF", 0); wav.writeUInt32LE(wav.length - 8, 4); wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16); wav.writeUInt16LE(1, 20); wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(rate, 24); wav.writeUInt32LE(rate * 2, 28); wav.writeUInt16LE(2, 32); wav.writeUInt16LE(16, 34);
  wav.write("data", 36); wav.writeUInt32LE(count * 2, 40);
  for (let i = 0; i < count; i++) {
    const t = i / rate;
    const voice = (Math.sin(2 * Math.PI * 140 * t) + .3 * Math.sin(2 * Math.PI * 313 * t)) * (.55 + .4 * Math.sin(2 * Math.PI * 9 * t));
    wav.writeInt16LE(Math.round(voice * (t >= 20 && t < 24 ? 6500 : 1200)), 44 + i * 2);
  }
  return wav;
}

try {
  await page.goto(base);
  await page.getByRole("heading", { name: /Long recording/ }).waitFor();
  await page.getByRole("button", { name: "Dark", exact: true }).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "dark");
  await page.reload();
  await page.getByRole("button", { name: "Dark", exact: true }).waitFor();
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
  await page.screenshot({ path: "test-results/desktop-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Light", exact: true }).click();
  assert.equal(await page.locator("html").getAttribute("data-theme"), "light");
  await page.evaluate(() => document.fonts.ready);
  assert.match(await page.locator("body").evaluate(body => getComputedStyle(body).fontFamily), /Noto Sans/);
  await page.screenshot({ path: "test-results/desktop-empty.png", fullPage: true });
  await page.getByRole("checkbox", { name: /Propose clips, then run a final editorial check/ }).uncheck();
  await page.getByRole("button", { name: "Load sample", exact: true }).click();
  await page.locator(".replay-settings summary").click();
  const markers = page.getByRole("checkbox", { name: /Include illustrative sample replay markers/ });
  assert.equal(await markers.isChecked(), false);
  const sample = await analyze();
  assert.ok(sample.clips.length >= 2);
  assert.deepEqual(sample.replays, []);
  assert.ok(sample.clips.some(c => c.transcript_used));

  const videoDownloading = page.waitForEvent("download", { timeout: 180000 });
  await page.getByRole("button", { name: "Download clip", exact: true }).first().click();
  const videoDownload = await videoDownloading;
  assert.match(videoDownload.suggestedFilename(), /\.mp4$/);
  await videoDownload.saveAs("test-results/selected-clip.mp4");
  assert.ok(readFileSync("test-results/selected-clip.mp4").length > 1000);

  await page.waitForFunction(() => document.querySelector("#preview video")?.readyState >= 1);
  await page.getByRole("button", { name: "Preview clip", exact: true }).first().click();
  await page.waitForFunction(({ start, end }) => {
    const video = document.querySelector("#preview video");
    return video && !video.paused && video.currentTime >= start && video.currentTime < end;
  }, sample.clips[0]);
  await page.locator("#preview video").evaluate((video, clip) => { video.currentTime = Math.max(clip.start, clip.end - .4); }, sample.clips[0]);
  await page.waitForFunction(end => {
    const video = document.querySelector("#preview video");
    return video?.paused && Math.abs(video.currentTime - end) < .08;
  }, sample.clips[0].end, { timeout: 7000 });
  // Native scrubbing is also contained within the selected cut.
  await page.locator("#preview video").evaluate(video => { video.currentTime = 0; });
  await page.waitForFunction(start => Math.abs(document.querySelector("#preview video").currentTime - start) < .08, sample.clips[0].start);
  const downloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Export shortlist" }).click();
  const download = await downloading;
  await download.saveAs("test-results/cutlist-shortlist.txt");
  assert.match(readFileSync("test-results/cutlist-shortlist.txt", "utf8"), /CUTLIST.*EDITOR SHORTLIST/);
  await page.screenshot({ path: "test-results/desktop-results.png", fullPage: true });
  const firstCard = page.locator(".clip-card").first();
  await firstCard.locator(".clip-editor > summary").click();
  await firstCard.getByLabel(`start seconds for clip ${sample.clips[0].id}`, { exact: true }).fill(String(sample.clips[0].start + .2));
  await firstCard.getByText("Output framing", { exact: true }).locator("..").locator("select").selectOption("vertical");
  await firstCard.getByRole("checkbox", { name: "Burn captions into MP4" }).check();
  const savedResponse = page.waitForResponse(response => response.request().method() === "PATCH" && response.url().includes(`/clips/${sample.clips[0].id}`));
  await firstCard.getByRole("button", { name: "Save edit", exact: true }).click();
  const saved = await (await savedResponse).json();
  assert.equal(saved.revision, 1);
  assert.equal(saved.crop.format, "vertical");
  assert.equal(saved.start, Math.round((sample.clips[0].start + .2) * 1000) / 1000);
  await firstCard.getByRole("button", { name: "Keep", exact: true }).click();
  await page.waitForFunction(() => document.querySelector('.clip-feedback button[aria-pressed="true"]')?.textContent === "Keep");
  const subtitlesDownloading = page.waitForEvent("download");
  await firstCard.getByRole("link", { name: "Download SRT", exact: true }).click();
  await (await subtitlesDownloading).saveAs("test-results/edited-clip.srt");
  assert.match(readFileSync("test-results/edited-clip.srt", "utf8"), /00:00:/);
  const timelineDownloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Premiere / Resolve XML", exact: true }).click();
  await (await timelineDownloading).saveAs("test-results/cutlist-timeline.xml");
  assert.match(readFileSync("test-results/cutlist-timeline.xml", "utf8"), /<xmeml version="5">/);
  const editedDownloading = page.waitForEvent("download", { timeout: 180000 });
  await firstCard.getByRole("button", { name: "Download clip", exact: true }).click();
  await (await editedDownloading).saveAs("test-results/vertical-captioned-clip.mp4");
  await page.locator(".quality-benchmark > summary").click();
  await page.getByLabel("Label start seconds", { exact: true }).fill("4");
  await page.getByLabel("Label end seconds", { exact: true }).fill("9");
  await page.getByRole("button", { name: "Add reference label", exact: true }).click();
  await page.locator(".reference-label").first().waitFor();
  const benchmarkDownloading = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download benchmark JSON", exact: true }).click();
  await (await benchmarkDownloading).saveAs("test-results/real-footage-benchmark.json");
  const benchmark = JSON.parse(readFileSync("test-results/real-footage-benchmark.json", "utf8"));
  assert.equal(benchmark.labels.length, 1);
  assert.equal(benchmark.sample, true);
  await page.locator(".batch-export > summary").click();
  await page.getByRole("button", { name: "Select kept", exact: true }).click();
  await page.getByRole("button", { name: "Prepare 1 clips", exact: true }).click();
  const zipLink = page.getByRole("link", { name: "Download clips ZIP", exact: true });
  await zipLink.waitFor({ timeout: 180000 });
  const zipDownloading = page.waitForEvent("download");
  await zipLink.click();
  await (await zipDownloading).saveAs("test-results/kept-clips.zip");
  assert.ok(readFileSync("test-results/kept-clips.zip").length > 1000);
  await page.locator(".transcript-workbench > summary").click();
  await page.getByLabel("Manual clip start", { exact: true }).fill("0");
  await page.getByLabel("Manual clip end", { exact: true }).fill("1");
  await page.getByText("New clip title", { exact: true }).locator("..").locator("input").fill("Manual transcript cut");
  const createdResponse = page.waitForResponse(r => r.request().method() === "POST" && /\/clips$/.test(new URL(r.url()).pathname));
  await page.getByRole("button", { name: "Create clip from selection", exact: true }).click();
  const createdClip = await (await createdResponse).json();
  assert.equal(createdClip.selection_method, "manual");
  const manualCard = page.locator(`#clip-${createdClip.id}`);
  await manualCard.locator(".clip-editor > summary").click();
  await manualCard.locator(".cut-segments > summary").click();
  await manualCard.getByText("Remove from · source seconds", { exact: true }).locator("..").locator("input").fill("0.3");
  await manualCard.getByText("Remove to · source seconds", { exact: true }).locator("..").locator("input").fill("0.6");
  await manualCard.getByRole("button", { name: "Remove interval from video", exact: true }).click();
  const compositionSaved = page.waitForResponse(r => r.request().method() === "PATCH" && r.url().includes(`/clips/${createdClip.id}`));
  await manualCard.getByRole("button", { name: "Save edit", exact: true }).click();
  const composed = await (await compositionSaved).json();
  assert.equal(composed.segments.length, 2);
  assert.ok(Math.abs(composed.output_duration - 0.7) < 0.001);
  await page.reload();
  await page.locator(".projects-panel > summary").click();
  await page.getByRole("button", { name: /Studio notes · sample recording/ }).first().click();
  await page.locator(".analysis-summary").waitFor();
  assert.ok(await page.locator('.clip-feedback button[aria-pressed="true"]').count() > 0);

  await page.getByRole("button", { name: "Load sample", exact: true }).click();
  await page.locator(".replay-settings summary").click();
  await markers.check();
  const withReplays = await analyze();
  assert.ok(withReplays.replays.length > 0);
  assert.match(withReplays.capabilities.replays, /illustrative/);
  await markers.uncheck();
  assert.deepEqual((await analyze()).replays, []);

  await page.getByRole("button", { name: "Clear source", exact: true }).click();
  await page.getByLabel("Upload captions").setInputFiles(path.resolve("public/sample/studio-notes.vtt"));
  const captions = await analyze();
  assert.equal(captions.media_url, null);
  assert.ok(captions.clips.every(c => c.transcript_used));
  assert.equal(await page.getByRole("button", { name: "Download clip", exact: true }).count(), 0);
  await page.getByLabel("Upload media").setInputFiles(path.resolve("public/sample/studio-notes.mp4"));
  const combined = await analyze();
  assert.ok(combined.media_url && combined.caption_count);
  await page.getByRole("button", { name: "Clear source", exact: true }).click();
  await page.getByRole("button", { name: "Remove captions", exact: true }).click();
  await page.getByRole("checkbox", { name: /Transcribe English speech when captions are missing/ }).uncheck();
  await page.getByLabel("Upload media").setInputFiles({ name: "animated.wav", mimeType: "audio/wav", buffer: animatedWav() });
  const audio = await analyze();
  assert.ok(audio.clips.length > 0);
  assert.equal(audio.caption_count, 0);
  assert.ok(audio.clips.every(c => !c.transcript_used));
  assert.ok(!audio.clips.some(c => c.evidence.some(e => /Laughter detected/.test(e))));
  const audioDownloading = page.waitForEvent("download", { timeout: 180000 });
  await page.getByRole("button", { name: "Download clip", exact: true }).first().click();
  const audioDownload = await audioDownloading;
  assert.match(audioDownload.suggestedFilename(), /\.m4a$/);
  await audioDownload.saveAs("test-results/selected-audio.m4a");

  await page.getByRole("button", { name: "Clear source", exact: true }).click();
  function stamp(seconds) { return `${String(Math.floor(seconds / 3600)).padStart(2, "0")}:${String(Math.floor(seconds / 60) % 60).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")},000`; }
  const manyCues = Array.from({ length: 25 }, (_, i) => `${i * 2 + 1}\n${stamp(i * 60)} --> ${stamp(i * 60 + 4)}\nWhat is the most useful lesson from project ${i}?\n\n${i * 2 + 2}\n${stamp(i * 60 + 4)} --> ${stamp(i * 60 + 12)}\nI learned that keeping a separate backup matters because a broken drive should never end the story. This is takeaway ${i}.\n`).join("\n");
  await page.getByLabel("Upload captions").setInputFiles({ name: "many-clips.srt", mimeType: "text/plain", buffer: Buffer.from(manyCues) });
  const many = await analyze();
  assert.ok(many.clips.length > 20);
  assert.equal(await page.locator(".clip-card").count(), 20);
  await page.getByRole("button", { name: "Next page", exact: true }).click();
  assert.equal(await page.locator(".clip-card").count(), many.clips.length - 20);
  await page.getByRole("textbox", { name: "Search all candidates" }).fill("takeaway 24");
  await page.waitForFunction(() => document.querySelectorAll(".clip-card").length === 1);
  await page.getByRole("textbox", { name: "Search all candidates" }).fill("");
  await page.getByRole("button", { name: "Remove captions", exact: true }).click();

  await page.getByLabel("Upload media").setInputFiles({ name: "broken.mp4", mimeType: "video/mp4", buffer: Buffer.from("not a video") });
  await page.getByRole("button", { name: /^Find clips/ }).click();
  await page.getByRole("alert").filter({ hasText: /decode/ }).waitFor({ timeout: 30000 });
  await page.getByRole("button", { name: "Load sample", exact: true }).click();
  await page.locator(".setup-panel > summary").click();
  await page.getByRole("button", { name: "Refresh availability", exact: true }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "test-results/mobile.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "Mobile layout overflows horizontally");
  await page.getByRole("button", { name: "Dark", exact: true }).click();
  await page.screenshot({ path: "test-results/mobile-dark.png", fullPage: true });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, "Dark mobile layout overflows horizontally");
  assert.deepEqual(errors, []);
  if (process.env.CUTLIST_BROWSER_ASR === "1") {
    await page.getByRole("checkbox", { name: /Transcribe English speech when captions are missing/ }).check();
    await page.getByRole("checkbox", { name: /Use matching sample captions/ }).uncheck();
    await page.getByText("Chapter size", { exact: true }).locator("..").locator("select").selectOption("60");
    const transcribed = await analyze();
    assert.equal(transcribed.caption_source, "automatic");
    assert.ok(transcribed.caption_count > 15);
  }
  console.log("PASS: persistent light/dark modes; sample with/without markers; caption, audio and combined inputs; playable bounded previews; video/audio clip downloads; shortlist export; decode errors; responsive layout.");
} finally {
  await browser.close();
}
