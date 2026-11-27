"""Non-destructive source intervals; every output format uses the same mapping."""
import math


def segments(clip):
    return clip.get("segments") or [{"start": clip["start"], "end": clip["end"]}]


def duration(clip):
    return round(sum(s["end"] - s["start"] for s in segments(clip)), 3)


def validate(values, source_duration, maximum):
    if not isinstance(values, list) or not 1 <= len(values) <= 16:
        raise ValueError("Keep 1–16 source intervals in a cut.")
    output, previous = [], 0
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Invalid source interval.")
        a, b = value.get("start"), value.get("end")
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (a, b)) or not 0 <= a < b <= source_duration or a < previous - .0001:
            raise ValueError("Source intervals must be finite, ordered, non-overlapping and inside the recording.")
        a, b = round(a, 3), round(b, 3)
        if b - a < .02:
            raise ValueError("Keep at least 0.02 seconds in each interval.")
        if output and abs(a - output[-1]["end"]) < .001:
            output[-1]["end"] = b
        else:
            output.append({"start": a, "end": b})
        previous = b
    if sum(s["end"] - s["start"] for s in output) > maximum + .001:
        raise ValueError("The retained video exceeds your maximum clip length.")
    return output


def overlaps(a, b):
    return any(x["start"] < y["end"] - .001 and x["end"] > y["start"] + .001 for x in segments(a) for y in segments(b))


def captions_for_output(clip):
    """Retimed cues remain relative to clip.start for existing SRT/ASS writers."""
    output, cursor = [], clip["start"]
    for segment in segments(clip):
        for cue in clip.get("captions", []):
            a, b = max(cue["start"], segment["start"]), min(cue["end"], segment["end"])
            if b > a:
                output.append({**cue, "start": round(cursor + a - segment["start"], 3), "end": round(cursor + b - segment["start"], 3)})
        cursor += segment["end"] - segment["start"]
    return output


def timeline_entries(result):
    """Each retained interval becomes an editor timeline item with source in/out."""
    entries = []
    for clip in result["clips"]:
        parts = segments(clip)
        for index, segment in enumerate(parts):
            entries.append({**clip, **segment, "id": clip["id"] if len(parts) == 1 else f"{clip['id']}-part-{index + 1}"})
    return {**result, "clips": entries}
