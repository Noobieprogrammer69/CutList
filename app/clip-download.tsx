"use client";

import { useEffect, useRef, useState } from "react";

type ExportState = { status: "not_started" | "queued" | "running" | "ready" | "error"; progress?: number; url?: string; filename?: string; error?: string };

export default function ClipDownload({ api, jobId, clipId, mediaKind, revision }: { api: string; jobId: string; clipId: number; mediaKind: string; revision: number }) {
  const [state, setState] = useState<ExportState>({ status: "not_started" });
  const controller = useRef<AbortController | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const endpoint = `${api}/api/jobs/${jobId}/clips/${clipId}/export?revision=${revision}`;
  const pending = state.status === "queued" || state.status === "running";

  useEffect(() => () => { controller.current?.abort(); if (timer.current) clearTimeout(timer.current); }, []);

  async function prepare() {
    if (pending) return;
    controller.current?.abort();
    const request = new AbortController();
    controller.current = request;
    setState({ status: "queued", progress: 0 });

    async function check(method: "GET" | "POST") {
      try {
        const response = await fetch(endpoint, { method, credentials: "include", signal: request.signal });
        const body = await response.json();
        if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Could not prepare this clip. Try again.");
        if (request.signal.aborted) return;
        const next: ExportState = body;
        setState(next);
        if (next.status === "ready" && next.url) {
          // Let the browser stream the file straight to Downloads. A fetch/blob
          // download would buffer the entire video in the editor's RAM.
          const link = document.createElement("a");
          link.href = `${api}${next.url}`;
          link.download = next.filename || "cutlist-clip";
          document.body.appendChild(link);
          link.click();
          link.remove();
        } else if (next.status !== "error") {
          timer.current = setTimeout(() => { void check("GET"); }, 750);
        }
      } catch (error) {
        if (!request.signal.aborted) setState({ status: "error", error: error instanceof TypeError ? "Cannot reach the local worker. Reconnect and try the download again." : error instanceof Error ? error.message : "Clip download failed. Try again." });
      }
    }
    await check("POST");
  }

  const icon = <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></svg>;
  return <div className="clip-download">
    {state.status === "ready" && state.url ? <a className="download-button" href={`${api}${state.url}`} download={state.filename}>{icon}Download clip <span>{mediaKind === "video" ? "MP4" : "M4A"}</span></a> :
      <button className="download-button" onClick={prepare} disabled={pending} aria-busy={pending}>
        {icon}{pending ? state.status === "queued" ? "Queued…" : `Preparing ${state.progress ?? 0}%` : state.status === "error" ? "Retry download" : "Download clip"}
      </button>}
    {pending && <span className="export-status" role="status">{state.status === "queued" ? "Waiting for the local worker" : "Preparing your clip. Download starts when ready."}</span>}
    {state.status === "error" && <span className="export-error" role="alert">{state.error || "Could not export this clip. Please try again."}</span>}
  </div>;
}
