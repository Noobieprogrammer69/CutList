import csv
import tempfile
import unittest
from pathlib import Path

import numpy as np

from worker.analysis import Event, audio_energy, energy_events, rank_clips, sound_events, transcript_events
from worker.captions import Cue, parse_captions, parse_replays
from worker.events import EventClassifier, EXPECTED_LABELS, REQUIRED, verify_labels


class CaptionsTest(unittest.TestCase):
    def test_srt_and_webvtt(self):
        srt = '\ufeff1\r\n00:00:01,125 --> 00:00:04,900\r\nWhy edit?\r\n\r\n2\r\n00:00:05,000 --> 00:00:09,000\r\nBecause every cut can make a story clearer.'
        vtt = 'WEBVTT\n\nNOTE a comment\nignored\n\nid-1\n00:01.125 --> 00:04.900 align:start\n<v Host>Why edit?</v>\n\nid-2\n00:05.000 --> 00:09.000\nBecause every cut can make a story clearer.'
        a, b = parse_captions(srt), parse_captions(vtt)
        self.assertEqual([(c.start, c.end) for c in a], [(c.start, c.end) for c in b])
        self.assertEqual(b[0].text, 'Why edit?')
        self.assertEqual(b[0].speaker, 'Host')

    def test_reject_invalid_captions(self):
        for text in ('plain prose', 'WEBVTT', '1\n00:90:01,000 --> 00:00:03,000\nOops', '1\n00:00:04,000 --> 00:00:03,000\nOops'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_captions(text)

    def test_replay_validation(self):
        self.assertEqual(parse_replays('00:04, 12.5\n1:12; 00:04'), [4, 12.5, 72])
        for text in ('nan', 'inf', '-2', '1:89', 'hello'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_replays(text)


class RankingTest(unittest.TestCase):
    def test_all_distinct_candidates_instead_of_a_five_result_quota(self):
        events = [Event(20 + i * 60, 'energy', 2, f'Volume rises at moment {i}') for i in range(18)]
        clips = rank_clips(events, [], 1200, 30)
        self.assertEqual(len(clips), 18)
        for a, b in zip(sorted(clips, key=lambda c: c['start']), sorted(clips, key=lambda c: c['start'])[1:]):
            self.assertLessEqual(a['end'], b['start'])

    def test_routine_gameplay_question_is_not_a_content_highlight(self):
        cues = [Cue(10, 16, 'Should we change our outfit before going into the city?'), Cue(16, 24, 'Yes, let us change it and get in the car now.'), Cue(25, 32, 'We drove across the map and parked by the shop.')]
        self.assertEqual(transcript_events(cues, []), [])

    def test_quiet_question_answer_can_beat_energy(self):
        cues = [Cue(10, 13, 'What changed your creative process?'), Cue(13.5, 20, 'I stopped adding effects because the story needed room to breathe.')]
        events = transcript_events(cues, []) + [Event(70, 'energy', 2.2, 'Volume rises at 01:10')]
        clips = rank_clips(events, cues, 100, 30)
        self.assertTrue(clips[0]['transcript_used'])
        self.assertEqual(clips[0]['start'], 10)
        self.assertEqual(clips[0]['end'], 20)

    def test_joke_setup_and_caption_laughter_are_separate(self):
        cues = [Cue(10, 13, 'Why did you add all those transitions?'), Cue(14, 18, 'The client wanted it to pop so I made the whole movie popcorn.'), Cue(18.2, 21, '[laughter]')]
        events = transcript_events(cues, [])
        self.assertTrue(any(e.kind == 'caption_reaction' for e in events))
        self.assertFalse(any(e.kind == 'laughter' for e in events))
        clips = rank_clips(events, cues, 25, 20)
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]['start'], 10)
        self.assertGreaterEqual(clips[0]['end'], 21)

    def test_story_claim_and_keywords(self):
        cues = [Cue(0, 5, 'Last year I lost my project drive.'), Cue(5.5, 12, 'Then I realized the assistant had a backup and finally we could finish.'), Cue(30, 38, 'Nobody needs more plugins because the best creative process starts with a story that matters to the people who watch it.')]
        events = transcript_events(cues, ['backup'])
        self.assertTrue(any('Story setup' in e.detail for e in events))
        self.assertTrue(any('Claim with' in e.detail for e in events))
        self.assertTrue(any(e.kind == 'keyword' for e in events))

    def test_bounds_length_and_nonoverlap(self):
        events = [Event(t, 'energy', 2, 'Volume rises', title='Energy') for t in [0, 4, 8, 12, 18, 25, 30, 38, 44, 49.9]]
        for maximum in [5, 10, 17, 45, 180]:
            clips = rank_clips(events, [], 50, maximum)
            self.assertLessEqual(len(clips), len(events))
            for clip in clips:
                self.assertGreaterEqual(clip['start'], 0)
                self.assertLessEqual(clip['end'], 50)
                self.assertLessEqual(clip['end'] - clip['start'], maximum + .001)
                self.assertLessEqual(clip['start'], clip['anchor'])
                self.assertGreaterEqual(clip['end'], clip['anchor'])
                for other in clips:
                    if clip is not other:
                        self.assertFalse(clip['start'] < other['end'] and clip['end'] > other['start'])

    def test_plain_captions_do_not_force_five_highlights(self):
        cues = [Cue(0, 10, 'This is the ordinary introduction to our recording.')]
        self.assertEqual(rank_clips(transcript_events(cues, []), cues, 10, 30), [])

    def test_audio_moment_after_partial_captions_keeps_its_own_boundaries(self):
        cues = [Cue(0, 5, 'An introduction with no other timed captions.')]
        clips = rank_clips([Event(70, 'energy', 2, 'Volume rises')], cues, 100, 30)
        self.assertEqual(len(clips), 1)
        self.assertLessEqual(clips[0]['start'], 70)
        self.assertGreaterEqual(clips[0]['end'], 70)
        self.assertFalse(clips[0]['transcript_used'])


class EnergyTest(unittest.TestCase):
    def features(self, samples):
        return audio_energy((np.clip(samples, -1, 1) * 32767).astype(np.int16))

    def test_silence_tone_and_steady_noise(self):
        t = np.arange(16000 * 40) / 16000
        signals = [np.zeros_like(t), .3 * np.sin(2 * np.pi * 220 * t), np.random.default_rng(7).normal(0, .04, len(t))]
        for samples in signals:
            with self.subTest(signal=float(np.max(samples))):
                self.assertEqual(energy_events(*self.features(samples)), [])

    def test_spike_is_not_laughter_or_highlight(self):
        samples = np.zeros(16000 * 30)
        samples[16000 * 15:16000 * 15 + 100] = .95
        self.assertEqual(energy_events(*self.features(samples)), [])

    def test_sustained_loud_bed_is_downweighted(self):
        t = np.arange(16000 * 60) / 16000
        samples = .03 * np.sin(2 * np.pi * 220 * t)
        samples[20 * 16000:40 * 16000] *= 12
        self.assertEqual(energy_events(*self.features(samples)), [])

    def test_relative_animated_passage(self):
        t = np.arange(16000 * 60) / 16000
        voice = (np.sin(2 * np.pi * 155 * t) + .4 * np.sin(2 * np.pi * 330 * t)) * (.55 + .4 * np.sin(2 * np.pi * 9 * t))
        amplitude = np.full_like(t, .04)
        amplitude[30 * 16000:34 * 16000] = .2
        events = energy_events(*self.features(voice * amplitude))
        self.assertTrue(events)
        self.assertTrue(all(e.kind == 'energy' for e in events))
        self.assertTrue(any(29 <= e.time <= 35 for e in events))


class ModelTest(unittest.TestCase):
    def test_missing_model_is_explicit_and_never_faked(self):
        with tempfile.TemporaryDirectory() as path:
            model = EventClassifier(Path(path))
            self.assertIsNone(model.tagger)
            self.assertIn('Unavailable', model.status)
            self.assertEqual(model.predict(np.zeros(48000)), {})

    def test_incompatible_label_map_rejected(self):
        with tempfile.TemporaryDirectory() as path:
            labels = Path(path) / 'labels.csv'
            labels.write_text('index,mid,display_name\n0,/test,Laughter\n')
            with self.assertRaises(ValueError):
                verify_labels(labels)

    def test_supported_event_indices_and_swapped_labels(self):
        with tempfile.TemporaryDirectory() as path:
            labels = Path(path) / 'labels.csv'
            mapping = {i: EXPECTED_LABELS.get(i, f'other-{i}') for i in range(527)}
            def save():
                with labels.open('w', newline='') as target:
                    writer = csv.writer(target)
                    writer.writerow(['index', 'mid', 'display_name'])
                    writer.writerows((i, '/test', label) for i, label in mapping.items())
            save()
            self.assertEqual(verify_labels(labels)[16], 'Laughter')
            mapping[16], mapping[137] = mapping[137], mapping[16]
            save()
            with self.assertRaises(ValueError):
                verify_labels(labels)

    def test_only_classifier_predictions_create_sound_reaction_evidence(self):
        class FakeClassifier:
            tagger = True
            def __init__(self, predictions):
                self.predictions = predictions
            def predict(self, _samples):
                return self.predictions
        samples = np.full(48000, 10000, dtype=np.int16)
        none, _ = sound_events(samples, FakeClassifier({'Speech': .9, 'Laughter': .05}))
        self.assertEqual(none, [])
        laughter, _ = sound_events(samples, FakeClassifier({'Speech': .1, 'Laughter': .8}))
        self.assertTrue(laughter)
        self.assertTrue(all(e.kind == 'laughter' and 'CED score' in e.detail for e in laughter))
        impulse = np.pad(np.ones(80, dtype=np.int16) * 30000, (24000, 23920))
        rejected, _ = sound_events(impulse, FakeClassifier({'Laughter': .99}))
        self.assertEqual(rejected, [], 'An isolated spike must not be labeled as laughter, even with a bad model prediction')

    def test_downloaded_model_cpu_and_spike(self):
        model = EventClassifier()
        if model.tagger is None:
            self.skipTest('Optional pretrained CED weights are not installed')
        self.assertTrue(REQUIRED.issubset(set(model.labels.values())))
        for signal in [np.zeros(48000, dtype=np.float32), np.pad(np.ones(80, dtype=np.float32) * .9, (24000, 23920))]:
            prediction = model.predict(signal)
            self.assertTrue(prediction)
            self.assertLess(prediction.get('Laughter', 0), .45)


if __name__ == '__main__':
    unittest.main()
