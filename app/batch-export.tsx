"use client";
import { useEffect, useState } from "react";
import { Clip, Result, readResponse } from "./types";
type Batch = { id: string; status: string; progress: number; total?: number; completed?: number; error?: string; url?: string };

export default function BatchExport({ api, result, onSaved }: { api: string; result: Result; onSaved: (clips: Clip[]) => void }) {
  const [batch, setBatch] = useState<Batch | null>(null), [error, setError] = useState("");
  const [selected, setSelected] = useState<number[]>(result.clips.filter(c => c.feedback !== "reject").slice(0, 100).map(c => c.id));
  useEffect(() => {
    if (!batch || !["queued", "running"].includes(batch.status)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const data: Batch = await readResponse(await fetch(`${api}/api/jobs/${result.id}/batches/${batch!.id}`, { credentials: "include", signal: controller.signal }));
        if (controller.signal.aborted) return;
        setBatch(data);
        if (["queued", "running"].includes(data.status)) timer = setTimeout(poll, 800);
      } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Batch status unavailable."); }
    }
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [batch?.id, result.id, api]); // eslint-disable-line react-hooks/exhaustive-deps
  async function start() {
    setError("");
    try { setBatch(await readResponse(await fetch(`${api}/api/jobs/${result.id}/batches`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clips: selected }) }))); }
    catch (e) { setError(e instanceof Error ? e.message : "Batch could not start."); }
  }
  async function applyStyle() {
    setError("");
    try {
      const preferences = await readResponse(await fetch(`${api}/api/preferences`, { credentials: "include" }));
      const data = await readResponse(await fetch(`${api}/api/jobs/${result.id}/batch-style`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ clips: result.clips.filter(c => selected.includes(c.id)).map(c => ({ id: c.id, revision: c.revision })), caption_style: preferences.caption_style }) }));
      onSaved(data.clips);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not apply style."); }
  }
  async function cancel() {
    if (!batch) return;
    try { await readResponse(await fetch(`${api}/api/jobs/${result.id}/batches/${batch.id}/cancel`, { method: "POST", credentials: "include" })); setBatch({ ...batch, status: "cancelled" }); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not cancel batch."); }
  }
  const busy = !!batch && ["queued", "running"].includes(batch.status);
  return <details className="batch-export"><summary>Batch export clips</summary><p>Select up to 100 saved cuts. The ZIP contains videos/audio, SRT, VTT and a manifest of the exact edit revisions. Renders run one at a time to limit memory.</p>
    <div className="editor-actions"><button className="small-button" disabled={busy} onClick={() => setSelected(result.clips.filter(c => c.feedback === "keep").slice(0, 100).map(c => c.id))}>Select kept</button><button className="small-button" disabled={busy} onClick={() => setSelected(result.clips.filter(c => c.feedback !== "reject").slice(0, 100).map(c => c.id))}>Select first 100 eligible</button><button className="text-button" disabled={busy} onClick={() => setSelected([])}>Clear selection</button></div>
    <div className="batch-selection">{result.clips.map(c => <label className="checkbox-label" key={c.id}><input type="checkbox" disabled={busy || !selected.includes(c.id) && selected.length >= 100} checked={selected.includes(c.id)} onChange={e => setSelected(e.target.checked ? [...selected, c.id] : selected.filter(i => i !== c.id))}/>{c.id}. {c.title}{c.feedback === "reject" ? " · skipped" : ""}</label>)}</div>
    <div className="editor-actions"><button className="small-button" disabled={busy || !selected.length} onClick={applyStyle}>Apply my caption default & burn in</button><button className="browse-button" disabled={busy || !selected.length} onClick={start}>{busy ? `Exporting ${batch.completed || 0}/${batch.total || selected.length}…` : `Prepare ${selected.length} clips`}</button></div>
    {busy && <><progress value={batch.progress} max={100}/><button className="text-button" onClick={cancel}>Cancel batch</button></>}{batch?.status === "ready" && batch.url && <a className="export-button" href={`${api}${batch.url}`} download>Download clips ZIP</a>}{(error || batch?.error) && <p role="alert" className="warning">{error || batch?.error}</p>}
  </details>;
}
