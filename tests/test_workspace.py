import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from app.main import app
from app.jobs import Job, JobStatus, JobStore, job_store, resume_job
from app.workspace import RecordStore, ConflictError, owner_for, workspace_id
from app.model_catalog import OMNI_12


class RecordTests(unittest.TestCase):
    def test_atomic_record_and_stale_update(self):
        with tempfile.TemporaryDirectory() as root:
            store = RecordStore(root)
            version = store.write('owner/library/a.json', {'title': 'first'}, '0')
            store.write('owner/library/a.json', {'title': 'second'}, version)
            with self.assertRaises(ConflictError):
                store.write('owner/library/a.json', {'title': 'stale'}, version)
            self.assertEqual(store.read('owner/library/a.json')[0]['title'], 'second')
            self.assertEqual(len(store.list('owner/library/')), 1)


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = RecordStore(self.temp.name)
        job_store._jobs.clear(); job_store.loaded.clear(); job_store.saved.clear(); job_store.running.clear()
        job_store._lock = asyncio.Lock()
        self.patcher = patch('app.main.default_store', return_value=self.repo)
        self.patcher.start()
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.client.get('/api/workspace')
        self.owner = owner_for(self.client.cookies.get('omni_workspace'))

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.patcher.stop()
        job_store._jobs.clear(); job_store.loaded.clear(); job_store.saved.clear(); job_store.running.clear()
        job_store.repository = None
        self.temp.cleanup()

    def seed(self, id='sample', status=JobStatus.completed, seconds=4, resolution='360p'):
        job = Job(id=id, owner=self.owner, status=status, video_uri='gs://example/video.mp4', duration_sec=seconds,
                  request={'mode': 'generate', 'generate_task': 'text_to_video', 'prompt': 'A paper lantern rises.'},
                  meta={'model': OMNI_12.id, 'mode': 'generate', 'api_task': 'text_to_video', 'output_resolution': resolution})
        job_store._jobs[id] = job
        job_store.loaded.add(self.owner)
        asyncio.run(job_store.save(job))
        return job

    def test_workspace_isolation_including_media_lineage_and_mutations(self):
        job = self.seed()
        self.assertEqual(self.client.get('/api/jobs/sample').status_code, 200)
        self.client.post('/api/workspace', json={'recovery_key': 'b' * 64})
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])
        for path in ['', '/video', '/download', '/lineage', '/export', '/assets/images/0']:
            self.assertEqual(self.client.get('/api/jobs/sample' + path).status_code, 404)
        self.assertEqual(self.client.patch('/api/jobs/sample', json={'version': 0, 'title': 'no'}).status_code, 404)
        self.assertEqual(self.client.get('/api/video', params={'uri': job.video_uri}).status_code, 404)

    def test_library_and_complete_inputs_survive_store_restart(self):
        self.seed()
        response = self.client.patch('/api/jobs/sample', json={'version': 0, 'title': 'Lantern', 'collection': 'Launch', 'favorite': True})
        self.assertEqual(response.status_code, 200)
        job_store._jobs.clear(); job_store.loaded.clear()
        job = self.client.get('/api/jobs/sample').json()
        self.assertEqual(job['library']['title'], 'Lantern')
        self.assertTrue(job['library']['favorite'])
        self.assertEqual(job['request']['prompt'], 'A paper lantern rises.')
        self.assertEqual(self.client.get('/api/jobs/sample/export').json()['request'], job['request'])

    def test_library_conflict_trash_restore_and_worker_save_do_not_lose_title(self):
        job = self.seed()
        self.client.patch('/api/jobs/sample', json={'version': 0, 'title': 'Keep this', 'trashed': True})
        self.assertEqual(self.client.patch('/api/jobs/sample', json={'version': 0, 'title': 'Stale'}).status_code, 409)
        job.touch(JobStatus.completed, 'Worker finished later')
        asyncio.run(job_store.save(job))
        restored = self.client.patch('/api/jobs/sample', json={'version': 1, 'trashed': False}).json()
        self.assertEqual(restored['library']['title'], 'Keep this')
        self.assertFalse(restored['library']['trashed'])
        self.assertEqual(restored['library']['version'], 2)

    def test_running_task_cannot_be_trashed(self):
        self.seed(status=JobStatus.generating)
        response = self.client.patch('/api/jobs/sample', json={'version': 0, 'trashed': True})
        self.assertEqual(response.status_code, 409)

    def test_idempotent_submission_and_changed_request_conflict(self):
        async def fake(job, **kwargs):
            job.meta['model'] = kwargs['model']
            job.touch(JobStatus.completed, 'Controlled test')
        with patch('app.main.run_generation_job', side_effect=fake) as run:
            body = {'prompt': 'A paper lantern', 'request_id': 'same-request-001', 'generate_task': 'text_to_video'}
            a = self.client.post('/api/generate', json=body)
            b = self.client.post('/api/generate', json=body)
            self.assertEqual(a.status_code, 200, a.text)
            self.assertEqual(a.json()['id'], b.json()['id'])
            self.assertEqual(run.call_count, 1)
            c = self.client.post('/api/generate', json={**body, 'prompt': 'Changed prompt'})
            self.assertEqual(c.status_code, 409)
            self.assertEqual(self.client.get('/api/submissions/same-request-001').json()['id'], a.json()['id'])
            self.assertEqual(self.client.get('/api/submissions/never-sent-0001').status_code, 404)

    def test_expired_interaction_uses_saved_clip_and_illegal_upscale_rejected(self):
        job = self.seed()
        job.interaction_id = 'expired-interaction'
        job.created_at = '2026-01-01T00:00:00+00:00'
        with patch('app.main.run_generation_job', new_callable=AsyncMock) as run:
            response = self.client.post('/api/generate', json={'mode': 'edit', 'prompt': 'Warm lighting', 'source_job_id': job.id})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIsNone(run.call_args.kwargs['previous_interaction_id'])
            self.assertEqual(run.call_args.kwargs['source_video_uri'], job.video_uri)
        bad = self.client.post('/api/generate', json={'mode': 'upscale', 'source_job_id': job.id, 'resolution': '4k'})
        self.assertEqual(bad.status_code, 400)

    def test_long_source_and_incomplete_frames_rejected_before_paid_call(self):
        self.seed(seconds=20)
        with patch('app.main.run_generation_job', new_callable=AsyncMock) as run:
            response = self.client.post('/api/generate', json={'mode': 'edit', 'prompt': 'Warm lighting', 'source_job_id': 'sample'})
            self.assertEqual(response.status_code, 400)
            frames = self.client.post('/api/generate', json={'prompt': 'Move', 'generate_task': 'frames_to_video', 'reference_images': [{'uri': 'gs://example/frame.png'}]})
            self.assertEqual(frames.status_code, 400)
            run.assert_not_called()

    def test_recovery_file_restores_same_private_workspace(self):
        self.seed()
        exported = self.client.get('/api/workspace/export').json()
        self.client.post('/api/workspace', json={'recovery_key': 'c'*64})
        self.assertEqual(self.client.get('/api/jobs').json()['jobs'], [])
        response = self.client.post('/api/workspace', json={'recovery_key': exported['recovery_key']})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/jobs/sample').status_code, 200)

    def test_unknown_submission_after_restart_is_not_automatically_reissued(self):
        self.seed(status=JobStatus.submitting)
        job_store._jobs.clear(); job_store.loaded.clear()
        with patch('app.omni_client.create_interaction', new_callable=AsyncMock) as create:
            job = self.client.get('/api/jobs/sample').json()
            self.assertEqual(job['status'], 'interrupted')
            create.assert_not_called()

    def test_cross_origin_mutation_is_rejected(self):
        response = self.client.post('/api/workspace', headers={'Origin': 'https://unrelated.example'}, json={'recovery_key': 'd'*64})
        self.assertEqual(response.status_code, 403)

    def test_page_and_scripts_are_revalidated_and_api_is_never_stored(self):
        for path in ['/', '/docs', '/static/app.js']:
            self.assertEqual(self.client.get(path).headers['cache-control'], 'no-cache', path)
        self.assertEqual(self.client.get('/api/jobs').headers['cache-control'], 'private, no-store')

    def test_same_site_mutations_pass_behind_a_front_end_or_a_local_tunnel(self):
        key = {'recovery_key': 'd' * 64}
        same = self.client.post('/api/workspace', headers={'Origin': 'http://testserver'}, json=key)
        fronted = self.client.post('/api/workspace', headers={'Origin': 'https://demo.example', 'X-Forwarded-Host': 'demo.example'}, json=key)
        tunnel = self.client.post('/api/workspace', headers={'Origin': 'http://localhost:8080'}, json=key)
        self.assertEqual([same.status_code, fronted.status_code, tunnel.status_code], [200, 200, 200])

    def test_reopening_a_workspace_does_not_rewrite_unchanged_records(self):
        self.seed()
        job_store._jobs.clear(); job_store.loaded.clear(); job_store.saved.clear()
        with patch.object(self.repo, 'write', wraps=self.repo.write) as write:
            self.assertEqual(self.client.get('/api/jobs/sample').status_code, 200)
            asyncio.run(job_store.save(job_store._jobs['sample']))
            write.assert_not_called()
            job_store._jobs['sample'].touch(JobStatus.completed, 'Changed later')
            asyncio.run(job_store.save(job_store._jobs['sample']))
            self.assertEqual(write.call_count, 1)

    def test_first_response_already_describes_the_task(self):
        source = self.seed()
        with patch('app.main.run_generation_job', new_callable=AsyncMock):
            edit = self.client.post('/api/generate', json={'mode': 'edit', 'prompt': 'Warm lighting', 'source_job_id': source.id, 'model': OMNI_12.id}).json()
            first = self.client.post('/api/generate', json={'prompt': 'A lantern', 'generate_task': 'text_to_video', 'model': OMNI_12.id}).json()
        self.assertEqual([edit['mode'], edit['source_job_id'], edit['model']], ['edit', source.id, OMNI_12.id])
        self.assertEqual([first['mode'], first['generate_task'], first['model']], ['generate', 'text_to_video', OMNI_12.id])

    def test_download_and_reference_report_a_clear_error_when_signing_is_unavailable(self):
        job = self.seed()
        job.meta['gcs_ref_items'] = [{'uri': 'gs://example/ref.png', 'mime_type': 'image/png'}]
        with patch('app.main.sign_gcs_read_url', side_effect=RuntimeError('signing unavailable')), patch('app.gcs_util.download_gcs_bytes', side_effect=RuntimeError('storage unavailable')):
            for path in ['/download', '/assets/images/0', '/video']:
                response = self.client.get('/api/jobs/sample' + path)
                self.assertEqual(response.status_code, 502, path)
                self.assertNotIn('signing unavailable', response.text)
                self.assertNotIn('storage unavailable', response.text)
        with patch('app.main.sign_gcs_read_url', side_effect=RuntimeError('signing unavailable')), patch('app.gcs_util.download_gcs_bytes', return_value=b'png-bytes'):
            direct = self.client.get('/api/jobs/sample/assets/images/0')
            self.assertEqual([direct.status_code, direct.content, direct.headers['content-type']], [200, b'png-bytes', 'image/png'])


class StoreHelperTests(unittest.TestCase):
    def test_signed_url_is_reused_and_download_is_signed_separately(self):
        import app.gcs_util as gcs
        gcs._signed.clear()
        with patch('app.gcs_util._sign_gcs_read_url', side_effect=lambda uri, minutes=None, download=None: f'https://signed/{uri[5:]}?d={download}&n={len(calls)}') as sign:
            calls = sign.call_args_list
            a = gcs.sign_gcs_read_url('gs://b/clip.mp4')
            b = gcs.sign_gcs_read_url('gs://b/clip.mp4')
            c = gcs.sign_gcs_read_url('gs://b/clip.mp4', download='omni-1.mp4')
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertEqual(sign.call_count, 2)
        gcs._signed.clear()

    def test_record_store_reuses_one_token_for_a_burst(self):
        import app.workspace as ws
        ws._token.update(value='', until=0.0)
        with patch('app.omni_client._get_access_token', return_value='token-1') as token:
            self.assertEqual([ws._bearer(), ws._bearer(), ws._bearer()], ['token-1'] * 3)
            self.assertEqual(token.call_count, 1)
        ws._token.update(value='', until=0.0)

    def test_cloud_records_share_one_connection_pool_and_keep_the_generation_check(self):
        import app.workspace as ws

        class Reply:
            def __init__(self, status, body=None, headers=None):
                self.status_code, self.body, self.headers = status, body or {}, headers or {}

            def json(self):
                return self.body

            def raise_for_status(self):
                assert self.status_code < 400, self.status_code

        class Http:
            def __init__(self):
                self.calls = []

            def get(self, url, **kw):
                self.calls.append(('GET', url, kw))
                return Reply(200, {'title': 'Boat'}, {'x-goog-generation': '7'})

            def post(self, url, **kw):
                self.calls.append(('POST', url, kw))
                return Reply(412) if kw['params'].get('ifGenerationMatch') == 'old' else Reply(200, {'generation': '8'})

        http = Http()
        store = RecordStore('gs://bucket/omni-output/workspaces')
        with patch('app.omni_client._requests_session', return_value=http) as opened, patch.object(ws, '_bearer', return_value='token-1'), \
                patch.object(http, 'mount', create=True):
            self.assertEqual(store.read('o/library/a.json'), ({'title': 'Boat'}, '7'))
            self.assertEqual(store.write('o/library/a.json', {'title': 'Red boat'}, '7'), '8')
            with self.assertRaises(ConflictError):
                store.write('o/library/a.json', {'title': 'stale'}, 'old')
        self.assertEqual(opened.call_count, 1)
        self.assertEqual([c[0] for c in http.calls], ['GET', 'POST', 'POST'])
        self.assertTrue(all(c[2]['headers'] == {'Authorization': 'Bearer token-1'} for c in http.calls))
        self.assertEqual(http.calls[1][2]['params']['ifGenerationMatch'], '7')
        self.assertEqual(http.calls[1][2]['params']['name'], 'omni-output/workspaces/o/library/a.json')

    def test_create_error_reads_as_one_line(self):
        from app.omni_client import _api_error_text

        class Reply:
            text = '{"error":{"message":"Model x is not supported by the Interactions API.","code":"invalid_request"}}'

            def json(self):
                import json
                return json.loads(self.text)

        self.assertEqual(_api_error_text(Reply()), 'invalid_request: Model x is not supported by the Interactions API.')

        class Plain(Reply):
            text = 'upstream unavailable'

            def json(self):
                raise ValueError('not json')

        self.assertEqual(_api_error_text(Plain()), 'upstream unavailable')


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_polls_original_id_and_never_generates(self):
        job = Job(id='recover', interaction_id='original', status=JobStatus.generating)
        result = {'id': 'original', 'status': 'completed'}
        with patch('app.omni_client.get_interaction', new_callable=AsyncMock, return_value=result) as get, patch('app.jobs._finalize_interaction', new_callable=AsyncMock) as finish, patch('app.omni_client.create_interaction', new_callable=AsyncMock) as create:
            await resume_job(job)
            get.assert_awaited_once_with('original')
            finish.assert_awaited_once_with(job, result)
            create.assert_not_called()
