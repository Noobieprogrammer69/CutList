import asyncio
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

import httpx
import numpy as np

import test_api as fixtures
from worker import main as worker
from worker.captions import Cue, parse_captions
from worker.editing import ass_subtitles, caption_style, subtitle_text
from worker.hardware import model_options, report
from worker.quality import evaluate
from worker.semantic import Reviewer, Unavailable, select
from worker.speakers import assign
from worker.tracking import crop_expression, smooth_track
from worker.visual import VisualContext, frame
from test_semantic import CUES, PROPOSAL


class EditorialTest(unittest.TestCase):
    def test_editorial_http_adapter_checks_context_and_handles_truncation(self):
        calls = []
        def serve(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, json={'done_reason': 'length', 'message': {'content': '{"decisions":[]}'}})
        reviewer = Reviewer(client=httpx.Client(transport=httpx.MockTransport(serve)))
        reviewer.profile = 'gaming'
        with self.assertRaises(Unavailable): reviewer.critique([{'id': 0, 'text': 'Go to the menu.'}], [{'start': 0, 'end': 5}], 30, 'funny')
        self.assertIn('Gaming/streams', calls[0]['messages'][0]['content'])
        self.assertIn('untrusted data', calls[0]['messages'][0]['content'])
        self.assertIn('decisions', calls[0]['format']['properties'])
        reviewer.client.close()

    def test_real_labels_measure_misses_duplicates_boring_and_trim_cost(self):
        labels = [{'id': 'a', 'start': 10, 'end': 20, 'kind': 'good'}, {'id': 'b', 'start': 50, 'end': 60, 'kind': 'good'}, {'start': 80, 'end': 100, 'kind': 'boring'}]
        clips = [{'id': 1, 'start': 10, 'end': 20, 'feedback': 'keep'}, {'id': 2, 'start': 11, 'end': 20, 'original_start': 10, 'feedback': 'keep'}, {'id': 3, 'start': 82, 'end': 90, 'feedback': 'reject'}]
        metrics = evaluate(clips, labels)
        self.assertEqual(metrics['matched'], 1, 'Duplicate predictions cannot both match the same reference')
        self.assertAlmostEqual(metrics['precision'], 1/3)
        self.assertEqual(metrics['recall'], .5)
        self.assertEqual(metrics['boring_hits'], 1)
        self.assertAlmostEqual(metrics['keep_rate'], 2/3)
        self.assertAlmostEqual(metrics['kept_without_trim_rate'], 1/3)
        self.assertIsNone(evaluate(clips, [])['precision'])
        self.assertIsNone(evaluate([], [])['recall'])

    def test_sections_are_published_after_editorial_check_and_cached_by_profile(self):
        log = []
        class Model:
            model = 'test-local'
            def ready(self): return {'available': True, 'digest': 'v1'}
            def review(self, rows, maximum, goal):
                log.append('propose')
                return {'clips': [PROPOSAL]}
            def critique(self, rows, clips, maximum, goal):
                log.append('critic')
                return {'decisions': [{'candidate': 0, 'accept': True, 'context_complete': True, 'payoff_complete': True, 'worth_editing': True, 'reason': 'Specific popcorn reversal.'}]}
            def close(self): pass
        with tempfile.TemporaryDirectory() as folder:
            one = select(CUES, 30, 'funny', folder, reviewer=Model(), profile='gaming', on_partial=lambda clips, coverage: log.append(('partial', len(clips), coverage['sections_complete'])))
            self.assertEqual(log, ['propose', 'critic', ('partial', 1, 1)])
            self.assertEqual(one['proposed'], 1)
            self.assertEqual(one['editorial_rejected'], 0)
            log.clear()
            select(CUES, 30, 'funny', folder, reviewer=Model(), profile='gaming')
            self.assertEqual(log, [])
            select(CUES, 30, 'funny', folder, reviewer=Model(), profile='interview')
            self.assertEqual(log, ['propose', 'critic'])

    def test_cpu_options_and_gpu_request_do_not_claim_a_speedup(self):
        with patch.dict(os.environ, {'CUTLIST_ACCELERATION': 'cpu'}):
            self.assertEqual(model_options()['num_gpu'], 0)
        with patch.dict(os.environ, {'CUTLIST_ACCELERATION': 'gpu'}):
            self.assertNotIn('num_gpu', model_options())
            self.assertEqual(report()['selected'], 'gpu_requested')
            self.assertIn('benchmarked', report()['message'])

    def test_speaker_colors_styles_and_untrusted_subtitle_text(self):
        cues = parse_captions('WEBVTT\n\n00:01.000 --> 00:04.000\n<v Host>Hello there.</v>')
        self.assertEqual(cues[0].speaker, 'Host')
        clip = {'start': 1, 'end': 4, 'captions': [{'start': 1, 'end': 4, 'text': '{\\pos(0,0)}Hello', 'speaker': 'Host'}], 'caption_style': {'size': 30, 'position': 'top', 'color': '#FFD166'}}
        ass = ass_subtitles(clip)
        self.assertIn('Noto Sans,30', ass)
        self.assertIn('｛＼pos(0,0)｝Hello', ass)
        self.assertIn('Host: ', ass)
        self.assertIn('Host: ', subtitle_text(clip))
        for invalid in ({'size': 200}, {'color': "red':movie=/etc/passwd"}, {'show_speakers': 'yes'}):
            with self.assertRaises(ValueError): caption_style(invalid)

    def test_voice_clusters_do_not_invent_names_and_leave_short_or_silent_cues_unknown(self):
        cues = [{'start': i, 'end': i+1, 'text': 'Voice'} for i in range(4)]
        output = assign(cues, [np.array([1., 0.]), np.array([.99, .01]), np.array([0., 1.]), None])
        self.assertEqual(output[0]['speaker'], output[1]['speaker'])
        self.assertNotEqual(output[0]['speaker'], output[2]['speaker'])
        self.assertNotIn('speaker', output[3])
        self.assertEqual(output[0]['speaker_source'], 'voice_model_prediction')

    def test_face_path_is_bounded_smooth_and_missing_faces_are_not_fabricated(self):
        path = smooth_track([(0, [(50, 20, 60, 60)]), (1, [(80, 20, 60, 60)]), (2, []), (3, [(250, 20, 60, 60)])], 480, 270, 9/16)
        self.assertEqual(path[0]['time'], 0)
        self.assertTrue(all(0 <= p['x'] <= 1 for p in path))
        self.assertLess(path[-1]['x'], 1)
        self.assertIn('clip((t-', crop_expression(path, .5))
        self.assertEqual(smooth_track([(0, [])], 480, 270, 9/16), [])

    def test_visual_review_checks_capabilities_and_frame_ids_and_sends_actual_images(self):
        requests = []
        def serve(request):
            requests.append(request)
            if request.url.path == '/api/tags': return httpx.Response(200, json={'models': [{'name': 'local-vision', 'size': 1_900_000_000, 'digest': 'vision-v1'}]})
            if request.url.path == '/api/show': return httpx.Response(200, json={'capabilities': ['completion', 'vision']})
            if request.url.path == '/api/chat':
                data = json.loads(request.content)
                self.assertEqual(len(data['messages'][-1]['images']), 3)
                self.assertGreater(len(data['messages'][-1]['images'][0]), 100)
                return httpx.Response(200, json={'message': {'content': json.dumps({'observations': [{'frame': i, 'description': 'A visible title card.'} for i in range(3)]})}})
            return httpx.Response(200, json={})
        reviewer = Reviewer(model='local-vision', client=httpx.Client(transport=httpx.MockTransport(serve)))
        with tempfile.TemporaryDirectory() as folder:
            visual = VisualContext(worker.ROOT / 'public/sample/studio-notes.mp4', folder, reviewer=reviewer)
            observations = visual.observe(0, 10)
            self.assertEqual(len(observations), 3)
            self.assertTrue(all(0 <= v['time'] <= 10 for v in observations))
            self.assertTrue(all(v['source'] == 'local_vision_prediction' for v in observations))
            self.assertEqual(visual.observe(0, 10), observations)
            self.assertEqual(sum(r.url.path == '/api/chat' for r in requests), 1)
            visual.close()

    def test_nonvision_model_is_never_used_as_visual_understanding(self):
        def serve(request):
            if request.url.path == '/api/tags': return httpx.Response(200, json={'models': [{'name': 'text-only', 'size': 1_000_000_000}]})
            if request.url.path == '/api/show': return httpx.Response(200, json={'capabilities': ['completion']})
            self.fail('A text-only model must never be asked to analyze an image')
        reviewer = Reviewer(model='text-only', client=httpx.Client(transport=httpx.MockTransport(serve)))
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(Unavailable):
            VisualContext(worker.ROOT / 'public/sample/studio-notes.mp4', folder, reviewer=reviewer)
        reviewer.client.close()


class OptionalModelsTest(unittest.TestCase):
    def test_actual_opencv_cpu_no_face_fallback(self):
        from worker.tracking import available, follow_faces
        if not available(): self.skipTest('Optional OpenCV dependency not installed; run npm run tracking:install')
        with self.assertRaisesRegex(ValueError, 'No consistent frontal face'):
            follow_faces(worker.ROOT / 'public/sample/studio-notes.mp4', 0, 3, 9/16)

    def test_actual_speaker_model_cpu_and_supplied_label_preservation(self):
        from worker.speakers import available, annotate
        if not available(): self.skipTest('Optional voice model not installed; run npm run speakers:download')
        cues = parse_captions((worker.ROOT / 'public/sample/studio-notes.vtt').read_text())[:4]
        captions = [{'start': c.start, 'end': c.end, 'text': c.text} for c in cues]
        captions[0]['speaker'] = 'Editor label'
        result = annotate(worker.ROOT / 'public/sample/studio-notes.mp4', {'start': 0, 'end': cues[-1].end, 'captions': captions})
        self.assertEqual(result[0]['speaker'], 'Editor label')
        self.assertTrue(any(c.get('speaker_source') == 'voice_model_prediction' for c in result[1:]))

    @unittest.skipUnless(os.environ.get('CUTLIST_TEST_VISION') == '1', 'Set CUTLIST_TEST_VISION=1 with the optional local vision model installed')
    def test_actual_local_vision_image_support(self):
        from worker.visual import status
        ready = status()
        if not ready['available']: self.skipTest(ready['message'])
        with tempfile.TemporaryDirectory() as folder:
            vision = VisualContext(worker.ROOT / 'public/sample/studio-notes.mp4', folder)
            try:
                observations = vision.observe(0, 8)
                self.assertEqual(len(observations), 3)
                self.assertTrue(all(0 <= r['time'] < 8 and r['description'] for r in observations))
            finally:
                vision.close()


class ImprovementApiTest(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ApiTest.asyncSetUp
    asyncTearDown = fixtures.ApiTest.asyncTearDown
    run_job = fixtures.ApiTest.run_job
    sample_job = fixtures.ApiTest.sample_job

    async def test_quality_labels_preferences_are_private_durable_and_reanalysis_reuses_labels(self):
        result = await self.sample_job()
        job = result['id']
        response = await self.client.post('/api/preferences', json={'caption_style': {'size': 30, 'color': '#FFD166'}})
        self.assertEqual(response.status_code, 200)
        for kind in ('good', 'boring'):
            response = await self.client.post(f'/api/jobs/{job}/labels', json={'start': 5, 'end': 10, 'kind': kind, 'reason': 'Independent editor reference'})
            self.assertEqual(response.status_code, 201)
        report = (await self.client.get(f'/api/jobs/{job}/benchmark')).json()
        self.assertEqual(len(report['labels']), 2)
        self.assertIn('baseline_evaluation', report)
        self.assertEqual((await self.client.post(f'/api/jobs/{job}/labels', json={'start': 0, 'end': result['duration'] + 10, 'kind': 'good'})).status_code, 422)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker.app), base_url='http://test') as stranger:
            self.assertEqual((await stranger.get(f'/api/jobs/{job}/benchmark')).status_code, 404)
            self.assertEqual((await stranger.get('/api/preferences')).json()['caption_style']['size'], 24)
        rerun = await self.run_job(data={'reuse_job': job})
        self.assertEqual(rerun['clips'][0]['caption_style']['size'], 30)
        self.assertEqual(len((await self.client.get(f'/api/jobs/{rerun["id"]}/benchmark')).json()['labels']), 2)
        worker.jobs.pop(job)
        self.assertEqual(len((await self.client.get(f'/api/jobs/{job}/benchmark')).json()['labels']), 2)

    async def test_reviewed_partial_clips_arrive_before_final_and_cannot_be_edited(self):
        entered, release = threading.Event(), threading.Event()
        def review(job, request_file, response_file, on_partial):
            data = json.loads(request_file.read_text())
            self.assertEqual(data['profile'], 'gaming')
            cues = [Cue(**c) for c in data['cues']]
            from worker.analysis import rank_clips, transcript_events
            clips = rank_clips(transcript_events(cues, []), cues, cues[-1].end, 30)[:1]
            clips[0]['editorial_review'] = {'status': 'accepted', 'reason': 'Fixture decision; model transport tested separately.'}
            on_partial(clips, {'sections_complete': 1, 'sections_total': 2, 'through_seconds': 60})
            entered.set()
            release.wait(5)
            return {'clips': clips, 'model': 'test', 'windows': 2}
        with patch.object(worker.semantic, 'status', return_value={'available': True}), patch.object(worker, 'run_content_review', side_effect=review):
            response = await self.client.post('/api/jobs', data={'sample': 'true', 'content_review': 'true', 'profile': 'gaming', 'maximum': '30'})
            job = response.json()['id']
            for _ in range(300):
                if entered.is_set(): break
                await asyncio.sleep(.01)
            try:
                self.assertTrue(entered.is_set())
                state = (await self.client.get(f'/api/jobs/{job}?compact=true')).json()
                self.assertEqual(state['status'], 'running')
                self.assertTrue(state['partial_result']['provisional'])
                self.assertEqual(state['partial_result']['coverage']['sections_complete'], 1)
                unchanged = (await self.client.get(f'/api/jobs/{job}?compact=true&since_sections=1')).json()
                self.assertNotIn('partial_result', unchanged, 'Repeated polling must not resend an unchanged shortlist')
                self.assertEqual(state['partial_result']['cues'], [], 'Do not resend full transcripts during section polling')
                clip = state['partial_result']['clips'][0]
                self.assertEqual((await self.client.patch(f'/api/jobs/{job}/clips/{clip["id"]}', json={'revision': 0})).status_code, 409)
            finally:
                release.set()
            for _ in range(200):
                state = (await self.client.get(f'/api/jobs/{job}')).json()
                if state['status'] == 'complete': break
                await asyncio.sleep(.01)
            self.assertEqual(state['status'], 'complete')
            self.assertFalse(state['result']['provisional'])
            self.assertIsNotNone(state['result']['timings']['first_results_seconds'])

    async def test_batch_zip_renders_saved_cuts_captions_and_manifest(self):
        result = await self.sample_job()
        job = result['id']
        chosen = [c['id'] for c in result['clips'][:2]]
        response = await self.client.post(f'/api/jobs/{job}/batches', json={'clips': chosen})
        self.assertEqual(response.status_code, 202, response.text)
        batch = response.json()['id']
        for _ in range(600):
            status = (await self.client.get(f'/api/jobs/{job}/batches/{batch}')).json()
            if status['status'] in {'ready', 'error'}: break
            await asyncio.sleep(.025)
        self.assertEqual(status['status'], 'ready', status)
        response = await self.client.get(status['url'])
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            self.assertEqual(sum(n.endswith('.mp4') for n in archive.namelist()), len(chosen))
            self.assertEqual(sum(n.endswith('.srt') for n in archive.namelist()), len(chosen))
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual([c['id'] for c in manifest['clips']], chosen)
        self.assertEqual((await self.client.post(f'/api/jobs/{job}/batches', json={'clips': [chosen[0], chosen[0]]})).status_code, 422)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=worker.app), base_url='http://test') as stranger:
            self.assertEqual((await stranger.get(status['url'])).status_code, 404)

    async def test_batch_style_is_atomic_and_rejects_stale_revisions(self):
        result = await self.sample_job()
        job = result['id']
        entries = [{'id': c['id'], 'revision': c['revision']} for c in result['clips'][:2]]
        response = await self.client.post(f'/api/jobs/{job}/batch-style', json={'clips': entries, 'caption_style': {'size': 32}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(all(c['caption_style']['size'] == 32 and c['burn_captions'] and c['revision'] == 1 for c in response.json()['clips']))
        bad = [{'id': entries[0]['id'], 'revision': 1}, {'id': entries[1]['id'], 'revision': 0}]
        self.assertEqual((await self.client.post(f'/api/jobs/{job}/batch-style', json={'clips': bad, 'caption_style': {'size': 16}})).status_code, 409)
        state = (await self.client.get(f'/api/jobs/{job}')).json()
        self.assertEqual(state['result']['clips'][0]['caption_style']['size'], 32, 'A failure must not partially style earlier clips')

    async def test_queued_batch_can_be_cancelled_without_rendering(self):
        result = await self.sample_job()
        job = result['id']
        gate = threading.Event()
        blocker = worker.export_pool.submit(gate.wait, 5)
        try:
            response = await self.client.post(f'/api/jobs/{job}/batches', json={'clips': [result['clips'][0]['id']]})
            batch = response.json()['id']
            self.assertEqual((await self.client.post(f'/api/jobs/{job}/batches/{batch}/cancel')).status_code, 200)
            self.assertEqual((await self.client.get(f'/api/jobs/{job}/batches/{batch}')).json()['status'], 'cancelled')
        finally:
            gate.set()
            blocker.result(timeout=5)

    async def test_styled_speaker_captions_and_moving_crop_render_with_ffmpeg(self):
        result = await self.sample_job()
        job, clip = result['id'], result['clips'][0]
        cues = [{**c, 'speaker': 'Guest'} for c in clip['captions']]
        response = await self.client.patch(f'/api/jobs/{job}/clips/{clip["id"]}', json={'revision': 0, 'captions': cues, 'burn_captions': True,
                          'caption_style': {'size': 28, 'position': 'top', 'color': '#FFD166'}, 'crop': {'format': 'vertical', 'x': .5, 'y': .5, 'resolution': '720', 'tracking': 'face'}})
        self.assertEqual(response.status_code, 200, response.text)
        with patch('worker.exports.follow_faces', return_value=[{'time': 0, 'x': .1}, {'time': 2, 'x': .9}]):
            response = await self.client.post(f'/api/jobs/{job}/clips/{clip["id"]}/export')
            self.assertEqual(response.status_code, 202)
            status = await fixtures.ApiTest.wait_for_export(self, job, clip['id'])
        response = await self.client.get(status['url'])
        self.assertGreater(len(response.content), 1000)
        subtitles = await self.client.get(f'/api/jobs/{job}/clips/{clip["id"]}/captions.srt')
        self.assertIn('Guest:', subtitles.text)


if __name__ == '__main__':
    unittest.main()
