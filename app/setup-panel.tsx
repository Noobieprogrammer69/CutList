"use client";
import { useEffect, useState } from "react";
import { readResponse } from "./types";
const API = process.env.NEXT_PUBLIC_WORKER_URL || "";
type Feature = { id: string; name: string; download_mb: number; model?: string; ready: boolean };
type Setup = { features: Feature[]; service_ready: boolean; service_installed: boolean; ffmpeg: boolean; free_disk_gb: number; recovery: string };
type Task = { id: string; feature: string; status: string; progress: number; message: string; error?: string; downloaded_mb?: number; total_mb?: number; log?: string[] };

export default function SetupPanel({ onChanged }: { onChanged?: () => void }) {
  const [setup, setSetup] = useState<Setup | null>(null), [task, setTask] = useState<Task | null>(null), [message, setMessage] = useState("");
  async function refresh() {
    try { setSetup(await readResponse(await fetch(`${API}/api/setup`, { credentials: "include" }))); setMessage(""); onChanged?.(); }
    catch (e) { setMessage(e instanceof Error ? e.message : "Start Cutlist to check its setup."); }
  }
  useEffect(() => {
    void refresh();
    try { const last = localStorage.getItem("cutlist-setup-task"); if (last) void fetch(`${API}/api/setup/tasks/${last}`, { credentials: "include" }).then(readResponse).then(setTask).catch(() => localStorage.removeItem("cutlist-setup-task")); } catch {}
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!task || !["queued", "running"].includes(task.status)) return;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const value: Task = await readResponse(await fetch(`${API}/api/setup/tasks/${task!.id}`, { credentials: "include", signal: controller.signal }));
        if (controller.signal.aborted) return;
        setTask(value);
        if (["queued", "running"].includes(value.status)) timer = setTimeout(poll, 800);
        else if (value.status === "complete") void refresh();
      } catch (e) { if (!controller.signal.aborted) setMessage(e instanceof Error ? e.message : "Setup connection lost. Refresh to reconnect."); }
    }
    void poll(); return () => { controller.abort(); clearTimeout(timer); };
  }, [task?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  async function action(endpoint: string, body?: object) {
    setMessage("");
    try {
      const value = await readResponse(await fetch(`${API}/api/setup/${endpoint}`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json", "X-Cutlist-Setup": "1" }, body: JSON.stringify(body || {}) }));
      if (value.id) { setTask(value); try { localStorage.setItem("cutlist-setup-task", value.id); } catch {} }
      else { setMessage(value.message || value.status); if (endpoint.endsWith("/cancel") && task) setTask({ ...task, status: "cancelled" }); }
    } catch (e) { setMessage(e instanceof Error ? e.message : "Setup failed. Refresh availability and retry."); }
  }
  const busy = !!task && ["queued", "running"].includes(task.status);
  return <details className="setup-panel"><summary>Setup & model downloads</summary>
    <p>Install the components you need here. Downloads require internet; recordings and transcripts are processed by your local worker. No account or paid API is required.</p>
    {setup && <><p>{setup.free_disk_gb.toFixed(1)} GB free · FFmpeg {setup.ffmpeg ? "ready" : "missing"} · local model service {setup.service_ready ? "connected" : "not connected"}</p>
      {!setup.service_ready && <div className="setup-service"><button className="browse-button" disabled={!setup.service_installed || busy} onClick={() => action("service")}>Start local model service</button>{!setup.service_installed && <p>Install Ollama with your system package manager, then refresh. Cutlist does not request administrator access.</p>}</div>}
      {!setup.ffmpeg && <p className="warning">FFmpeg is missing. Install FFmpeg using your system package manager before processing recordings.</p>}
      <div className="setup-features">{setup.features.map(f => <div className="setup-feature" key={f.id}><div><strong>{f.name}</strong><span>{f.model || "Local CPU component"} · approximately {f.download_mb} MB</span></div><button className="small-button" disabled={busy || f.ready || !!f.model && !setup.service_ready} onClick={() => action("tasks", { feature: f.id })}>{f.ready ? "Installed" : "Download & install"}</button></div>)}</div>
      <p className="editor-help">{setup.recovery} Installed weights still need real-footage testing; availability alone does not establish clip quality.</p></>}
    <div className="editor-actions"><button className="small-button" onClick={refresh}>Refresh availability</button><a href={`${API}/api/setup/launcher`} download>Download desktop launcher</a><a href="https://ollama.com/library/embeddinggemma" target="_blank" rel="noreferrer">Search model & terms</a></div>
    {task && <div className="setup-progress" role="status"><strong>{task.feature} · {task.status}</strong><p>{task.message}</p>{busy && <><progress value={task.progress || undefined} max={100}/><button className="text-button" onClick={() => action(`tasks/${task.id}/cancel`)}>Cancel download</button></>}{task.total_mb != null && <p>{task.downloaded_mb} / {task.total_mb} MB</p>}{task.error && <p className="warning">{task.error}</p>}{["error", "cancelled"].includes(task.status) && <button className="small-button" onClick={() => action("tasks", { feature: task.feature })}>Retry installation</button>}{!!task.log?.length && <details><summary>Installation log</summary><pre>{task.log.join("\n")}</pre></details>}</div>}
    {message && <p role="status" className="warning">{message}</p>}
  </details>;
}
