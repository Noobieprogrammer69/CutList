import asyncio
import json
import os
import time
import unittest
from pathlib import Path

from worker.transcription import token_words, words_to_cues, chunks, available


class TimingTest(unittest.TestCase):
    def test_quiet_setup_gain_is_not_limited_by_later_loud_reaction(self):
        import numpy as np
        from worker.transcription import normalize_speech
        quiet = np.ones(8000, dtype=np.float32) * .006
        loud = np.ones(8000, dtype=np.float32) * .9
        normalized = normalize_speech(np.concatenate([quiet, loud]))
        self.assertGreater(float(np.sqrt(np.mean(normalized[:8000] ** 2))), .04)
        self.assertLessEqual(float(np.max(np.abs(normalized))), .97001)

    def test_cancelled_transcription_resumes_completed_chunks_and_rejects_changed_audio(self):
        import numpy as np
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from worker.transcription import transcribe
        class Decoder:
            count = 0
            def create_stream(self):
                return SimpleNamespace(accept_waveform=lambda *_: None, result=SimpleNamespace(tokens=[' Hello', ' world', '.'], timestamps=[1., 1.3, 1.8]))
            def decode_stream(self, stream): self.count += 1
            def decode_streams(self, streams): self.count += len(streams)
        decoder = Decoder()
        t = np.arange(16000 * 45) / 16000
        signal = ((.5 + .4 * np.sin(t * 2 * np.pi * 7)) * np.sin(t * 2 * np.pi * 160) * 7000).astype('<i2')
        with tempfile.TemporaryDirectory() as folder:
            pcm, output = Path(folder) / 'source.pcm', Path(folder) / 'speech.json'
            signal.tofile(pcm)
            def interrupted(_): raise KeyboardInterrupt('simulated cancellation')
            with patch('sherpa_onnx.OfflineRecognizer.from_transducer', return_value=decoder):
                with self.assertRaises(KeyboardInterrupt):
                    transcribe(pcm, output, progress=interrupted)
                self.assertFalse(output.exists())
                resumed = transcribe(pcm, output)
                self.assertGreaterEqual(resumed['checkpoint_hits'], 1)
                self.assertTrue(resumed['words'])
                before = decoder.count
                reused = transcribe(pcm, output)
                self.assertEqual(reused['checkpoint_hits'], reused['chunks'])
                self.assertEqual(decoder.count, before)
                signal[0] += 100
                signal.tofile(pcm)
                changed = transcribe(pcm, output)
                self.assertEqual(changed['checkpoint_hits'], 0)
                self.assertGreater(decoder.count, before)

    def test_silence_tone_and_impulse_never_create_automatic_captions(self):
        import numpy as np
        import tempfile
        from unittest.mock import patch
        from worker.transcription import transcribe
        t = np.arange(16000 * 15) / 16000
        for samples in [np.zeros(len(t)), np.sin(t * 2 * np.pi * 220) * 6000, np.pad(np.ones(100) * 30000, (120000, len(t) - 120100))]:
            with tempfile.TemporaryDirectory() as folder:
                pcm, output = Path(folder) / 'audio.pcm', Path(folder) / 'speech.json'
                samples.astype('<i2').tofile(pcm)
                with patch('sherpa_onnx.OfflineRecognizer.from_transducer', side_effect=AssertionError('Nonspeech must not be sent to the recognizer')):
                    result = transcribe(pcm, output)
                self.assertEqual(result['words'], [])
                self.assertEqual(result['cues'], [])

    def test_model_tokens_with_spaces_and_sentencepiece(self):
        for tokens in [[" Hello", " world", ".", " ", "An", "other", " answer", "?"], ["▁Hello", "▁world", ".", "▁An", "other", "▁answer", "?"]]:
            words = token_words(tokens, [i * .2 for i in range(len(tokens))], 5, 10)
            self.assertEqual([w['text'] for w in words], ['Hello', 'world.', 'Another', 'answer?'])
            cues = words_to_cues(words)
            self.assertEqual(len(cues), 2)
            self.assertTrue(all(5 <= w['start'] < w['end'] <= 10 for w in words))

    def test_chunks_are_bounded_and_follow_low_energy(self):
        import numpy as np
        samples = np.ones(16000 * 65, dtype='<i2') * 5000
        for second in [15, 30, 45, 60]:
            samples[second * 16000:(second + 1) * 16000] = 0
        cuts = list(chunks(samples))
        self.assertTrue(all(b - a <= 20 * 16000 for a, b in cuts))
        self.assertEqual(cuts[-1][1], len(samples))
        self.assertTrue(15 <= cuts[0][1] / 16000 <= 16)


@unittest.skipUnless(os.environ.get('CUTLIST_TEST_SPEECH') == '1' and available(), 'Run npm run test:speech after installing the optional speech model')
class SpeechIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_sample_through_real_pipeline_without_supplied_captions(self):
        # Import the same isolated worker used by the ordinary API tests.
        import httpx
        from worker.tests.test_api import app, worker
        async def heartbeat():
            while True:
                await asyncio.sleep(.025)
        timer = asyncio.create_task(heartbeat())
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                created = await client.post('/api/jobs', data={'sample': 'true', 'content_review': 'false', 'sample_captions': 'false', 'auto_transcribe': 'true', 'maximum': '35'})
                self.assertEqual(created.status_code, 202, created.text)
                job = created.json()['id']
                deadline = time.monotonic() + 300
                while time.monotonic() < deadline:
                    state = (await client.get('/api/jobs/' + job)).json()
                    if state['status'] in {'complete', 'error'}:
                        break
                    await asyncio.sleep(.2)
                self.assertEqual(state['status'], 'complete', state)
                result = state['result']
                self.assertEqual(result['caption_source'], 'automatic')
                self.assertGreater(len(result['words']), 150)
                self.assertGreater(len(result['clips']), 0)
                self.assertIn('biggest mistake', ' '.join(c['text'] for c in result['cues']).lower())
                self.assertTrue(all(0 <= w['start'] < w['end'] <= result['duration'] for w in result['words']))
                self.assertTrue(any(c['transcript_used'] and c['caption_source'] == 'automatic' for c in result['clips']))
                speech = json.loads((worker.DATA / job / 'speech.json').read_text())
                print(f"\nReal CPU speech inference: {speech['processing_seconds']} seconds; peak subprocess RSS {speech['peak_rss_mb']} MB; {len(result['words'])} words", flush=True)
                self.assertLess(speech['peak_rss_mb'], 4096)
                self.assertFalse((worker.DATA / job / 'analysis.pcm').exists())
        finally:
            timer.cancel()


if __name__ == '__main__':
    unittest.main()
