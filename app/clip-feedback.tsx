"use client";
import { useState } from "react";
import { Clip, readResponse } from "./types";

export default function ClipFeedback({ api, job, clip, onSaved }: { api: string; job: string; clip: Clip; onSaved: (clip: Clip) => void }) {
  const [reason, setReason] = useState(clip.feedback_reason || "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function rate(value: string) {
    setBusy(true); setError("");
    try { onSaved(await readResponse(await fetch(`${api}/api/jobs/${job}/clips/${clip.id}/feedback`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value, reason }) }))); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save feedback."); }
    finally { setBusy(false); }
  }
  return <div className="clip-feedback"><span>Would you edit this?</span><button className="small-button" disabled={busy} aria-pressed={clip.feedback === "keep"} onClick={() => rate(clip.feedback === "keep" ? "unrated" : "keep")}>Keep</button><button className="small-button" disabled={busy} aria-pressed={clip.feedback === "reject"} onClick={() => rate(clip.feedback === "reject" ? "unrated" : "reject")}>Skip</button><input aria-label={`Feedback reason for clip ${clip.id}`} placeholder="Why? (optional; saved with Keep or Skip)" value={reason} maxLength={500} onChange={e => setReason(e.target.value)}/>{error && <span role="alert">{error}</span>}</div>;
}
