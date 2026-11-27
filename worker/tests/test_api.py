import io
import asyncio
import httpx
import os
import json
import subprocess
import tempfile
import time
import unittest
import wave
from pathlib import Path

import numpy as np

# Isolate test uploads from real editor files.
TEST_DATA = tempfile.TemporaryDirectory()
os.environ['CUTLIST_DATA'] = TEST_DATA.name
from worker.main import ROOT, app
from worker import main as worker
from unittest.mock import patch


def wav_bytes(samples):
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(samples.astype('<i2').tobytes())
    return output.getvalue()


def animated_wav():
    t = np.arange(16000 * 45) / 16000
    amp = np.full_like(t, 1200)
    amp[20 * 16000:24 * 16000] = 6500
    voice = (np.sin(2 * np.pi * 140 * t) + .3 * np.sin(2 * np.pi * 313 * t)) * (.55 + .4 * np.sin(2 * np.pi * 9 * t))
    return wav_bytes(voice * amp)


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        # Some locked-down Linux runners cannot wake selectors from a worker
        # thread. A timer also gives every test a bounded scheduling interval.
        async def heartbeat():
            while True:
                await asyncio.sleep(.025)
        self.heartbeat = asyncio.create_task(heartbeat())

    async def asyncTearDown(self):
        await self.client.aclose()
        self.heartbeat.cancel()

    async def run_job(self, **kwargs):
        kwargs['data'] = {'content_review': 'false', **kwargs.get('data', {}), 'auto_transcribe': 'false'}
        response = await self.client.post('/api/jobs', **kwargs)
        self.assertEqual(response.status_code, 202, response.text)
        job = response.json()['id']
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            state = (await self.client.get(f'/api/jobs/{job}')).json()
            if state['status'] == 'complete':
                return state['result']
            if state['status'] == 'error':
                self.fail(state['error'])
            await asyncio.sleep(.05)
        self.fail('Worker did not finish within 90 seconds')

    async def sample_job(self):
        return await self.run_job(data={'sample': 'true', 'maximum': '30'})

    async def wait_for_export(self, job, clip, expected='ready'):
        endpoint = f'/api/jobs/{job}/clips/{clip}/export'
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            response = await self.client.get(endpoint)
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json()
            if state['status'] == expected:
                return state
            if state['status'] == 'error':
                self.fail(state.get('error'))
            await asyncio.sleep(.05)
        self.fail('Clip export did not finish within 90 seconds')

    def media_info(self, path):
        proc = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, check=True)
        return json.loads(proc.stdout)

    async def test_captions_only_and_export(self):
        captions = (ROOT / 'public/sample/studio-notes.vtt').read_bytes()
        result = await self.run_job(files={'captions': ('podcast.vtt', captions, 'text/vtt')}, data={'maximum': '30'})
        self.assertGreater(len(result['clips']), 0)
        self.assertIsNone(result['media_url'])
        self.assertTrue(all(c['transcript_used'] for c in result['clips']))
        exported = await self.client.get(f"/api/jobs/{result['id']}/shortlist")
        self.assertIn('IN 00:', exported.text)
        self.assertIn('OUT 00:', exported.text)
        self.assertIn('Transcript used: yes', exported.text)

    async def test_audio_only_silence_steady_and_spike(self):
        t = np.arange(16000 * 15) / 16000
        for kind, signal in [('silence', np.zeros_like(t)), ('steady', np.sin(2 * np.pi * 220 * t) * 6000), ('spike', np.pad(np.full(100, 30000), (120000, len(t) - 120100)))]:
            with self.subTest(kind=kind):
                result = await self.run_job(files={'media': (f'{kind}.wav', wav_bytes(signal), 'audio/wav')})
                self.assertEqual(result['clips'], [])
                self.assertIn('Unavailable', result['capabilities']['transcript'])
                self.assertEqual(result['media_kind'], 'audio')
                self.assertTrue(result['media_url'])

    async def test_audio_only_dynamic_recording(self):
        result = await self.run_job(files={'media': ('animated.wav', animated_wav(), 'audio/wav')})
        self.assertTrue(result['clips'])
        self.assertTrue(all(not c['transcript_used'] for c in result['clips']))
        self.assertTrue(any('Volume rises' in e for c in result['clips'] for e in c['evidence']))
        self.assertFalse(any('Laughter detected' in e for c in result['clips'] for e in c['evidence']))

    async def test_video_clip_download_contains_selected_interval_and_audio(self):
        result = await self.sample_job()
        clip = result['clips'][0]
        endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}"
        self.assertEqual((await self.client.get(endpoint + '/download')).status_code, 409)
        self.assertEqual((await self.client.post(endpoint + '/export')).status_code, 202)
        # A duplicate request shares the render instead of spawning another FFmpeg.
        self.assertEqual((await self.client.post(endpoint + '/export')).status_code, 202)
        ready = await self.wait_for_export(result['id'], clip['id'])
        self.assertEqual(ready['progress'], 100)
        download = await self.client.get(ready['url'])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.headers['content-type'], 'video/mp4')
        self.assertIn('attachment;', download.headers['content-disposition'])
        self.assertTrue(ready['filename'].endswith('.mp4'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'clip.mp4'
            path.write_bytes(download.content)
            info = self.media_info(path)
            self.assertEqual({s['codec_type'] for s in info['streams']}, {'video', 'audio'})
            self.assertTrue(any(s['codec_name'] == 'h264' for s in info['streams']))
            self.assertAlmostEqual(float(info['format']['duration']), clip['end'] - clip['start'], delta=.15)
            def decode(source, at):
                audio = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(source), '-t', '2', '-vn', '-ac', '1', '-ar', '16000', '-f', 's16le', 'pipe:1'], capture_output=True, check=True)
                return np.frombuffer(audio.stdout, dtype='<i2').astype(np.float32)
            original = decode(ROOT / 'public/sample/studio-notes.mp4', clip['start'] + .5)
            exported = decode(path, .5)
            size = min(len(original), len(exported))
            self.assertGreater(size, 16000)
            self.assertGreater(float(np.corrcoef(original[:size], exported[:size])[0, 1]), .92,
                               'Export must contain the selected spoken moment, not the beginning of the recording')
        # Existing analyses and clips survive a worker restart, with source paths private.
        worker.jobs.pop(result['id'])
        restored = await self.client.get(f"/api/jobs/{result['id']}")
        self.assertNotIn('source', restored.json())
        self.assertNotIn('preview', restored.json())
        again = await self.client.post(endpoint + '/export')
        self.assertEqual(again.json()['status'], 'ready')
        self.assertEqual((await self.client.get(ready['url'])).content, download.content)

    async def test_audio_clip_download_is_trimmed_m4a(self):
        result = await self.run_job(files={'media': ('animated.wav', animated_wav(), 'audio/wav')})
        clip = result['clips'][0]
        endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}/export"
        await self.client.post(endpoint)
        ready = await self.wait_for_export(result['id'], clip['id'])
        download = await self.client.get(ready['url'])
        self.assertEqual(download.headers['content-type'], 'audio/mp4')
        self.assertTrue(ready['filename'].endswith('.m4a'))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'clip.m4a'
            path.write_bytes(download.content)
            info = self.media_info(path)
            self.assertEqual({s['codec_type'] for s in info['streams']}, {'audio'})
            self.assertEqual(info['streams'][0]['codec_name'], 'aac')
            self.assertAlmostEqual(float(info['format']['duration']), clip['end'] - clip['start'], delta=.05)

    async def test_clip_download_rejects_missing_clip_and_captions_only(self):
        result = await self.run_job(files={'captions': ('sample.vtt', (ROOT / 'public/sample/studio-notes.vtt').read_bytes(), 'text/vtt')})
        endpoint = f"/api/jobs/{result['id']}/clips/{result['clips'][0]['id']}"
        for suffix in ['/export', '/download']:
            response = await self.client.get(endpoint + suffix)
            self.assertEqual(response.status_code, 409)
            self.assertIn('matching recording', response.json()['detail'])
        self.assertEqual((await self.client.post(endpoint + '/export')).status_code, 409)
        self.assertEqual((await self.client.post(f"/api/jobs/{result['id']}/clips/999/export")).status_code, 404)

    async def test_failed_clip_export_can_retry_without_partial_download(self):
        result = await self.sample_job()
        clip = result['clips'][0]
        endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}"
        with patch.object(worker.clip_exports, 'run_ffmpeg', side_effect=ValueError('Test decode failure')):
            await self.client.post(endpoint + '/export')
            state = await self.wait_for_export(result['id'], clip['id'], expected='error')
            self.assertIn('Test decode failure', state['error'])
            self.assertEqual((await self.client.get(endpoint + '/download')).status_code, 409)
        await self.client.post(endpoint + '/export')
        ready = await self.wait_for_export(result['id'], clip['id'])
        self.assertEqual((await self.client.get(ready['url'])).status_code, 200)

    async def test_media_plus_captions_and_range_preview(self):
        result = await self.run_job(files={'media': ('sample.mp4', (ROOT / 'public/sample/studio-notes.mp4').read_bytes(), 'video/mp4'), 'captions': ('sample.srt', (ROOT / 'public/sample/studio-notes.srt').read_bytes(), 'application/x-subrip')}, data={'maximum': '30'})
        self.assertGreater(len(result['clips']), 0)
        self.assertEqual(result['media_kind'], 'video')
        media = await self.client.get(result['media_url'], headers={'Range': 'bytes=0-511'})
        self.assertEqual(media.status_code, 206)
        self.assertEqual(len(media.content), 512)
        self.assertTrue(all(c['end'] <= result['duration'] and c['start'] >= 0 for c in result['clips']))

    async def test_sample_same_pipeline_without_and_with_illustrative_replays(self):
        for markers in [False, True]:
            result = await self.run_job(data={'sample': 'true', 'sample_replays': str(markers).lower(), 'maximum': '35'})
            self.assertGreater(len(result['clips']), 0)
            self.assertEqual(bool(result['replays']), markers)
            if markers:
                self.assertIn('illustrative', result['capabilities']['replays'])
            else:
                self.assertFalse(any('replay marker' in e for c in result['clips'] for e in c['evidence']))
            for c in result['clips']:
                self.assertLessEqual(c['end'] - c['start'], 35.001)
                for other in result['clips']:
                    if c['id'] != other['id']:
                        self.assertFalse(c['start'] < other['end'] and c['end'] > other['start'])

    async def test_useful_decode_error(self):
        response = await self.client.post('/api/jobs', files={'media': ('broken.mp4', b'not a video', 'video/mp4')})
        job = response.json()['id']
        for _ in range(100):
            state = (await self.client.get(f'/api/jobs/{job}')).json()
            if state['status'] == 'error':
                self.assertIn('decode', state['error'])
                self.assertIn('MP4', state['error'])
                break
            await asyncio.sleep(.05)
        else:
            self.fail('Expected a useful decode error')

    async def test_invalid_inputs(self):
        for data in [{'maximum': '0'}, {'maximum': 'nan'}, {'replay_markers': 'nan'}, {}]:
            response = await self.client.post('/api/jobs', data=data)
            self.assertEqual(response.status_code, 422)
        self.assertEqual((await self.client.get('/api/jobs/not-a-job')).status_code, 404)
        response = await self.client.post('/api/jobs', files={'captions': ('bad.vtt', b'not captions')})
        self.assertEqual(response.status_code, 422)

    async def test_cached_recording_reanalysis_skips_upload_and_speech(self):
        result = await self.sample_job()
        with patch.object(worker, 'ffmpeg', side_effect=AssertionError('Cached reanalysis must not decode the source again')):
            second = await self.run_job(data={'reuse_job': result['id'], 'maximum': '40', 'content_review': 'false', 'goal': 'funny'})
        self.assertNotEqual(second['id'], result['id'])
        self.assertEqual(second['duration'], result['duration'])
        self.assertEqual(second['maximum'], 40)
        self.assertEqual(second['goal'], 'funny')
        self.assertEqual((await self.client.get(second['media_url'])).status_code, 200)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as other:
            denied = await other.post('/api/jobs', data={'reuse_job': result['id']})
            self.assertEqual(denied.status_code, 404)
        await self.client.delete(f"/api/jobs/{result['id']}")
        self.assertEqual((await self.client.get(second['media_url'])).status_code, 200, 'Deleting the first project must not remove the reused source')

    async def test_sixteen_hour_limit_and_no_transcript_word_payload_in_compact_status(self):
        info = {'format': {'duration': str(15 * 3600)}, 'streams': [{'codec_type': 'video', 'codec_name': 'h264'}]}
        with patch.object(worker.subprocess, 'run') as ffprobe:
            ffprobe.return_value.returncode = 0
            ffprobe.return_value.stdout = json.dumps(info).encode()
            _, duration = worker.probe(Path('long.mp4'))
            self.assertEqual(duration, 15 * 3600)
            info['format']['duration'] = str(17 * 3600)
            ffprobe.return_value.stdout = json.dumps(info).encode()
            with self.assertRaisesRegex(ValueError, '16 hours'):
                worker.probe(Path('too-long.mp4'))
        result = await self.sample_job()
        response = (await self.client.get(f"/api/jobs/{result['id']}?compact=true")).json()
        self.assertNotIn('words', response['result'])
        self.assertEqual(len(response['result']['clips']), len(result['clips']))

    async def test_full_video_is_not_reencoded_and_only_requested_clip_is_previewed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.mkv'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(ROOT / 'public/sample/studio-notes.mp4'), '-c', 'copy', str(path)], check=True)
            calls = []
            actual = worker.ffmpeg
            def record(job, args, *rest, **kwargs):
                calls.append(args)
                return actual(job, args, *rest, **kwargs)
            with patch.object(worker, 'ffmpeg', side_effect=record):
                result = await self.run_job(files={'media': ('source.mkv', path.read_bytes()), 'captions': ('source.vtt', (ROOT / 'public/sample/studio-notes.vtt').read_bytes())}, data={'content_review': 'false'})
            self.assertEqual(result['preview_mode'], 'clips')
            self.assertTrue(calls)
            self.assertTrue(all('-vn' in args for args in calls), 'Analysis must not encode the full video')
            clip = result['clips'][0]
            endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}"
            await self.client.post(endpoint + '/export')
            await self.wait_for_export(result['id'], clip['id'])
            playable = await self.client.get(endpoint + '/play', headers={'Range': 'bytes=0-511'})
            self.assertEqual(playable.status_code, 206)
            self.assertEqual(playable.headers['content-type'], 'video/mp4')

    async def test_maximum_quality_export_keeps_original_hd_dimensions(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'original-hd.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=navy:s=1920x1080:r=10:d=10',
                            '-f', 'lavfi', '-i', 'sine=frequency=220:duration=10', '-c:v', 'libx264', '-threads', '2', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', str(path)], check=True)
            captions = b'WEBVTT\n\n00:00.000 --> 00:02.000\nWhat changed the way you handle difficult projects?\n\n00:02.000 --> 00:08.000\nI learned that keeping a separate backup matters because one broken drive should never end the story.\n'
            result = await self.run_job(files={'media': ('original-hd.mp4', path.read_bytes()), 'captions': ('source.vtt', captions)})
            clip = result['clips'][0]
            endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}"
            edited = await self.client.patch(endpoint, json={'revision': 0, 'quality': 'maximum'})
            self.assertEqual(edited.status_code, 200, edited.text)
            await self.client.post(endpoint + '/export')
            ready = await self.wait_for_export(result['id'], clip['id'])
            download = await self.client.get(ready['url'])
            exported = Path(folder) / 'exported.mp4'
            exported.write_bytes(download.content)
            video = next(s for s in self.media_info(exported)['streams'] if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height']), (1920, 1080))
            self.assertTrue(result['source_quality_preserved'])

    async def test_saved_clip_export_does_not_wait_for_a_long_analysis_queue(self):
        import threading
        result = await self.sample_job()
        gate = threading.Event()
        blocked_analysis = worker.pool.submit(gate.wait, 10)
        try:
            clip = result['clips'][0]
            endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}"
            await self.client.post(endpoint + '/export')
            ready = await self.wait_for_export(result['id'], clip['id'])
            self.assertFalse(blocked_analysis.done(), 'A saved clip should render while the analysis worker is occupied')
            self.assertEqual((await self.client.get(ready['url'])).status_code, 200)
        finally:
            gate.set()
            blocked_analysis.result(timeout=5)

    async def test_content_review_failure_is_explicit_and_never_model_approved(self):
        result = await self.sample_job()
        self.assertEqual(result['selection_method'], 'basic')
        self.assertIn('meaning', result['capabilities'])
        self.assertFalse(any(c.get('selection_method') == 'local_model' for c in result['clips']))

    async def test_content_review_uses_requested_goal_and_validated_payoff(self):
        from worker.semantic import grounded_clips
        from worker.captions import Cue
        def review(job, input_path, output_path, on_partial=lambda *_: None):
            data = json.loads(input_path.read_text())
            self.assertEqual(data['goal'], 'funny')
            cues = [Cue(**c) for c in data['cues']]
            payoff = next(i for i, c in enumerate(cues) if 'popcorn' in c.text)
            setup = payoff - 2
            answer = {'clips': [{'start_cue': setup, 'end_cue': payoff, 'payoff_cue': payoff, 'category': 'joke', 'score': 8,
                                'standalone': True, 'has_payoff': True, 'title': 'A literal editing request', 'reason': 'The client reverses the make it pop request.',
                                'setup_quote': 'make the video pop', 'payoff_quote': 'make it a little less popcorn'}]}
            clips = grounded_clips(answer, cues, 0, len(cues), data['maximum'], data['goal'])
            self.assertTrue(clips)
            for index, clip in enumerate(clips, 1): clip['id'] = index
            return {'clips': clips, 'model': 'test-local-model', 'windows': 1, 'cache_hits': 0, 'processing_seconds': .1}
        with patch.object(worker.semantic, 'status', return_value={'available': True}), patch.object(worker, 'run_content_review', side_effect=review):
            result = await self.run_job(data={'sample': 'true', 'content_review': 'true', 'goal': 'funny', 'maximum': '45'})
        self.assertEqual(result['selection_method'], 'local_model')
        self.assertEqual(result['goal'], 'funny')
        self.assertEqual(len(result['clips']), 1)
        self.assertIn('popcorn', result['clips'][0]['payoff_quote'])


    async def test_private_projects_and_origin_protection(self):
        result = await self.sample_job()
        job = result['id']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as stranger:
            for suffix in ['', '/media', '/shortlist', '/timeline.xml', '/clips/1/download']:
                self.assertEqual((await stranger.get(f'/api/jobs/{job}' + suffix)).status_code, 404)
            self.assertEqual((await stranger.delete(f'/api/jobs/{job}')).status_code, 404)
            self.assertNotIn(job, [j['id'] for j in (await stranger.get('/api/jobs')).json()])
        bad = await self.client.post('/api/jobs', data={'sample': 'true'}, headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(bad.status_code, 403)
        self.assertIn(job, [j['id'] for j in (await self.client.get('/api/jobs')).json()])
        with patch.dict(os.environ, {'CUTLIST_ACCESS_KEY': 'private-test-key'}):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as locked:
                self.assertEqual((await locked.get('/api/jobs')).status_code, 401)
                self.assertEqual((await locked.post('/api/session', json={'key': 'wrong'})).status_code, 401)
                self.assertEqual((await locked.post('/api/session', json={'key': 'private-test-key'})).status_code, 200)
                self.assertEqual((await locked.get('/api/jobs')).status_code, 200)

    async def test_resumable_upload_offsets_and_restart(self):
        import hashlib
        recording = (ROOT / 'public/sample/studio-notes.mp4').read_bytes()
        metadata = {'name': 'resumed.mp4', 'size': len(recording), 'fingerprint': hashlib.sha256(recording).hexdigest()}
        started = await self.client.post('/api/uploads', json=metadata)
        self.assertEqual(started.status_code, 201)
        identifier = started.json()['id']
        endpoint = '/api/uploads/' + identifier
        first = await self.client.put(endpoint, content=recording[:500000], headers={'Content-Range': f'bytes 0-499999/{len(recording)}'})
        self.assertEqual(first.json()['offset'], 500000)
        # A new Store instance reads the same durable offset after a restart.
        from worker.store import Store
        self.assertEqual(Store(worker.DATA).upload(identifier)['offset'], 500000)
        resumed = (await self.client.post('/api/uploads', json=metadata)).json()
        self.assertEqual(resumed['id'], identifier)
        self.assertEqual(resumed['offset'], 500000)
        self.assertEqual((await self.client.put(endpoint, content=b'x', headers={'Content-Range': f'bytes 0-0/{len(recording)}'})).status_code, 409)
        bad = await self.client.put(endpoint, content=b'too short', headers={'Content-Range': f'bytes 500000-500099/{len(recording)}'})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(Path(worker.store.upload(identifier)['path']).stat().st_size, 500000)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as stranger:
            self.assertEqual((await stranger.get(endpoint)).status_code, 404)
        final = await self.client.put(endpoint, content=recording[500000:], headers={'Content-Range': f'bytes 500000-{len(recording)-1}/{len(recording)}'})
        self.assertEqual(final.json()['offset'], len(recording))
        result = await self.run_job(data={'upload_id': identifier}, files={'captions': ('source.vtt', (ROOT / 'public/sample/studio-notes.vtt').read_bytes())})
        self.assertEqual(result['source_name'], 'resumed.mp4')
        self.assertEqual((await self.client.delete(f"/api/jobs/{result['id']}")).status_code, 200)
        self.assertIsNone(worker.store.upload(identifier), 'Deleting a project also discards its temporary upload copy')

    async def test_timing_captions_crop_feedback_and_timeline(self):
        import xml.etree.ElementTree as ET
        result = await self.sample_job()
        clip = result['clips'][0]
        endpoint = f"/api/jobs/{result['id']}/clips/{clip['id']}"
        a, b = clip['start'] + .1, clip['end'] - .1
        data = {'revision': 0, 'start': a, 'end': b, 'crop': {'format': 'vertical', 'x': .2, 'y': .5}, 'burn_captions': True,
                'captions': [{'start': a, 'end': min(a + 2, b), 'text': 'Edited caption with a colon: yes & <literal> text.'}]}
        response = await self.client.patch(endpoint, json=data)
        self.assertEqual(response.status_code, 200, response.text)
        edited = response.json()
        self.assertEqual(edited['revision'], 1)
        self.assertEqual((await self.client.patch(endpoint, json=data)).status_code, 409, 'Stale edits must not overwrite a newer revision')
        for invalid in [{'start': -1}, {'end': result['duration'] + 1}, {'start': 0, 'end': 100}, {'crop': {'format': 'vertical', 'x': 2, 'y': 0}}]:
            bad = await self.client.patch(endpoint, json={**data, 'revision': 1, **invalid})
            self.assertEqual(bad.status_code, 422, bad.text)
        if len(result['clips']) > 1:
            other = result['clips'][1]
            bad = await self.client.patch(endpoint, json={'revision': 1, 'start': other['start'], 'end': other['end']})
            self.assertEqual(bad.status_code, 422)
            self.assertIn('overlaps', bad.text)
        srt = await self.client.get(endpoint + '/captions.srt')
        self.assertIn('00:00:00,000 --> 00:00:02,000', srt.text)
        self.assertIn('Edited caption', srt.text)
        vtt = await self.client.get(endpoint + '/captions.vtt')
        self.assertTrue(vtt.text.startswith('WEBVTT'))
        await self.client.post(endpoint + '/export')
        ready = await self.wait_for_export(result['id'], clip['id'])
        download = await self.client.get(ready['url'])
        self.assertEqual(download.status_code, 200)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'vertical.mp4'
            path.write_bytes(download.content)
            info = self.media_info(path)
            video = next(s for s in info['streams'] if s['codec_type'] == 'video')
            self.assertEqual((video['width'], video['height']), (720, 1280))
            self.assertAlmostEqual(float(info['format']['duration']), b - a, delta=.15)
        self.assertEqual((await self.client.get(endpoint + '/download?revision=0')).status_code, 409)
        feedback = await self.client.post(endpoint + '/feedback', json={'value': 'keep', 'reason': 'Useful explanation'})
        self.assertEqual(feedback.json()['feedback'], 'keep')
        report = (await self.client.get('/api/quality')).json()
        self.assertEqual((report['kept'], report['trimmed']), (1, 1))
        xml = ET.fromstring((await self.client.get(f"/api/jobs/{result['id']}/timeline.xml")).text)
        item = xml.find(f".//clipitem[@id='video-{clip['id']}']")
        from fractions import Fraction
        fps = float(Fraction(result['media_info']['fps']))
        self.assertEqual(int(item.findtext('in')), round(a * fps))
        self.assertEqual(int(item.findtext('out')), round(b * fps))
        self.assertNotIn(str(worker.DATA), ET.tostring(xml, encoding='unicode'))
        await self.client.post(endpoint + '/feedback', json={'value': 'reject'})
        xml = ET.fromstring((await self.client.get(f"/api/jobs/{result['id']}/timeline.xml")).text)
        self.assertIsNone(xml.find(f".//clipitem[@id='video-{clip['id']}']"))
        worker.jobs.pop(result['id'])
        restored = (await self.client.get(f"/api/jobs/{result['id']}")).json()['result']['clips'][0]
        self.assertEqual(restored['captions'][0]['text'], data['captions'][0]['text'])
        self.assertEqual(restored['revision'], 1)

    async def test_queue_cancel_retry_and_expiry(self):
        import threading
        gate = threading.Event()
        blocker = worker.pool.submit(gate.wait, 10)
        try:
            created = (await self.client.post('/api/jobs', data={'sample': 'true', 'auto_transcribe': 'false'})).json()
            job = created['id']
            self.assertEqual((await self.client.post(f'/api/jobs/{job}/cancel')).status_code, 200)
            self.assertEqual((await self.client.get(f'/api/jobs/{job}')).json()['status'], 'cancelled')
            self.assertEqual((await self.client.post(f'/api/jobs/{job}/retry')).status_code, 202)
        finally:
            gate.set()
        blocker.result(timeout=10)
        for _ in range(100):
            state = (await self.client.get(f'/api/jobs/{job}')).json()
            if state['status'] == 'complete':
                break
            await asyncio.sleep(.05)
        self.assertEqual(state['status'], 'complete')
        worker.cleanup(worker.store.get(job)['expires'] + 1)
        self.assertEqual((await self.client.get(f'/api/jobs/{job}')).status_code, 404)
        self.assertFalse((worker.DATA / job).exists())

    async def test_running_cancel_stops_real_ffmpeg_and_retries(self):
        original = worker.ffmpeg
        def realtime(job, args, *rest, **kwargs):
            return original(job, ['-re', *args], *rest, **kwargs)
        with patch.object(worker, 'ffmpeg', side_effect=realtime):
            response = await self.client.post('/api/jobs', data={'sample': 'true', 'auto_transcribe': 'false'})
            job = response.json()['id']
            for _ in range(100):
                if job in worker.processes:
                    break
                await asyncio.sleep(.02)
            self.assertIn(job, worker.processes)
            process = worker.processes[job]
            self.assertEqual((await self.client.post(f'/api/jobs/{job}/cancel')).status_code, 200)
            for _ in range(100):
                if worker.futures[job].done():
                    break
                await asyncio.sleep(.02)
            self.assertIsNotNone(process.poll(), 'Cancelling must stop the decoding process')
            self.assertEqual(worker.jobs[job]['status'], 'cancelled')
            self.assertFalse((worker.DATA / job / 'analysis.pcm').exists())
        self.assertEqual((await self.client.post(f'/api/jobs/{job}/retry')).status_code, 202)
        for _ in range(100):
            state = (await self.client.get(f'/api/jobs/{job}')).json()
            if state['status'] == 'complete':
                break
            await asyncio.sleep(.05)
        self.assertEqual(state['status'], 'complete')

    async def test_restart_recovers_durable_running_job(self):
        result = await self.sample_job()
        job = result['id']
        worker.futures[job].result(timeout=5)
        state = worker.store.get(job)
        state.update(status='running', progress=12)
        worker.store.save(state)
        worker.jobs.pop(job)
        worker.recover_jobs()
        for _ in range(100):
            restored = (await self.client.get(f'/api/jobs/{job}')).json()
            if restored['status'] == 'complete':
                break
            await asyncio.sleep(.05)
        self.assertEqual(restored['status'], 'complete')
        self.assertEqual(restored['result']['clips'], result['clips'])
        self.assertNotIn('parameters', restored)


if __name__ == '__main__':
    unittest.main()
