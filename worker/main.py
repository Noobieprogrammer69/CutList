"""Local, single-job worker. Uploads and PCM stay on disk, not in RAM."""
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import sys
import copy
import errno
import signal
import hashlib
from contextlib import asynccontextmanager
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, JSONResponse

from worker.analysis import Event, audio_energy, energy_events, rank_clips, sound_events, transcript_events
from worker.captions import Cue, parse_captions, parse_replays, timecode
from worker.events import EventClassifier
from worker.exports import ClipExports
from worker.store import Store
from worker.auth import COOKIE, set_cookie, verify_invite
from worker import transcription
from worker import semantic
from worker import hardware, visual, tracking, speakers, quality, composition, preferences, retrieval
from worker.workspace import install as install_workspace, remember_edit
from worker.inference import slot
from worker.editing import edit_clip, clip_captions, subtitle_text, timeline_xml, caption_style, DEFAULT_STYLE
from worker.uploads import install as install_uploads

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("CUTLIST_DATA", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
store = Store(DATA)


@asynccontextmanager
async def lifespan(application):
    recover_jobs()
    async def sweep():
        while True:
            cleanup()
            await asyncio.sleep(60)
    task = asyncio.create_task(sweep())
    try:
        yield
    finally:
        global stopping
        stopping = True
        task.cancel()
        for process in list(processes.values()):
            if process.poll() is None:
                stop_process(process)
        pool.shutdown(wait=False, cancel_futures=True)
        export_pool.shutdown(wait=False, cancel_futures=True)
        workspace_pool.shutdown(wait=False, cancel_futures=True)
        if "setup_manager" in globals(): setup_manager.close()


app = FastAPI(title="Cutlist worker", docs_url=None, lifespan=lifespan)
ORIGINS = [origin.strip() for origin in os.environ.get("CUTLIST_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=ORIGINS, allow_credentials=True,
                   allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"], allow_headers=["*"])
pool = ThreadPoolExecutor(max_workers=1)
export_pool = ThreadPoolExecutor(max_workers=1)
jobs = {}
guard = threading.RLock()
processes = {}
futures = {}
stopping = False
log = logging.getLogger("cutlist")
MAX_BYTES = min(256, max(1, int(os.environ.get("CUTLIST_MAX_UPLOAD_GB", "64")))) * 1024 ** 3
MAX_SECONDS = 16 * 3600


class Cancelled(Exception):
    pass


def link_or_copy(source, destination):
    try:
        os.link(source, destination)
    except OSError as exc:
        if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EACCES}:
            raise
        shutil.copy2(source, destination)
    return str(destination)


@app.middleware("http")
async def private_workspace(request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    if request.method not in {"GET", "HEAD"} and request.headers.get("origin") and request.headers["origin"] not in ORIGINS:
        return JSONResponse({"detail": "This origin is not allowed to change workspace files."}, 403)
    session = store.session(request.cookies.get(COOKIE))
    token = None
    if not session and not os.environ.get("CUTLIST_ACCESS_KEY"):
        owner, token = store.new_session()
        session = {"owner": owner}
    request.state.owner = session["owner"] if session else None
    public = request.url.path in {"/api/health", "/api/session"}
    if not session and not public:
        return JSONResponse({"detail": "Unlock this workspace with the access key."}, 401)
    match = re.match(r"/api/jobs/([a-f0-9]{32})(?:/|$)", request.url.path)
    if match:
        if not store.authorized(match[1], request.state.owner, time.time()):
            return JSONResponse({"detail": "Project not found in this workspace, or it has expired."}, 404)
    response = await call_next(request)
    if token:
        set_cookie(response, token)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def stop_process(process):
    if getattr(process, "cutlist_group", False):
        try: os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    else:
        process.terminate()


def check_cancel(job):
    if stopping or jobs.get(job, {}).get("status") in {"cancelled", "deleting"}:
        raise Cancelled("Analysis cancelled.")


def probe(path):
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, timeout=30)
        if proc.returncode:
            raise ValueError("FFmpeg cannot decode this file. Try exporting an MP4 (H.264/AAC), WebM, MP3 or WAV.")
        info = json.loads(proc.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("The file has no readable duration. Re-export it as MP4, MP3 or WAV.")
        if duration > MAX_SECONDS:
            raise ValueError("Recordings up to 16 hours are supported. Split longer recordings first.")
        return info, duration
    except FileNotFoundError:
        raise ValueError("FFmpeg / ffprobe is missing. On Omarchy run: sudo pacman -S ffmpeg") from None
    except subprocess.TimeoutExpired:
        raise ValueError("Reading the format timed out. The recording may be damaged.") from None


def ffmpeg(job, args, duration, low, high, message, on_progress=None):
    check_cancel(job)
    # Drain progress incrementally and put diagnostic output on disk.
    with tempfile.TemporaryFile() as err:
        process = subprocess.Popen(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-threads", "2", "-y", "-protocol_whitelist", "file,pipe", *args, "-progress", "pipe:1", "-nostats"], stdout=subprocess.PIPE, stderr=err, text=True)
        with guard:
            processes[job] = process
        try:
            check_cancel(job)
            for line in process.stdout:
                if line.startswith("out_time_us="):
                    try:
                        elapsed = int(line.split("=", 1)[1]) / 1_000_000
                        percent = min(high, low + int((high - low) * elapsed / duration))
                        if on_progress is None:
                            update(job, progress=percent, message=message)
                        else:
                            on_progress(percent)
                    except ValueError:
                        pass
            if process.wait() != 0:
                check_cancel(job)
                err.seek(0)
                diagnostic = err.read(2000).decode(errors="replace")
                log.warning("FFmpeg decode failed: %s", diagnostic)
                raise ValueError("The recording could not be decoded completely. Try re-exporting it as MP4 (H.264/AAC), MP3 or WAV.")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            with guard:
                processes.pop(job, None)


def update(job, **fields):
    with guard:
        check_cancel(job)
        jobs[job].update(fields)
        if "owner" in jobs[job]:
            store.save(jobs[job])


def run_content_review(job, request_file, response_file, on_partial=lambda *_: None):
    error = "The local model did not return a complete review."
    with tempfile.TemporaryFile() as diagnostic:
        process = subprocess.Popen([sys.executable, "-m", "worker.semantic", str(request_file), str(response_file)], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=diagnostic, text=True)
        with guard:
            processes[job] = process
        try:
            check_cancel(job)
            for line in process.stdout:
                check_cancel(job)
                payload = json.loads(line)
                if payload.get("error"):
                    error = payload["error"]
                elif "partial_clips" in payload:
                    on_partial(payload["partial_clips"], payload["coverage"])
                else:
                    update(job, progress=76 + int(payload.get("progress", 0) * 19), message=payload.get("message", "Reviewing transcript"))
            if process.wait() != 0:
                check_cancel(job)
                raise ValueError(error)
            return json.loads(response_file.read_text())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
            with guard:
                processes.pop(job, None)


def run_chapter_analysis(job, request_file, response_file, on_partial):
    error = "Chapter processing did not complete."
    with tempfile.TemporaryFile() as diagnostic:
        process = subprocess.Popen([sys.executable, "-m", "worker.chapters", str(request_file), str(response_file)], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=diagnostic, text=True, start_new_session=True)
        process.cutlist_group = True
        with guard: processes[job] = process
        try:
            check_cancel(job)
            for line in process.stdout:
                check_cancel(job)
                payload = json.loads(line)
                if payload.get("error"): error = payload["error"]
                elif "partial_clips" in payload: on_partial(payload["partial_clips"], payload["coverage"])
                else:
                    update(job, stage="chapters", progress=5+int(payload.get("progress", 0)*70), processed_seconds=payload.get("processed_seconds"), eta_seconds=None,
                           message=payload.get("message", "Processing sequential chapters"))
            if process.wait():
                check_cancel(job)
                raise ValueError(error)
            return json.loads(response_file.read_text())
        finally:
            if process.poll() is None:
                stop_process(process)
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: os.killpg(process.pid, signal.SIGKILL); process.wait()
            process.stdout.close()
            with guard: processes.pop(job, None)


def run_visual_review(job, request_file, response_file):
    error = "Visual sequence review did not finish."
    with tempfile.TemporaryFile() as diagnostic:
        process = subprocess.Popen([sys.executable, "-m", "worker.visual", str(request_file), str(response_file)], cwd=ROOT,
                                   stdout=subprocess.PIPE, stderr=diagnostic, text=True, start_new_session=True)
        process.cutlist_group = True
        with guard: processes[job] = process
        try:
            check_cancel(job)
            for line in process.stdout:
                check_cancel(job)
                row = json.loads(line)
                if row.get("error"): error = row["error"]
                else: update(job, progress=76+int(row.get("progress", 0)*19), stage="visual_sequences", message=row.get("message", "Reviewing visual action"))
            if process.wait():
                check_cancel(job)
                raise ValueError(error)
            return json.loads(response_file.read_text())
        finally:
            if process.poll() is None: stop_process(process); process.wait()
            process.stdout.close()
            with guard: processes.pop(job, None)


def analyze(job, source, caption_text, maximum, keywords, replays, sample, auto_transcribe=True, content_review=True, goal="mixed", profile="general", visual_review=False, chaptered=True, chapter_seconds=300):
    folder = DATA / job
    pcm = folder / "analysis.pcm"
    started = time.monotonic()
    timings = {}
    chapter_stats, chapter_mode = None, False
    try:
        update(job, status="running", progress=3, message="Reading recording and captions")
        cues = parse_captions(caption_text) if caption_text.strip() else []
        caption_source = "sample" if sample and cues else "uploaded" if cues else None
        words = []
        warnings, capabilities = [], {}
        features = folder / "features.npz"
        cached_speech = (folder / "speech.json").is_file() and not cues
        timings["transcription_reused"] = bool(cached_speech or cues and jobs[job].get("reuse_job"))
        duration, media_url, has_audio = 0, None, False
        db = crest = variation = np.array([])
        samples = None
        if source:
            update(job, source=str(source))
            info, duration = probe(source)
            with source.open("rb") as fingerprint_file:
                size = source.stat().st_size
                first = fingerprint_file.read(1024*1024)
                fingerprint_file.seek(max(0, size-1024*1024))
                recording_key = hashlib.sha256(str(size).encode() + first + fingerprint_file.read(1024*1024)).hexdigest()
            update(job, recording_key=recording_key)
            has_audio = any(s["codec_type"] == "audio" for s in info["streams"])
            video = next((s for s in info["streams"] if s["codec_type"] == "video" and not s.get("disposition", {}).get("attached_pic")), None)
            audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
            media_info = {"width": video.get("width", 0) if video else 0, "height": video.get("height", 0) if video else 0,
                          "fps": (video.get("avg_frame_rate") or "25/1") if video else "25/1",
                          "sample_rate": audio.get("sample_rate", 48000), "channels": audio.get("channels", 2), "has_audio": has_audio}
            rotation = next((s.get("rotation", 0) for s in (video or {}).get("side_data_list", []) if "rotation" in s), 0)
            if abs(rotation) % 180 == 90:
                media_info["width"], media_info["height"] = media_info["height"], media_info["width"]
            chapter_mode = chaptered and duration > chapter_seconds and has_audio and not cues and not cached_speech and auto_transcribe and transcription.available()
            if has_audio and not chapter_mode and not (features.is_file() and (cues or cached_speech or not auto_transcribe)):
                if shutil.disk_usage(folder).free < duration * 32000 + 64 * 1024 ** 2:
                    raise ValueError("Not enough temporary disk space to decode the audio. Free disk space or add timed captions and try again.")
                ffmpeg(job, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", str(pcm)], duration, 5, 20, "Decoding audio to a disk-backed waveform")
                if not pcm.stat().st_size:
                    raise ValueError("The audio stream decoded to an empty waveform.")
                samples = np.memmap(pcm, dtype="<i2", mode="r")
            if video and source.suffix.lower() in {".mp4", ".webm"} and video["codec_name"] in {"h264", "vp8", "vp9", "av1"}:
                preview = source
            elif video:
                preview = source
                update(job, preview_mode="clips")
                warnings.append("This format needs a browser-compatible preview. Only the requested clip will be converted; the full recording is not re-encoded.")
            elif has_audio:
                preview = source
                if source.suffix.lower() not in {".wav", ".mp3", ".m4a", ".mp4", ".ogg", ".oga", ".flac", ".webm", ".aac"}:
                    update(job, preview_mode="clips")
            else:
                raise ValueError("No playable audio or video stream was found in this file.")
            media_url = f"/api/jobs/{job}/media"
            update(job, preview=str(preview), media_kind="video" if video else "audio")
        elif cues:
            duration = max(c.end for c in cues)
            if duration > MAX_SECONDS:
                raise ValueError("Captions exceed the 16 hour recording limit.")
        else:
            raise ValueError("Add a recording, captions, or load the sample before finding clips.")
        if chapter_mode:
            request_path, response_path = folder / "chapters-input.json", folder / "chapters-output.json"
            request_path.write_text(json.dumps({"source": str(source), "duration": duration, "maximum": maximum, "chapter_seconds": chapter_seconds,
                                                "content_review": content_review, "goal": goal, "profile": profile, "keywords": keywords, "replays": replays,
                                                "has_video": bool(video), "visual_review": visual_review, "sequences": True, "features_output": str(features)}))
            first_started = time.monotonic()
            def chapter_partial(values, coverage):
                elapsed = round(time.monotonic()-started, 2)
                if values and not timings.get("first_results_seconds"): timings["first_results_seconds"] = elapsed
                prepared = []
                for value in values:
                    prepared.append({**value, "revision": 0, "captions": [], "crop": {"format": "source", "x": .5, "y": .5, "resolution": "source"}, "quality": "high", "feedback": "unrated", "burn_captions": False, "caption_source": "automatic" if value.get("transcript_used") else None})
                method = coverage.get("selection_method", "basic")
                snapshot = {"id": job, "duration": duration, "maximum": maximum, "clips": prepared, "capabilities": {"transcript": "Available — completed chapter speech", "meaning": "Available — completed chapters reviewed" if method == "local_model" else "Basic or mixed chapter candidates; inspect each clip's review evidence"},
                            "warnings": [], "media_url": media_url, "media_kind": jobs[job].get("media_kind"), "preview_mode": jobs[job].get("preview_mode", "source"),
                            "source_name": jobs[job]["source_name"], "waveform": [], "sample": sample, "replays": replays, "caption_count": 0, "cues": [],
                            "expires": jobs[job].get("expires"), "media_info": media_info, "goal": goal, "profile": profile, "provisional": True, "coverage": coverage,
                            "selection_method": method, "timings": {**timings, "total_seconds": elapsed}}
                update(job, partial_result=snapshot)
            try:
                chapter_stats = run_chapter_analysis(job, request_path, response_path, chapter_partial)
                speech = chapter_stats["speech"]
                cues, words = [Cue(**c) for c in speech["cues"]], speech["words"]
                caption_source = "automatic" if cues else None
                temporary = (folder / "speech.json").with_suffix(".tmp")
                temporary.write_text(json.dumps(speech)); temporary.replace(folder / "speech.json")
                warnings.extend(chapter_stats.get("warnings", []))
                timings["chapter_pipeline_seconds"] = round(time.monotonic()-first_started, 2)
                timings["chapter_count"] = chapter_stats["windows"]
            except Cancelled:
                raise
            except Exception as exc:
                warnings.append(f"Chapter processing failed: {exc}. Retrying with the standard speech path; chapter checkpoints are preserved.")
        if not cues and has_audio and auto_transcribe and not chapter_stats:
            speech_started = time.monotonic()
            if cached_speech or transcription.available():
                try:
                    update(job, progress=25, stage="transcription", message="Transcribing speech · batched CPU · completed chunks are saved")
                    output = folder / "speech.json"
                    if not output.is_file():
                        if not pcm.is_file():
                            ffmpeg(job, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm)], duration, 5, 20, "Decoding audio for standard transcription")
                        with slot(lambda: check_cancel(job)), tempfile.TemporaryFile() as diagnostic:
                            process = subprocess.Popen([sys.executable, "-m", "worker.transcription", str(pcm), str(output)], cwd=ROOT,
                                                       stdout=subprocess.PIPE, stderr=diagnostic, text=True)
                            with guard:
                                processes[job] = process
                            try:
                                check_cancel(job)
                                for line in process.stdout:
                                    check_cancel(job)
                                    message = json.loads(line)
                                    fraction = message.get("progress", 0) / 100
                                    elapsed = time.monotonic() - speech_started
                                    processed = duration * fraction
                                    eta = round(elapsed / fraction - elapsed) if fraction > .015 else None
                                    update(job, progress=25 + int(fraction * 35), processed_seconds=round(processed, 1), total_seconds=duration,
                                           eta_seconds=eta, message=f"Transcribing speech · {timecode(processed)} / {timecode(duration)} · chunks saved")
                                if process.wait() != 0:
                                    check_cancel(job)
                                    raise ValueError("Speech recognition failed; try supplied captions instead.")
                            finally:
                                if process.poll() is None:
                                    process.kill()
                                    process.wait()
                                process.stdout.close()
                                with guard:
                                    processes.pop(job, None)
                    speech = json.loads(output.read_text())
                    cues, words = [Cue(**cue) for cue in speech["cues"]], speech["words"]
                    caption_source = "automatic" if cues else None
                except Cancelled:
                    raise
                except Exception as exc:
                    warnings.append(f"Automatic transcription unavailable: {exc} Audio analysis still runs.")
            else:
                warnings.append("Speech model is not installed. Run npm run speech:download, or add captions. Audio analysis still runs.")
            timings["transcription_seconds"] = round(time.monotonic() - speech_started, 2)
            timings["transcription_reused"] = cached_speech
        if cues and source:
            if any(c.end > duration for c in cues):
                warnings.append("Captions beyond the recording were trimmed or ignored; check that the files match.")
            cues = [Cue(c.start, min(c.end, duration), c.text, c.speaker) for c in cues if c.start < duration]
        if any(t >= duration for t in replays):
            raise ValueError(f"Replay markers must be inside the recording ({timecode(duration)}).")
        classifier = EventClassifier() if has_audio else None
        capabilities["transcript"] = ("Available — automatic Parakeet English transcription on CPU; review recognition errors" if caption_source == "automatic" else "Available — supplied captions, local pattern analysis") if cues else "Unavailable — no transcript; audio analysis only"
        capabilities["energy"] = "Available — relative conversational energy and pause analysis" if has_audio else "Unavailable — no audio supplied"
        capabilities["events"] = classifier.status if classifier else "Unavailable — no audio supplied"
        capabilities["replays"] = (f"{len(replays)} illustrative sample markers — not audience data" if sample else f"{len(replays)} editor-supplied timestamps") if replays else "Not supplied — no replay history inferred from the recording"
        events = transcript_events(cues, keywords)
        if has_audio:
            update(job, progress=61, stage="audio", eta_seconds=None, message="Reading cached audio features" if features.is_file() else "Measuring audio dynamics in bounded blocks")
            if features.is_file():
                try:
                    with np.load(features, allow_pickle=False) as cached:
                        db, crest, variation = cached["db"], cached["crest"], cached["variation"]
                except (ValueError, OSError, KeyError):
                    features.unlink(missing_ok=True)
            if not features.is_file():
                if samples is None:
                    ffmpeg(job, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", str(pcm)], duration, 61, 64, "Rebuilding audio features")
                    samples = np.memmap(pcm, dtype="<i2", mode="r")
                db, crest, variation = audio_energy(samples, check=lambda: check_cancel(job))
                np.savez_compressed(features, db=db, crest=crest, variation=variation)
            energy = energy_events(db, crest, variation)
            try:
                sound_cache = folder / "sounds.json"
                if sound_cache.is_file() and classifier.tagger is not None:
                    saved = json.loads(sound_cache.read_text())
                    sounds, music = [Event(**e) for e in saved["events"]], saved["music"]
                elif classifier.tagger is not None:
                    if samples is None:
                        ffmpeg(job, ["-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "s16le", str(pcm)], duration, 64, 65, "Decoding audio for sound-event analysis")
                        samples = np.memmap(pcm, dtype="<i2", mode="r")
                    sounds, music = sound_events(samples, classifier, lambda p, m: update(job, progress=65 + int((p - 45) * .25), message=m))
                    sound_cache.write_text(json.dumps({"events": [vars(e) for e in sounds], "music": music}))
                else:
                    sounds, music = [], []
            except Cancelled:
                raise
            except Exception as exc:
                sounds, music = [], []
                capabilities["events"] = f"Unavailable — sound classification failed: {exc}"
                warnings.append("Sound-event inference failed. Results use the other available evidence.")
            energy = [e for e in energy if not any(a <= e.time <= b and m > .5 and speech < .25 for a, b, m, speech in music)]
            events.extend(energy)
            events.extend(sounds)
        for marker in replays:
            events.append(Event(marker, "replay", 2.2, f"{'Illustrative sample' if sample else 'Editor-supplied'} replay marker at {timecode(marker)}", title="A replay-marked moment"))
        update(job, progress=76, stage="selection", message="Finding complete setups and payoffs · no five-clip cap")
        clips = rank_clips(events, cues, duration, maximum, db)
        baseline_clips = copy.deepcopy(clips)
        cue_values = [{"start": c.start, "end": c.end, "text": c.text, **({"speaker": c.speaker} if c.speaker else {})} for c in cues]
        style = store.preferences(jobs[job].get("owner")).get("caption_style", DEFAULT_STYLE)
        def prepare(values):
            prepared = copy.deepcopy(values)
            for clip in prepared:
                clip.update(revision=0, captions=clip_captions(cue_values, clip["start"], clip["end"]),
                            crop={"format": "source", "x": .5, "y": .5, "resolution": "source", "tracking": "manual"}, quality="high", burn_captions=False,
                            caption_style=style, feedback="unrated", caption_source=caption_source if clip["transcript_used"] else None)
            return prepared
        def partial(values, coverage):
            elapsed = round(time.monotonic() - started, 2)
            timings.setdefault("first_results_seconds", elapsed if values else None)
            if values and timings.get("first_results_seconds") is None:
                timings["first_results_seconds"] = elapsed
            partial_clips = prepare(values)
            for clip in partial_clips:
                clip["captions"] = []  # Editable cues arrive once with the final result.
            draft = {"id": job, "duration": round(duration, 3), "clips": partial_clips, "capabilities": {**capabilities, "meaning": "Available — completed sections passed proposal and final editorial review"},
                     "warnings": [], "media_url": media_url, "media_kind": jobs[job].get("media_kind"), "preview_mode": jobs[job].get("preview_mode", "source"),
                     "waveform": [], "sample": sample, "replays": replays, "maximum": maximum, "source_name": jobs[job]["source_name"],
                     "caption_count": len(cues), "cues": [], "caption_source": caption_source, "expires": jobs[job].get("expires"),
                     "media_info": media_info if source else {}, "goal": goal, "profile": profile, "selection_method": "local_model", "provisional": True,
                     "coverage": coverage, "timings": {**timings, "total_seconds": elapsed}}
            update(job, partial_result=draft)
        capabilities["meaning"] = "Basic candidates — pattern matching only; meaning and humor have not been model-reviewed"
        semantic_stats = chapter_stats
        if chapter_stats:
            clips = chapter_stats["clips"]
            capabilities["meaning"] = "Available — sequential chapters proposed and checked for context and payoff" if chapter_stats["selection_method"] == "local_model" else "Basic or mixed chapter candidates — check each clip's actual review status"
        if content_review and cues and not chapter_stats:
            readiness = semantic.status()
            if readiness["available"]:
                request_file, response_file = folder / "review-input.json", folder / "review-output.json"
                request_file.write_text(json.dumps({"cues": cue_values,
                                                    "maximum": maximum, "goal": goal, "profile": profile, "keywords": keywords, "events": [vars(e) for e in events],
                                                    "source": str(source) if source and video else None, "visual_review": visual_review, "sequences": True}))
                try:
                    update(job, stage="content_review", message="Local model reviewing meaning, setup and payoff")
                    semantic_stats = run_content_review(job, request_file, response_file, on_partial=partial)
                    clips = semantic_stats["clips"]
                    capabilities["meaning"] = f"Available — local {semantic_stats['model']} proposed and independently rechecked setup, payoff and interest; model judgments can be wrong"
                    if semantic_stats.get("visual_error"):
                        warnings.append(semantic_stats["visual_error"])
                except Cancelled:
                    raise
                except Exception as exc:
                    warnings.append(f"Content review did not complete: {exc}. These are basic candidates, not model-approved highlights. Completed sections are cached for retry.")
                    capabilities["meaning"] = "Unavailable — content review failed; basic candidates only"
            else:
                warnings.append(readiness["message"] + " These are basic candidates; they have not been judged for meaning or humor.")
                capabilities["meaning"] = "Unavailable — local content model is not ready; basic candidates only"
        elif content_review and not cues and not chapter_stats:
            warnings.append("No transcript is available for content review. Audio candidates cannot establish whether a moment makes sense or is funny.")
        if visual_review and source and video and not cues:
            ready = visual.status()
            if ready["available"]:
                request_path, response_path = folder / "visual-input.json", folder / "visual-output.json"
                request_path.write_text(json.dumps({"source": str(source), "duration": duration, "maximum": maximum, "goal": goal}))
                try:
                    vision_stats = run_visual_review(job, request_path, response_path)
                    from worker.analysis import select_nonoverlapping
                    clips = select_nonoverlapping([{k: v for k, v in c.items() if k != "id"} for c in clips + vision_stats["clips"]])
                    semantic_stats = {**vision_stats, "selection_method": "mixed" if any(c.get("selection_method") != "local_vision" for c in clips) else "local_vision"}
                except Cancelled: raise
                except Exception as exc: warnings.append(f"Visual-only review failed: {exc}. Audio candidates remain available.")
            else:
                warnings.append(ready["message"])
        capabilities["visual"] = "Available — ordered visual sequences; sampled action predictions require review" if semantic_stats and semantic_stats.get("visual_review") else "Unavailable — sampled-frame review did not run" if visual_review else "Not enabled — speech/audio review only"
        capabilities["hardware"] = hardware.report()["message"]
        update(job, progress=96, stage="finalizing", message="Preparing every distinct qualifying candidate")
        clips = prepare(clips)
        preference_report = None
        if store.preferences(jobs[job].get("owner")).get("personalization"):
            preference_report, learned = preferences.learn(store.all(jobs[job]["owner"]))
            if learned is not None:
                clips = preferences.rerank(clips, learned)
                preference_report["applied"] = True
        # A lightweight waveform is real measured data, not a decorative score.
        waveform = []
        if len(db):
            waveform = [round(float(np.max(x)), 1) for x in np.array_split(db, min(200, len(db)))]
        result = {"id": job, "duration": round(duration, 3), "clips": clips, "capabilities": capabilities,
                  "warnings": warnings, "media_url": media_url, "media_kind": jobs[job].get("media_kind"),
                  "waveform": waveform, "sample": sample, "replays": replays,
                  "maximum": maximum, "source_name": jobs[job]["source_name"], "caption_count": len(cues),
                  "cues": cue_values, "words": words, "caption_source": caption_source,
                  "media_info": media_info if source else {}, "expires": jobs[job].get("expires"), "goal": goal,
                  "selection_method": semantic_stats.get("selection_method", "local_model") if semantic_stats else "basic", "timings": {**timings, "total_seconds": round(time.monotonic() - started, 2)},
                  "semantic_stats": {k: v for k, v in (semantic_stats or {}).items() if k not in {"clips", "speech"}}, "source_quality_preserved": True}
        result.update(baseline_clips=baseline_clips, profile=profile, provisional=False, personalization=preference_report)
        result["preview_mode"] = jobs[job].get("preview_mode", "source")
        (folder / "result.json").write_text(json.dumps(result))
        update(job, status="complete", progress=100, message="Shortlist ready" if clips else "No strong moments found", result=result, partial_result=None)
    except Cancelled:
        pass
    except Exception as exc:
        if isinstance(exc, ValueError):
            log.warning("Analysis failed: %s", exc)
        else:
            log.exception("Analysis failed")
        if jobs.get(job, {}).get("status") not in {"cancelled", "deleting"}:
            update(job, status="error", message=str(exc), error=str(exc))
    finally:
        if 'samples' in locals() and isinstance(samples, np.memmap):
            samples._mmap.close()
        pcm.unlink(missing_ok=True)


@app.get("/api/health")
def health():
    return {"status": "ok", "api_revision": 2, "ffmpeg": bool(shutil.which("ffmpeg") and shutil.which("ffprobe")), "event_model": EventClassifier().status,
            "speech_model": transcription.available(), "access_key_required": bool(os.environ.get("CUTLIST_ACCESS_KEY")),
            "content_model": semantic.status(), "vision_model": visual.status(), "search_model": retrieval.status(), "hardware": hardware.report(), "tracking": tracking.available(), "speaker_model": speakers.available(),
            "max_duration_hours": MAX_SECONDS // 3600, "max_upload_gb": MAX_BYTES // 1024 ** 3}


@app.post("/api/jobs", status_code=202)
async def create_job(request: Request, media: UploadFile | None = File(None), captions: UploadFile | None = File(None),
                     maximum: float = Form(45), keywords: str = Form(""), replay_markers: str = Form(""),
                     sample: bool = Form(False), sample_replays: bool = Form(False), sample_captions: bool = Form(True),
                     auto_transcribe: bool = Form(True), upload_id: str = Form(""), retention_days: int = Form(7),
                     content_review: bool = Form(True), goal: str = Form("mixed"), reuse_job: str = Form(""), refresh_transcript: bool = Form(False),
                     profile: str = Form("general"), visual_review: bool = Form(False), chaptered: bool = Form(True), chapter_seconds: int = Form(300)):
    if not math.isfinite(maximum) or not 5 <= maximum <= 180:
        raise HTTPException(422, "Maximum clip length must be between 5 and 180 seconds.")
    if retention_days not in {1, 7, 30}:
        raise HTTPException(422, "Choose 1, 7, or 30 days of storage.")
    if goal not in {"funny", "stories", "mixed"}:
        raise HTTPException(422, "Choose funny, stories, or mixed clip selection.")
    if profile not in semantic.PROFILES:
        raise HTTPException(422, "Choose a supported editorial profile.")
    if not 60 <= chapter_seconds <= 900:
        raise HTTPException(422, "Choose a chapter size from 60 to 900 seconds.")
    with guard:
        if sum(j["status"] in {"queued", "running", "uploading"} for j in jobs.values()) >= 4:
            raise HTTPException(429, "The local worker queue is full. Let an analysis finish and try again.")
    job = uuid.uuid4().hex
    folder = DATA / job
    folder.mkdir()
    source, text = None, ""
    jobs[job] = {"id": job, "owner": request.state.owner, "created": time.time(), "expires": time.time() + retention_days * 86400,
                 "status": "uploading", "progress": 0, "message": "Receiving files", "source_name": "Captions only"}
    try:
        if upload_id and media and media.filename:
            raise ValueError("Use a resumed upload or a direct recording upload, not both.")
        if reuse_job and (sample or upload_id or media and media.filename):
            raise ValueError("Choose a saved project or a new recording, not both.")
        replay_values = parse_replays(replay_markers)
        keyword_values = [word.strip() for word in keywords.split(",") if word.strip()]
        if len(keyword_values) > 20 or any(len(w) > 80 for w in keyword_values):
            raise ValueError("Use up to 20 keywords, each at most 80 characters.")
        if sample:
            source = ROOT / "public" / "sample" / "studio-notes.mp4"
            if not source.exists():
                raise ValueError("Sample is missing. Run .venv/bin/python scripts/make_sample.py first.")
            text = (source.parent / "studio-notes.vtt").read_text() if sample_captions else ""
            jobs[job]["source_name"] = "Studio notes · sample recording"
            if sample_replays:
                replay_values.extend(json.loads((source.parent / "markers.json").read_text()))
                replay_values = sorted(set(replay_values))
        else:
            if reuse_job:
                old = store.get(reuse_job)
                if not old or old["owner"] != request.state.owner or old.get("expires", 0) <= time.time() or not old.get("result"):
                    raise HTTPException(404, "Saved project not found in this workspace.")
                old_source = old.get("source")
                if old_source:
                    if not Path(old_source).is_file():
                        raise ValueError("The saved recording is unavailable. Upload it again.")
                    source = folder / ("source" + Path(old_source).suffix)
                    link_or_copy(old_source, source)
                jobs[job]["source_name"] = old.get("source_name", old["result"]["source_name"])
                jobs[job]["quality_labels"] = copy.deepcopy(old.get("quality_labels", []))
                sample = bool(old["result"].get("sample"))
                text = old.get("parameters", [None, ""])[1]
                if refresh_transcript:
                    if not source:
                        raise ValueError("Add the source recording before requesting a new transcription.")
                    text, auto_transcribe = "", True
                previous = DATA / reuse_job
                for filename in ("speech.json", "features.npz", "sounds.json"):
                    if refresh_transcript and filename == "speech.json":
                        continue
                    if (previous / filename).is_file():
                        link_or_copy(previous / filename, folder / filename)
                # Independent directory entries; deleting either project cannot
                # remove the other's source or completed model sections.
                for dirname in ("speech.parts", "review-output.parts", "review-output.visual", "chapters-output.parts", "search-index"):
                    if refresh_transcript and dirname == "speech.parts":
                        continue
                    if (previous / dirname).is_dir():
                        shutil.copytree(previous / dirname, folder / dirname, copy_function=link_or_copy)
            if upload_id:
                upload = get_upload(upload_id, request.state.owner)
                if upload["offset"] != upload["size"]:
                    raise ValueError("The recording upload is incomplete. Select the same file to resume it.")
                original = Path(upload["path"])
                if not original.is_file():
                    raise ValueError("This upload expired. Select the recording again.")
                suffix = Path(upload["name"]).suffix.lower()
                source = folder / ("source" + (suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".media"))
                os.link(original, source)
                jobs[job]["source_name"] = upload["name"]
            if media and media.filename:
                suffix = Path(media.filename).suffix.lower()
                source = folder / ("source" + (suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".media"))
                total = 0
                with source.open("wb") as output:
                    while chunk := await media.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError(f"Files up to {MAX_BYTES // 1024 ** 3} GB are supported. Set CUTLIST_MAX_UPLOAD_GB for a different disk budget.")
                        output.write(chunk)
                jobs[job]["source_name"] = Path(media.filename).name
            if captions and captions.filename:
                if Path(captions.filename).suffix.lower() not in {".srt", ".vtt"}:
                    raise ValueError("Captions must be an SRT or WebVTT (.vtt) file.")
                raw = await captions.read(5 * 1024 * 1024 + 1)
                if len(raw) > 5 * 1024 * 1024:
                    raise ValueError("Please use a caption file smaller than 5 MB.")
                text = raw.decode("utf-8-sig")
                parse_captions(text)
        if not source and not text:
            raise ValueError("Add a recording or captions, or load the sample.")
        parameters = [str(source) if source else None, text, maximum, keyword_values, replay_values, sample, auto_transcribe, content_review, goal, profile, visual_review, chaptered, chapter_seconds]
        update(job, status="queued", parameters=parameters, source=str(source) if source else None, upload_id=upload_id or None, reuse_job=reuse_job or None, message="Queued for local analysis")
        futures[job] = pool.submit(analyze, job, source, text, maximum, keyword_values, replay_values, sample, auto_transcribe, content_review, goal, profile, visual_review, chaptered, chapter_seconds)
        return {"id": job}
    except (ValueError, UnicodeDecodeError) as exc:
        update(job, status="error", error=str(exc))
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(exc)) from None
    except HTTPException:
        jobs.pop(job, None)
        store.delete(job)
        shutil.rmtree(folder, ignore_errors=True)
        raise
    except OSError:
        jobs.pop(job, None)
        store.delete(job)
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(507, "The recording could not be saved. Check free disk space and the worker's data directory permissions, then resume the upload.") from None
    finally:
        if media:
            await media.close()
        if captions:
            await captions.close()


def get_job(job):
    if not re.fullmatch(r"[a-f0-9]{32}", job):
        raise HTTPException(404, "Analysis not found")
    if job not in jobs:
        saved = store.get(job)
        if not saved:
            raise HTTPException(404, "Project not found.")
        jobs[job] = saved
    return jobs[job]


@app.get("/api/jobs/{job}")
def job_status(job: str, compact: bool = False, since_sections: int = -1):
    state = get_job(job)
    with guard:
        public = {key: value for key, value in state.items() if key not in {"preview", "source", "owner", "parameters", "upload_id", "edit_history", "removed_clips", "next_clip_id"}}
    if compact and public.get("result"):
        public["result"] = {k: v for k, v in public["result"].items() if k not in {"words", "baseline_clips"}}
    public.pop("quality_labels", None)
    if public.get("partial_result") and public["partial_result"].get("coverage", {}).get("sections_complete", 0) <= since_sections:
        public.pop("partial_result")
    return public


@app.get("/api/jobs/{job}/media")
def media_file(job: str):
    state = get_job(job)
    preview = state.get("preview")
    if not preview or not Path(preview).is_file():
        raise HTTPException(404, "No media preview available for this analysis.")
    return FileResponse(preview)


clip_exports = ClipExports(DATA, export_pool, ffmpeg)


def export_context(job, clip_id):
    state = get_job(job)
    result = state.get("result")
    if not result or state["status"] != "complete":
        raise HTTPException(409, "Wait for Find clips to finish before downloading a clip.")
    clip = next((c for c in result["clips"] if c["id"] == clip_id), None)
    if clip is None:
        raise HTTPException(404, "This clip is not in the shortlist.")
    if not result.get("media_url"):
        raise HTTPException(409, "Add the matching recording to preview or download this clip.")
    source = state.get("source") or state.get("preview")
    if not source or not Path(source).is_file():
        raise HTTPException(404, "The recording is no longer available. Upload it and run Find clips again.")
    if not (math.isfinite(clip["start"]) and math.isfinite(clip["end"]) and
            0 <= clip["start"] < clip["end"] <= result["duration"] + .001 and
            composition.duration(clip) <= result["maximum"] + .001):
        raise HTTPException(409, "This clip has invalid boundaries. Run Find clips again.")
    snapshot = copy.deepcopy(clip)
    snapshot["_has_audio"] = result.get("media_info", {}).get("has_audio", True)
    return snapshot, Path(source), result["media_kind"], result["source_name"]


@app.post("/api/jobs/{job}/clips/{clip_id}/export", status_code=202)
def prepare_clip(job: str, clip_id: int, revision: int | None = None):
    clip, source, kind, source_name = export_context(job, clip_id)
    if revision is not None and revision != clip.get("revision", 0):
        raise HTTPException(409, "This clip changed in another tab. Reopen the saved project before downloading.")
    return clip_exports.start(job, clip, source, kind, source_name)


@app.get("/api/jobs/{job}/clips/{clip_id}/export")
def clip_export_status(job: str, clip_id: int, revision: int | None = None):
    clip, _, kind, source_name = export_context(job, clip_id)
    if revision is not None and revision != clip.get("revision", 0):
        raise HTTPException(409, "This clip changed in another tab. Reopen the saved project before downloading.")
    return clip_exports.status(job, clip, kind, source_name)


@app.get("/api/jobs/{job}/clips/{clip_id}/download")
def download_clip(job: str, clip_id: int, revision: int | None = None):
    clip, _, kind, source_name = export_context(job, clip_id)
    if revision is not None and revision != clip.get("revision", 0):
        raise HTTPException(409, "This download belongs to an earlier edit. Prepare the current clip again.")
    target, filename = clip_exports.paths(job, clip, kind, source_name)
    if not target.is_file() or not target.stat().st_size:
        raise HTTPException(409, "This clip is not ready to download. Prepare the clip first.")
    return FileResponse(target, filename=filename, media_type="video/mp4" if kind == "video" else "audio/mp4")


@app.get("/api/jobs/{job}/clips/{clip_id}/play")
def play_rendered_clip(job: str, clip_id: int, revision: int | None = None):
    clip, _, kind, source_name = export_context(job, clip_id)
    if revision is not None and revision != clip.get("revision", 0):
        raise HTTPException(409, "This preview belongs to an earlier edit. Reopen the saved project.")
    target, _ = clip_exports.paths(job, clip, kind, source_name)
    if not target.is_file():
        raise HTTPException(409, "Prepare this clip before playing it.")
    return FileResponse(target, media_type="video/mp4" if kind == "video" else "audio/mp4")


@app.get("/api/jobs/{job}/shortlist")
def export_shortlist(job: str):
    result = get_job(job).get("result")
    if not result:
        raise HTTPException(409, "Analysis is not complete yet.")
    lines = ["CUTLIST — EDITOR SHORTLIST", result["source_name"], f"Source duration: {timecode(result['duration'])}",
             "Scores are local ranking signals, not viewer replay statistics.", "", "ANALYSIS AVAILABLE"]
    lines.extend(f"{key}: {value}" for key, value in result["capabilities"].items())
    for clip in result["clips"]:
        lines.extend(["", f"{clip['id']}. {clip['title']}", f"IN {timecode(clip['start'])}   OUT {timecode(clip['end'])}",
                      f"Transcript used: {'yes' if clip['transcript_used'] else 'no'}", clip["reason"], clip["boundary_note"], *[f"- {e}" for e in clip["evidence"]]])
        if clip["excerpt"]:
            lines.append(clip["excerpt"])
        if len(composition.segments(clip)) > 1:
            lines.append(f"Retained output duration: {composition.duration(clip):.3f}s")
            lines.extend(f"KEEP {timecode(part['start'])} — {timecode(part['end'])}" for part in composition.segments(clip))
    if not result["clips"]:
        lines.append("\nNo strong moments found with the available evidence.")
    lines.extend(result["warnings"])
    return PlainTextResponse("\n".join(lines) + "\n", headers={"Content-Disposition": 'attachment; filename="cutlist-shortlist.txt"'})


get_upload = install_uploads(app, store, DATA, MAX_BYTES)


@app.get("/api/session")
def session_status(request: Request):
    return {"unlocked": bool(request.state.owner), "access_key_required": bool(os.environ.get("CUTLIST_ACCESS_KEY")),
            "storage": "Files are stored on the machine running Cutlist. Projects expire automatically after the selected period."}


@app.post("/api/session")
def unlock(request: Request, data: dict = Body(...)):
    verify_invite(data.get("key"))
    if request.state.owner:
        return {"unlocked": True}
    _, token = store.new_session()
    response = JSONResponse({"unlocked": True})
    set_cookie(response, token)
    return response


@app.get("/api/jobs")
def project_list(request: Request):
    return store.summaries(request.state.owner)


def cancel(job):
    with guard:
        state = get_job(job)
        state.update(status="cancelled", message="Analysis cancelled. You can retry this project.")
        store.save(state)
        if job in futures:
            futures[job].cancel()
        process = processes.get(job)
        if process and process.poll() is None:
            stop_process(process)


@app.post("/api/jobs/{job}/cancel")
def cancel_job(job: str):
    if get_job(job)["status"] not in {"queued", "running"}:
        raise HTTPException(409, "Only queued or running analysis can be cancelled.")
    cancel(job)
    return {"status": "cancelled"}


@app.post("/api/jobs/{job}/retry", status_code=202)
def retry_job(job: str):
    with guard:
        state = get_job(job)
        if state["status"] not in {"error", "cancelled"} or job in processes or job in futures and not futures[job].done():
            raise HTTPException(409, "Wait until the current process stops before retrying.")
        parameters = state.get("parameters")
        if not parameters or parameters[0] and not Path(parameters[0]).is_file():
            raise HTTPException(409, "The source recording is unavailable. Select it and start a new project.")
        state.update(status="queued", progress=0, message="Retry queued", error=None)
        store.save(state)
        futures[job] = pool.submit(analyze, job, *[Path(parameters[0]) if parameters[0] else None, *parameters[1:]])
    return {"id": job}


@app.delete("/api/jobs/{job}")
def delete_job(job: str):
    with guard:
        if job in processes or get_job(job)["status"] in {"queued", "running"}:
            cancel(job)
            raise HTTPException(409, "Analysis is stopping. Delete this project again in a moment.")
        # Keep a tombstone in memory so a previously queued task cannot revive it.
        get_job(job)["status"] = "deleting"
        clip_exports.cancel(job)
        discard_upload(get_job(job))
        shutil.rmtree(DATA / job, ignore_errors=True)
        store.delete(job)
    return {"deleted": True}


@app.patch("/api/jobs/{job}/clips/{clip_id}")
def save_clip(job: str, clip_id: int, data: dict = Body(...)):
    with guard:
        state = get_job(job)
        if state["status"] != "complete":
            raise HTTPException(409, "Wait for analysis to finish.")
        try:
            edited = edit_clip(state["result"], clip_id, data)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from None
        remember_edit(state, next(c for c in state["result"]["clips"] if c["id"] == clip_id))
        state["result"]["clips"] = [edited if c["id"] == clip_id else c for c in state["result"]["clips"]]
        persist_result(state)
    return edited


def persist_result(state):
    target = DATA / state["id"] / "result.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state["result"]))
    temporary.replace(target)
    store.save(state)


@app.post("/api/jobs/{job}/clips/{clip_id}/feedback")
def feedback(job: str, clip_id: int, data: dict = Body(...)):
    with guard:
        state = get_job(job)
        clip = next((c for c in state.get("result", {}).get("clips", []) if c["id"] == clip_id), None)
        if not clip:
            raise HTTPException(404, "Clip not found.")
        value, reason = data.get("value"), data.get("reason", "")
        if value not in {"keep", "reject", "unrated"} or not isinstance(reason, str) or len(reason) > 500:
            raise HTTPException(422, "Choose keep, reject, or unrated and a reason up to 500 characters.")
        clip.update(feedback=value, feedback_reason=reason, feedback_at=time.time())
        persist_result(state)
    return clip


@app.get("/api/quality")
def quality_report(request: Request):
    rows = []
    for project, source_name, c in store.feedback_rows(request.state.owner):
            rows.append({"project": project, "source": source_name, "clip": c["id"], "start": c["start"], "end": c["end"],
                         "original_start": c.get("original_start", c["start"]), "original_end": c.get("original_end", c["end"]),
                         "segments": composition.segments(c), "original_segments": c.get("original_segments", [{"start": c.get("original_start", c["start"]), "end": c.get("original_end", c["end"])}]),
                         "feedback": c.get("feedback", "unrated"), "reason": c.get("feedback_reason", ""), "signals": c.get("evidence", [])})
    reviewed = [r for r in rows if r["feedback"] != "unrated"]
    return {"suggested": len(rows), "reviewed": len(reviewed), "kept": sum(r["feedback"] == "keep" for r in reviewed),
            "rejected": sum(r["feedback"] == "reject" for r in reviewed),
            "trimmed": sum((r["start"], r["end"]) != (r["original_start"], r["original_end"]) or r["segments"] != r["original_segments"] for r in rows), "clips": rows,
            "note": "Your editorial decisions, stored locally. Optional preference ranking uses eligible feedback; language-model weights are unchanged. No virality score is claimed."}


@app.get("/api/preferences")
def preferences(request: Request):
    return {"caption_style": DEFAULT_STYLE, **store.preferences(request.state.owner)}


@app.post("/api/preferences")
def save_preferences(request: Request, data: dict = Body(...)):
    try:
        value = {**store.preferences(request.state.owner), "caption_style": caption_style(data.get("caption_style", {}))}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    store.preferences(request.state.owner, value)
    return value


@app.get("/api/jobs/{job}/benchmark")
def quality_benchmark(job: str):
    state = get_job(job)
    if state["status"] != "complete" or not state.get("result"):
        raise HTTPException(409, "Finish analysis before evaluating editorial quality.")
    return JSONResponse(quality.benchmark(state), headers={"Content-Disposition": 'attachment; filename="cutlist-real-footage-benchmark.json"'})


@app.post("/api/jobs/{job}/labels", status_code=201)
def add_quality_label(job: str, data: dict = Body(...)):
    with guard:
        state = get_job(job)
        if state["status"] != "complete":
            raise HTTPException(409, "Finish analysis before labelling the recording.")
        if len(state.get("quality_labels", [])) >= 1000:
            raise HTTPException(422, "Use up to 1,000 labels per recording.")
        try:
            label = {**quality.interval_label(data, state["result"]["duration"]), "id": uuid.uuid4().hex}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        state.setdefault("quality_labels", []).append(label)
        store.save(state)
    return label


@app.delete("/api/jobs/{job}/labels/{label}")
def delete_quality_label(job: str, label: str):
    with guard:
        state = get_job(job)
        state["quality_labels"] = [x for x in state.get("quality_labels", []) if x["id"] != label]
        store.save(state)
    return {"deleted": True}


speaker_guard = threading.Lock()


@app.post("/api/jobs/{job}/clips/{clip_id}/speakers")
def label_speakers(job: str, clip_id: int, data: dict = Body(...)):
    clip, source, _, _ = export_context(job, clip_id)
    if data.get("revision") != clip.get("revision", 0):
        raise HTTPException(409, "Reopen the current clip before labelling voices.")
    if not speaker_guard.acquire(blocking=False):
        raise HTTPException(429, "Another clip is being labelled. Try again shortly.")
    try:
        cues = speakers.annotate(source, clip)
        return save_clip(job, clip_id, {"revision": clip.get("revision", 0), "captions": cues})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    finally:
        speaker_guard.release()


@app.post("/api/jobs/{job}/batches", status_code=202)
def batch_export(job: str, data: dict = Body(...)):
    with guard:
        state = get_job(job)
        if state["status"] != "complete" or not state.get("result"):
            raise HTTPException(409, "Finish analysis before exporting a batch.")
        result = state["result"]
        ids = data.get("clips", [c["id"] for c in result["clips"] if c.get("feedback") != "reject"])
        if not isinstance(ids, list) or not 1 <= len(ids) <= 100 or any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
            raise HTTPException(422, "Choose 1–100 distinct clip IDs per batch.")
        contexts = [export_context(job, i) for i in ids]
        clips = [c[0] for c in contexts]
        return clip_exports.batch_start(job, clips, *contexts[0][1:])


@app.post("/api/jobs/{job}/batch-style")
def batch_style(job: str, data: dict = Body(...)):
    with guard:
        state = get_job(job)
        if state["status"] != "complete" or not state.get("result"):
            raise HTTPException(409, "Finish analysis before editing clips.")
        entries = data.get("clips")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 100 or any(not isinstance(e, dict) or type(e.get("id")) is not int or type(e.get("revision")) is not int for e in entries):
            raise HTTPException(422, "Choose 1–100 clips with their current revisions.")
        if len({e["id"] for e in entries}) != len(entries):
            raise HTTPException(422, "Choose each clip only once.")
        try:
            style = caption_style(data.get("caption_style", {}))
            edits = [edit_clip(state["result"], e["id"], {"revision": e["revision"], "caption_style": style, "burn_captions": True}) for e in entries]
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        for old in state["result"]["clips"]:
            if old["id"] in {c["id"] for c in edits}: remember_edit(state, old)
        replacements = {c["id"]: c for c in edits}
        state["result"]["clips"] = [replacements.get(c["id"], c) for c in state["result"]["clips"]]
        persist_result(state)
    return {"clips": edits}


@app.get("/api/jobs/{job}/batches/{batch}")
def batch_status(job: str, batch: str):
    return clip_exports.batch_status(job, batch)


@app.get("/api/jobs/{job}/batches/{batch}/download")
def download_batch(job: str, batch: str):
    status = clip_exports.batch_status(job, batch)
    if status["status"] != "ready":
        raise HTTPException(409, "Batch is still being prepared.")
    return FileResponse(DATA / job / "batches" / (batch + ".zip"), filename="cutlist-clips.zip", media_type="application/zip")


@app.post("/api/jobs/{job}/batches/{batch}/cancel")
def cancel_batch(job: str, batch: str):
    clip_exports.batch_status(job, batch)
    with clip_exports.lock:
        state = clip_exports.batches.get((job, batch))
        if not state or state["status"] not in {"queued", "running"}:
            raise HTTPException(409, "Only an unfinished batch can be cancelled.")
        running = state["status"] == "running"
        state.update(status="cancelled", error="Batch cancelled. Finished individual clips can be reused.")
    if running:
        with guard:
            process = processes.get(job)
            if process and process.poll() is None:
                stop_process(process)
    return {"status": "cancelled"}


@app.get("/api/jobs/{job}/clips/{clip_id}/captions.{kind}")
def captions_file(job: str, clip_id: int, kind: str):
    if kind not in {"srt", "vtt"}:
        raise HTTPException(404, "Choose SRT or VTT.")
    clip = next((c for c in get_job(job).get("result", {}).get("clips", []) if c["id"] == clip_id), None)
    if not clip:
        raise HTTPException(404, "Clip not found.")
    return PlainTextResponse(subtitle_text(clip, kind), headers={"Content-Disposition": f'attachment; filename="clip-{clip_id}.{kind}"'})


@app.get("/api/jobs/{job}/timeline.xml")
def timeline_file(job: str):
    result = get_job(job).get("result")
    if not result or not result.get("media_url"):
        raise HTTPException(409, "Add matching media to export a timeline.")
    return PlainTextResponse(timeline_xml(result), media_type="application/xml", headers={"Content-Disposition": 'attachment; filename="cutlist-timeline.xml"'})


def recover_jobs():
    for state in store.all():
        jobs[state["id"]] = state
        if state["status"] in {"queued", "running"} and state.get("parameters") and state.get("expires", 0) > time.time():
            state.update(status="queued", message="Resuming after worker restart")
            store.save(state)
            params = state["parameters"]
            futures[state["id"]] = pool.submit(analyze, state["id"], Path(params[0]) if params[0] else None, *params[1:])
        elif state["status"] == "uploading":
            state.update(status="error", message="File transfer interrupted. Select the source again.", error="Upload interrupted before the project was queued.")
            store.save(state)


def cleanup(now=None):
    now = now or time.time()
    with store.connection() as db:
        db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
    for state in store.expired(now):
        if state.get("expires", float("inf")) <= now:
            if state["id"] in processes or state["status"] in {"queued", "running"}:
                cancel(state["id"])
            else:
                jobs[state["id"]] = {**state, "status": "deleting"}
                clip_exports.cancel(state["id"])
                discard_upload(state)
                shutil.rmtree(DATA / state["id"], ignore_errors=True)
                store.delete(state["id"])
    for upload in store.uploads():
        if upload["expires"] <= now:
            Path(upload["path"]).unlink(missing_ok=True)
            store.delete_upload(upload["id"])


def discard_upload(state):
    upload = store.upload(state.get("upload_id", ""))
    if upload and upload["owner"] == state["owner"]:
        Path(upload["path"]).unlink(missing_ok=True)
        store.delete_upload(upload["id"])


workspace_pool = install_workspace(app, store, DATA, get_job, guard, persist_result)

from worker.setup import SetupManager, install as install_setup
setup_manager = SetupManager(ROOT, DATA)
install_setup(app, setup_manager)
