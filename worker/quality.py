"""Independent editor labels and one-to-one temporal evaluation on real projects."""
import math
from worker.composition import segments, duration


def interval_label(data, duration):
    a, b = data.get("start"), data.get("end")
    if any(type(t) not in (int, float) or not math.isfinite(t) for t in (a, b)) or not 0 <= a < b <= duration:
        raise ValueError("Label a positive interval inside the recording, in source seconds.")
    if data.get("kind") not in {"good", "boring"}:
        raise ValueError("Choose a good moment or a boring/contextless section.")
    reason = data.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 500:
        raise ValueError("Use a label note of at most 500 characters.")
    return {"start": a, "end": b, "kind": data["kind"], "reason": reason}


def iou(a, b):
    intersection = sum(max(0, min(x["end"], y["end"]) - max(x["start"], y["start"])) for x in segments(a) for y in segments(b))
    return intersection / max(1e-9, duration(a) + duration(b) - intersection)


def evaluate(clips, labels, threshold=.5):
    good = [x for x in labels if x["kind"] == "good"]
    boring = [x for x in labels if x["kind"] == "boring"]
    # Highest-overlap pairs first; each prediction and reference matches once.
    pairs = sorted(((iou(c, g), i, j) for i, c in enumerate(clips) for j, g in enumerate(good)), reverse=True)
    used_c, used_g, matches = set(), set(), []
    for overlap, i, j in pairs:
        if overlap >= threshold and i not in used_c and j not in used_g:
            used_c.add(i)
            used_g.add(j)
            matches.append({"clip": clips[i]["id"], "label": good[j].get("id", j), "iou": round(overlap, 3),
                            "start_error_seconds": round(clips[i]["start"] - good[j]["start"], 3),
                            "end_error_seconds": round(clips[i]["end"] - good[j]["end"], 3)})
    reviewed = [c for c in clips if c.get("feedback") in {"keep", "reject"}]
    kept = [c for c in reviewed if c.get("feedback") == "keep"]
    untrimmed = [c for c in kept if abs(c["start"] - c.get("original_start", c["start"])) < .001 and abs(c["end"] - c.get("original_end", c["end"])) < .001 and segments(c) == c.get("original_segments", segments(c))]
    return {"labelled_good": len(good), "labelled_boring": len(boring), "suggested": len(clips), "matched": len(matches),
            "precision": len(matches) / len(clips) if good and clips else None,
            "recall": len(matches) / len(good) if good else None,
            "boring_hits": sum(any(sum(max(0, min(s["end"], b["end"]) - max(s["start"], b["start"])) for s in segments(c)) / duration(c) >= .5 for b in boring) for c in clips),
            "reviewed": len(reviewed), "keep_rate": len(kept) / len(reviewed) if reviewed else None,
            "kept_without_trim_rate": len(untrimmed) / len(reviewed) if reviewed else None,
            "iou_threshold": threshold, "matches": matches,
            "note": "Precision/recall assume all worthwhile moments have been labelled. Partial labels underestimate precision. No labels means no accuracy claim."}


def benchmark(state):
    result = state["result"]
    labels = state.get("quality_labels", [])
    baseline = result.get("baseline_clips", [])
    return {"version": 1, "source": result["source_name"], "duration": result["duration"],
            "sample": result.get("sample", False), "labels": labels,
            "predictions": result["clips"], "baseline_predictions": baseline,
            "selection_method": result.get("selection_method"), "profile": result.get("profile", "general"),
            "evaluation": evaluate(result["clips"], labels), "baseline_evaluation": evaluate(baseline, labels),
            "timings": result.get("timings", {}), "capabilities": result.get("capabilities", {})}
