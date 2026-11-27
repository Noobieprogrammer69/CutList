"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { constrainClipSeek, stopAtClipEnd } from "./preview";
import ThemeToggle from "./theme-toggle";
import ClipDownload from "./clip-download";

import { Clip, Result, Job, readResponse } from "./types";
import { uploadMedia } from "./upload";
import ClipEditor from "./clip-editor";
import ClipFeedback from "./clip-feedback";
import QualityBenchmark from "./quality-benchmark";
import BatchExport from "./batch-export";
import SetupPanel from "./setup-panel";
import TranscriptWorkbench from "./transcript-workbench";
import PreferencePanel from "./preference-panel";

const API = process.env.NEXT_PUBLIC_WORKER_URL || "";
function tc(value: number, exact = false) {
  const ms = Math.round(value * 1000), seconds = Math.floor(ms / 1000);
  const base = `${seconds >= 3600 ? `${Math.floor(seconds / 3600).toString().padStart(2, "0")}:` : ""}${Math.floor(seconds / 60 % 60).toString().padStart(2, "0")}:${(seconds % 60).toString().padStart(2, "0")}`;
  return exact ? `${base}.${(ms % 1000).toString().padStart(3, "0")}` : base;
}
function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    cut: <><path d="m4 4 16 16M4 20 20 4"/><circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/></>,
    upload: <><path d="M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5"/></>,
    play: <path d="m8 4 12 8-12 8Z"/>,
    file: <><path d="M14 2H5v20h14V7l-5-5Zm0 0v6h5M8 13h8m-8 4h6"/></>,
    sparkle: <><path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/><path d="M21 2v4m-2-2h4"/></>,
    download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
    check: <path d="m5 12 4 4L19 6"/>,
    wave: <path d="M3 10v4m4-7v10m5-15v20m5-16v12m4-8v4"/>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/></>,
    x: <path d="m6 6 12 12M6 18 18 6"/>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.file}</svg>;
}

export default function Home() {
  const [media, setMedia] = useState<File | null>(null);
  const [captions, setCaptions] = useState<File | null>(null);
  const [sample, setSample] = useState(false);
  const [sampleReplays, setSampleReplays] = useState(false);
  const [goal, setGoal] = useState("mixed");
  const [profile, setProfile] = useState("general");
  const [visualReview, setVisualReview] = useState(false);
  const [visionModel, setVisionModel] = useState({ available: false, message: "Checking optional visual model…" });
  const [hardwareInfo, setHardwareInfo] = useState("Checking hardware…");
  const [contentReview, setContentReview] = useState(true);
  const [contentModel, setContentModel] = useState<{ available: boolean; message: string }>({ available: false, message: "Checking local content model…" });
  const [limits, setLimits] = useState({ max_upload_gb: 64, max_duration_hours: 16 });
  const [clipSearch, setClipSearch] = useState("");
  const [clipFilter, setClipFilter] = useState("all");
  const [clipSort, setClipSort] = useState("rank");
  const [page, setPage] = useState(0);
  const [renderedUrl, setRenderedUrl] = useState("");
  const [previewPreparing, setPreviewPreparing] = useState<number | null>(null);
  const previewController = useRef<AbortController | null>(null);
  const pendingPreview = useRef(false);
  const [maximum, setMaximum] = useState(45);
  const [keywords, setKeywords] = useState("");
  const [replays, setReplays] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [localUrl, setLocalUrl] = useState("");
  const [active, setActive] = useState<Clip | null>(null);
  const [health, setHealth] = useState("Checking local worker…");
  const player = useRef<HTMLMediaElement | null>(null);
  const interval = useRef<Clip | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mediaInput = useRef<HTMLInputElement>(null);
  const captionInput = useRef<HTMLInputElement>(null);
  const uploadController = useRef<AbortController | null>(null);
  const [chaptered, setChaptered] = useState(true);
  const [chapterSeconds, setChapterSeconds] = useState(300);
  const pendingSeek = useRef<number | null>(null);
  const [automatic, setAutomatic] = useState(true);
  const [sampleCaptions, setSampleCaptions] = useState(true);
  const [retention, setRetention] = useState(7);
  const [projects, setProjects] = useState<Job[]>([]);
  const [ready, setReady] = useState(false);
  const [accessKey, setAccessKey] = useState("");
  const [locked, setLocked] = useState(false);
  const [uploadNote, setUploadNote] = useState("");
  const [quality, setQuality] = useState<{ reviewed: number; kept: number; rejected: number; trimmed: number } | null>(null);
  async function checkModels(signal?: AbortSignal) {
    const data = await readResponse(await fetch(`${API}/api/health`, { credentials: "include", signal }));
    setVisionModel(data.vision_model || { available: false, message: "Visual model unavailable." });
    setHardwareInfo(data.hardware?.message || "CPU processing");
    setContentModel(data.content_model || { available: false, message: "Restart the worker to enable content review." });
    setLimits({ max_upload_gb: data.max_upload_gb || 64, max_duration_hours: data.max_duration_hours || 16 });
    setHealth(data.ffmpeg ? `Worker connected · speech model ${data.speech_model ? "ready" : "optional download"}` : "FFmpeg is missing — install it to analyze media");
  }
  async function refreshProjects() {
    setProjects(await readResponse(await fetch(`${API}/api/jobs`, { credentials: "include" })));
    setQuality(await readResponse(await fetch(`${API}/api/quality`, { credentials: "include" })));
  }
  async function unlock() {
    try {
      await readResponse(await fetch(`${API}/api/session`, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ key: accessKey }) }));
      setLocked(false); setReady(true); setAccessKey(""); setError(""); await refreshProjects();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not unlock the workspace."); }
  }

  useEffect(() => {
    const controller = new AbortController();
    async function initialize() {
      try {
        const session = await readResponse(await fetch(`${API}/api/session`, { credentials: "include", signal: controller.signal }));
        setLocked(!session.unlocked); setReady(session.unlocked);
        await checkModels(controller.signal);
        if (session.unlocked) await refreshProjects();
      } catch { if (!controller.signal.aborted) setHealth("Start the local worker with npm run dev"); }
    }
    void initialize();
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const url = media ? URL.createObjectURL(media) : "";
    setLocalUrl(url);
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [media]);
  useEffect(() => {
    if (!job?.id || !["queued", "running"].includes(job.status)) return;
    const controller = new AbortController();
    let polling: ReturnType<typeof setTimeout>;
    let retries = 0;
    let seenSections = -1;
    async function poll() {
      try {
        const response = await fetch(`${API}/api/jobs/${job!.id}?compact=true&since_sections=${seenSections}`, { credentials: "include", signal: controller.signal });
        if (!response.ok) throw new Error("The local worker could not find this analysis. Please try again.");
        const data: Job = await response.json();
        retries = 0;
        if (controller.signal.aborted) return;
        if (data.status === "complete") {
          player.current?.pause(); interval.current = null; setActive(null);
          setJob(data); setResult(data.result!); setBusy(false); void refreshProjects().catch(() => {}); return;
        }
        if (["error", "cancelled"].includes(data.status)) {
          setJob(data); setError(data.status === "error" ? data.error || data.message : ""); setBusy(false); void refreshProjects().catch(() => {}); return;
        }
        if (data.partial_result) { setResult(data.partial_result); seenSections = data.partial_result.coverage?.sections_complete ?? seenSections; }
        setJob(data);
        polling = setTimeout(poll, 650);
      } catch (err) {
        if (controller.signal.aborted) return;
        if (++retries < 4) { polling = setTimeout(poll, 1500); return; }
        setError(err instanceof Error ? err.message : "Worker connection lost. Restart npm run dev."); setBusy(false);
      }
    }
    polling = setTimeout(poll, 100);
    return () => { controller.abort(); clearTimeout(polling); };
  // Keep one polling loop per job; progress updates do not recreate it.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id]);
  useEffect(() => () => { uploadController.current?.abort(); if (timer.current) clearTimeout(timer.current); }, []);

  function stopAtEnd() {
    const node = player.current, clip = interval.current;
    if (stopAtClipEnd(node, clip)) {
      if (timer.current) clearTimeout(timer.current);
    }
  }
  function scheduleStop() {
    if (timer.current) clearTimeout(timer.current);
    const node = player.current, clip = interval.current;
    if (!node || !clip || node.paused) return;
    if (node.currentTime < clip.start || node.currentTime >= clip.end) node.currentTime = clip.start;
    timer.current = setTimeout(() => { stopAtEnd(); scheduleStop(); }, Math.max(10, Math.min(50, (clip.end - node.currentTime) * 1000 / node.playbackRate)));
  }
  function resetResults() {
    previewController.current?.abort(); setRenderedUrl(""); setPreviewPreparing(null); pendingPreview.current = false;
    player.current?.pause(); interval.current = null;
    if (timer.current) clearTimeout(timer.current);
    setActive(null); setResult(null); setJob(null); setError("");
  }
  function chooseMedia(file: File | null) {
    resetResults(); setSample(false); setSampleReplays(false); setMedia(file);
    if (!file && mediaInput.current) mediaInput.current.value = "";
  }
  function loadSample() {
    resetResults(); setMedia(null); setCaptions(null); setSample(true); setSampleReplays(false); setReplays("");
  }
  async function findClips(reuseId?: string, refreshTranscript = false) {
    resetResults(); setBusy(true); setUploadProgress(0); setUploadNote("");
    const controller = new AbortController(); uploadController.current = controller;
    const form = new FormData();
    if (captions) form.append("captions", captions);
    form.append("maximum", String(maximum)); form.append("keywords", keywords);
    form.append("replay_markers", replays); form.append("sample", String(!reuseId && sample)); form.append("sample_replays", String(sampleReplays));
    form.append("chaptered", String(chaptered)); form.append("chapter_seconds", String(chapterSeconds));
    form.append("profile", profile); form.append("visual_review", String(visualReview));
    form.append("goal", goal); form.append("content_review", String(contentReview)); if (reuseId) form.append("reuse_job", reuseId); form.append("refresh_transcript", String(refreshTranscript));
    form.append("sample_captions", String(sampleCaptions)); form.append("auto_transcribe", String(automatic)); form.append("retention_days", String(retention));
    try {
      if (media && !reuseId) form.append("upload_id", await uploadMedia(API, media, setUploadProgress, controller.signal));
      const data = await readResponse(await fetch(`${API}/api/jobs`, { method: "POST", credentials: "include", signal: controller.signal, body: form }));
      setJob({ id: data.id, status: "queued", progress: 1, message: "Queued for analysis" });
      void refreshProjects().catch(() => {});
    } catch (err) {
      if (controller.signal.aborted) setUploadNote("Upload paused. Click Find clips with the same recording to resume from its saved position.");
      else setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }
  async function openProject(id: string) {
    try {
      const state: Job = await readResponse(await fetch(`${API}/api/jobs/${id}?compact=true`, { credentials: "include" }));
      resetResults(); setSample(false); setMedia(null); setCaptions(null); setJob(state); setResult(state.result || state.partial_result || null);
      if (state.result) { setProfile(state.result.profile || "general"); setGoal(state.result.goal || "mixed"); setMaximum(state.result.maximum); setReplays(state.result.replays.map(t => tc(t, true)).join(", ")); }
      if (mediaInput.current) mediaInput.current.value = ""; if (captionInput.current) captionInput.current.value = "";
      setBusy(["queued", "running"].includes(state.status));
      if (state.status === "error") setError(state.error || state.message);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not open project."); }
  }
  async function projectAction(id: string, action: "cancel" | "retry" | "delete") {
    try {
      await readResponse(await fetch(`${API}/api/jobs/${id}${action === "delete" ? "" : `/${action}`}`, { method: action === "delete" ? "DELETE" : "POST", credentials: "include" }));
      if (action === "delete") { if (job?.id === id) resetResults(); }
      else if (action === "retry") { setJob(null); setTimeout(() => { void openProject(id); }, 0); }
      else if (job?.id === id) { setBusy(false); setJob({ ...job, status: "cancelled", message: "Analysis cancelled" }); }
      await refreshProjects();
    } catch (err) { setError(err instanceof Error ? err.message : "Project action failed."); }
  }
  function updateClip(clip: Clip) {
    setResult(previous => previous ? { ...previous, clips: previous.clips.map(c => c.id === clip.id ? clip : c) } : previous);
    player.current?.pause(); interval.current = null; setActive(null);
    void refreshProjects().catch(() => {});
  }
  async function preview(clip: Clip) {
    previewController.current?.abort();
    const controller = new AbortController(); previewController.current = controller;
    if (result?.preview_mode === "clips") {
      const saved = result.clips.find(c => c.id === clip.id);
      if (!saved || saved.start !== clip.start || saved.end !== clip.end || JSON.stringify(saved.segments) !== JSON.stringify(clip.segments)) { setError("Save this timing edit before preparing a preview for this format."); return; }
      setPreviewPreparing(clip.id); setError("");
      try {
        const endpoint = `${API}/api/jobs/${result.id}/clips/${clip.id}/export?revision=${clip.revision}`;
        let state = await readResponse(await fetch(endpoint, { method: "POST", credentials: "include", signal: controller.signal }));
        while (["queued", "running"].includes(state.status)) {
          await new Promise(resolve => setTimeout(resolve, 750));
          if (controller.signal.aborted) return;
          state = await readResponse(await fetch(endpoint, { credentials: "include", signal: controller.signal }));
        }
        if (state.status !== "ready") throw new Error(state.error || "Preview could not be prepared.");
        const url = `${API}/api/jobs/${result.id}/clips/${clip.id}/play?revision=${clip.revision}`;
        interval.current = { ...clip, start: 0, end: (clip.output_duration ?? (clip.segments?.reduce((sum, p) => sum+p.end-p.start, 0) ?? clip.end-clip.start)), segments: undefined }; setActive(clip);
        if (url === renderedUrl && player.current) { player.current.currentTime = 0; await player.current.play(); scheduleStop(); }
        else { pendingPreview.current = true; setRenderedUrl(url); }
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Preview failed."); }
      finally { if (!controller.signal.aborted) setPreviewPreparing(null); }
    } else {
      interval.current = clip; setActive(clip);
      const node = player.current;
      if (!node) return;
      if (node.readyState < 1) { pendingPreview.current = true; return; }
      node.currentTime = clip.start;
      try { await node.play(); scheduleStop(); } catch { setError("This recording cannot play in your browser. Try an H.264/AAC MP4 or download the selected clip."); }
    }
    document.getElementById("preview")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  async function reloadResult() {
    try { const state = await readResponse(await fetch(`${API}/api/jobs/${result!.id}?compact=true`, { credentials: "include" })); setResult(state.result); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not reload project."); }
  }
  function seekSource(time: number) {
    if (result?.preview_mode === "clips") { setError("This format needs a prepared clip preview. Create/save your cut, then use Preview clip."); return; }
    interval.current = null; setActive(null); player.current?.pause();
    if (renderedUrl) { pendingSeek.current = time; setRenderedUrl(""); }
    else if (player.current?.readyState) player.current.currentTime = time;
    else pendingSeek.current = time;
    document.getElementById("preview")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }
  const previewUrl = renderedUrl || (result ? result.preview_mode !== "clips" && result.media_url ? `${API}${result.media_url}` : "" : sample ? "/sample/studio-notes.mp4" : localUrl);
  const filteredClips = useMemo(() => {
    const query = clipSearch.trim().toLocaleLowerCase();
    const clips = (result?.clips || []).filter(c => (!query || `${c.title} ${c.excerpt}`.toLocaleLowerCase().includes(query)) &&
      (clipFilter === "all" || clipFilter === "reviewed" && ["local_model", "local_vision"].includes(c.selection_method || "") || clipFilter === "kept" && c.feedback === "keep" || clipFilter === "audio" && !c.transcript_used));
    return clipSort === "time" ? clips.toSorted((a, b) => a.start - b.start) : clips;
  }, [result, clipSearch, clipFilter, clipSort]);
  const pageSize = 20;
  const currentPage = Math.min(page, Math.max(0, Math.ceil(filteredClips.length / pageSize) - 1));
  const visibleClips = filteredClips.slice(currentPage * pageSize, (currentPage + 1) * pageSize);
  useEffect(() => { setPage(0); }, [result?.id, clipSearch, clipFilter, clipSort]);
  const audioOnly = result ? result.media_kind === "audio" : !!media?.type.startsWith("audio/");
  const playable = !!previewUrl;
  const mediaProps = {
    onLoadedMetadata: () => { if (pendingSeek.current != null && player.current) { player.current.currentTime = pendingSeek.current; pendingSeek.current = null; } if (pendingPreview.current && player.current && interval.current) { pendingPreview.current = false; player.current.currentTime = interval.current.start; void player.current.play().then(scheduleStop).catch(() => setError("Use the player controls to start playback.")); } },
    src: previewUrl, controls: true, preload: "metadata", onTimeUpdate: stopAtEnd, onPlay: scheduleStop, onRateChange: scheduleStop,
    onSeeking: () => constrainClipSeek(player.current, interval.current),
    onPause: () => { if (timer.current) clearTimeout(timer.current); },
    ref: (node: HTMLMediaElement | null) => { player.current = node; },
    onError: () => { if (result) setError("Preview cannot be decoded in this browser. Re-export the source as H.264/AAC MP4 or MP3."); },
  };

  return <div className="app-shell">
    <aside className="rail" aria-label="Brand"><a className="brand-mark" href="/" aria-label="Cutlist home"><Icon name="cut" size={25}/></a><span className="rail-label">THE EDIT STARTS HERE</span><span className="rail-bottom">CL / 01</span></aside>
    <main>
      <header className="topbar"><a href="/" className="wordmark">cutlist<span>®</span></a><div className="topbar-meta"><span>FIND THE MOMENTS WORTH EDITING.</span><span className="local-tag"><span/>EDITOR WORKSPACE</span><ThemeToggle/></div></header>
      <section className="intro"><div><div className="eyebrow">LESS SCRUBBING. MORE STORY.</div><h1>Long recording.<br/><span>Shortlist, sorted.</span></h1><p>Find the moments worth editing.<br className="mobile-break"/> A little signal. A lot less timeline.</p></div><div className="intro-note"><Icon name="wave" size={28}/><p>Made for the edit.<br/>Ready for your next cut.</p></div></section>
      <section className="speed-feature" aria-label="Fast local workflow"><span className="feature-badge">FAST LOCAL WORKFLOW</span><div><strong>Long streams. Original-resolution exports.</strong><p>See reviewed sections as they finish. Reuse transcripts and saved work. Optional visual context and hardware-aware model processing; export cuts from the original recording.</p></div><span className="feature-limit">Up to {limits.max_duration_hours} hours · no five-clip cap</span></section>
      {ready && <SetupPanel onChanged={() => { void checkModels().catch(() => {}); }}/>}
      {locked && <div className="access-panel"><label className="field"><span>Workspace access key</span><input type="password" value={accessKey} onChange={e => setAccessKey(e.target.value)} onKeyDown={e => { if (e.key === "Enter") void unlock(); }}/></label><button className="browse-button" onClick={unlock}>Unlock workspace</button></div>}
      {ready && <details className="projects-panel"><summary>Saved projects <span>{projects.length}</span></summary><p>Private to this browser workspace. No account needed. Clearing browser cookies removes access; download your work before files expire.</p><button className="small-button" onClick={() => { void refreshProjects().catch(e => setError(e.message)); }}>Refresh projects</button>{projects.map(project => <div className="project-row" key={project.id}><button className="text-button" disabled={busy} onClick={() => openProject(project.id)}>{project.source_name || "Untitled recording"}</button><span>{project.status} · expires {new Date((project.expires || 0) * 1000).toLocaleDateString()}</span>{["queued", "running"].includes(project.status) && <button className="small-button" onClick={() => projectAction(project.id, "cancel")}>Cancel</button>}{["error", "cancelled"].includes(project.status) && <button className="small-button" onClick={() => projectAction(project.id, "retry")}>Retry</button>}<button className="text-button" onClick={() => projectAction(project.id, "delete")}>Delete files</button></div>)}{quality && <p className="quality-note">Your reviews: {quality.kept} kept · {quality.rejected} skipped · {quality.trimmed} adjusted. <a href={`${API}/api/quality`} download="cutlist-feedback.json">Export feedback</a></p>}</details>}
      <div className="workspace">
        <section className="source-panel" aria-labelledby="source-heading">
          <div className="section-heading"><h2 id="source-heading"><span className="step">01</span>Your source</h2><button className="text-button" onClick={loadSample} disabled={busy}><Icon name="play" size={14}/>Load sample</button></div>
          <div className={`dropzone ${dragging ? "dragging" : ""} ${sample || media ? "loaded" : ""}`} onDragOver={e => { e.preventDefault(); if (!busy) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); if (!busy && e.dataTransfer.files[0]) chooseMedia(e.dataTransfer.files[0]); }}>
            <input ref={mediaInput} id="media-input" type="file" accept="audio/*,video/*,.mkv,.mov,.m4a,.flac,.ogg" disabled={busy} onChange={e => chooseMedia(e.target.files?.[0] || null)} aria-label="Upload media"/>
            {sample || media ? <><div className="file-orbit"><Icon name="check" size={26}/></div><strong>{sample ? "Studio notes" : media!.name}</strong><span>{sample ? "Sample podcast · synthesized voices · matching captions" : `${(media!.size / 1024 / 1024).toFixed(1)} MB · ready to analyze`}</span><button className="text-button" onClick={() => mediaInput.current?.click()} disabled={busy}>Choose another recording</button><button className="text-button" onClick={() => chooseMedia(null)} disabled={busy}>Clear source</button></> : <><div className="file-orbit"><Icon name="upload" size={27}/></div><strong>Drop a recording here</strong><span>Podcasts, interviews, streams. You know the ones.</span><button className="browse-button" onClick={() => mediaInput.current?.click()} disabled={busy}>Browse files</button><small>MP4, MOV, WebM, MP3, WAV & more · up to {limits.max_upload_gb} GB / {limits.max_duration_hours} hours</small></>}
          </div>
          <div className="caption-upload"><div className="caption-icon"><Icon name="file"/></div><div><strong>Give the story some context <span>Optional</span></strong><p>{sample ? "Matching captions available below" : captions ? captions.name : "Add SRT or WebVTT captions. Quiet gems count, too."}</p></div><input ref={captionInput} type="file" accept=".srt,.vtt" disabled={busy || sample} aria-label="Upload captions" onChange={e => { resetResults(); setCaptions(e.target.files?.[0] || null); }}/>{captions && !sample ? <button className="icon-button" aria-label="Remove captions" onClick={() => { resetResults(); setCaptions(null); if (captionInput.current) captionInput.current.value = ""; }} disabled={busy}><Icon name="x" size={17}/></button> : <button className="small-button" onClick={() => captionInput.current?.click()} disabled={busy || sample}>{sample ? "Included" : "Add captions"}</button>}</div>
          {sample && <div className="sample-note">An original, synthetic conversation about editing. The same analysis runs on your uploads. <a href="/sample/studio-notes.vtt" download>Download captions</a></div>}
          <div className="speech-settings"><label className="checkbox-label"><input type="checkbox" checked={automatic} disabled={busy} onChange={e => setAutomatic(e.target.checked)}/>Transcribe English speech when captions are missing <small>Free CPU model; works offline after download</small></label>{sample && <label className="checkbox-label"><input type="checkbox" checked={sampleCaptions} disabled={busy} onChange={e => setSampleCaptions(e.target.checked)}/>Use matching sample captions <small>Uncheck to try speech recognition</small></label>}</div>
          <div className="chapter-settings"><label className="checkbox-label"><input type="checkbox" checked={chaptered} disabled={busy} onChange={e => setChaptered(e.target.checked)}/>Transcribe and review chapter by chapter <small>Early clips from long streams · models run sequentially · checkpoints resume completed work</small></label><label className="field"><span>Chapter size</span><select value={chapterSeconds} disabled={busy || !chaptered} onChange={e => setChapterSeconds(Number(e.target.value))}><option value={60}>1 minute · more model switching</option><option value={300}>5 minutes</option><option value={600}>10 minutes</option><option value={900}>15 minutes</option></select></label></div>
          <div className="selection-settings"><label className="field"><span>Editorial profile</span><select value={profile} disabled={busy} onChange={e => setProfile(e.target.value)}><option value="general">General conversation</option><option value="gaming">Gaming / streams</option><option value="interview">Interview / podcast</option><option value="tutorial">Tutorial / explanation</option><option value="storytelling">Storytelling</option></select></label><label className="field"><span>What makes a good clip for this recording?</span><select value={goal} disabled={busy} onChange={e => setGoal(e.target.value)}><option value="mixed">A mix of strong moments</option><option value="funny">Funny exchanges & stories</option><option value="stories">Stories, explanations & strong opinions</option></select></label><label className="checkbox-label"><input type="checkbox" checked={contentReview} disabled={busy} onChange={e => setContentReview(e.target.checked)}/>Propose clips, then run a final editorial check <small>Free local language model · two passes add review time</small></label><p className={contentModel.available ? "model-ready" : "model-missing"}>{contentModel.available ? "Local content review is ready." : "Content review model is not ready. Basic candidates alone cannot judge meaning or humor."}</p><label className="checkbox-label"><input type="checkbox" checked={visualReview} disabled={busy || !contentReview} onChange={e => setVisualReview(e.target.checked)}/>Review short visual sequences <small>Optional local vision model · ordered frames around events · adds processing time</small></label><p className="editor-help">{visionModel.message}</p><p className="editor-help">{hardwareInfo}</p><details className="model-setup"><summary>Model setup & availability</summary><p>{contentModel.message}</p><p>For content review, start Ollama in another terminal, then download the free model once:</p><pre>ollama serve</pre><pre>npm run editor:download</pre><p>For sound-based laughter detection, separately run:</p><pre>npm run model:download</pre><p>Optional visual model, face-following dependency, and anonymous voice model:</p><pre>npm run vision:download</pre><pre>npm run tracking:install</pre><pre>npm run speakers:download</pre><button className="small-button" onClick={() => { void checkModels().catch(e => setError(e.message)); }}>Check models again</button><p>Recordings stay with your local worker. Downloads install weights; no paid API is used.</p></details></div>
          <div className="settings-grid"><label className="field"><span>Maximum clip length</span><div className="number-field"><Icon name="clock" size={17}/><input type="number" min="5" max="180" value={maximum} disabled={busy} onChange={e => setMaximum(Number(e.target.value))}/><span>seconds</span></div></label><label className="field"><span>Keywords <small>Optional</small></span><input placeholder="e.g. creative process, clients" value={keywords} maxLength={1600} disabled={busy} onChange={e => setKeywords(e.target.value)}/></label></div>
          <details className="replay-settings"><summary>Have replay timestamps? <span>Optional</span></summary><p>Add timestamps you collected yourself. A video file contains no viewer replay history.</p><label className="field"><span>Replay markers</span><input aria-label="Replay markers" placeholder="00:24, 01:12, 02:08" value={replays} disabled={busy} onChange={e => setReplays(e.target.value)}/></label>{sample && <label className="checkbox-label"><input type="checkbox" checked={sampleReplays} disabled={busy} onChange={e => setSampleReplays(e.target.checked)}/>Include illustrative sample replay markers <small>Demo markers, not audience data</small></label>}</details>
          <label className="field retention-field"><span>Automatically delete project files after</span><select value={retention} disabled={busy} onChange={e => setRetention(Number(e.target.value))}><option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option></select></label>
          <button className="find-button" onClick={() => findClips()} disabled={!ready || busy || (!sample && !media && !captions) || maximum < 5 || maximum > 180 || !Number.isFinite(maximum)}><Icon name="sparkle" size={19}/>{busy ? "Finding your moments…" : "Find clips"}<span>{busy ? "IN PROGRESS" : "ALL DISTINCT CANDIDATES"}</span></button>
          {result && <><button className="reanalyze-button" disabled={busy} onClick={() => findClips(result.id)}>Re-analyze saved recording · reuse transcript</button>{result.media_url && <button className="text-button" disabled={busy} onClick={() => findClips(result.id, true)}>Transcribe this recording again · slower fresh pass</button>}</>}
          <p className="privacy-note">Recordings are sent to the Cutlist worker. This local launch stores them on this laptop; a hosted worker stores them on its server. Files expire after your selected period.</p>
          {busy && <div className="progress-panel" role="status" aria-live="polite"><div><span>{job?.message || (uploadProgress < 100 ? "Sending recording to your local worker" : "Preparing local analysis")}</span><strong>{job?.progress ?? uploadProgress}%</strong></div><progress value={job?.progress ?? uploadProgress} max="100"/><small>{job ? "Projects keep running if you close this tab." : "Uploads use saved 8 MB chunks. Reselect the same file to resume."}</small>{job?.eta_seconds != null && job.eta_seconds > 0 && <p className="eta-note">Speech pass: about {Math.ceil(job.eta_seconds / 60)} minutes remaining at the current rate. Content review time is additional.</p>}{job ? <button className="small-button" onClick={() => projectAction(job.id, "cancel")}>Cancel analysis</button> : <button className="small-button" onClick={() => uploadController.current?.abort()}>Pause upload</button>}</div>}
          {uploadNote && <p role="status" className="warning">{uploadNote}</p>}
          {job && ["error", "cancelled"].includes(job.status) && <button className="small-button" onClick={() => projectAction(job.id, "retry")}>Retry analysis</button>}
          {error && <div className="error" role="alert">{error}</div>}
        </section>
        <section className="preview-panel" id="preview" aria-labelledby="preview-heading"><div className="section-heading"><h2 id="preview-heading"><span className="step">02</span>The listening room</h2><span className="quiet-label">{active ? `CLIP ${String(active.id).padStart(2, "0")}` : "SOURCE PREVIEW"}</span></div>
          <div className={`player-wrap ${playable ? "has-media" : ""}`}>{playable ? audioOnly ? <div className="audio-player"><Icon name="wave" size={50}/><p>{result?.source_name || media?.name}</p><audio key={previewUrl} {...mediaProps}/></div> : <video key={previewUrl} {...mediaProps} aria-label="Recording preview"/> : <div className="empty-preview"><div className="preview-art"><div/><div/><div/><div/><div/><div/><div/><div/><div/><div/><div/><div/><div/><div/><div/></div><p>{result?.preview_mode === "clips" ? "Preview only the cut you choose." : "The good stuff is in there."}</p><span>{result?.preview_mode === "clips" ? "Click Preview clip below. No full-video conversion needed." : "Add a recording. Let’s find it."}</span></div>}</div>
          <div className="player-caption"><span>{active ? `${tc(active.start, true)} — ${tc(active.end, true)}` : sample ? "STUDIO NOTES / SAMPLE RECORDING" : result ? result.source_name : "A FRESH PERSPECTIVE ON YOUR FOOTAGE"}</span>{active && result?.preview_mode !== "clips" ? <button className="text-button" onClick={() => { interval.current = null; setActive(null); if (timer.current) clearTimeout(timer.current); player.current?.play().catch(() => setError("Use the player controls to start playback.")); }}>Play full source</button> : <span>{result ? tc(result.duration) : sample ? "SYNTHESIZED DEMO" : ""}</span>}</div>
          {active && <p className="interval-note">Preview plays this interval only and stops at {tc(active.end, true)}.</p>}
          <div className="signal-note"><span className="eyebrow">SIGNALS, NOT GUESSWORK</span><h3>Good clips need a reason.</h3><p>A shift in energy. A real reaction. An answer that lands. We look for the moment, then make room for its setup.</p><div className="signal-chips"><span><Icon name="wave" size={15}/>Audio dynamics</span><span><Icon name="file" size={15}/>Spoken context</span><span><Icon name="sparkle" size={15}/>Reactions</span></div></div>
        </section>
      </div>
      <section className="shortlist" aria-labelledby="shortlist-heading"><div className="shortlist-header"><div><span className="eyebrow">THE START OF YOUR NEXT EDIT</span><h2 id="shortlist-heading">Your shortlist<span>{result ? String(result.clips.length).padStart(2, "0") : "—"}</span></h2></div>{result && !result.provisional && <div className="shortlist-exports">{result.media_url && <a className="export-button" href={`${API}/api/jobs/${result.id}/timeline.xml`} download>Premiere / Resolve XML</a>}<a className="export-button" href={`${API}/api/jobs/${result.id}/shortlist`} download><Icon name="download" size={17}/>Export shortlist</a></div>}</div>
        {result ? <><div className="analysis-summary"><div><Icon name="check" size={17}/>{result.clips.length ? `${result.clips.length} distinct candidates found` : "No strong moments found"}<span>·</span>{result.provisional ? `${result.coverage?.sections_complete || 0}/${result.coverage?.sections_total || 0} sections reviewed` : `${tc(result.duration)} analyzed`}<span>·</span>{result.caption_count ? `${result.caption_count} caption cues` : "No transcript"}</div><span>{result.selection_method === "local_model" ? "Reviewed for meaning and payoff" : result.selection_method === "local_vision" ? "Visual model candidates · review predictions" : "Basic / mixed candidates · inspect review evidence"}</span></div>
          {result.provisional && <p className="progressive-note" role="status">Reviewed results are arriving while later sections continue. Preview now; ranking and clip IDs may change. Editing and export unlock when analysis finishes. Chapter mode alternates transcription and review while keeping only one model active at a time.</p>}
          {result.waveform.length > 0 && <div className="waveform" aria-label="Measured source waveform">{result.waveform.map((db, i) => { const time = i / result.waveform.length * result.duration; const selected = result.clips.some(c => (c.segments ?? [{ start: c.start, end: c.end }]).some(p => p.start <= time && p.end >= time)); return <span key={i} className={selected ? "selected" : ""} style={{ height: `${Math.max(3, Math.min(100, (db + 65) * 1.6))}%` }}/>; })}</div>}
          <p className="project-expiry">Project files expire {new Date(result.expires * 1000).toLocaleString()}. XML uses the original media and current timings; relink the recording in your editor. Skipped clips are excluded from XML.</p>
          {!["local_model", "local_vision"].includes(result.selection_method || "") && <p className="warning">Some or all candidates have not been model-reviewed. Check the evidence and review status on each cut; basic candidates cannot establish meaning or humor. Enable the local content model, then re-analyze using this saved transcript.</p>}
          {result.timings && <p className="analysis-timing">This pass: {result.timings.total_seconds.toFixed(1)} seconds{result.timings.first_results_seconds != null ? ` · first reviewed results at ${result.timings.first_results_seconds.toFixed(1)}s` : ""}{result.timings.transcription_reused ? " · saved transcript reused" : ""}. Exports use your original recording; a 360p source cannot provide HD detail.</p>}
          {!result.provisional && <TranscriptWorkbench key={result.id} api={API} result={result} onSeek={seekSource} onReload={reloadResult} onCreated={clip => { setResult(previous => previous ? { ...previous, clips: [...previous.clips.filter(c => c.id !== clip.id), clip] } : previous); setClipFilter("all"); }}/>}
          {!result.provisional && <PreferencePanel api={API} result={result} onSaved={clips => setResult(previous => previous ? { ...previous, clips } : previous)}/>}
          {!result.provisional && <QualityBenchmark key={result.id} api={API} result={result}/>}
          {!result.provisional && result.media_url && result.clips.length > 0 && <BatchExport key={result.id} api={API} result={result} onSaved={clips => { setResult(previous => previous ? { ...previous, clips: previous.clips.map(c => clips.find(edit => edit.id === c.id) || c) } : previous); }}/>}
          <div className="clip-toolbar"><label className="field"><span>Search all candidates</span><input value={clipSearch} onChange={e => setClipSearch(e.target.value)} placeholder="Search titles and transcripts"/></label><label className="field"><span>Show</span><select value={clipFilter} onChange={e => setClipFilter(e.target.value)}><option value="all">All candidates</option><option value="reviewed">Model reviewed</option><option value="kept">Kept</option><option value="audio">Audio / markers only</option></select></label><label className="field"><span>Sort</span><select value={clipSort} onChange={e => setClipSort(e.target.value)}><option value="rank">Strongest first</option><option value="time">Source order</option></select></label></div>
          <p className="candidate-count">{filteredClips.length ? `Showing ${currentPage * pageSize + 1}–${Math.min(filteredClips.length, (currentPage + 1) * pageSize)} of ${filteredClips.length}` : "No candidates match this filter"}. All distinct qualifying candidates are included; overlapping versions are removed.</p>
          {result.warnings.map(w => <p key={w} className="warning">{w}</p>)}
          <div className="clip-list">{visibleClips.map(clip => <article id={`clip-${clip.id}`} key={clip.id} className={`clip-card ${active?.start === clip.start && active?.end === clip.end ? "active" : ""}`}><div className="rank">{String(clip.rank || clip.id).padStart(2, "0")}</div><div className="clip-body"><div className="clip-topline"><span className="timecode">{tc(clip.start, true)} <span>—</span> {tc(clip.end, true)}</span><span className="duration">{(clip.output_duration ?? clip.end-clip.start).toFixed(1)}s</span><span className={`transcript-badge ${clip.transcript_used ? "" : "audio"}`}>{clip.transcript_used ? clip.caption_source === "automatic" ? "AUTO TRANSCRIPT" : "TRANSCRIPT USED" : clip.selection_method === "local_vision" ? "VISUAL SEQUENCE" : clip.selection_method === "manual" ? "EDITOR SELECTED" : "AUDIO / MARKERS ONLY"}</span></div><h3>{clip.title}</h3><p className="reason">{clip.reason}</p>{clip.editorial_review && <p className="editorial-check"><strong>Final editorial check:</strong> {clip.editorial_review.reason}</p>}{!!clip.visual_context?.length && <details className="visual-evidence"><summary>Sampled visual observations · model predictions</summary>{clip.visual_context.map((v, i) => <p key={i}>{tc(v.time)} · {v.description}</p>)}</details>}{clip.setup_quote && <blockquote className="payoff-quotes"><p><strong>Setup</strong> “{clip.setup_quote}”</p><p><strong>Payoff</strong> “{clip.payoff_quote}”</p></blockquote>}<ul className="evidence">{clip.evidence.map(e => <li key={e}><span/>{e}</li>)}</ul><div className="clip-bottom"><span>{clip.boundary_note}</span>{result.media_url ? <div className="clip-actions"><button className="preview-button" disabled={previewPreparing !== null} onClick={() => preview(clip)}><Icon name="play" size={14}/>{previewPreparing === clip.id ? "Preparing preview…" : "Preview clip"}</button>{!result.provisional && <ClipDownload key={`${result.id}-${clip.id}-${clip.revision}`} api={API} jobId={result.id} clipId={clip.id} mediaKind={result.media_kind} revision={clip.revision}/>}</div> : <span className="no-media">Add media to preview or download this interval</span>}</div>{!result.provisional && <><ClipEditor key={`${result.id}-${clip.id}-${clip.revision}`} api={API} clip={clip} result={result} onSaved={updateClip} onPreview={preview}/><ClipFeedback api={API} job={result.id} clip={clip} onSaved={updateClip}/></>}{!result.provisional && clip.captions.length > 0 && <div className="subtitle-links"><a href={`${API}/api/jobs/${result.id}/clips/${clip.id}/captions.srt`} download>Download SRT</a><a href={`${API}/api/jobs/${result.id}/clips/${clip.id}/captions.vtt`} download>Download VTT</a></div>}{clip.excerpt && <details className="transcript"><summary>Read transcript</summary><p>{clip.excerpt}</p></details>}</div></article>)}</div>
          {filteredClips.length > pageSize && <nav className="clip-pagination" aria-label="Candidate pages"><button className="small-button" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Previous page</button><span>Page {currentPage + 1} of {Math.ceil(filteredClips.length / pageSize)}</span><button className="small-button" disabled={(currentPage + 1) * pageSize >= filteredClips.length} onClick={() => setPage(currentPage + 1)}>Next page</button></nav>}
          {!result.clips.length && <div className="empty-shortlist"><Icon name="wave" size={28}/><h3>A quiet timeline is a valid result.</h3><p>We didn’t find enough evidence for a highlight. Add captions, try a keyword, or choose a recording with more conversation.</p></div>}
          <details className="availability" open><summary>Analysis available for this recording</summary><div>{Object.entries(result.capabilities).map(([key, value]) => <p key={key}><strong>{({transcript: "Spoken content", energy: "Audio energy", events: "Laughter & reactions", replays: "Replay markers", meaning: "Meaning & payoff", visual: "Visual context", hardware: "Processing hardware"} as Record<string, string>)[key]}</strong><span className={value.startsWith("Unavailable") ? "unavailable" : ""}>{value}</span></p>)}</div><small>Transcription writes down the speech. Local model review separately judges context and payoff when available. Basic pattern matching does not understand the stream. Review every cut. No YouTube or retention access is required.</small></details>
        </> : <div className="empty-shortlist"><div className="empty-lines"><span/><span/><span/></div><h3>A few good moments beat a long timeline.</h3><p>Your ranked clips will appear here, with exact timecodes<br/>and the evidence behind each candidate.</p></div>}
      </section>
      <footer><span>cutlist <span className="footer-slash">/</span> A sharper starting point.</span><span>{health}</span><span>BUILT FOR THE HUMAN IN THE EDIT.</span></footer>
    </main>
  </div>;
}
