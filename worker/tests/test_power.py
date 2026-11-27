import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import httpx
import numpy as np

import test_api as fixtures
from worker import main as worker, composition, preferences, retrieval, chapters, transcription
from worker.captions import Cue
from worker.downloads import download
from worker.semantic import Reviewer, Unavailable
from worker.visual import VisualContext, motion_anchors


class PowerUnitTest(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('CUTLIST_TEST_SEARCH') == '1', 'Enable installed embedding model smoke test with CUTLIST_TEST_SEARCH=1')
    def test_actual_local_semantic_search_model(self):
        if not retrieval.status()['available']: self.skipTest(retrieval.status()['message'])
        cues = [{'start': 0, 'end': 10, 'text': 'We solved the editing problem by keeping the story simple.'}, {'start': 60, 'end': 70, 'text': 'The car is parked outside the garage.'}]
        with tempfile.TemporaryDirectory() as folder:
            result = retrieval.search(cues, 'editing advice', 30, folder)
        self.assertEqual(result['method'], 'semantic')
        self.assertEqual(result['passages_indexed'], 2)
        self.assertTrue(all(m['id'] in {0, 1} for m in result['matches']))

    @unittest.skipUnless(os.environ.get('CUTLIST_TEST_VISION') == '1', 'Enable installed visual sequence model test with CUTLIST_TEST_VISION=1')
    def test_actual_local_visual_sequence_runtime(self):
        from worker.visual import status
        if not status()['available']: self.skipTest(status()['message'])
        with tempfile.TemporaryDirectory() as folder:
            observer = VisualContext(worker.ROOT/'public/sample/studio-notes.mp4', folder, sequences=True)
            try:
                observed, clips = observer.sequence(0, 8)
                self.assertEqual(len(observed), 6)
                self.assertTrue(all(0 <= c['start'] < c['end'] <= 8 for c in clips))
            finally: observer.close()

    def test_retained_intervals_validate_and_subtitles_map_to_output(self):
        clip = {'start': 0, 'end': 12, 'segments': [{'start': 0, 'end': 2}, {'start': 10, 'end': 12}], 'captions': [
            {'start': 0, 'end': 2, 'text': 'setup'}, {'start': 3, 'end': 7, 'text': 'mistake'}, {'start': 10, 'end': 12, 'text': 'payoff'}]}
        self.assertEqual(composition.duration(clip), 4)
        output = composition.captions_for_output(clip)
        self.assertEqual([(c['start'], c['end'], c['text']) for c in output], [(0, 2, 'setup'), (2, 4, 'payoff')])
        self.assertFalse(composition.overlaps(clip, {'start': 4, 'end': 6}))
        for parts in ([], [{'start': 4, 'end': 5}, {'start': 1, 'end': 2}], [{'start': float('nan'), 'end': 2}], [{'start': 0, 'end': 13}]):
            with self.assertRaises(ValueError): composition.validate(parts, 12, 5)

    def test_preference_learning_holds_out_recordings_and_ignores_sample_copies(self):
        projects = []
        for g in range(3):
            clips = []
            for i in range(10):
                good = i % 2 == 0
                clips.append({'id': i, 'start': i*30, 'end': i*30+20, 'score': 8, 'feedback': 'keep' if good else 'reject', 'category': 'insight' if good else 'reaction',
                              'excerpt': 'Practical thoughtful story explanation' if good else 'Sponsor promotional offer discount', 'selection_method': 'local_model', 'transcript_used': True})
            projects.append({'created': g, 'result': {'source_name': f'real-recording-{g}.mp4', 'duration': 350, 'sample': False, 'clips': clips}})
        report, weights = preferences.learn(projects)
        self.assertTrue(report['eligible'], report)
        self.assertEqual(report['held_out_recordings'], 3)
        ranked = preferences.rerank(projects[0]['result']['clips'], weights)
        self.assertEqual(ranked[0]['feedback'], 'keep')
        self.assertTrue(all(abs(c['preference_adjustment']) <= .8 for c in ranked))
        copied = [projects[0]]*5
        self.assertFalse(preferences.learn(copied)[0]['eligible'])
        self.assertEqual(preferences.learn([{**p, 'result': {**p['result'], 'sample': True}} for p in projects])[0]['reviews'], 0)

    def test_semantic_search_uses_entire_transcript_and_reuses_index(self):
        calls = []
        def serve(request):
            body = json.loads(request.content) if request.content else {}
            if request.url.path == '/api/tags': return httpx.Response(200, json={'models': [{'name': retrieval.MODEL, 'digest': 'v1', 'size': 622000000}]})
            if request.url.path == '/api/show': return httpx.Response(200, json={'capabilities': ['embedding']})
            if request.url.path == '/api/embed':
                calls.append(body['input'])
                vectors = [[1., 0, 0, 0, 0, 0, 0, 0] if 'story' in text or 'advice' in text else [0., 1, 0, 0, 0, 0, 0, 0] for text in body['input']]
                self.assertFalse(body['truncate'])
                return httpx.Response(200, json={'embeddings': vectors})
            return httpx.Response(200, json={})
        class NoCritic:
            def ready(self): return {'available': False}
            def close(self): pass
        cues = [{'start': 0, 'end': 8, 'text': 'A practical story about solving the real problem.'}, {'start': 80, 'end': 90, 'text': 'Open the inventory and change an outfit.'}]
        with tempfile.TemporaryDirectory() as folder:
            for _ in range(2):
                model = retrieval.Embedder(client=httpx.Client(transport=httpx.MockTransport(serve)))
                result = retrieval.search(cues, 'useful advice', 30, folder, embedder=model, reviewer=NoCritic())
                self.assertEqual(result['matches'][0]['start'], 0)
                self.assertFalse(result['matches'][0]['brief_reviewed'])
                self.assertEqual(result['passages_indexed'], 2)
        self.assertEqual(sum(len(c) > 1 for c in calls), 1, 'Second search must reuse document vectors')
        self.assertEqual(sum(len(c) == 1 for c in calls), 2)

    def test_query_reviewer_cannot_invent_passage_ids(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'message': {'content': '{"matches":[{"id":999,"relevant":true,"reason":"invented"}]}'}})))
        reviewer = Reviewer(client=client)
        with self.assertRaises(Unavailable): retrieval.judge('advice', [{'id': 1, 'text': 'actual content'}], reviewer)
        client.close()

    def test_visual_sequences_are_ordered_and_rejection_does_not_become_a_clip(self):
        def serve(request):
            if request.url.path == '/api/tags': return httpx.Response(200, json={'models': [{'name': 'vision-test', 'size': 1900000000}]})
            if request.url.path == '/api/show': return httpx.Response(200, json={'capabilities': ['vision']})
            if request.url.path == '/api/chat':
                body = json.loads(request.content)
                self.assertEqual(len(body['messages'][-1]['images']), 6)
                if 'moments' in body['format']['properties']:
                    answer = {'observations': [{'frame': i, 'description': 'A visible graphic frame.'} for i in range(6)], 'moments': [{'first_frame': 0, 'last_frame': 5, 'score': 9, 'context_complete': True, 'payoff_visible': True, 'title': 'Claimed event', 'reason': 'An alleged visible payoff.'}]}
                else:
                    answer = {'decisions': [{'candidate': 0, 'accept': False, 'reason': 'Routine motion, with no actual payoff.'}]}
                return httpx.Response(200, json={'message': {'content': json.dumps(answer)}})
            return httpx.Response(200, json={})
        model = Reviewer(model='vision-test', client=httpx.Client(transport=httpx.MockTransport(serve)))
        with tempfile.TemporaryDirectory() as folder:
            observer = VisualContext(worker.ROOT/'public/sample/studio-notes.mp4', folder, reviewer=model, sequences=True)
            observed, clips = observer.sequence(0, 8)
            self.assertEqual([r['time'] for r in observed], sorted(r['time'] for r in observed))
            self.assertEqual(clips, [])
            observer.close()

    def test_resumable_download_keeps_parts_and_verifies_bytes(self):
        payload = b'complete model bytes for checksum validation'
        class Response(io.BytesIO):
            status = 206
            headers = {'Content-Range': f'bytes 8-{len(payload)-1}/{len(payload)}', 'Content-Length': str(len(payload)-8)}
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/'model.onnx'
            target.with_suffix('.onnx.part').write_bytes(payload[:8])
            def open_url(request, timeout):
                self.assertEqual(request.headers.get('Range'), 'bytes=8-')
                return Response(payload[8:])
            with patch('urllib.request.urlopen', side_effect=open_url): download('https://example.test/model', target, hashlib.sha256(payload).hexdigest())
            self.assertEqual(target.read_bytes(), payload)

    def test_setup_model_stream_reports_progress_and_useful_failure(self):
        from worker.setup import SetupManager
        class Stream:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def raise_for_status(self): pass
            def iter_lines(self):
                for row in [{'status': 'pulling', 'digest': 'layer1', 'total': 100, 'completed': 50}, {'status': 'pulling', 'digest': 'layer1', 'total': 100, 'completed': 100}, {'status': 'success'}]: yield json.dumps(row)
        class Client(Stream):
            def stream(self, *args, **kwargs): return Stream()
        with tempfile.TemporaryDirectory() as folder:
            manager = SetupManager(worker.ROOT, folder)
            manager.tasks['test'] = {'owner': 'editor', 'id': 'test', 'feature': 'search', 'status': 'queued', 'progress': 0, 'log': []}
            with patch('worker.setup.httpx.Client', return_value=Client()): manager.install('test')
            report = manager.public('test', 'editor')
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['progress'], 100)
            manager.tasks['test']['status'] = 'queued'
            with patch('worker.setup.httpx.Client', side_effect=OSError('offline')): manager.install('test')
            self.assertEqual(manager.public('test', 'editor')['status'], 'error')
            self.assertIn('Retry', manager.public('test', 'editor')['error'])
            manager.close()

    @unittest.skipUnless(os.environ.get('CUTLIST_TEST_SPEECH') == '1', 'Enable actual CPU chapter speech test with CUTLIST_TEST_SPEECH=1')
    def test_real_chapters_publish_before_later_speech_and_reuse_checkpoints(self):
        if not transcription.available(): self.skipTest('Speech weights are missing')
        source = worker.ROOT/'public/sample/studio-notes.mp4'
        total = float(json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(source)]))['format']['duration'])
        request = {'source': str(source), 'duration': total, 'chapter_seconds': 60, 'maximum': 20, 'goal': 'mixed', 'content_review': False, 'has_video': True}
        published = []
        with tempfile.TemporaryDirectory() as folder:
            def publish(clips, coverage):
                published.append(coverage)
                if coverage['sections_complete'] == 1:
                    self.assertFalse((Path(folder)/'chapter-00001/speech.json').exists(), 'First results must precede transcription of the final chapter')
            first = chapters.analyze(request, folder, publish=publish)
            self.assertEqual(len(published), 2)
            self.assertGreater(len(first['speech']['words']), 150)
            self.assertTrue(all(0 <= w['start'] < w['end'] <= total for w in first['speech']['words']))
            with patch.object(chapters, 'run_child', side_effect=AssertionError('Completed speech must not run again')):
                second = chapters.analyze(request, folder)
            self.assertEqual(first['speech']['words'], second['speech']['words'])


class PowerApiTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiTest.asyncSetUp
    asyncTearDown = fixtures.ApiTest.asyncTearDown
    run_job = fixtures.ApiTest.run_job
    sample_job = fixtures.ApiTest.sample_job

    @unittest.skipUnless(os.environ.get('CUTLIST_TEST_SPEECH') == '1', 'Enable actual chapter API workflow with CUTLIST_TEST_SPEECH=1')
    async def test_normal_api_chapter_workflow_publishes_during_transcription(self):
        if not transcription.available(): self.skipTest('Speech model missing')
        response = await self.client.post('/api/jobs', data={'sample': 'true', 'sample_captions': 'false', 'auto_transcribe': 'true', 'content_review': 'false', 'chaptered': 'true', 'chapter_seconds': '60', 'maximum': '30'})
        self.assertEqual(response.status_code, 202, response.text)
        job, seen_first = response.json()['id'], False
        for _ in range(3000):
            state = (await self.client.get(f'/api/jobs/{job}')).json()
            snapshot = state.get('partial_result') or {}
            if snapshot.get('coverage', {}).get('sections_complete') == 1: seen_first = True
            if state['status'] in {'complete', 'error'}: break
            await asyncio.sleep(.03)
        self.assertEqual(state['status'], 'complete', state.get('error'))
        self.assertTrue(seen_first, 'The ordinary API must publish chapter one before final analysis finishes')
        result = state['result']
        self.assertEqual(result['caption_source'], 'automatic')
        self.assertEqual(result['timings']['chapter_count'], 2)
        self.assertFalse(any('No transcript is available' in warning for warning in result['warnings']))
        self.assertTrue(all(0 <= c['start'] < c['end'] <= result['duration'] for c in result['cues']))

    async def test_manual_creation_remove_restore_saved_undo_and_private_transcript(self):
        result = await self.sample_job()
        job = result['id']
        page = (await self.client.get(f'/api/jobs/{job}/transcript?limit=3')).json()
        self.assertEqual(len(page['cues']), 3)
        self.assertIn('word_timing', page)
        for c in result['clips']:
            self.assertEqual((await self.client.delete(f'/api/jobs/{job}/clips/{c["id"]}?revision=0')).status_code, 200)
        made = await self.client.post(f'/api/jobs/{job}/clips', json={'start': 2, 'end': 6, 'title': 'Manual moment'})
        self.assertEqual(made.status_code, 201, made.text)
        clip = made.json()
        endpoint = f'/api/jobs/{job}/clips/{clip["id"]}'
        self.assertEqual(clip['selection_method'], 'manual')
        edited = await self.client.patch(endpoint, json={'revision': 0, 'title': 'Changed title'})
        self.assertEqual(edited.status_code, 200)
        undone = await self.client.post(endpoint+'/undo', json={'revision': 1})
        self.assertEqual(undone.json()['title'], 'Manual moment')
        self.assertEqual(undone.json()['revision'], 2)
        self.assertEqual((await self.client.delete(endpoint+'?revision=2')).status_code, 200)
        restored = (await self.client.post(endpoint+'/restore')).json()
        self.assertEqual(restored['revision'], 3)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker.app), base_url='http://test') as stranger:
            self.assertEqual((await stranger.get(f'/api/jobs/{job}/transcript')).status_code, 404)
            self.assertEqual((await stranger.post(f'/api/jobs/{job}/clips', json={'start': 20, 'end': 22})).status_code, 404)

    async def test_composed_video_export_has_retained_duration_and_matching_subtitles_xml(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'tones.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x180:r=25:d=8', '-f', 'lavfi', '-i', "aevalsrc=0.2*sin(2*PI*if(lt(t\\,4)\\,220\\,440)*t):s=16000:d=8", '-c:v', 'libx264', '-threads', '1', '-c:a', 'aac', '-shortest', str(source)], check=True)
            captions = 'WEBVTT\n\n00:00.000 --> 00:02.000\nFirst retained sentence.\n\n00:02.000 --> 00:06.000\nRemoved mistake sentence.\n\n00:06.000 --> 00:08.000\nFinal retained sentence.'
            result = await self.run_job(files={'media': ('tones.mp4', source.read_bytes()), 'captions': ('tones.vtt', captions.encode())}, data={'maximum': '5'})
            job = result['id']
            for c in result['clips']: await self.client.delete(f'/api/jobs/{job}/clips/{c["id"]}?revision=0')
            response = await self.client.post(f'/api/jobs/{job}/clips', json={'segments': [{'start': 0, 'end': 2}, {'start': 6, 'end': 8}], 'title': 'Joined source intervals'})
            self.assertEqual(response.status_code, 201, response.text)
            clip = response.json(); endpoint = f'/api/jobs/{job}/clips/{clip["id"]}'
            self.assertEqual(clip['output_duration'], 4)
            self.assertEqual((await self.client.post(endpoint+'/export')).status_code, 202)
            export = await fixtures.ApiTest.wait_for_export(self, job, clip['id'])
            target = Path(folder)/'joined.mp4'; target.write_bytes((await self.client.get(export['url'])).content)
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', str(target)]))
            self.assertAlmostEqual(float(info['format']['duration']), 4, delta=.15)
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(target), '-vn', '-ac', '1', '-ar', '16000', '-f', 'f32le', 'pipe:1'])
            samples = np.frombuffer(raw, dtype='<f4')
            def peak(a):
                data = samples[int(a*16000):int((a+1)*16000)]
                return int(np.argmax(abs(np.fft.rfft(data))))
            self.assertAlmostEqual(peak(.3), 220, delta=3)
            self.assertAlmostEqual(peak(2.3), 440, delta=3)
            srt = (await self.client.get(endpoint+'/captions.srt')).text
            self.assertNotIn('Removed mistake', srt)
            self.assertIn('00:00:02,000 --> 00:00:04,000', srt)
            shortlist = (await self.client.get(f'/api/jobs/{job}/shortlist')).text
            self.assertIn('Retained output duration: 4.000s', shortlist)
            self.assertIn('KEEP 00:00:06.000 — 00:00:08.000', shortlist)
            xml = ET.fromstring((await self.client.get(f'/api/jobs/{job}/timeline.xml')).text.split('<!DOCTYPE xmeml>')[-1])
            self.assertEqual(xml.findtext('.//sequence/duration'), '100')
            # A different clip may use a removed gap without overlapping retained footage.
            self.assertEqual((await self.client.post(f'/api/jobs/{job}/clips', json={'start': 3, 'end': 4})).status_code, 201)

    async def test_setup_only_accepts_fixed_actions_and_private_tasks(self):
        self.assertEqual((await self.client.post('/api/setup/tasks', json={'feature': 'editor'})).status_code, 403)
        headers = {'X-Cutlist-Setup': '1'}
        self.assertEqual((await self.client.post('/api/setup/tasks', json={'feature': 'search'}, headers={**headers, 'X-Forwarded-For': '203.0.113.4'})).status_code, 403)
        self.assertEqual((await self.client.post('/api/setup/tasks', json={'feature': 'shell-command'}, headers=headers)).status_code, 422)
        self.assertEqual((await self.client.post('/api/setup/tasks', json={'feature': {}}, headers=headers)).status_code, 422)
        with patch.object(worker.setup_manager, 'install', return_value=None):
            response = await self.client.post('/api/setup/tasks', json={'feature': 'search'}, headers=headers)
            self.assertEqual(response.status_code, 202)
            task = response.json()['id']
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker.app), base_url='http://test') as stranger:
                self.assertEqual((await stranger.get(f'/api/setup/tasks/{task}')).status_code, 404)
            self.assertEqual((await self.client.post(f'/api/setup/tasks/{task}/cancel', headers=headers)).status_code, 200)
        launcher = await self.client.get('/api/setup/launcher')
        self.assertEqual(launcher.status_code, 200)
        self.assertIn('[Desktop Entry]', launcher.text)

    async def test_semantic_search_task_uses_source_transcript_and_is_private(self):
        result = await self.sample_job()
        job = result['id']
        def search(cues, query, maximum, directory, **kwargs):
            self.assertGreater(len(cues), 10)
            self.assertEqual(query, 'Useful creative advice')
            return {'matches': [], 'method': 'semantic', 'message': 'No passage matched the brief.'}
        with patch.object(retrieval, 'search', side_effect=search):
            response = await self.client.post(f'/api/jobs/{job}/search', json={'query': 'Useful creative advice'})
            task = response.json()['id']
            for _ in range(200):
                state = (await self.client.get(f'/api/jobs/{job}/search/{task}')).json()
                if state['status'] not in {'queued', 'running'}: break
                await asyncio.sleep(.01)
            self.assertEqual(state['status'], 'complete', state)
            self.assertEqual(state['result']['matches'], [])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker.app), base_url='http://test') as stranger:
            self.assertEqual((await stranger.get(f'/api/jobs/{job}/search/{task}')).status_code, 404)


if __name__ == '__main__': unittest.main()
