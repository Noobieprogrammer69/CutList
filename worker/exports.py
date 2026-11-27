"""One bounded render worker keeps clip previews responsive during analysis."""
import logging
import re
import threading
import copy
import json
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException

from worker.captions import timecode
from worker.editing import ass_subtitles
from worker.tracking import follow_faces, crop_expression
from worker import composition

log = logging.getLogger("cutlist.exports")


class ClipExports:
    def __init__(self, data, executor, run_ffmpeg):
        self.data = Path(data)
        self.executor = executor
        self.run_ffmpeg = run_ffmpeg
        self.states = {}
        self.lock = threading.Lock()
        self.batches = {}

    def paths(self, job, clip, kind, source_name):
        extension = "mp4" if kind == "video" else "m4a"
        target = self.data / job / "clips" / f"clip-{clip['id']:02d}-v{clip.get('revision', 0)}.{extension}"
        stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(source_name).stem).strip("-")[:70] or "cutlist"
        start, end = (timecode(clip[key]).replace(":", "-") for key in ("start", "end"))
        filename = f"{stem}-clip-{clip['id']:02d}-{start}-to-{end}.{extension}"
        return target, filename

    def status(self, job, clip, kind, source_name):
        target, filename = self.paths(job, clip, kind, source_name)
        if target.is_file() and target.stat().st_size:
            return {"status": "ready", "progress": 100, "filename": filename,
                    "url": f"/api/jobs/{job}/clips/{clip['id']}/download?revision={clip.get('revision', 0)}"}
        with self.lock:
            return dict(self.states.get((job, clip["id"], clip.get("revision", 0)), {"status": "not_started", "progress": 0}))

    def start(self, job, clip, source, kind, source_name):
        state = self.status(job, clip, kind, source_name)
        if state["status"] in {"ready", "queued", "running"}:
            return state
        key = (job, clip["id"], clip.get("revision", 0))
        with self.lock:
            # A double-click or a second tab shares the same render.
            existing = self.states.get(key, {})
            if existing.get("status") in {"queued", "running"}:
                return dict(existing)
            pending = sum(s["status"] in {"queued", "running"} for s in self.states.values())
            if pending >= 5:
                raise HTTPException(429, "The clip export queue is full. Let a download finish and try again.")
            self.states[key] = {"status": "queued", "progress": 0}
        self.executor.submit(self.render, job, copy.deepcopy(clip), source, kind, source_name)
        return {"status": "queued", "progress": 0}

    def update(self, key, **fields):
        with self.lock:
            self.states[key].update(fields)

    def cancel(self, job):
        with self.lock:
            for key, state in self.states.items():
                if key[0] == job:
                    state.update(status="error", error="Project deleted or expired.")
            for key, state in self.batches.items():
                if key[0] == job:
                    state.update(status="error", error="Project deleted or expired.")

    def render(self, job, clip, source, kind, source_name):
        key = (job, clip["id"], clip.get("revision", 0))
        target, _ = self.paths(job, clip, kind, source_name)
        temporary = target.with_name(f"{target.stem}.partial{target.suffix}")
        try:
            if target.is_file() and target.stat().st_size:
                return
            with self.lock:
                if self.states.get(key, {}).get("status") == "error":
                    return
            target.parent.mkdir(parents=True, exist_ok=True)
            self.update(key, status="running", progress=0)
            duration = composition.duration(clip)
            # Input seeking is accurate when transcoding. Stream copy would
            # extend the interval to keyframes and can cut off the setup.
            args = ["-ss", f"{clip['start']:.3f}", "-i", str(source), "-t", f"{duration:.3f}"]
            parts = composition.segments(clip)
            multiple = len(parts) > 1
            has_audio = kind != "video" or clip.get("_has_audio", True)
            graph = []
            if multiple:
                args = []
                labels = []
                for i, part in enumerate(parts):
                    args.extend(["-threads", "1", "-ss", str(part["start"]), "-t", str(part["end"] - part["start"]), "-i", str(source)])
                    if kind == "video":
                        graph.append(f"[{i}:v:0]setpts=PTS-STARTPTS[v{i}]")
                        labels.append(f"[v{i}]")
                    if has_audio:
                        graph.append(f"[{i}:a:0]asetpts=PTS-STARTPTS[a{i}]")
                        labels.append(f"[a{i}]")
                graph.append("".join(labels) + f"concat=n={len(parts)}:v={int(kind == 'video')}:a={int(has_audio)}" + ("[joinedv]" if kind == "video" else "") + ("[joineda]" if has_audio else ""))
            if kind == "video":
                crop = clip.get("crop", {"format": "source", "x": .5, "y": .5})
                filters = []
                if crop["format"] != "source":
                    aspect = "9/16" if crop["format"] == "vertical" else "1"
                    x = str(crop['x'])
                    if crop.get("tracking") == "face":
                        self.update(key, message="Following faces in this clip")
                        points, cursor = [], 0
                        for part in parts:
                            points.extend({**p, "time": p["time"] + cursor} for p in follow_faces(source, part["start"], part["end"], 9/16 if crop["format"] == "vertical" else 1))
                            cursor += part["end"] - part["start"]
                        x = crop_expression(points, crop["x"])
                    filters.append(f"crop=w='trunc(min(iw,ih*{aspect})/2)*2':h='trunc(min(ih,iw/({aspect}))/2)*2':x='(iw-ow)*({x})':y='(ih-oh)*{crop['y']}'")
                    resolution = crop.get("resolution", "720")
                    if resolution != "source":
                        width = int(resolution)
                        filters.append(f"scale={width}:{int(width * 16 / 9)}" if crop["format"] == "vertical" else f"scale={width}:{width}")
                    filters.append("setsar=1")
                filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
                if clip.get("burn_captions") and clip.get("captions"):
                    subtitle = target.with_suffix(".ass")
                    subtitle.write_text(ass_subtitles(clip), encoding="utf-8")
                    # Text never enters the filter expression, only a generated
                    # filename. Escape paths for FFmpeg's filter grammar.
                    def escape(path):
                        return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
                    fonts = Path(__file__).resolve().parents[1] / "public/fonts"
                    filters.append(f"subtitles=filename='{escape(subtitle)}':fontsdir='{escape(fonts)}'")
                if multiple:
                    graph.append("[joinedv]" + ",".join(filters) + "[finalv]")
                    args.extend(["-filter_complex_threads", "1", "-filter_buffered_frames", "64", "-filter_complex", ";".join(graph), "-map", "[finalv]"])
                    if has_audio:
                        args.extend(["-map", "[joineda]"])
                else:
                    args.extend(["-map", "0:V:0", "-map", "0:a:0?", "-vf", ",".join(filters)])
                args.extend([
                             "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "14" if clip.get("quality") == "maximum" else "18", "-pix_fmt", "yuv420p"])
            else:
                if multiple:
                    args.extend(["-filter_complex_threads", "1", "-filter_buffered_frames", "64", "-filter_complex", ";".join(graph), "-map", "[joineda]", "-vn"])
                else:
                    args.extend(["-map", "0:a:0", "-vn"])
            args.extend(["-c:a", "aac", "-b:a", "192k", "-map_metadata", "-1", "-movflags", "+faststart", str(temporary)])
            self.run_ffmpeg(job, args, duration, 0, 99, "Preparing clip download",
                            on_progress=lambda percent: self.update(key, progress=percent))
            if not temporary.is_file() or not temporary.stat().st_size:
                raise ValueError("FFmpeg produced an empty clip. Try re-exporting your recording.")
            temporary.replace(target)
            self.update(key, status="ready", progress=100)
        except Exception as exc:
            log.warning("Clip export failed: %s", exc)
            self.update(key, status="error", error=f"Could not prepare this clip: {exc}")
        finally:
            temporary.unlink(missing_ok=True)

    def batch_start(self, job, clips, source, kind, source_name):
        with self.lock:
            if sum(b.get("status") in {"queued", "running"} for b in self.batches.values()) >= 2:
                raise HTTPException(429, "Two batch exports are already pending. Wait for one to finish.")
            batch = uuid.uuid4().hex
            state = {"id": batch, "job": job, "status": "queued", "progress": 0, "completed": 0, "total": len(clips)}
            self.batches[(job, batch)] = state
        self.executor.submit(self.batch_render, job, batch, copy.deepcopy(clips), source, kind, source_name)
        return dict(state)

    def batch_status(self, job, batch):
        if not re.fullmatch(r"[a-f0-9]{32}", batch):
            raise HTTPException(404, "Batch not found.")
        with self.lock:
            state = self.batches.get((job, batch))
            if state:
                return dict(state)
        target = self.data / job / "batches" / (batch + ".zip")
        if target.is_file():
            return {"id": batch, "status": "ready", "progress": 100, "url": f"/api/jobs/{job}/batches/{batch}/download"}
        raise HTTPException(404, "Batch not found or interrupted by restart. Start a new batch; finished clips will be reused.")

    def batch_render(self, job, batch, clips, source, kind, source_name):
        state = self.batches[(job, batch)]
        folder = self.data / job / "batches"
        target = folder / (batch + ".zip")
        temporary = target.with_suffix(".partial")
        try:
            with self.lock:
                if state.get("status") in {"cancelled", "error"}:
                    return
                state.update(status="running")
            folder.mkdir(exist_ok=True)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                for i, clip in enumerate(clips):
                    if state.get("status") in {"cancelled", "error"}:
                        raise ValueError("Batch cancelled, or project deleted/expired.")
                    key = (job, clip["id"], clip.get("revision", 0))
                    with self.lock:
                        self.states.setdefault(key, {"status": "queued", "progress": 0})
                    self.render(job, clip, source, kind, source_name)
                    if state.get("status") in {"cancelled", "error"}:
                        raise ValueError("Batch cancelled, or project deleted/expired.")
                    rendered, filename = self.paths(job, clip, kind, source_name)
                    if not rendered.is_file():
                        raise ValueError(self.states.get(key, {}).get("error", "A clip could not be rendered."))
                    archive.write(rendered, filename)
                    from worker.editing import subtitle_text
                    for extension in ("srt", "vtt"):
                        archive.writestr(str(Path(filename).with_suffix("." + extension)), subtitle_text(clip, extension))
                    state.update(completed=i + 1, progress=round((i + 1) / len(clips) * 99))
                archive.writestr("manifest.json", json.dumps({"source": source_name, "clips": clips, "note": "Snapshot of the saved clip revisions at batch start."}, indent=2))
            temporary.replace(target)
            state.update(status="ready", progress=100, url=f"/api/jobs/{job}/batches/{batch}/download")
        except Exception as exc:
            if state.get("status") != "cancelled":
                state.update(status="error", error=str(exc))
        finally:
            temporary.unlink(missing_ok=True)
