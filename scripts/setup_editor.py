"""Download a free local content model. No recording or transcript is sent."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.semantic import MODEL, URL, local_url


def main():
    import httpx
    local_url(URL)
    if not shutil.which("ollama"):
        raise ValueError("Install Ollama first (Omarchy: sudo pacman -S --needed ollama), then run ollama serve in another terminal.")
    if "cloud" in MODEL.casefold() or not MODEL.startswith(("qwen3:", "qwen2.5:")):
        raise ValueError("Choose a local Qwen model tag; cloud models are disabled.")
    try:
        with httpx.Client(trust_env=False) as client:
            response = client.get(URL + "/api/tags", timeout=2)
            response.raise_for_status()
    except httpx.HTTPError:
        raise ValueError("Cannot reach local Ollama. Run ollama serve in another terminal, then run npm run editor:download again.") from None
    print(f"Downloading {MODEL} for local transcript review. This is a one-time model download; no recordings are sent.", flush=True)
    subprocess.run(["ollama", "pull", MODEL], env={**os.environ, "OLLAMA_HOST": URL}, check=True)
    print("Model installed. Restart Cutlist, open your saved recording, and choose Re-analyze saved recording. Its transcript is reused.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Content model setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
