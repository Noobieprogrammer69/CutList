import math
from bisect import bisect_left, bisect_right, insort
import re
from dataclasses import dataclass

import numpy as np

from worker.captions import Cue, timecode
from worker.events import LAUGHTER, REACTIONS


@dataclass
class Event:
    time: float
    kind: str
    weight: float
    detail: str
    setup: float | None = None
    payoff: float | None = None
    title: str = ""


def transcript_events(cues: list[Cue], keywords: list[str]) -> list[Event]:
    events = []
    claim = re.compile(r"\b(never|nobody|everyone|actually|secret|wrong|opposite|surpris\w*|only|twice|percent)\b", re.I)
    explain = re.compile(r"\b(because|here.s why|for example|instead|means|so that|reason|turns out)\b", re.I)
    story = re.compile(r"\b(when I|I once|last year|one day|first time|years ago|we once)\b", re.I)
    payoff = re.compile(r"\b(realiz\w*|turns out|finally|learned|ever since|that.s how|ended up|in the end)\b", re.I)
    for i, cue in enumerate(cues):
        following = []
        for nxt in cues[i + 1:i + 6]:
            if nxt.start - cue.end > 20 or ("?" in nxt.text and following):
                break
            following.append(nxt)
        text = " ".join(x.text for x in [cue, *following])
        title = re.sub(r"^[\w ]{1,24}:\s*", "", cue.text)
        title = title[:86].rsplit(" ", 1)[0] + "…" if len(title) > 86 else title
        smalltalk = re.search(r"\b(how are you|how.s it going|ready to (start|begin)|can you hear me|welcome (back|to)|like and subscribe)\b", cue.text, re.I)
        substantive_answer = explain.search(text) or payoff.search(text)
        if "?" in cue.text and following and len(following[0].text.split()) >= 7 and len(text.split()) >= 14 and substantive_answer and not smalltalk:
            answer = following[:2]
            events.append(Event(answer[0].start, "transcript", 3.5, f"Question followed by an answer at {timecode(cue.start)}", cue.start, answer[-1].end, title))
        if claim.search(cue.text) and explain.search(text) and len(text.split()) >= 18:
            end = next((x.end for x in [cue, *following] if explain.search(x.text)), cue.end)
            events.append(Event(cue.start, "transcript", 3.2, f"Claim with an explanation at {timecode(cue.start)}", cue.start, end, title))
        if story.search(cue.text):
            ending = next((x for x in following if payoff.search(x.text)), None)
            if ending:
                events.append(Event(ending.start, "transcript", 3.8, f"Story setup and payoff at {timecode(ending.start)}", cue.start, ending.end, title))
        if re.search(r"[\[(](?:laughter|laughs?|laughing|chuckles?|applause|cheering)[\])]", cue.text, re.I):
            setup = cues[max(0, i - 2)].start
            events.append(Event(cue.start, "caption_reaction", 2.6, f"Reaction noted in captions at {timecode(cue.start)} (not audio-confirmed)", setup, cue.end, "A setup with a reaction"))
        for word in keywords:
            if re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", cue.text, re.I):
                events.append(Event(cue.start, "keyword", 1.8, f'Keyword “{word}” at {timecode(cue.start)}', cues[max(0, i - 1)].start, cue.end, title))
    return events


def audio_energy(samples, rate=16000, check=lambda: None):
    """Vectorised 100 ms features in bounded ten-second blocks."""
    step = rate // 10
    result = [[], [], []]
    for start in range(0, len(samples), rate * 10):
        check()
        block = np.asarray(samples[start:start + rate * 10], dtype=np.float32) / 32768
        count = len(block) // step
        full = block[:count * step].reshape(count, step)
        if count:
            rms = np.sqrt(np.mean(full * full, axis=1))
            result[0].extend(20 * np.log10(np.maximum(rms, 1e-6)))
            result[1].extend(np.max(np.abs(full), axis=1) / np.maximum(rms, 1e-6))
            sub = full.reshape(count, 10, rate // 100)
            result[2].extend(np.std(np.sqrt(np.mean(sub * sub, axis=2)), axis=1) / np.maximum(rms, 1e-6))
        tail = block[count * step:]
        if len(tail):
            rms = float(np.sqrt(np.mean(tail * tail)))
            result[0].append(20 * math.log10(max(rms, 1e-6)))
            result[1].append(float(np.max(np.abs(tail))) / max(rms, 1e-6))
            subs = tail[:len(tail) // (rate // 100) * (rate // 100)].reshape(-1, rate // 100)
            result[2].append(float(np.std(np.sqrt(np.mean(subs * subs, axis=1)))) / max(rms, 1e-6) if len(subs) else 0)
    return tuple(np.asarray(values) for values in result)


def energy_events(db, crest, variation):
    """Require a sustained animated passage relative to active nearby conversation.

    Silence is excluded from the baseline. Impulses, sustained plateaus and
    steady tones cannot qualify solely through their absolute amplitude.
    """
    events = []
    if len(db) < 20 or np.max(db) < -45:
        return events
    for i in range(10, len(db) - 8, 5):
        surrounding = np.concatenate((db[max(0, i - 150):max(0, i - 15)], db[i + 35:min(len(db), i + 150)]))
        active = surrounding[surrounding > -48]
        if len(active) < 15:
            continue
        baseline = float(np.median(active))
        passage = db[i:i + 15]
        rise = float(np.percentile(passage, 60) - baseline)
        elevated = (passage > baseline + 4) & (passage > -38)
        if rise < 4 or np.sum(elevated) < 6:
            continue
        if np.median(variation[i:i + 15]) < .12 or np.median(crest[i:i + 15]) > 12:
            continue
        # A long, consistently loud bed is not a sudden conversational change.
        plateau = db[i:min(len(db), i + 200)]
        if len(plateau) > 80 and np.mean(plateau > baseline + 4) > .8:
            continue
        moment = (i + int(np.argmax(passage))) / 10
        if any(abs(moment - old.time) < 8 for old in events):
            continue
        events.append(Event(moment, "energy", min(2.4, 1.4 + rise / 12), f"Volume rises at {timecode(moment)} (+{rise:.1f} dB vs nearby audio)", title="An animated moment"))
    return events


def sound_events(samples, classifier, progress=lambda *_: None):
    events, music = [], []
    if classifier.tagger is None:
        return events, music
    # Overlapping 3-second windows. Keep temporal resolution honest in evidence.
    duration = len(samples) / 16000
    for start in range(0, len(samples), 24000):
        frame = np.asarray(samples[start:start + 48000], dtype=np.float32) / 32768
        if len(frame) < 8000 or np.sqrt(np.mean(frame ** 2)) < .003:
            continue
        # A classifier must also have more than an isolated impulse to work
        # with. This rejects clicks/bumps before any sound label is considered.
        chunks = frame[:len(frame) // 320 * 320].reshape(-1, 320)
        active_seconds = float(np.sum(np.sqrt(np.mean(chunks ** 2, axis=1)) > .003)) * .02
        if active_seconds < .26:
            continue
        prediction = classifier.predict(frame)
        at = min(duration, start / 16000 + len(frame) / 32000)
        music.append((start / 16000, min(duration, (start + 48000) / 16000), prediction.get("Music", 0), prediction.get("Speech", 0)))
        laugh = max((prediction.get(x, 0) for x in LAUGHTER), default=0)
        label, reaction = max(((x, prediction.get(x, 0)) for x in REACTIONS), key=lambda x: x[1])
        if laugh >= .45:
            events.append(Event(at, "laughter", 3.2 + laugh, f"Laughter detected near {timecode(at)} (CED score {laugh:.2f}, 3 s window)", title="A moment that got a laugh"))
        elif reaction >= .55:
            events.append(Event(at, "reaction", 2.2 + reaction, f"{label} detected near {timecode(at)} (CED score {reaction:.2f}, 3 s window)", title="A reaction worth a closer look"))
        if start % 240000 == 0:
            progress(45 + int(35 * start / max(len(samples), 1)), "Listening for laughter and reactions on CPU")
    return events, music


def boundaries(event, cues, duration, maximum, db, index=None):
    start = event.setup if event.setup is not None else max(0, event.time - min(10, maximum * .55))
    end = event.payoff if event.payoff is not None else min(duration, event.time + 7)
    starts, ends = index if index is not None else ([c.start for c in cues], np.maximum.accumulate([c.end for c in cues]).tolist() if cues else [])
    nearby = cues[bisect_left(ends, event.time - 4):bisect_right(starts, event.time + 4)]
    near_captions = any(c.start - 4 <= event.time <= c.end + 4 for c in nearby)
    if cues and near_captions:
        anchor = min(bisect_left(ends, event.time), len(cues) - 1)
        if event.setup is None:
            # Walk backward to the preceding question or complete setup line.
            preceding = [c for c in cues[max(0, anchor - 3):anchor + 1] if c.start >= event.time - min(18, maximum * .7)]
            question = next((c for c in reversed(preceding) if "?" in c.text), None)
            start = question.start if question else (preceding[-2].start if len(preceding) > 1 else cues[anchor].start)
        if event.payoff is None:
            end = cues[min(len(cues) - 1, anchor + 1)].end
        # Include a nearby captioned response after a punchline.
        responses = [c for c in cues[bisect_left(starts, end):bisect_right(starts, end + 2)] if end <= c.start <= end + 2 and re.search(r"\[(?:laughter|laughs|applause)\]", c.text, re.I)]
        if responses:
            end = responses[-1].end
    elif len(db):
        for target, before in ((start, True), (end, False)):
            lo, hi = max(0, int((target - 2) * 10)), min(len(db), int((target + 2) * 10))
            pauses = [j / 10 for j in range(lo, hi) if db[j] < -43]
            if pauses:
                if before:
                    start = min(pauses, key=lambda x: abs(x - target))
                else:
                    end = min(pauses, key=lambda x: abs(x - target))
    start, end = max(0, start), min(duration, end)
    if end - start > maximum:
        # Preserve the setup whenever the anchor can still fit; otherwise keep
        # the anchor and choose the earliest complete caption that fits it.
        if event.time - start >= maximum:
            start = max(0, event.time - maximum * .7)
            choices = starts[bisect_left(starts, start):bisect_right(starts, event.time)]
            if choices:
                start = choices[0]
        end = min(duration, start + maximum)
        choices = [c.end for c in cues[bisect_left(ends, event.time):bisect_right(starts, end)] if event.time <= c.end <= end and c.end > start]
        if choices:
            end = choices[-1]
    if end <= event.time:
        end = min(duration, event.time + .5, start + maximum)
    return round(start, 3), round(end, 3)


def rank_clips(events, cues, duration, maximum, db=(), limit=None):
    candidates = []
    index = ([c.start for c in cues], np.maximum.accumulate([c.end for c in cues]).tolist() if cues else [])
    ordered = sorted(events, key=lambda e: e.time)
    times = [e.time for e in ordered]
    for event in events:
        start, end = boundaries(event, cues, duration, maximum, db, index)
        if end - start < 1 or not start <= event.time <= end:
            continue
        inside = ordered[bisect_left(times, start):bisect_right(times, end)]
        # Repetition within a signal cannot swamp a quiet but complete answer.
        weights = {}
        for item in inside:
            weights[item.kind] = max(weights.get(item.kind, 0), item.weight)
        strongest = max(weights.values())
        score = strongest + .4 * sum(w for w in weights.values()) - .4 * strongest
        evidence = []
        for item in sorted(inside, key=lambda x: -x.weight):
            if item.detail not in evidence:
                evidence.append(item.detail)
        excerpt = " ".join(c.text for c in cues[bisect_right(index[1], start):bisect_left(index[0], end)] if c.start < end and c.end > start)
        used_transcript = bool(excerpt)
        lead = max(inside, key=lambda item: item.weight)
        context = max((item for item in inside if item.setup is not None), key=lambda item: item.weight, default=event)
        truncated = context.setup is not None and (start > context.setup + .1 or end < (context.payoff or end) - .1)
        # Prefer a complete, quiet exchange over a louder fragment with its
        # explanation or payoff outside the editor's duration limit.
        if truncated:
            score *= .68
        candidates.append({"start": start, "end": end, "anchor": round(event.time, 3), "score": round(score, 2),
                           "title": lead.title or event.title or "A moment worth reviewing", "evidence": evidence[:8], "selection_method": "basic",
                           "transcript_used": used_transcript, "excerpt": excerpt,
                           "evidence_events": [{"time": x.time, "kind": x.kind, "detail": x.detail} for x in inside],
                           "boundary_note": "Length limit trims this exchange; review the context." if truncated else "Caption boundaries" if used_transcript else "Audio pauses and context padding",
                           "reason": "Text patterns suggest an exchange. Meaning and payoff have not been model-reviewed." if "transcript" in weights else "A captioned reaction with its preceding setup." if "caption_reaction" in weights else "An editor-supplied replay marker with surrounding context." if "replay" in weights else "A keyword match in the spoken context." if "keyword" in weights else "An audible change with surrounding context."})
    return select_nonoverlapping(candidates, limit)


def select_nonoverlapping(candidates, limit=None):
    selected, chronological, anchors = [], [], []
    for candidate in sorted(candidates, key=lambda x: (-x["score"], x["start"])):
        at = bisect_left(chronological, (candidate["start"],))
        if at and chronological[at - 1][1] > candidate["start"] or at < len(chronological) and chronological[at][0] < candidate["end"]:
            continue
        anchor = candidate.get("anchor", candidate["start"])
        pos = bisect_left(anchors, anchor)
        if pos and anchor - anchors[pos - 1] < 5 or pos < len(anchors) and anchors[pos] - anchor < 5:
            continue
        insort(chronological, (candidate["start"], candidate["end"]))
        insort(anchors, anchor)
        selected.append(candidate)
        if limit is not None and len(selected) >= limit:
            break
    return [{"id": i + 1, **clip} for i, clip in enumerate(selected)]
