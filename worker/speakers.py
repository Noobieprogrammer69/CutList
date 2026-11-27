"""Cue-level anonymous voice clustering. Not identity recognition or overlap diarization."""
import os
import subprocess
from pathlib import Path

import numpy as np

MODEL = Path(os.environ.get("CUTLIST_SPEAKER_MODEL", Path(__file__).resolve().parents[1] / "models/speakers/embedding.onnx"))


def available():
    return MODEL.is_file()


def assign(captions, embeddings, threshold=.65):
    centers, counts, output = [], [], []
    for cue, vector in zip(captions, embeddings):
        item = dict(cue)
        if vector is None:
            output.append(item)
            continue
        norm = np.linalg.norm(vector)
        if not np.isfinite(norm) or norm < 1e-8:
            output.append(item)
            continue
        vector = np.asarray(vector) / norm
        similarities = [float(np.dot(vector, c / np.linalg.norm(c))) for c in centers]
        best = int(np.argmax(similarities)) if similarities else -1
        if best < 0 or similarities[best] < threshold:
            best = len(centers)
            if best >= 8:
                output.append(item)
                continue
            centers.append(vector.copy())
            counts.append(1)
        else:
            centers[best] += vector
            counts[best] += 1
        item.update(speaker=f"Speaker {best + 1}", speaker_source="voice_model_prediction")
        output.append(item)
    return output


def annotate(source, clip):
    if not available():
        raise ValueError("Voice model missing. Run npm run speakers:download, or enter speaker names in the caption editor.")
    import sherpa_onnx
    proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(clip["start"]), "-i", str(source), "-t", str(clip["end"] - clip["start"]),
                           "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"], capture_output=True, timeout=60)
    if proc.returncode:
        raise ValueError("The clip audio could not be decoded for speaker labels.")
    samples = np.frombuffer(proc.stdout, dtype="<f4")
    extractor = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(MODEL), num_threads=2, provider="cpu"))
    embeddings = []
    for cue in clip.get("captions", []):
        if cue.get("speaker"):
            # Preserve supplied/manual voices; do not rename them automatically.
            embeddings.append(None)
            continue
        a, b = (round((cue[k] - clip["start"]) * 16000) for k in ("start", "end"))
        segment = samples[max(0, a):min(len(samples), b)]
        if len(segment) < 8000 or float(np.sqrt(np.mean(segment ** 2))) < .002:
            embeddings.append(None)
            continue
        stream = extractor.create_stream()
        stream.accept_waveform(16000, segment)
        stream.input_finished()
        embeddings.append(np.asarray(extractor.compute(stream)) if extractor.is_ready(stream) else None)
    return assign(clip.get("captions", []), embeddings)
