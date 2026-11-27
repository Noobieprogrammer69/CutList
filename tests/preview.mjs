import assert from "node:assert/strict";
import test from "node:test";
import { stopAtClipEnd, constrainClipSeek } from "../app/preview.ts";

function player(currentTime) {
  return { currentTime, paused: false, pause() { this.paused = true; } };
}
test("selected interval stops at its exact end and corrects overshoot", () => {
  for (const time of [19.98, 20, 20.25]) {
    const media = player(time);
    assert.equal(stopAtClipEnd(media, { start: 12.125, end: 20 }), true);
    assert.equal(media.paused, true);
    assert.equal(media.currentTime, 20);
  }
});
test("playback inside an interval continues and full-source playback is unrestricted", () => {
  const media = player(15);
  assert.equal(stopAtClipEnd(media, { start: 12.125, end: 20 }), false);
  assert.equal(stopAtClipEnd(media, null), false);
  assert.equal(media.paused, false);
  assert.equal(stopAtClipEnd(null, null), false);
});
test("native scrubbing stays inside the selected interval", () => {
  for (const [time, expected] of [[0, 12.125], [15, 15], [99, 20]]) {
    const media = player(time);
    constrainClipSeek(media, { start: 12.125, end: 20 });
    assert.equal(media.currentTime, expected);
  }
  const media = player(50);
  constrainClipSeek(media, null);
  assert.equal(media.currentTime, 50);
});
test("multi-part preview skips removed source footage and stops at the final retained end", () => {
  const clip = { start: 2, end: 20, segments: [{ start: 2, end: 5 }, { start: 16, end: 20 }] };
  const media = player(4.98);
  assert.equal(stopAtClipEnd(media, clip), false);
  assert.equal(media.currentTime, 16);
  assert.equal(media.paused, false);
  media.currentTime = 10;
  constrainClipSeek(media, clip);
  assert.equal(media.currentTime, 16);
  media.currentTime = 20.1;
  assert.equal(stopAtClipEnd(media, clip), true);
  assert.equal(media.currentTime, 20);
});
