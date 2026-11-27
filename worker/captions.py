"""SRT/WebVTT parsing without a service or an LLM."""
import html
import math
import re
from dataclasses import dataclass


@dataclass
class Cue:
    start: float
    end: float
    text: str
    speaker: str | None = None


def timestamp(value: str) -> float:
    value = value.strip().replace(",", ".")
    if not re.fullmatch(r"\d{1,3}:\d{2}(?::\d{2})?(?:\.\d{1,3})?", value):
        raise ValueError(f"Invalid timestamp: {value}. Use MM:SS or HH:MM:SS.mmm.")
    parts = [float(x) for x in value.split(":")]
    if any(x >= 60 for x in parts[1:]):
        raise ValueError(f"Invalid timestamp: {value}; seconds and minutes must be below 60.")
    result = sum(x * 60 ** i for i, x in enumerate(reversed(parts)))
    if not math.isfinite(result):
        raise ValueError("Timestamps must be finite.")
    return result


def parse_captions(text: str) -> list[Cue]:
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").strip()
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = block.splitlines()
        if not lines or lines[0].startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        timing = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing is None:
            raise ValueError("Caption block has no timing line. Upload a valid SRT or WebVTT file.")
        start_text, end_text = lines[timing].split("-->", 1)
        start, end = timestamp(start_text), timestamp(end_text.strip().split()[0])
        if end <= start:
            raise ValueError("Each caption end time must be later than its start time.")
        content = " ".join(lines[timing + 1:])
        voice = re.search(r"<v\s+([^>]+)>", content)
        speaker = html.unescape(voice.group(1)).strip()[:60] if voice else None
        content = re.sub(r"<v\s+([^>]+)>", "", content)
        content = html.unescape(re.sub(r"<[^>]*>", "", content)).strip()
        if content:
            cues.append(Cue(start, end, content, speaker))
    if not cues:
        raise ValueError("No timed captions found. Use an SRT or WebVTT file with spoken text.")
    if len(cues) > 50000:
        raise ValueError("Please use fewer than 50,000 caption cues per recording.")
    return sorted(cues, key=lambda c: (c.start, c.end))


def parse_replays(text: str) -> list[float]:
    if not text.strip():
        return []
    tokens = [s.strip() for s in re.split(r"[,;\n]", text) if s.strip()]
    if len(tokens) > 200:
        raise ValueError("Use up to 200 replay markers.")
    values = []
    for token in tokens:
        try:
            value = timestamp(token) if ":" in token else float(token)
        except ValueError:
            raise ValueError(f"Invalid replay marker '{token}'. Use seconds or MM:SS, separated by commas.") from None
        if not math.isfinite(value) or value < 0:
            raise ValueError("Replay markers must be finite, non-negative timestamps.")
        values.append(value)
    return sorted(set(values))


def timecode(seconds: float) -> str:
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
