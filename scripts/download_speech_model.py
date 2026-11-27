"""Install a pinned free CPU speech model; verify all downloaded bytes."""
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.transcription import MODEL
from worker.downloads import download

REVISION = "84b478d541cdbf0c296a7ab5f1e20a6f8f998114"
BASE = f"https://huggingface.co/csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8/resolve/{REVISION}"
HASHES = {
    "encoder.int8.onnx": "92a10bd9c23936ea0566c29f963bc57c2a5df4261b6a8921c39abea66557ca8b",
    "decoder.int8.onnx": "4d097bae3bac5c96018d5293ba39f1b657296102b142affda40378f38b020f41",
    "joiner.int8.onnx": "77a29f3e5ac7d37074e0af465391b80f314015d5b97f0f90c6c7767f1d56f428",
    "tokens.txt": "ec182b70dd42113aff6c5372c75cac58c952443eb22322f57bbd7f53977d497d",
}


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main():
    print("NVIDIA Parakeet TDT 0.6b v2 · English · CPU · ~661 MB download")
    print("Weights: NVIDIA, CC BY 4.0. ONNX conversion: sherpa-onnx project. See MODEL-NOTICES.md.")
    MODEL.mkdir(parents=True, exist_ok=True)
    for name, expected in HASHES.items():
        target = MODEL / name
        if target.is_file() and digest(target) == expected:
            print(f"Verified {name} (already installed)", flush=True)
            continue
        print(f"Downloading {name}…", flush=True)
        download(f"{BASE}/{name}", target, expected)
    print("Speech model ready. Restart Cutlist or run Find clips again.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Speech model setup failed: {exc}\nCaptions and audio-only analysis still work. Retry when online.", file=sys.stderr)
        sys.exit(1)
