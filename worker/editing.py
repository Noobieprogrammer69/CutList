"""Validate editor decisions; export source-relative captions and timelines."""
import copy
import math
import re
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote
import xml.etree.ElementTree as ET
from worker import composition


def clip_captions(cues, start, end):
    return [{**c, "start": round(max(start, c["start"]), 3), "end": round(min(end, c["end"]), 3)}
            for c in cues if c["start"] < end and c["end"] > start]


DEFAULT_STYLE = {"size": 24, "color": "#FFFFFF", "outline": 2, "position": "bottom", "show_speakers": True, "speaker_colors": True}


def caption_style(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid caption style.")
    style = {**DEFAULT_STYLE, **{k: v for k, v in value.items() if k in DEFAULT_STYLE}}
    if type(style["size"]) is not int or not 16 <= style["size"] <= 48 or type(style["outline"]) is not int or not 0 <= style["outline"] <= 5:
        raise ValueError("Caption size must be 16–48; outline 0–5.")
    if not isinstance(style["color"], str) or not re.fullmatch(r"#[0-9A-Fa-f]{6}", style["color"]) or style["position"] not in {"top", "bottom"}:
        raise ValueError("Choose a caption color and top/bottom position.")
    if any(type(style[k]) is not bool for k in ("show_speakers", "speaker_colors")):
        raise ValueError("Speaker label settings must be true or false.")
    return style


def edit_clip(result, clip_id, data):
    clip = next((c for c in result["clips"] if c["id"] == clip_id), None)
    if clip is None:
        raise ValueError("Clip not found.")
    if data.get("revision") != clip.get("revision", 0):
        raise RuntimeError("This clip changed in another tab. Reopen the project before editing.")
    edited = copy.deepcopy(clip)
    start, end = data.get("start", clip["start"]), data.get("end", clip["end"])
    if any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in (start, end)):
        raise ValueError("Clip times must be finite numbers in seconds.")
    if not 0 <= start < end <= result["duration"]:
        raise ValueError("Keep the interval inside the recording and within your maximum clip length.")
    changed = (start, end) != (clip["start"], clip["end"])
    parts = composition.validate(data.get("segments", [{"start": start, "end": end}] if changed else composition.segments(clip)), result["duration"], result["maximum"])
    start, end = parts[0]["start"], parts[-1]["end"]
    if any(composition.overlaps({"segments": parts}, c) for c in result["clips"] if c["id"] != clip_id):
        raise ValueError("This interval overlaps another shortlisted clip. Shorten one of the clips first.")
    changed = changed or parts != composition.segments(clip)
    edited.update(start=round(start, 3), end=round(end, 3), revision=clip.get("revision", 0) + 1)
    edited.setdefault("original_start", clip["start"])
    edited.setdefault("original_end", clip["end"])
    edited.setdefault("original_segments", composition.segments(clip))
    edited.update(segments=parts, output_duration=composition.duration({"segments": parts}))
    captions = data.get("captions", clip_captions(result.get("cues", []), start, end) if changed else clip.get("captions", []))
    if not isinstance(captions, list) or len(captions) > 500:
        raise ValueError("Use at most 500 caption cues per clip.")
    previous = start
    for cue in captions:
        if not isinstance(cue, dict):
            raise ValueError("Invalid caption cue.")
        a, b, text = cue.get("start"), cue.get("end"), cue.get("text")
        if any(not isinstance(x, (float, int)) or isinstance(x, bool) or not math.isfinite(x) for x in (a, b)):
            raise ValueError("Caption times must be numbers.")
        if not start <= a < b <= end + .001 or a < previous - .001 or not isinstance(text, str) or len(text) > 2000:
            raise ValueError("Caption cues must be ordered, non-overlapping, and inside the clip.")
        previous = b
        if cue.get("speaker") is not None and (not isinstance(cue["speaker"], str) or len(cue["speaker"]) > 60 or "\n" in cue["speaker"]):
            raise ValueError("Speaker labels must be at most 60 characters on one line.")
    crop = data.get("crop", clip.get("crop", {"format": "source", "x": .5, "y": .5}))
    if not isinstance(crop, dict) or crop.get("format") not in {"source", "vertical", "square"}:
        raise ValueError("Choose original, vertical, or square framing.")
    if any(not isinstance(crop.get(k), (float, int)) or not math.isfinite(crop[k]) or not 0 <= crop[k] <= 1 for k in ("x", "y")):
        raise ValueError("Crop position must be between 0 and 1.")
    if crop.get("resolution", "720") not in {"source", "720", "1080"}:
        raise ValueError("Choose source pixels, 720, or 1080 crop output.")
    if crop.get("tracking", "manual") not in {"manual", "face"}:
        raise ValueError("Choose manual framing or face following.")
    # Never trust client-provided crop expressions or paths.
    crop = {k: v for k, v in crop.items() if k in {"format", "x", "y", "resolution", "tracking"}}
    quality = data.get("quality", clip.get("quality", "high"))
    if quality not in {"high", "maximum"}:
        raise ValueError("Choose high or maximum export quality.")
    edited["quality"] = quality
    edited["caption_style"] = caption_style(data.get("caption_style", clip.get("caption_style", DEFAULT_STYLE)))
    edited.update(captions=captions, crop=crop, burn_captions=bool(data.get("burn_captions", clip.get("burn_captions", False))))
    if "title" in data:
        if not isinstance(data["title"], str) or not data["title"].strip() or len(data["title"]) > 200:
            raise ValueError("Use a title between 1 and 200 characters.")
        edited["title"] = data["title"].strip()
    edited["excerpt"] = " ".join(c["text"] for c in captions)
    edited["boundary_note"] = "Editor-adjusted boundaries" if changed else edited["boundary_note"]
    if changed and "evidence_events" in edited:
        edited["evidence"] = [e["detail"] for e in edited["evidence_events"] if start <= e["time"] <= end]
    return edited


def subtitle_text(clip, kind="srt"):
    def stamp(t):
        ms = round(max(0, t - clip["start"]) * 1000)
        return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}{',' if kind == 'srt' else '.'}{ms % 1000:03d}"
    lines = ["WEBVTT", ""] if kind == "vtt" else []
    for i, cue in enumerate(composition.captions_for_output(clip), 1):
        text = cue["text"].replace("-->", "→").replace("\x00", "")
        if cue.get("speaker") and clip.get("caption_style", {}).get("show_speakers", True):
            text = cue["speaker"] + ": " + text
        lines.extend([str(i), f"{stamp(cue['start'])} --> {stamp(cue['end'])}", text, ""])
    return "\n".join(lines) + "\n"


def ass_subtitles(clip):
    """Style and color speaker labels without accepting arbitrary ASS markup."""
    style = caption_style(clip.get("caption_style", DEFAULT_STYLE))
    def color(hexcode):
        return "&H00" + hexcode[5:7] + hexcode[3:5] + hexcode[1:3]
    def stamp(t):
        cs = round(max(0, t - clip["start"]) * 100)
        return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"
    def escape(text):
        return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", "\\N")
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 384", "PlayResY: 288", "[V4+ Styles]",
             "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV",
             f"Style: Default,Noto Sans,{style['size']},{color(style['color'])},&H00111111,1,{style['outline']},0,{8 if style['position'] == 'top' else 2},16,16,24",
             "[Events]", "Format: Layer,Start,End,Style,Text"]
    palette = ["#FFD166", "#73DACA", "#B4A7FF", "#FF9CA5", "#A6E3A1"]
    names = list(dict.fromkeys(c["speaker"] for c in clip.get("captions", []) if c.get("speaker")))
    for cue in composition.captions_for_output(clip):
        text = escape(cue["text"])
        name = cue.get("speaker")
        if name and style["show_speakers"]:
            prefix = escape(name) + ": "
            if style["speaker_colors"]:
                prefix = "{\\c&H" + color(palette[names.index(name) % len(palette)])[4:] + "&}" + prefix + "{\\r}"
            text = prefix + text
        lines.append(f"Dialogue: 0,{stamp(cue['start'])},{stamp(cue['end'])},Default,{text}")
    return "\n".join(lines) + "\n"


def timeline_xml(result):
    """FCP 7 interchange (xmeml). Relink original media in Premiere/Resolve."""
    result = composition.timeline_entries(result)
    media = result.get("media_info", {})
    try:
        raw_rate = Fraction(media.get("fps", "25/1"))
    except (ValueError, ZeroDivisionError):
        raw_rate = Fraction(25)
    if raw_rate <= 0 or raw_rate > 240:
        raw_rate = Fraction(25)
    fps, ntsc, timebase = float(raw_rate), raw_rate.denominator == 1001, round(float(raw_rate))
    root = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(root, "sequence", id="cutlist-sequence")
    def tag(parent, name, text):
        ET.SubElement(parent, name).text = str(text)
    def rate(parent):
        r = ET.SubElement(parent, "rate")
        tag(r, "timebase", timebase)
        tag(r, "ntsc", "TRUE" if ntsc else "FALSE")
    tag(sequence, "name", "Cutlist — " + result["source_name"])
    rate(sequence)
    entries, cursor = [], 0
    for clip in result["clips"]:
        if clip.get("feedback") == "reject":
            continue
        a, b = round(clip["start"] * fps), round(clip["end"] * fps)
        if b > a:
            entries.append((clip, a, b, cursor))
            cursor += b - a
    tag(sequence, "duration", cursor)
    seqmedia = ET.SubElement(sequence, "media")
    tracks = (["video"] if result.get("media_kind") == "video" else []) + (["audio"] if media.get("has_audio", True) else [])
    containers = {}
    for kind in tracks:
        container = containers[kind] = ET.SubElement(seqmedia, kind)
        characteristics = ET.SubElement(ET.SubElement(container, "format"), "samplecharacteristics")
        if kind == "video":
            rate(characteristics)
            tag(characteristics, "width", media.get("width", 1280))
            tag(characteristics, "height", media.get("height", 720))
            tag(characteristics, "anamorphic", "FALSE")
            tag(characteristics, "pixelaspectratio", "square")
            tag(characteristics, "fielddominance", "none")
        else:
            tag(characteristics, "depth", 16)
            tag(characteristics, "samplerate", media.get("sample_rate", 48000))
    for kind in tracks:
        track = ET.SubElement(containers[kind], "track")
        for index, (clip, a, b, position) in enumerate(entries, 1):
            item = ET.SubElement(track, "clipitem", id=f"{kind}-{clip['id']}")
            tag(item, "name", clip["title"])
            tag(item, "enabled", "TRUE")
            tag(item, "duration", round(result["duration"] * fps))
            rate(item)
            for key, value in (("start", position), ("end", position + b - a), ("in", a), ("out", b)):
                tag(item, key, value)
            file = ET.SubElement(item, "file", id="source-1")
            tag(file, "name", Path(result["source_name"]).name)
            tag(file, "pathurl", "file://localhost/" + quote(Path(result["source_name"]).name))
            rate(file)
            tag(file, "duration", round(result["duration"] * fps))
            fm = ET.SubElement(file, "media")
            video = ET.SubElement(ET.SubElement(fm, "video"), "samplecharacteristics")
            rate(video)
            tag(video, "width", media.get("width", 1280))
            tag(video, "height", media.get("height", 720))
            tag(video, "anamorphic", "FALSE")
            tag(video, "pixelaspectratio", "square")
            audio = ET.SubElement(fm, "audio")
            ac = ET.SubElement(audio, "samplecharacteristics")
            tag(ac, "depth", 16)
            tag(ac, "samplerate", media.get("sample_rate", 48000))
            tag(audio, "channelcount", media.get("channels", 2))
            sourcetrack = ET.SubElement(item, "sourcetrack")
            tag(sourcetrack, "mediatype", kind)
            tag(sourcetrack, "trackindex", 1)
            if len(tracks) == 2:
                for linked in tracks:
                    link = ET.SubElement(item, "link")
                    tag(link, "linkclipref", f"{linked}-{clip['id']}")
                    tag(link, "mediatype", linked)
                    tag(link, "trackindex", 1)
                    tag(link, "clipindex", index)
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n' + ET.tostring(root, encoding="unicode")
