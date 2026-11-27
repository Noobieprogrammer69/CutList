"""Small local preference ranker, evaluated on held-out recordings before use."""
import hashlib
import re

import numpy as np
from worker.composition import duration

DIM = 144
STOP = set("the a an and or to of in is it this that for with on i you we they was are be as at my your".split())
CATEGORIES = ["joke", "funny_story", "story", "insight", "opinion", "reaction", "visual_event", "manual"]


def features(clip):
    x = np.zeros(DIM, dtype=np.float64)
    text = clip.get("excerpt", "")
    tokens = [t for t in re.findall(r"[a-z]{3,}", text.casefold()) if t not in STOP]
    for token in set(tokens):
        x[int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big") % 128] += 1
    norm = np.linalg.norm(x[:128])
    if norm: x[:128] /= norm
    length = duration(clip)
    x[128:132] = [length / 180, min(1, len(tokens) / max(1, length) / 5), float(clip.get("transcript_used", False)), float(clip.get("selection_method") == "local_model")]
    for i, kind in enumerate(CATEGORIES): x[132+i] = float(clip.get("category") == kind)
    return x


def pair_accuracy(scores, labels):
    good, bad = scores[labels == 1], scores[labels == 0]
    if not len(good) or not len(bad): return None
    differences = good[:, None] - bad[None, :]
    return float(np.mean((differences > 0) + .5 * (differences == 0)))


def fit(x, y):
    # Bounded pairwise logistic learning; never fine-tunes the language model.
    pairs = (x[y == 1][:50, None, :] - x[y == 0][None, :50, :]).reshape(-1, DIM)
    weights = np.zeros(DIM)
    if not len(pairs): return weights
    for _ in range(180):
        margins = np.clip(pairs @ weights, -20, 20)
        gradient = -(pairs / (1 + np.exp(margins))[:, None]).mean(axis=0) + .08 * weights
        weights -= .3 * gradient
    return weights


def learn(projects):
    # Re-analyses of the same recording and repeated ratings cannot leak across
    # train/test. Source name + duration group copies; text/time deduplicates cuts.
    unique = {}
    for state in sorted(projects, key=lambda p: p.get("created", 0)):
        result = state.get("result") or {}
        if result.get("sample"): continue
        group = state.get("recording_key") or hashlib.sha256((result.get("source_name", "") + str(round(result.get("duration", 0), 1))).encode()).hexdigest()
        for c in result.get("clips", []):
            if c.get("feedback") not in {"keep", "reject"} or c.get("selection_method") == "manual": continue
            key = (group, round(c.get("original_start", c["start"])), round(c.get("original_end", c["end"])))
            unique[key] = (group, c)
    rows = list(unique.values())
    groups = sorted({g for g, _ in rows})
    kept = sum(c["feedback"] == "keep" for _, c in rows)
    base = {"reviews": len(rows), "recordings": len(groups), "kept": kept, "rejected": len(rows)-kept, "eligible": False, "applied": False,
            "message": "Needs at least 20 reviewed clips across 3 real recordings, with at least 5 kept and 5 skipped. Synthetic sample feedback is excluded."}
    if len(rows) < 20 or len(groups) < 3 or min(kept, len(rows)-kept) < 5:
        return base, None
    x = np.stack([features(c) for _, c in rows])
    y = np.array([int(c["feedback"] == "keep") for _, c in rows])
    raw = np.array([c.get("base_score", c.get("score", 0)) for _, c in rows])
    folds = []
    for group in groups[:8]:
        test = np.array([g == group for g, _ in rows])
        if len(set(y[test])) < 2 or len(set(y[~test])) < 2: continue
        weights = fit(x[~test], y[~test])
        predicted = np.tanh(x[test] @ weights)
        # Evaluate the same bounded blend that is used for future candidates.
        folds.append((pair_accuracy(raw[test], y[test]), pair_accuracy(raw[test] + .8 * predicted, y[test])))
    if not folds:
        return {**base, "message": "Need recordings containing both kept and skipped examples for held-out evaluation."}, None
    baseline, personalized = np.mean(folds, axis=0)
    eligible = bool(personalized > baseline + .02)
    report = {**base, "eligible": eligible, "held_out_recordings": len(folds), "baseline_pair_accuracy": round(float(baseline), 3),
              "personalized_pair_accuracy": round(float(personalized), 3),
              "message": "Held-out ranking improved; personalization can be enabled." if eligible else "No measured ranking improvement on held-out recordings. Original ranking retained."}
    return report, fit(x, y) if eligible else None


def rerank(clips, weights):
    values = []
    for clip in clips:
        base = clip.get("base_score", clip["score"])
        adjustment = round(float(.8 * np.tanh(features(clip) @ weights)), 3)
        values.append({**clip, "base_score": base, "preference_adjustment": adjustment, "score": round(base + adjustment, 3)})
    values.sort(key=lambda c: (-c["score"], c["start"]))
    return [{**c, "rank": i + 1} for i, c in enumerate(values)]
