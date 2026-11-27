"""Reproducible local timings. No fabricated multi-hour/GPU performance claims."""
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
from worker.analysis import Event, audio_energy, rank_clips
from worker.captions import Cue
from worker.transcription import available


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip-speech', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = {'cpu_threads_available': os.cpu_count(), 'recording': 'bundled synthetic sample',
              'note': 'Sample timings and a synthetic long transcript test. These are not measured 15-hour media processing times or editorial accuracy.'}
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        pcm, speech = folder / 'audio.pcm', folder / 'speech.json'
        started = time.perf_counter()
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(ROOT / 'public/sample/studio-notes.mp4'), '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', str(pcm)], check=True)
        report['audio_decode_seconds'] = round(time.perf_counter() - started, 3)
        import numpy as np
        samples = np.memmap(pcm, dtype='<i2', mode='r')
        started = time.perf_counter()
        audio_energy(samples)
        report['audio_features_seconds'] = round(time.perf_counter() - started, 3)
        samples._mmap.close()
        if not args.skip_speech and available():
            for name in ['speech_first_pass', 'speech_checkpoint_reuse']:
                subprocess.run([sys.executable, '-m', 'worker.transcription', str(pcm), str(speech)], cwd=ROOT, stdout=subprocess.DEVNULL, check=True)
                data = json.loads(speech.read_text())
                report[name] = {k: data[k] for k in ('processing_seconds', 'peak_rss_mb', 'threads', 'batch_size', 'checkpoint_hits', 'chunks')}
                report[name]['recognized_words'] = len(data['words'])
        cues = [Cue(i * 5, i * 5 + 4, 'A complete timed sentence with a little context.') for i in range(15 * 3600 // 5)]
        events = [Event(i * 60 + 30, 'energy', 2, 'Synthetic event for ranking performance') for i in range(900)]
        started = time.perf_counter()
        clips = rank_clips(events, cues, 15 * 3600, 45)
        report['synthetic_15h_ranking'] = {'seconds': round(time.perf_counter() - started, 3), 'cues': len(cues), 'events': len(events), 'results': len(clips)}
    encoded = json.dumps(report, indent=2)
    print(encoded)
    if args.output:
        args.output.write_text(encoded + '\n')


if __name__ == '__main__':
    main()
