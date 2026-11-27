"""Offline English ASR with a free quantized CPU model. No paid service."""
import json
import os
import hashlib
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL = Path(os.environ.get("CUTLIST_ASR_MODEL_DIR", ROOT / "models/parakeet"))
FILES = ("encoder.int8.onnx", "decoder.int8.onnx", "joiner.int8.onnx", "tokens.txt")


def available():
    return all((MODEL / name).is_file() for name in FILES)


def chunks(samples, maximum=20):
    """Seek a quiet 100 ms boundary around 15 s; never decode an entire episode."""
    rate, start, total = 16000, 0, len(samples)
    while start < total:
        if total - start <= maximum * rate:
            end = total
        else:
            low, high = start + int(maximum * .6) * rate, min(total, start + (maximum - 1) * rate)
            candidates = []
            for index in range(low, high, rate // 10):
                frame = np.asarray(samples[index:index + rate // 10], dtype=np.float32)
                candidates.append((float(np.mean(frame * frame)), index + rate // 20))
            _, end = min(candidates)
        yield start, end
        if end == total:
            break
        start = max(start + rate, end - rate // 4)


def token_words(tokens, timestamps, offset, end):
    words = []
    boundary = True
    for token, stamp in zip(tokens, timestamps):
        if token in {"<blk>", "<unk>", "<s>", "</s>"}:
            continue
        at = min(end, max(offset, offset + float(stamp)))
        boundary = boundary or token.startswith(("▁", " "))
        value = token.lstrip("▁ ")
        if not value:
            continue
        if boundary or not words:
            words.append({"text": value, "start": round(at, 3), "end": round(min(end, at + .3), 3)})
            boundary = False
        elif words:
            words[-1]["text"] += value
            words[-1]["end"] = round(min(end, at + .12), 3)
    for index, word in enumerate(words):
        if index + 1 < len(words):
            word["end"] = round(min(end, max(word["start"] + .02, words[index + 1]["start"])), 3)
        word["end"] = min(end, max(word["start"] + .02, word["end"]))
    return [w for w in words if w["end"] > w["start"]]


def words_to_cues(words):
    cues, current = [], []
    for word in words:
        if current and (word["start"] - current[-1]["end"] > .75 or word["end"] - current[0]["start"] > 7 or len(current) >= 20):
            cues.append({"start": current[0]["start"], "end": current[-1]["end"], "text": " ".join(w["text"] for w in current)})
            current = []
        current.append(word)
        if word["text"].endswith((".", "?", "!")):
            cues.append({"start": current[0]["start"], "end": current[-1]["end"], "text": " ".join(w["text"] for w in current)})
            current = []
    if current:
        cues.append({"start": current[0]["start"], "end": current[-1]["end"], "text": " ".join(w["text"] for w in current)})
    return cues


def normalize_speech(audio):
    """Bounded local gain: a loud reaction must not mask a quiet setup line."""
    for start in range(0, len(audio), 8000):
        frame = audio[start:start + 8000]
        rms = float(np.sqrt(np.mean(frame * frame)))
        peak = max(float(np.max(np.abs(frame))), 1e-6)
        frame *= min(8, .08 / max(rms, 1e-6), .97 / peak)
    return audio


def transcribe(pcm, output, model=MODEL, progress=lambda *_: None, threads=None, batch_size=None):
    import resource
    import sherpa_onnx
    started = time.monotonic()
    threads = max(1, min(8, int(threads or os.environ.get("CUTLIST_ASR_THREADS", min(4, os.cpu_count() or 2)))))
    batch_size = max(1, min(4, int(batch_size or os.environ.get("CUTLIST_ASR_BATCH", "2"))))
    chunk_seconds = max(8, min(20, int(os.environ.get("CUTLIST_ASR_CHUNK_SECONDS", "20"))))
    recognizer = None
    samples = np.memmap(pcm, dtype="<i2", mode="r")
    # Small atomic files preserve completed speech chunks across cancellation
    # and restarts; no multi-hour transcript is rewritten after every chunk.
    directory = Path(output).with_suffix(".parts")
    directory.mkdir(exist_ok=True)
    signature = hashlib.sha256(bytes(samples[:32768]) + bytes(samples[-32768:]) + str(len(samples)).encode()).hexdigest()
    model_stamp = [(name, (model / name).stat().st_size if (model / name).is_file() else 0) for name in FILES]
    identity = {"version": 4, "audio": signature, "model": model_stamp, "chunk_seconds": chunk_seconds}
    identity_text = json.dumps(identity, sort_keys=True)
    manifest = directory / "identity.json"
    if not manifest.exists() or manifest.read_text() != identity_text:
        for old in directory.glob("*.json"):
            old.unlink()
        manifest.write_text(identity_text)
    spans = list(chunks(samples, chunk_seconds))
    parts, pending = [None] * len(spans), []
    words, cursor, hits, decoded_seconds, skipped_seconds = [], 0, 0, 0, 0

    def commit(index, found, cache=False):
        nonlocal cursor
        parts[index] = found
        if not cache:
            target = directory / f"{spans[index][0]}-{spans[index][1]}.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(found))
            temporary.replace(target)
        while cursor < len(parts) and parts[cursor] is not None:
            for original in parts[cursor]:
                word = dict(original)
                if words and word["start"] < words[-1]["end"]:
                    if any(old["text"].casefold() == word["text"].casefold() and abs(old["start"] - word["start"]) < .6 for old in words[-6:]):
                        continue
                    word["start"] = words[-1]["end"]
                if word["end"] > word["start"]:
                    words.append(word)
            cursor += 1
            progress(100 * spans[cursor - 1][1] / len(samples))

    def flush():
        nonlocal decoded_seconds
        if not pending:
            return
        streams = [item[1] for item in pending]
        if len(streams) == 1:
            recognizer.decode_stream(streams[0])
        else:
            recognizer.decode_streams(streams)
        for index, stream in pending:
            start, end = spans[index]
            found = token_words(stream.result.tokens, stream.result.timestamps, start / 16000, end / 16000)
            commit(index, found)
            decoded_seconds += (end - start) / 16000
        pending.clear()
    try:
        for index, (start, end) in enumerate(spans):
            cached = directory / f"{start}-{end}.json"
            if cached.exists():
                try:
                    found = json.loads(cached.read_text())
                    if not isinstance(found, list) or any(not isinstance(w, dict) or not start / 16000 <= w['start'] < w['end'] <= end / 16000 for w in found):
                        raise ValueError("Invalid checkpoint")
                    commit(index, found, cache=True)
                    hits += 1
                    continue
                except (ValueError, TypeError, KeyError):
                    cached.unlink(missing_ok=True)
            audio = np.asarray(samples[start:end], dtype=np.float32) / 32768
            rms = float(np.sqrt(np.mean(audio * audio)))
            frames = audio[:len(audio) // 320 * 320].reshape(-1, 320)
            envelope = np.sqrt(np.mean(frames * frames, axis=1)) if len(frames) else np.array([0])
            if rms < .002 or float(np.std(envelope)) / max(rms, 1e-6) < .08 or np.sum(envelope > .003) * .02 < .4:
                skipped_seconds += (end - start) / 16000
                commit(index, [])
                continue
            if recognizer is None:
                recognizer = sherpa_onnx.OfflineRecognizer.from_transducer(
                    encoder=str(model / FILES[0]), decoder=str(model / FILES[1]), joiner=str(model / FILES[2]), tokens=str(model / FILES[3]),
                    model_type="nemo_transducer", num_threads=threads, provider="cpu", decoding_method="greedy_search")
            normalize_speech(audio)
            stream = recognizer.create_stream()
            # A short tail gives a word at the chunk boundary room to finish;
            # output timestamps remain clamped to the actual source interval.
            stream.accept_waveform(16000, np.pad(audio, (0, 6400)))
            pending.append((index, stream))
            if len(pending) >= batch_size:
                flush()
        flush()
        result = {"engine": "Parakeet TDT 0.6b v2 int8 · English · CPU", "words": words, "cues": words_to_cues(words),
                  "processing_seconds": round(time.monotonic() - started, 2), "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
                  "threads": threads, "batch_size": batch_size, "checkpoint_hits": hits, "chunks": len(spans),
                  "chunk_seconds": chunk_seconds,
                  "decoded_seconds": round(decoded_seconds, 2), "skipped_seconds": round(skipped_seconds, 2)}
        temporary = Path(output).with_suffix(".tmp")
        temporary.write_text(json.dumps(result))
        temporary.replace(output)
        return result
    finally:
        samples._mmap.close()


if __name__ == "__main__":
    import sys
    try:
        transcribe(Path(sys.argv[1]), Path(sys.argv[2]), progress=lambda value: print(json.dumps({"progress": value}), flush=True))
    except Exception as error:
        print(json.dumps({"error": str(error)}), flush=True)
        sys.exit(1)
