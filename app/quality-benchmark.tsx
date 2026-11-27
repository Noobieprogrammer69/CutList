"use client";
import { useEffect, useState } from "react";
import { Result, readResponse } from "./types";

type Label = { id: string; start: number; end: number; kind: string; reason: string };
type Evaluation = { precision: number | null; recall: number | null; keep_rate: number | null; kept_without_trim_rate: number | null; boring_hits: number; note: string };
type Report = { labels: Label[]; evaluation: Evaluation; baseline_evaluation: Evaluation };
const percent = (v: number | null) => v == null ? "Needs labels/reviews" : `${Math.round(v * 100)}%`;

export default function QualityBenchmark({ api, result }: { api: string; result: Result }) {
  const [report, setReport] = useState<Report | null>(null);
  const [start, setStart] = useState(0), [end, setEnd] = useState(Math.min(30, result.duration));
  const [kind, setKind] = useState("good"), [reason, setReason] = useState("");
  const [message, setMessage] = useState(""), [saving, setSaving] = useState(false);
  async function refresh() { setReport(await readResponse(await fetch(`${api}/api/jobs/${result.id}/benchmark`, { credentials: "include" }))); }
  useEffect(() => { void refresh().catch(e => setMessage(e.message)); }, [result.id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function add() {
    setSaving(true); setMessage("");
    try {
      await readResponse(await fetch(`${api}/api/jobs/${result.id}/labels`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ start, end, kind, reason }) }));
      setReason(""); await refresh();
    } catch (e) { setMessage(e instanceof Error ? e.message : "Could not save label."); }
    finally { setSaving(false); }
  }
  return <details className="quality-benchmark"><summary>Real-footage quality benchmark</summary>
    <p>Label worthwhile moments even when Cutlist missed them, and boring or contextless sections. These references are separate from Keep / Skip. Label all worthwhile moments before interpreting precision/recall.</p>
    {result.sample && <p className="warning">This sample is synthetic. Its scores do not establish real-stream quality.</p>}
    <div className="edit-grid"><label className="field"><span>Label start · source seconds</span><input aria-label="Label start seconds" type="number" min={0} max={result.duration} step="0.1" value={start} onChange={e => setStart(Number(e.target.value))}/></label><label className="field"><span>Label end · source seconds</span><input aria-label="Label end seconds" type="number" min={0} max={result.duration} step="0.1" value={end} onChange={e => setEnd(Number(e.target.value))}/></label></div>
    <label className="field"><span>Reference label</span><select value={kind} onChange={e => setKind(e.target.value)}><option value="good">Worth editing</option><option value="boring">Boring / contextless</option></select></label>
    <label className="field"><span>Why? <small>Optional</small></span><input maxLength={500} value={reason} onChange={e => setReason(e.target.value)}/></label>
    <div className="editor-actions"><button className="small-button" disabled={saving} onClick={add}>{saving ? "Saving…" : "Add reference label"}</button><button className="text-button" onClick={() => { void refresh().catch(e => setMessage(e.message)); }}>Refresh evaluation</button><a href={`${api}/api/jobs/${result.id}/benchmark`} download>Download benchmark JSON</a></div>
    {report && <><table className="quality-table"><caption>Temporal matches · at least 50% interval overlap</caption><thead><tr><th>Measure</th><th>Current shortlist</th><th>Basic detector</th></tr></thead><tbody>{([ ["Precision", "precision"], ["Recall", "recall"], ["Kept", "keep_rate"], ["Kept without trimming", "kept_without_trim_rate"] ] as const).map(([name, key]) => <tr key={key}><th>{name}</th><td>{percent(report.evaluation[key])}</td><td>{percent(report.baseline_evaluation[key])}</td></tr>)}<tr><th>Suggestions in boring sections</th><td>{report.evaluation.boring_hits}</td><td>{report.baseline_evaluation.boring_hits}</td></tr></tbody></table><p className="editor-help">{report.evaluation.note} Labels and feedback do not automatically train a model.</p>{report.labels.map(l => <div className="reference-label" key={l.id}><span>{l.start.toFixed(1)}–{l.end.toFixed(1)}s · {l.kind} · {l.reason}</span><button className="text-button" onClick={async () => { try { await readResponse(await fetch(`${api}/api/jobs/${result.id}/labels/${l.id}`, { method: "DELETE", credentials: "include" })); await refresh(); } catch (e) { setMessage(e instanceof Error ? e.message : "Could not remove label."); } }}>Remove</button></div>)}</>}
    {message && <p role="alert">{message}</p>}
  </details>;
}
