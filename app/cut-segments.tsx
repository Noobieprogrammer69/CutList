"use client";
import { useState } from "react";
import { Clip, Segment } from "./types";

export default function CutSegments({ draft, onChange }: { draft: Clip; onChange: (clip: Clip) => void }) {
  const [a, setA] = useState(draft.start), [b, setB] = useState(Math.min(draft.end, draft.start+1));
  const [history, setHistory] = useState<Clip[]>([]), [message, setMessage] = useState("");
  const parts = draft.segments ?? [{ start: draft.start, end: draft.end }];
  function apply(next: Segment[]) {
    if (!next.length) { setMessage("Keep some footage in the cut."); return; }
    if (next.length > 16) { setMessage("A cut supports up to 16 retained intervals. Remove fewer gaps."); return; }
    setHistory([...history.slice(-19), structuredClone(draft)]);
    onChange({ ...draft, start: next[0].start, end: next.at(-1)!.end, segments: next, output_duration: next.reduce((sum, s) => sum+s.end-s.start, 0) });
    setMessage("Draft updated. Preview the cuts, then save. The source recording is unchanged.");
  }
  function remove(start: number, end: number) {
    if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) { setMessage("Choose a positive interval to remove."); return; }
    const next = parts.flatMap(p => end <= p.start || start >= p.end ? [p] : [ ...(start > p.start ? [{ start: p.start, end: start }] : []), ...(end < p.end ? [{ start: end, end: p.end }] : []) ]).filter(p => p.end-p.start >= .02);
    apply(next);
  }
  function tighten() {
    const spans: Segment[] = [];
    for (const p of parts) for (const cue of draft.captions) {
      const start = Math.max(p.start, cue.start-.12), end = Math.min(p.end, cue.end+.12);
      if (start >= end) continue;
      if (spans.length && start <= spans.at(-1)!.end+.001) spans.at(-1)!.end = Math.max(spans.at(-1)!.end, end);
      else spans.push({ start, end });
    }
    apply(spans);
  }
  return <details className="cut-segments"><summary>Remove pauses or mistakes from this cut</summary><p>Retained portions join in source order. Preview skips removed portions; downloads and subtitles use the same edit. Caption gaps may include meaningful pauses, so review before saving.</p>
    <div className="edit-grid"><label className="field"><span>Remove from · source seconds</span><input type="number" step="0.01" value={a} onChange={e => setA(Number(e.target.value))}/></label><label className="field"><span>Remove to · source seconds</span><input type="number" step="0.01" value={b} onChange={e => setB(Number(e.target.value))}/></label></div><div className="editor-actions"><button className="small-button" onClick={() => remove(a, b)}>Remove interval from video</button><button className="small-button" disabled={!draft.captions.length} onClick={tighten}>Tighten caption gaps</button><button className="text-button" disabled={!history.length} onClick={() => { const old = history.at(-1)!; setHistory(history.slice(0, -1)); onChange(old); setMessage("Draft cut undone."); }}>Undo draft cut</button></div>
    <p className="editor-help">{parts.length} retained interval{parts.length === 1 ? "" : "s"}: {parts.map(p => `${p.start.toFixed(2)}–${p.end.toFixed(2)}s`).join(" · ")}</p>
    <div className="sentence-cuts">{draft.captions.map((cue, i) => <div key={i}><span>{cue.text}</span><button className="text-button" onClick={() => remove(cue.start, cue.end)}>Remove sentence from video</button></div>)}</div>{message && <p role="status">{message}</p>}
  </details>;
}
