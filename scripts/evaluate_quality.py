"""Repeatable temporal precision/recall against independently editable labels."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.analysis import rank_clips, transcript_events
from worker.captions import Cue, parse_captions


def iou(a, b):
    intersection = max(0, min(a[1], b[1]) - max(a[0], b[0]))
    return intersection / max(1e-9, max(a[1], b[1]) - min(a[0], b[0]))


def evaluate(path):
    dataset = json.loads(Path(path).read_text())
    if "predictions" in dataset and "labels" in dataset:
        from worker.quality import evaluate as real_evaluate
        return {"source": dataset.get("source"), "sample": dataset.get("sample"), "evaluation": real_evaluate(dataset["predictions"], dataset["labels"]),
                "baseline_evaluation": real_evaluate(dataset.get("baseline_predictions", []), dataset["labels"]),
                "note": "Independent editor labels; sample/synthetic data does not prove real-stream quality."}
    rows, tp, predicted, expected = [], 0, 0, 0
    for case in dataset["cases"]:
        cues = parse_captions((Path(path).parent / case["captions_file"]).read_text()) if "captions_file" in case else [Cue(*c) for c in case["cues"]]
        clips = rank_clips(transcript_events(cues, case.get("keywords", [])), cues, case["duration"], case.get("maximum", 45))
        targets, matched = case["expected"], set()
        hits = 0
        for clip in clips:
            best = max(((iou((clip["start"], clip["end"]), target), i) for i, target in enumerate(targets) if i not in matched), default=(0, -1))
            if best[0] >= .5:
                hits += 1
                matched.add(best[1])
        tp += hits
        predicted += len(clips)
        expected += len(targets)
        rows.append({"case": case["name"], "hits": hits, "expected": len(targets), "suggested": len(clips), "passed": hits == len(targets) == len(clips), "intervals": [[c["start"], c["end"]] for c in clips]})
    return {"description": dataset.get("description", ""), "iou_threshold": .5, "precision": tp / predicted if predicted else 1,
            "recall": tp / expected if expected else 1, "cases": rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", nargs="?", default=ROOT / "benchmarks/clip_quality.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.dataset)
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    sys.exit(0 if all(row["passed"] for row in report.get("cases", [])) else 1)
