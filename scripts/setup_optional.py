"""Explicit downloads of optional local models/dependencies; no media is sent."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("feature", choices=["vision", "search", "speakers", "tracking"])
    args = parser.parse_args()
    if args.feature in {"vision", "search"}:
        if args.feature == "vision":
            from worker.visual import MODEL
        else:
            from worker.retrieval import MODEL
        from worker.semantic import URL, local_url
        local_url(URL)
        if "cloud" in MODEL.casefold() or not MODEL.startswith(("qwen3-vl:", "embeddinggemma:")):
            raise ValueError("Choose a supported downloaded local model, not a cloud tag.")
        if not shutil.which("ollama"):
            raise ValueError("Install/start Ollama first, then run this command again.")
        subprocess.run(["ollama", "pull", MODEL], env={**os.environ, "OLLAMA_HOST": URL}, check=True)
    elif args.feature == "tracking":
        if not shutil.which("uv"):
            raise ValueError("Install uv first, or install worker/requirements-tracking.txt in .venv using pip.")
        subprocess.run(["uv", "pip", "install", "--python", sys.executable, "-r", str(ROOT / "worker/requirements-tracking.txt")], check=True)
    else:
        import httpx
        from worker.speakers import MODEL
        url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
        MODEL.parent.mkdir(parents=True, exist_ok=True)
        temporary = MODEL.with_suffix(".download")
        try:
            with httpx.stream("GET", url, follow_redirects=True, timeout=120) as response:
                response.raise_for_status()
                count = 0
                with temporary.open("wb") as target:
                    for data in response.iter_bytes(1024 * 1024):
                        count += len(data)
                        if count > 500 * 1024 ** 2:
                            raise ValueError("Voice model exceeded the download size budget.")
                        target.write(data)
            if count < 100_000:
                raise ValueError("Incomplete speaker model download.")
            import sherpa_onnx
            # Validate the ONNX model before publishing it to the worker.
            sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(temporary), num_threads=1, provider="cpu"))
            temporary.replace(MODEL)
        finally:
            temporary.unlink(missing_ok=True)
    print(f"Optional {args.feature} feature installed. Restart Cutlist to refresh availability.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
