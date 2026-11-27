"""Sequential chapter coordinator: decode -> speech subprocess -> editorial subprocess.

Only a few minutes of PCM exist at a time. Each chapter has lookback/lookahead,
source-relative timestamps and durable speech/review checkpoints.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import tempfile

import numpy as np

from worker.analysis import Event, audio_energy, energy_events, rank_clips, select_nonoverlapping, transcript_events
from worker.captions import Cue, timecode
from worker.transcription import words_to_cues
from worker import semantic
from worker.inference import slot


def run_child(args, check=lambda: None, on_line=lambda *_: None):
    with tempfile.TemporaryFile(mode="w+") as diagnostic, subprocess.Popen(args, stdout=subprocess.PIPE, stderr=diagnostic, text=True) as child:
        # Diagnostics can be substantial for native libraries; redirect them to
        # disk rather than filling an unread stderr pipe.
        try:
            for line in child.stdout:
                check()
                on_line(line)
            if child.wait():
                diagnostic.seek(0)
                raise ValueError("A chapter process failed. " + diagnostic.read()[-1500:])
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)


def analyze(request, folder, progress=lambda *_: None, publish=lambda *_: None, check=lambda: None):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    source = Path(request["source"])
    total, size, maximum = request["duration"], request.get("chapter_seconds", 300), request["maximum"]
    source_stat = source.stat()
    signature = {"size": source_stat.st_size, "modified": source_stat.st_mtime_ns, "duration": total, "chapter_seconds": size, "maximum": maximum}
    identity = folder / "identity.json"
    if identity.exists() and json.loads(identity.read_text()) != signature:
        import shutil
        for old in folder.iterdir():
            if old.is_dir(): shutil.rmtree(old)
    identity_tmp = identity.with_suffix(".tmp")
    identity_tmp.write_text(json.dumps(signature)); identity_tmp.replace(identity)
    started, all_words, all_clips, warnings, reviewed, speech_seconds = time.monotonic(), [], [], [], 0, 0
    global_features = None
    if request.get("features_output"):
        count = math.ceil(total/.1)
        global_features = [np.full(count, -100., dtype=np.float32), np.zeros(count, dtype=np.float32), np.zeros(count, dtype=np.float32)]
    chapter_count = math.ceil(total / size)
    content = request.get("content_review", True) and semantic.status()["available"]
    if request.get("content_review", True) and not content:
        warnings.append("Local content model unavailable; chapter results are basic candidates, with meaning not reviewed.")
    for number in range(chapter_count):
        check()
        core_start, core_end = number * size, min(total, (number+1) * size)
        a, b = max(0, core_start-maximum-3), min(total, core_end+maximum+3)
        part = folder / f"chapter-{number:05d}"
        part.mkdir(exist_ok=True)
        pcm, speech_path = part / "audio.pcm", part / "speech.json"
        if not speech_path.is_file():
            progress(number / chapter_count, f"Chapter {number+1}/{chapter_count} · decoding {timecode(a)}–{timecode(b)}", core_start)
            process = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-threads", "2", "-ss", str(a), "-i", str(source), "-t", str(b-a),
                                      "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm)], capture_output=True, timeout=max(60, size * 2))
            if process.returncode: raise ValueError("A chapter could not be decoded. Re-export this recording as MP4/AAC or provide captions.")
            def speech_line(line):
                payload = json.loads(line)
                if payload.get("error"): raise ValueError(payload["error"])
                fraction = payload.get("progress", 0)/100
                progress((number+.6*fraction) / chapter_count, f"Chapter {number+1}/{chapter_count} · transcribing before review", min(total, a+fraction*(b-a)))
            with slot(check):
                run_child([sys.executable, "-m", "worker.transcription", str(pcm), str(speech_path)], check, speech_line)
        speech = json.loads(speech_path.read_text())
        speech_seconds += speech.get("processing_seconds", 0)
        local_cues = [Cue(**c) for c in speech["cues"]]
        cues = [Cue(round(c.start+a, 3), round(min(total, c.end+a), 3), c.text, c.speaker) for c in local_cues]
        for word in speech["words"]:
            start, end = word["start"]+a, min(total, word["end"]+a)
            if core_start <= start < core_end and end > start:
                all_words.append({**word, "start": round(start, 3), "end": round(end, 3)})
        # A cached speech file can avoid PCM entirely. Audio evidence can be
        # rebuilt cheaply for this chapter without re-running recognition.
        feature_path = part / "features.npz"
        if feature_path.is_file():
            with np.load(feature_path, allow_pickle=False) as features:
                db, crest, variation = features["db"], features["crest"], features["variation"]
        else:
            if not pcm.is_file():
                result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-ss", str(a), "-i", str(source), "-t", str(b-a), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm)], capture_output=True, timeout=max(60, size*2))
                if result.returncode: raise ValueError("Could not decode chapter audio features.")
            samples = np.memmap(pcm, dtype="<i2", mode="r")
            try: db, crest, variation = audio_energy(samples, check=check)
            finally: samples._mmap.close()
            np.savez_compressed(feature_path, db=db, crest=crest, variation=variation)
        if global_features is not None and len(db):
            lo, hi = math.ceil(core_start/.1), min(len(global_features[0]), math.ceil(core_end/.1))
            indices = np.clip(np.floor((np.arange(lo, hi)*.1-a)/.1).astype(int), 0, len(db)-1)
            for destination, values in zip(global_features, (db, crest, variation)): destination[lo:hi] = values[indices]
        pcm.unlink(missing_ok=True)
        events = transcript_events(cues, request.get("keywords", []))
        for event in energy_events(db, crest, variation):
            events.append(Event(event.time+a, event.kind, event.weight, event.detail.replace(timecode(event.time), timecode(event.time+a)), title=event.title))
        for marker in request.get("replays", []):
            if a <= marker < b: events.append(Event(marker, "replay", 2.2, f"Editor-supplied replay marker at {timecode(marker)}"))
        clips = rank_clips(events, cues, total, maximum)
        if content and cues:
            input_path, output_path = part / "review-input.json", part / "review-output.json"
            input_path.write_text(json.dumps({"cues": [vars(c) for c in cues], "maximum": maximum, "goal": request["goal"], "profile": request.get("profile", "general"),
                                             "keywords": request.get("keywords", []), "events": [vars(e) for e in events],
                                             "source": str(source) if request.get("has_video") else None, "visual_review": request.get("visual_review", False), "sequences": True}))
            try:
                progress((number+.65)/chapter_count, f"Chapter {number+1}/{chapter_count} · editorial review; speech model released", core_end)
                def editorial_line(line):
                    payload = json.loads(line)
                    if payload.get("error"): raise ValueError(payload["error"])
                    progress((number+.65+.3*payload.get("progress", 0))/chapter_count, f"Chapter {number+1}/{chapter_count} · {payload.get('message', 'checking context')}", core_end)
                run_child([sys.executable, "-m", "worker.semantic", str(input_path), str(output_path)], check, editorial_line)
                review = json.loads(output_path.read_text())
                clips = review["clips"]
                reviewed += 1
                if review.get("visual_error") and review["visual_error"] not in warnings: warnings.append(review["visual_error"])
            except Exception as exc:
                check()
                warnings.append(f"Chapter {number+1} editorial review failed: {exc}. Later chapters use basic candidates; completed reviews are cached for retry.")
                content = False
        all_clips.extend(clips)
        # Renumber cumulative proposals so IDs never collide across chapters.
        merged = select_nonoverlapping([{k: v for k, v in c.items() if k != "id"} for c in all_clips])
        publish(merged, {"sections_complete": number+1, "sections_total": chapter_count, "through_seconds": core_end, "chaptered": True,
                         "selection_method": "local_model" if reviewed == number+1 else "mixed" if reviewed else "basic"})
        progress((number+1)/chapter_count, f"Chapter {number+1}/{chapter_count} ready; next section queued", core_end)
    all_words.sort(key=lambda w: (w["start"], w["end"]))
    last_end = 0
    cleaned = []
    for w in all_words:
        w["start"] = max(last_end, w["start"])
        if w["end"] > w["start"]:
            cleaned.append(w)
            last_end = w["end"]
    speech = {"engine": "Parakeet English CPU · sequential chapters", "words": cleaned, "cues": words_to_cues(cleaned), "chapters": chapter_count,
              "processing_seconds": round(speech_seconds, 3)}
    if global_features is not None:
        target = Path(request["features_output"])
        temporary = target.with_suffix(".tmp")
        with temporary.open("wb") as out: np.savez_compressed(out, db=global_features[0], crest=global_features[1], variation=global_features[2])
        temporary.replace(target)
    return {"speech": speech, "clips": merged if chapter_count else [], "warnings": warnings, "model": semantic.MODEL,
            "windows": chapter_count, "chapters_reviewed": reviewed, "selection_method": "local_model" if reviewed == chapter_count else "mixed" if reviewed else "basic",
            "processing_seconds": round(time.monotonic()-started, 3), "visual_review": bool(request.get("visual_review") and reviewed)}


if __name__ == "__main__":
    import signal
    # A process group lets the API stop FFmpeg, speech and editorial children.
    try:
        request_path, response_path = map(Path, sys.argv[1:3])
        result = analyze(json.loads(request_path.read_text()), response_path.with_suffix(".parts"),
                         progress=lambda fraction, message, seconds: print(json.dumps({"progress": fraction, "message": message, "processed_seconds": seconds}), flush=True),
                         publish=lambda clips, coverage: print(json.dumps({"partial_clips": clips, "coverage": coverage}), flush=True))
        temporary = response_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result))
        temporary.replace(response_path)
    except Exception as exc:
        print(json.dumps({"error": str(exc)}), flush=True)
        sys.exit(1)
