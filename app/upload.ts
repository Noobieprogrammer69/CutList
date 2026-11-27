import { readResponse } from "./types";

const CHUNK = 8 * 1024 * 1024;
export async function uploadMedia(api: string, file: File, progress: (percent: number) => void, signal: AbortSignal) {
  // Read at most 2 MB to identify a reselected file, never a whole recording.
  const samples = new Blob([file.slice(0, 1024 * 1024), file.slice(Math.max(0, file.size - 1024 * 1024)), `${file.name}:${file.size}:${file.lastModified}`]);
  const digest = await crypto.subtle.digest("SHA-256", await samples.arrayBuffer());
  const fingerprint = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  const upload = await readResponse(await fetch(`${api}/api/uploads`, { method: "POST", credentials: "include", signal,
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: file.name, size: file.size, fingerprint }) }));
  let offset: number = upload.offset;
  const chunkSize = Math.min(CHUNK, upload.chunk_bytes || 4 * 1024 * 1024);
  progress(Math.round(offset / file.size * 100));
  while (offset < file.size) {
    const end = Math.min(offset + chunkSize, file.size);
    const next = await readResponse(await fetch(`${api}/api/uploads/${upload.id}`, { method: "PUT", credentials: "include", signal,
      headers: { "Content-Range": `bytes ${offset}-${end - 1}/${file.size}`, "Content-Type": "application/octet-stream" }, body: file.slice(offset, end) }));
    offset = next.offset;
    progress(Math.round(offset / file.size * 100));
  }
  return upload.id as string;
}
