"use client";
import { useEffect, useState } from "react";
import { Clip, Cue, Result, readResponse } from "./types";

type Page = { cues: (Cue & { index: number })[]; words: { start: number; end: number; text: string }[]; total: number; offset: number; word_timing: string };
type Match = { id: number; start: number; end: number; text: string; similarity: number; brief_reviewed: boolean; reason: string };
type Search = { id: string; status: string; progress: number; message: string; error?: string; result?: { matches: Match[]; message: string } };

export default function TranscriptWorkbench({ api, result, onCreated, onSeek, onReload }: { api: string; result: Result; onCreated: (c: Clip) => void; onSeek: (time: number) => void; onReload: () => void }) {
  const [page, setPage] = useState<Page | null>(null), [offset, setOffset] = useState(0), [filter, setFilter] = useState("");
  const [start, setStart] = useState(0), [end, setEnd] = useState(Math.min(result.maximum, result.duration)), [title, setTitle] = useState("");
  const [message, setMessage] = useState(""), [busy, setBusy] = useState(false), [removed, setRemoved] = useState<number | null>(null);
  const [brief, setBrief] = useState(""), [search, setSearch] = useState<Search | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${api}/api/jobs/${result.id}/transcript?offset=${offset}&limit=50&query=${encodeURIComponent(filter)}`, { credentials: "include", signal: controller.signal }).then(readResponse).then(setPage).catch(e => { if (!controller.signal.aborted) setMessage(e.message); });
    return () => controller.abort();
  }, [result.id, offset, filter, api]);
  useEffect(() => {
    if (!search || !["queued", "running"].includes(search.status)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value: Search = await readResponse(await fetch(`${api}/api/jobs/${result.id}/search/${search!.id}`, { credentials: "include", signal: controller.signal }));
        if (controller.signal.aborted) return;
        setSearch(value); if (["queued", "running"].includes(value.status)) timer = setTimeout(poll, 700);
      } catch (e) { if (!controller.signal.aborted) setMessage(e instanceof Error ? e.message : "Search connection lost."); }
    }
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [search?.id, result.id, api]); // eslint-disable-line react-hooks/exhaustive-deps
  async function request(endpoint: string, body?: object, method = "POST") {
    return readResponse(await fetch(`${api}/api/jobs/${result.id}${endpoint}`, { method, credentials: "include", headers: { "Content-Type": "application/json" }, ...(body ? { body: JSON.stringify(body) } : {}) }));
  }
  async function create() {
    setBusy(true); setMessage("");
    try { const clip = await request("/clips", { start, end, title: title || "Editor-selected moment" }); onCreated(clip); setMessage(`Created clip ${clip.id}. It is ready to preview, edit and export.`); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Could not create clip."); }
    finally { setBusy(false); }
  }
  function selected() {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) return;
    const element = (node: Node | null) => (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>("[data-source-start]");
    const a = element(selection.anchorNode), b = element(selection.focusNode);
    if (!a || !b || !a.closest(".transcript-pages") || !b.closest(".transcript-pages")) return;
    setStart(Math.min(Number(a.dataset.sourceStart), Number(b.dataset.sourceStart)));
    setEnd(Math.max(Number(a.dataset.sourceEnd), Number(b.dataset.sourceEnd)));
  }
  async function remove(clip: Clip) {
    try { await request(`/clips/${clip.id}?revision=${clip.revision}`, undefined, "DELETE"); setRemoved(clip.id); onReload(); setMessage(`Removed clip ${clip.id}. You can now replace that interval or undo removal.`); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Could not remove the overlapping clip."); }
  }
  const overlaps = result.clips.filter(c => (c.segments ?? [{ start: c.start, end: c.end }]).some(p => start < p.end && end > p.start));
  const searching = !!search && ["queued", "running"].includes(search.status);
  return <details className="transcript-workbench"><summary>Search the recording & create your own cuts</summary>
    <p>Search all spoken content, click a sentence to seek, or highlight words to select an interval. Automatic word timings are estimates; supplied captions select whole cue boundaries. Your source recording is never changed.</p>
    <div className="semantic-search"><label className="field"><span>Find moments by meaning</span><textarea aria-label="Search brief" maxLength={600} value={brief} onChange={e => setBrief(e.target.value)} placeholder="Find the guest’s best practical advice, excluding sponsorships"/></label><button className="browse-button" disabled={searching || brief.trim().length < 3 || !result.caption_count} onClick={async () => { try { setSearch(await request("/search", { query: brief })); } catch (e) { setMessage(e instanceof Error ? e.message : "Search could not start."); } }}>Search full transcript</button>{searching && <><progress max={100} value={search.progress}/><p role="status">{search.message}</p><button className="text-button" onClick={async () => { await request(`/search/${search.id}/cancel`); setSearch({ ...search, status: "cancelled" }); }}>Cancel search</button></>}{search?.error && <p className="warning">{search.error}</p>}{search?.result && <><p>{search.result.message}</p>{search.result.matches.map(m => <div className="semantic-match" key={m.id}><button className="text-button" onClick={() => { setStart(m.start); setEnd(m.end); onSeek(m.start); }}>{m.start.toFixed(2)}–{m.end.toFixed(2)}s · Select passage</button><p>{m.text}</p><small>{m.reason} · {m.brief_reviewed ? "Brief reviewed" : "Similarity only"} · similarity {m.similarity.toFixed(3)}, not a highlight probability</small></div>)}</>}</div>
    <div className="edit-grid"><label className="field"><span>Selection start · source seconds</span><input aria-label="Manual clip start" type="number" min={0} max={result.duration} step="0.01" value={start} onChange={e => setStart(Number(e.target.value))}/></label><label className="field"><span>Selection end · source seconds</span><input aria-label="Manual clip end" type="number" min={0} max={result.duration} step="0.01" value={end} onChange={e => setEnd(Number(e.target.value))}/></label></div>
    <label className="field"><span>New clip title</span><input maxLength={200} value={title} onChange={e => setTitle(e.target.value)}/></label><div className="editor-actions"><button className="browse-button" disabled={busy || start < 0 || end <= start || end-start > result.maximum} onClick={create}>Create clip from selection</button><button className="small-button" onClick={() => onSeek(start)} disabled={!result.media_url}>Seek to selection</button><span>{(end-start).toFixed(2)}s / {result.maximum}s</span></div>
    {!!overlaps.length && <details><summary>Selection overlaps existing clips</summary><p>Shortlisted source intervals stay distinct. Edit an existing cut, or remove it before creating a replacement.</p>{overlaps.map(c => <div className="reference-label" key={c.id}><a href={`#clip-${c.id}`}>Clip {c.id}: {c.title}</a><button className="text-button" onClick={() => remove(c)}>Remove from shortlist</button></div>)}</details>}
    {removed != null && <button className="small-button" onClick={async () => { try { const restored: Clip = await request(`/clips/${removed}/restore`); onCreated(restored); setRemoved(null); setMessage("Removed clip restored."); } catch (e) { setMessage(e instanceof Error ? e.message : "Could not restore."); } }}>Undo removal</button>}
    {message && <p role="status" className="warning">{message}</p>}
    <label className="field"><span>Browse transcript · exact text filter</span><input value={filter} onChange={e => { setFilter(e.target.value); setOffset(0); }}/></label>
    <p className="editor-help">{page?.word_timing}. {page?.total || 0} matching cues.</p>
    <div className="transcript-pages" onMouseUp={selected}>{page?.cues.map(c => { const words = page.words.filter(w => c.start <= w.start && w.start < c.end); return <p key={c.index}><button className="transcript-time" onClick={() => onSeek(c.start)} aria-label={`Seek to ${c.start.toFixed(2)} seconds`}>{c.start.toFixed(2)}s</button> {c.speaker && <strong>{c.speaker}: </strong>}{words.length ? words.map((w, i) => <span key={i} data-source-start={w.start} data-source-end={w.end}>{w.text} </span>) : <span data-source-start={c.start} data-source-end={c.end}>{c.text}</span>} <button className="text-button" onClick={() => setStart(c.start)}>Start here</button><button className="text-button" onClick={() => setEnd(c.end)}>End here</button></p>; })}</div>
    <nav className="editor-actions" aria-label="Transcript pages"><button className="small-button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset-50))}>Previous transcript page</button><span>{offset+1}–{Math.min(offset+50, page?.total || 0)} / {page?.total || 0}</span><button className="small-button" disabled={offset+50 >= (page?.total || 0)} onClick={() => setOffset(offset+50)}>Next transcript page</button></nav>
  </details>;
}
