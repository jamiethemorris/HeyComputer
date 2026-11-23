import json
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, PropertyMock, patch

from heycomputer.protocol import PlanError
from heycomputer.settings import PRESETS
from heycomputer.worker import Recorder, Worker


class ChatGPTWorkerTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(directory) / 'Data' / 'HeyComputer'
        self.worker = Worker(self.root / 'session')
        self.auth = SimpleNamespace(account_status=Mock(return_value='Signed out'), sign_in=Mock(), sign_out=Mock(),
                                    access_token=Mock(side_effect=AssertionError('Account IPC must not load a token')))
        self.enterContext(patch.dict(sys.modules, {'heycomputer.chatgpt_auth': self.auth}))
        self.enterContext(patch('heycomputer.worker.providers.api_key', return_value=''))
        self.enterContext(patch.object(self.worker.recorder, 'devices', return_value=[]))
        self.enterContext(patch.object(self.worker.speaker, 'devices', return_value=[]))
        self.enterContext(patch.object(self.worker.speaker, 'voices', return_value=[]))
        self.enterContext(patch.object(self.worker, 'get_desktop', side_effect=AssertionError('No desktop access')))
        self.next_id = 0

    def request(self, kind, **fields):
        self.next_id += 1
        job_id = f'j{self.next_id}'
        path = self.worker.directory / (job_id + '.response.json')
        self.worker.dispatch(dict(fields, id=job_id, kind=kind))
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(path.is_file(), f'worker did not answer {kind}')
        return json.loads(path.read_text())

    def test_account_buttons_only_use_runtime_root_and_publish_status(self):
        self.assertEqual(self.request('settings')['chatgpt_status'], 'signed_out')
        self.auth.sign_in.side_effect = lambda root: setattr(self.auth.account_status, 'return_value', 'Signed in')
        self.auth.sign_out.side_effect = lambda root: setattr(self.auth.account_status, 'return_value', 'Signed out')
        for kind, status in [('chatgpt_sign_in', 'signed_in'), ('chatgpt_status', 'signed_in'), ('chatgpt_sign_out', 'signed_out')]:
            with self.subTest(kind=kind):
                result = self.request(kind, root='/not-the-runtime')
                self.assertEqual(result, {'id': result['id'], 'kind': 'chatgpt_account', 'status': status})
        self.auth.sign_in.assert_called_once_with(self.root)
        self.auth.sign_out.assert_called_once_with(self.root)
        self.assertTrue(all(call.args == (self.root,) for call in self.auth.account_status.call_args_list))
        self.auth.access_token.assert_not_called()
        self.assertFalse(self.worker.config_path.exists())

    def test_account_failure_and_unknown_status_never_echo_auth_details(self):
        fixture_detail = 'synthetic-secret-auth-detail'
        self.auth.sign_in.side_effect = PlanError(fixture_detail)
        self.auth.account_status.return_value = fixture_detail
        failure = self.request('chatgpt_sign_in')
        self.assertEqual(failure['kind'], 'chatgpt_account')
        self.assertEqual(failure['status'], 'unavailable')
        self.assertIn('sign in', failure['message'].lower())
        self.assertEqual(self.request('settings')['chatgpt_status'], 'unavailable')
        ipc = ''.join(path.read_text() for path in self.worker.directory.glob('*.json'))
        self.assertNotIn(fixture_detail, ipc)

    def test_account_mutation_is_refused_while_microphone_is_recording(self):
        with patch.object(Recorder, 'stream', new_callable=PropertyMock, return_value=object()):
            failure = self.request('chatgpt_sign_in')
        self.assertEqual(failure['kind'], 'error')
        self.assertIn('recording', failure['message'].lower())
        self.auth.sign_in.assert_not_called()

    def test_chatgpt_save_pins_endpoint_and_excludes_credential_fields(self):
        preset = PRESETS['fallback']['chatgpt']
        result = self.request('save_settings', config={'fallback_provider': 'chatgpt',
            'fallback_endpoint': 'https://example.invalid/path', 'access_token': 'not-a-credential'})
        saved = json.loads(self.worker.config_path.read_text())
        self.assertEqual(result['config'], saved)
        self.assertEqual(saved['fallback_endpoint'], preset['endpoint'])
        self.assertEqual(saved['fallback_model'], preset['model'])
        self.assertNotIn('access_token', saved)

    def test_planner_and_parameter_continuation_get_backend_only_credential(self):
        self.worker.config['fallback_provider'] = 'chatgpt'
        self.auth.access_token.configure_mock(side_effect=None, return_value='synthetic-bearer')
        reply = {'kind': 'reply', 'message': 'Fixture reply'}
        with patch('heycomputer.worker.providers.plan', return_value=dict(reply)) as primary, \
             patch('heycomputer.worker.providers.openai_plan', return_value=dict(reply)) as continuation:
            initial = self.request('plan', text='fixture', root='/not-the-runtime', access_token='ignore-me')
            prep = {'id': 'j2', 'created': time.time(), 'project': 'fixture', 'text': 'inspect'}
            with patch.object(self.worker, 'parameter_preparation', prep):
                followup = self.request('parameter_continue', snapshot={'project': 'fixture', 'fx_parameters': [{}]})
        self.assertEqual(initial['kind'], 'reply')
        self.assertEqual(followup['kind'], 'reply')
        for planner in (primary, continuation):
            self.assertEqual(planner.call_args.kwargs, {'access_token': 'synthetic-bearer'})
            self.assertNotIn('synthetic-bearer', str(planner.call_args.args))
        self.assertEqual([call.args for call in self.auth.access_token.call_args_list], [(self.root,), (self.root,)])
        self.assertNotIn('synthetic-bearer', json.dumps([initial, followup]))

    def test_chatgpt_auth_failure_leaves_native_quick_plans_available(self):
        self.worker.config['fallback_provider'] = 'chatgpt'
        self.auth.access_token.side_effect = PlanError('private auth failure')
        result = self.request('plan', text='stop', snapshot={'timeline': {}})
        self.assertEqual(result['kind'], 'plan')
        self.assertEqual(result['actions'][0]['op'], 'transport')

    def test_chatgpt_visual_control_never_opens_desktop_without_a_credential(self):
        self.worker.config.update(fallback_provider='chatgpt', vision_enabled=True)
        self.auth.access_token.side_effect = PlanError('private auth failure')
        with patch('heycomputer.worker.providers.plan', return_value={'kind': 'vision'}):
            result = self.request('plan', text='fixture')
        self.assertEqual(result['kind'], 'error')
        self.assertIn('Sign in', result['message'])
        self.assertNotIn('private auth failure', result['message'])

    def test_visual_control_passes_credential_only_to_observation_request(self):
        self.worker.config.update(fallback_provider='chatgpt', vision_enabled=True)
        self.auth.access_token.configure_mock(side_effect=None, return_value='synthetic-bearer')
        reply = {'kind': 'visual_done', 'message': 'Fixture done'}

        def run(text, history, config, desktop, cancelled, guard, progress, *, access_token, **kwargs):
            self.assertEqual(access_token, 'synthetic-bearer')
            self.assertNotIn('synthetic-bearer', json.dumps(config))
            self.assertFalse(cancelled.is_set())
            guard()
            return reply

        (self.worker.directory / 'visual-guard.json').write_text(
            json.dumps({'project': 'fixture', 'revision': 1, 'recording': False}))
        with patch('heycomputer.worker.providers.plan', return_value={'kind': 'vision'}), \
             patch.object(self.worker, 'get_desktop', return_value=object()), \
             patch('heycomputer.vision.run', side_effect=run):
            result = self.request('plan', text='fixture', snapshot={'project': 'fixture', 'revision': 1})
        self.assertEqual(result['kind'], 'visual_done')
        self.assertNotIn('synthetic-bearer', json.dumps(result))

    def test_signed_in_without_usage_and_unconfirmed_revocation_are_visible(self):
        self.auth.account_status.return_value = 'Signed in (ChatGPT plan usage not enabled)'
        self.assertEqual(self.request('chatgpt_status')['status'], 'usage_disabled')

        def unconfirmed(root):
            self.auth.account_status.return_value = 'Signed out'
            return 'Signed out locally; remote revocation was not confirmed. Disconnect the app in ChatGPT Settings.'

        self.auth.sign_out.side_effect = unconfirmed
        result = self.request('chatgpt_sign_out')
        self.assertEqual(result['status'], 'signed_out')
        self.assertIn('disconnect', result['message'].lower())

    def test_chatgpt_catalog_only_returns_picker_fields(self):
        self.auth.access_token.configure_mock(side_effect=None, return_value='synthetic-bearer')
        models = [{'slug': 'model-a', 'display_name': 'Model A', 'private': 'synthetic-bearer'},
                  {'slug': 'model-b', 'display_name': 'synthetic-bearer'}]
        with patch('heycomputer.worker.providers.chatgpt_list_models', return_value=models) as catalog:
            result = self.request('chatgpt_models')
        self.assertEqual(result['kind'], 'chatgpt_models')
        self.assertEqual(result['models'], [{'slug': 'model-a', 'display_name': 'Model A'}])
        catalog.assert_called_once_with(self.auth.access_token.return_value)
        self.auth.access_token.assert_called_once_with(self.root)

    def test_chatgpt_catalog_requires_account_without_leaking_auth_failure(self):
        self.auth.access_token.side_effect = PlanError('private auth detail')
        with patch('heycomputer.worker.providers.chatgpt_list_models') as catalog:
            result = self.request('chatgpt_models')
        self.assertEqual(result['kind'], 'chatgpt_models')
        self.assertEqual(result['models'], [])
        self.assertIn('Sign in', result['message'])
        catalog.assert_not_called()
