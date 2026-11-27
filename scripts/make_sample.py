"""Reproducible, original synthetic podcast. No downloads, invented audience data,
or pretend laughter. Captions use measured speech durations, not guesses.
Requires espeak-ng (or espeak) only when regenerating the bundled sample.
"""
import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public" / "sample"
LINES = [
    ("Host", "Welcome to Studio Notes. Today we're talking about the messy reality of freelance editing.", 1.0, .65),
    ("Host", "What is the biggest mistake you made when you started editing?", .6, .65),
    ("Guest", "I thought the secret was buying more plugins. But actually, my best project used none of them.", .5, 1.0),
    ("Guest", "Because the story was already there. I just had to cut out everything that was getting in its way.", 4.0, 1.0),
    ("Host", "That makes sense. Let's talk about the less glamorous part: working with clients.", 1.0, .65),
    ("Host", "What happened the first time a client asked you to make the video pop?", .6, .65),
    ("Guest", "I once spent an entire night adding transitions. Every cut had a different effect. I was so proud.", .5, 1.25),
    ("Guest", "In the end, the client sent it back and said: could you make it a little less popcorn?", .7, 1.25),
    ("Host", "Okay, that one got me. Sometimes the best transition is just a cut.", 4.0, .7),
    ("Host", "People often talk about speed. How do you actually finish a difficult edit?", .6, .6),
    ("Guest", "I never start with the timeline anymore. Instead, I write one sentence about what the viewer should feel.", .5, .52),
    ("Guest", "For example, if the story is about taking a risk, every shot needs to help us feel that uncertainty. That's why quiet moments matter.", 4.0, .52),
    ("Host", "Do you have a story about a project that almost went wrong?", .6, .7),
    ("Guest", "Last year, my drive failed the night before delivery. I thought I had lost three weeks of work.", .5, .8),
    ("Guest", "Then I realized the assistant had kept a backup on a tiny old drive. We finished the film on time. Ever since, backups are part of my creative process.", 1.0, .8),
    ("Host", "That is a useful lesson to end on. Thanks for listening to Studio Notes.", 1.0, .6),
]


def tc(seconds):
    ms = round(seconds * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def main():
    import numpy as np
    speech = shutil.which("espeak-ng") or shutil.which("espeak")
    if not speech:
        raise SystemExit("Install espeak-ng to regenerate: sudo pacman -S espeak-ng")
    OUT.mkdir(parents=True, exist_ok=True)
    cues, audio, offset = [], [], 1.0
    rate = 22050
    audio.append(np.zeros(rate, dtype=np.int16))
    with tempfile.TemporaryDirectory() as temporary:
        tmp = Path(temporary)
        for index, (speaker, text, gap, gain) in enumerate(LINES):
            filename = tmp / f"{index}.wav"
            subprocess.run([speech, "-v", "en-us" if speaker == "Host" else "en-gb", "-s", "165" if speaker == "Host" else "158", "-p", "42" if speaker == "Host" else "33", "-w", str(filename), text], check=True)
            with wave.open(str(filename)) as source:
                rate = source.getframerate()
                data = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
            data = np.clip(data.astype(np.float32) * gain, -32767, 32767).astype(np.int16)
            length = len(data) / rate
            cues.append((offset, offset + length, f"{speaker}: {text}"))
            audio.extend([data, np.zeros(round(gap * rate), dtype=np.int16)])
            offset += length + gap
        with wave.open(str(tmp / "podcast.wav"), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(rate)
            target.writeframes(np.concatenate(audio).tobytes())
        vtt = "WEBVTT\n\n" + "\n\n".join(f"{tc(a)} --> {tc(b)}\n{text}" for a, b, text in cues) + "\n"
        (OUT / "studio-notes.vtt").write_text(vtt)
        srt = "\n\n".join(f"{i + 1}\n{tc(a).replace('.', ',')} --> {tc(b).replace('.', ',')}\n{text}" for i, (a, b, text) in enumerate(cues)) + "\n"
        (OUT / "studio-notes.srt").write_text(srt)
        (OUT / "markers.json").write_text(json.dumps([round(cues[7][0] + 2, 3), round(cues[14][0] + 3, 3)]))
        font = "/usr/share/fonts/TTF/DejaVuSans.ttf"
        if not Path(font).exists():
            font = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        filters = [
            "drawbox=x=48:y=42:w=864:h=456:color=0x20262b:t=fill",
            f"drawtext=fontfile={font}:text='STUDIO NOTES':fontcolor=0xdefb8a:fontsize=22:x=90:y=84",
            f"drawtext=fontfile={font}:text='Inside the edit.':fontcolor=white:fontsize=54:x=86:y=160",
            f"drawtext=fontfile={font}:text='A conversation about creative work':fontcolor=0xa4adaf:fontsize=22:x=90:y=240",
            "drawbox=x=90:y=310:w=365:h=94:color=0x30393e:t=fill",
            "drawbox=x=475:y=310:w=365:h=94:color=0x30393e:t=fill",
            f"drawtext=fontfile={font}:text='MAYA  /  HOST':fontcolor=white:fontsize=20:x=116:y=345",
            f"drawtext=fontfile={font}:text='ALEX  /  EDITOR':fontcolor=white:fontsize=20:x=500:y=345",
            f"drawtext=fontfile={font}:text='CUTLIST SAMPLE  -  SYNTHESIZED VOICES':fontcolor=0xa4adaf:fontsize=14:x=90:y=448",
        ]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x111719:s=960x540:r=10", "-i", str(tmp / "podcast.wav"), "-vf", ",".join(filters), "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-t", str(offset), "-shortest", "-movflags", "+faststart", str(OUT / "studio-notes.mp4")], check=True)
    print(f"Built {offset:.1f}s sample with {len(cues)} timed captions. Replay markers are illustrative only.")


if __name__ == "__main__":
    main()
