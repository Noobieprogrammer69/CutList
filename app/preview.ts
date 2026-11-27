export type ClipWindow = { start: number; end: number; segments?: { start: number; end: number }[] };
type Player = { currentTime: number; pause(): void };

// Used by both timeupdate and a short timer: timeupdate alone can be ~250 ms
// apart. Clamping the displayed playhead keeps exact in/out points intact.
export function stopAtClipEnd(player: Player | null, clip: ClipWindow | null): boolean {
  if (player && clip?.segments) {
    for (let i = 0; i < clip.segments.length - 1; i++) {
      const segment = clip.segments[i], next = clip.segments[i + 1];
      if (player.currentTime >= segment.end - .025 && player.currentTime < next.start) {
        player.currentTime = next.start;
        return false;
      }
    }
  }
  if (!player || !clip || player.currentTime < clip.end - .025) return false;
  player.pause();
  player.currentTime = clip.end;
  return true;
}

export function constrainClipSeek(player: Player | null, clip: ClipWindow | null): void {
  if (player && clip && (player.currentTime < clip.start || player.currentTime > clip.end)) {
    player.currentTime = Math.min(clip.end, Math.max(clip.start, player.currentTime));
  }
  if (player && clip?.segments) {
    for (let i = 0; i < clip.segments.length - 1; i++) {
      if (clip.segments[i].end < player.currentTime && player.currentTime < clip.segments[i+1].start) {
        player.currentTime = clip.segments[i+1].start;
        return;
      }
    }
  }
}
