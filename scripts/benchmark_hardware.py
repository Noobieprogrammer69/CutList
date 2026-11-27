"""Compare actual local model CPU/offload timings using identical captions."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.captions import parse_captions
from worker.hardware import report
from worker.semantic import Reviewer, select, windows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--captions", type=Path, default=ROOT / "public/sample/studio-notes.vtt")
    parser.add_argument("--media", type=Path, help="Optionally measure audio decode and a fresh speech pass on this file")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = {"hardware": report(), "caption_source": args.captions.name, "runs": [],
              "note": "Measured local timings; no editorial quality claim without independent real-footage labels. Offload requested does not guarantee GPU use."}
    cues = parse_captions(args.captions.read_text())
    lo, hi = next(windows(cues, 45))
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        if args.media:
            started = time.perf_counter()
            pcm = folder / "audio.pcm"
            subprocess.run(["ffmpeg", "-v", "error", "-i", str(args.media), "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", str(pcm)], check=True)
            result["audio_decode_seconds"] = round(time.perf_counter() - started, 3)
            speech_path = folder / "speech.json"
            subprocess.run([sys.executable, "-m", "worker.transcription", str(pcm), str(speech_path)], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
            result["speech"] = {k: v for k, v in json.loads(speech_path.read_text()).items() if k not in {"cues", "words"}}
        for mode in ("cpu", "gpu") if result["hardware"]["gpu_available"] else ("cpu",):
            os.environ["CUTLIST_ACCELERATION"] = mode
            reviewer = Reviewer()
            ready = reviewer.ready()
            if not ready["available"]:
                reviewer.client.close()
                result["runs"].append({"mode": mode, "available": False, "reason": ready["message"]})
                continue
            started = time.perf_counter()
            selection = select(cues[lo:hi], 45, "mixed", folder / mode, reviewer=reviewer)
            result["runs"].append({"mode": mode, "available": True, "wall_seconds": round(time.perf_counter() - started, 3),
                                   "intervals": [[c["start"], c["end"]] for c in selection["clips"]], "model_metrics": selection["inference_metrics"]})
    encoded = json.dumps(result, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
