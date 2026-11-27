"""Download only the 6.13 MB quantized CED model and its actual class map.

Upstream: https://huggingface.co/k2-fsa/sherpa-onnx-ced-tiny-audio-tagging-2024-04-19
Fixed revision and SHA-256 prevent mismatched models and label maps.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.events import EventClassifier, MODEL_DIR, verify_labels
from worker.downloads import download

BASE = "https://huggingface.co/k2-fsa/sherpa-onnx-ced-tiny-audio-tagging-2024-04-19/resolve/efe3a5c"
HASH = "73aa22e783115f6a4ae169b36089907e07d0fd44795595eddea9c1bfc74cc945"


def main():
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for filename in ("model.int8.onnx", "class_labels_indices.csv"):
        target = MODEL_DIR / filename
        temporary = target.with_suffix(target.suffix + ".part")
        print(f"Downloading {filename}…", flush=True)
        download(f"{BASE}/{filename}", temporary, HASH if filename.endswith("onnx") else None, maximum=10*1024**2)
        if filename.endswith("csv"): verify_labels(temporary)
        temporary.replace(target)
    classifier = EventClassifier()
    if classifier.tagger is None:
        raise ValueError(classifier.status)
    import numpy as np
    predictions = classifier.predict(np.zeros(48000, dtype=np.float32))
    if not predictions or any(not np.isfinite(v) for v in predictions.values()):
        raise ValueError("CPU model smoke test failed.")
    print(classifier.status)
    print("CPU inference smoke test passed. Restart Cutlist or run Find clips again.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Model setup failed: {error}\nCore analysis still works without this download. Retry when online.", file=sys.stderr)
        sys.exit(1)
