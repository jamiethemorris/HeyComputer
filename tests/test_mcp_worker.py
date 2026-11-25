import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, PropertyMock, patch

from routing_fixture import OPENROUTER_DEFAULTS
from heycomputer import worker
from test_mcp_responses import inline_provider


def offered(*names):
    schemas = json.loads(Path(__file__).with_name('mcp_083_schemas.json').read_text())
    return [{'name': name, 'inputSchema': schemas[name]} for name in names]


class MCPWorkerTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(inline_provider())
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).parent))
        self.w = worker.Worker(Path(tmp) / 'Data' / 'HeyComputer' / 'session')
        self.w.config.update(OPENROUTER_DEFAULTS)
        self.w.config.update(reaper_mcp_enabled=True, fallback_provider='chatgpt', aliases='BD means Kick')
        self.enterContext(patch.dict(os.environ, HEYCOMPUTER_REAPER_MCP_COMMAND='fixture-only'))
        self.snapshot = dict(project='fixture', revision=2, timeline={})
        self.guard = self.w.directory / 'visual-guard.json'
        self.guard.write_text(json.dumps(dict(self.snapshot, job='j1', recording=False)))
        self.token = self.enterContext(patch.object(self.w, 'chatgpt_token', return_value='fixture-credential'))
        self.legacy = self.enterContext(patch.object(worker.providers, 'plan', return_value={'kind': 'reply', 'message': 'legacy'}))
        self.client = self.enterContext(patch('heycomputer.reaper_mcp_client.ReaperMCPClient', autospec=True))
        self.bridge = self.client.return_value.__enter__.return_value
        self.bridge.list_tools.return_value = offered('track_get_all')
        self.provider = self.enterContext(patch('heycomputer.chatgpt_responses.request_response', return_value={
            'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant',
            'content': [{'type': 'output_text', 'text': 'fixture response'}]}]}))
        # Run the normal IPC completion path inline; no daemon or timing sleeps.
        thread = self.enterContext(patch.object(worker.threading, 'Thread'))
        thread.side_effect = lambda target, args, daemon: Mock(start=lambda: target(*args))

    def request(self, text, kind='plan', **fields):
        self.w.dispatch(dict(text=text, snapshot=self.snapshot, id='j1', kind=kind, **fields))
        return json.loads((self.w.directory / 'j1.response.json').read_text())

    def test_mcp_uses_backend_auth_and_recent_history_without_returning_a_plan(self):
        history = [dict(role='assistant', kind='receipt', text='Kick was created')]
        result = self.request('Mute the BD track', history=history, access_token='untrusted')
        self.assertEqual((result['kind'], result['message']), ('reply', 'fixture response'))
        self.assertFalse(result.get('actions'))
        self.assertEqual(self.client.call_args.args[0], 'fixture-only')
        token, payload = self.provider.call_args.args
        self.assertEqual(token, 'fixture-credential')
        self.assertIn('Kick was created', str(payload['input']))
        self.assertIn('BD means Kick', str(payload['input']))
        self.assertNotIn(token, str(payload) + str(result))
        self.legacy.assert_not_called()

    def test_direct_is_standalone_and_transport_still_interrupts_a_conversation(self):
        history = [dict(kind='reply', role='assistant', text='Which track?')]
        self.w.config['speech_corrections'] = 'stahp = stop'
        result = self.request('stahp', history=history)
        self.assertEqual(result['actions'][0]['value'], 'stop')
        self.token.assert_not_called()
        self.client.assert_not_called()
        text = 'Stop and mute the Kick track'
        self.request(text)
        self.assertIn(text, str(self.provider.call_args.args[1]['input']))

    def test_mcp_answer_does_not_hold_the_next_request_on_the_legacy_planner(self):
        first = self.request('Count the MIDI notes on track one')
        history = [dict(role='assistant', kind='reply', text=first['message'])]
        self.request('Split the second item on track one at 4 seconds', history=history)
        self.assertIn('Split the second item', str(self.provider.call_args.args[1]['input']))
        self.legacy.assert_not_called()

    def test_answer_to_an_mcp_question_returns_to_mcp(self):
        self.provider.return_value['output'][0]['content'][0]['text'] = 'Which item?'
        first = self.request('Split the item on track one at 4 seconds')
        history = [dict(role='assistant', kind='reply', text=first['message'])]
        self.request('The second one', history=history)
        self.assertIn('The second one', str(self.provider.call_args.args[1]['input']))
        self.legacy.assert_not_called()

    def test_legacy_routes_preserve_followup_inspection_and_visual_preparation(self):
        history = [dict(kind='reply', role='assistant', text='Which track?')]
        self.request('The Kick track', history=history)
        self.assertEqual(self.legacy.call_args.args[3], history)
        for kind, op, expected in [('inspect', 'inspect_fx', 'parameter_prepare'),
                                   ('vision', 'open_fx', 'visual_prepare')]:
            self.legacy.return_value = dict(kind=kind, actions=[{'op': op}], message='Adjust the cutoff')
            self.w.config['vision_enabled'] = True
            result = self.request('Create a track and set the filter cutoff to 300 Hz')
            self.assertEqual(result['kind'], expected)
        self.client.assert_not_called()

    def test_compound_request_reaches_model_intact_with_companion_tools(self):
        text = ('Create a Short Room return, copy the reverb, set decay to 800 ms, '
                'send Kick at -6 dB and automate the send to -12 dB at bar 5.')
        self.bridge.list_tools.return_value = offered('transport_get_state')
        result = self.request(text)
        self.assertEqual(result['message'], 'fixture response')
        payload = self.provider.call_args.args[1]
        self.assertEqual(json.loads(payload['input'][0]['content'])['request'], text)
        self.assertTrue({'hc_copy_fx', 'hc_set_fx_param', 'hc_send_create', 'hc_automation_points'}
                        <= {t['name'] for t in payload['tools']})
        self.legacy.assert_not_called()

    def start_panel(self, text, existing_bus=False):
        directory = self.w.directory
        proc = subprocess.Popen(['lua', 'tests/fixtures/mcp_panel.lua', str(directory.parents[2])],
                                cwd=Path(__file__).resolve().parents[1], text=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        assert proc.stdout is not None and proc.stdin is not None
        output, commands = proc.stdout, proc.stdin
        self.addCleanup(output.close)
        self.addCleanup(proc.wait, 3)
        self.addCleanup(commands.close)
        def frame(**fields):
            commands.write(json.dumps(fields) + '\n'); commands.flush()
            return json.loads(output.readline())
        (directory / 'ready.json').write_text('{}')
        self.assertEqual(frame()['pending'], 'settings')
        (directory / 'j1.response.json').write_text(json.dumps(dict(id='j1', kind='settings',
            config=self.w.config, keys={}, chatgpt_status='signed_in')))
        frame(bus=existing_bus)
        self.assertEqual(frame(text=text)['pending'], 'plan')
        path = directory / 'j2.request.json'
        job = json.loads(path.read_text()); path.unlink()
        return frame, job

    def test_panel_worker_compound_trace(self):
        from test_mcp_loop import Client, call, message
        text = ('Create a Short Room return, copy the reverb, set decay to 800 ms, '
                'send Kick at -6 dB and automate the send to -12 dB at bar 5.')
        frame, job = self.start_panel(text)
        mcp = Client(); self.bridge.list_tools.side_effect = mcp.list_tools
        def rpc(name, args):
            if name == 'track_create': frame(bus=True)
            return mcp.call(name, args)
        self.bridge.call.side_effect = rpc
        def edit(op, target, **value):
            return call('hc_' + op, op, target=target, value=json.dumps(value))
        turns = [call('track_get_all', 'list'), call('track_create', 'create', name='Short Room'),
                 call('hc_tracks', '1'), edit('copy_fx', 'src', fx='original', destination='bus', position='last', anchor=''),
                 call('hc_tracks', '2'), call('hc_inspect_fx', 'inspect', target='bus', value='copy', filter='Decay'),
                 edit('set_fx_param', 'bus', fx='copy', index=7, name='Decay', value='800 ms', exact=False),
                 call('hc_tracks', '3'), edit('send_create', 'src', destination='bus', volume_db=-6),
                 call('hc_tracks', '4'), edit('automation_points', 'src', envelope=dict(kind='send_volume', destination='bus'),
                      points=[dict(at={'bar':5}, value='-12 dB', shape='square')], replace=False), message('Ready')]
        self.provider.side_effect = [dict(status='completed', output=[t]) for t in turns]
        def poll(_):
            frame()
            if not (self.w.directory / 'j2.companion-reply.json').exists():
                raise RuntimeError('No panel receipt')  # Never wait on a missing fixture.
        with patch.object(worker.time, 'sleep', side_effect=poll):
            self.w.dispatch(job)
        state = frame()
        self.assertEqual(state['message'], 'Ready')
        self.assertEqual(state['edits'], ['copy_fx', 'set_fx_param', 'send_create', 'automation_points'])
        payload = self.provider.call_args.args[1]['input']
        feedback = {i['call_id']: json.loads(i['output']) for i in payload if i.get('type')=='function_call_output'}
        self.assertEqual(feedback['set_fx_param']['verified']['parameter_values'][0]['value'], '0.8 s')
        self.assertEqual(json.loads(payload[0]['content'])['request'], text)
        self.legacy.assert_not_called()

    def trace_first_native_write(self, after_write):
        from test_mcp_loop import call, message
        frame, job = self.start_panel('Copy the reverb to the return', existing_bus=True)
        self.bridge.list_tools.return_value = offered('transport_get_state')
        self.bridge.call.return_value = dict(recording=False, play_state=0)
        turns = [call('hc_tracks','inventory'), call('hc_copy_fx','edit',target='src',value=json.dumps(
            dict(fx='original', destination='bus', position='last', anchor=''))), message('Copied')]
        self.provider.side_effect = [dict(status='completed', output=[t]) for t in turns]
        path = self.w.directory / 'j2.companion-call.json'
        receipt = self.w.directory / 'j2.companion-reply.json'
        published = []
        def poll(_):
            raw = path.read_bytes()
            name = json.loads(raw)['request']['name']; published.append(name)
            frame()
            if name == 'hc_copy_fx':
                frame()  # Publish the advanced revision before the worker consumes the receipt.
                after_write(frame, path, raw, receipt)
        with patch.object(worker.time, 'sleep', side_effect=poll):
            self.w.dispatch(job)
        state = frame()
        self.assertEqual(state['edits'], ['copy_fx'])
        self.assertEqual(published, ['hc_tracks', 'hc_copy_fx'])
        self.legacy.assert_not_called()
        return state['message']

    def test_first_companion_write_can_advance_revision(self):
        self.assertEqual(self.trace_first_native_write(lambda *_: None), 'Copied')

    def test_uncertain_panel_write_is_not_replayed_or_reported_as_refused(self):
        def redeliver(frame, path, raw, receipt):
            receipt.unlink()  # Lose the actual post-execution receipt.
            path.write_bytes(raw); frame()  # Redeliver the uncertain write.
        self.assertIn('may have applied', self.trace_first_native_write(redeliver))
        self.assertEqual(self.provider.call_count, 2)

    def test_expiry_after_execution_discards_real_success_receipt(self):
        clock = self.enterContext(patch.object(worker.time, 'monotonic', return_value=100))
        def expire(*_):
            clock.return_value += worker.MCP_REQUEST_SECONDS
        result = self.trace_first_native_write(expire)
        self.assertIn('timed out', result); self.assertIn('may have applied', result)
        self.assertEqual(self.provider.call_count, 2)

    def test_lost_read_then_cancellation_never_reinspects(self):
        from test_mcp_loop import call
        frame, job = self.start_panel('Inspect routing')
        self.bridge.list_tools.return_value = offered('transport_get_state')
        self.provider.return_value = dict(status='completed', output=[call('hc_tracks','inventory')])
        counts = []
        def poll(_):
            path = self.w.directory / 'j2.companion-call.json'
            receipt = self.w.directory / 'j2.companion-reply.json'
            if not counts:
                raw = path.read_bytes()
                counts.append(frame()['inspections']); receipt.unlink()
                path.write_bytes(raw); counts.append(frame()['inspections'])
                self.assertEqual(json.loads(receipt.read_text())['receipt']['status'], 'unknown')
                receipt.unlink()
            else:
                self.assertFalse(path.exists())
                self.w.cancel_visual.set()
        with patch.object(worker.time, 'sleep', side_effect=poll):
            self.w.dispatch(job)
        state = frame()
        self.assertIn('cancelled', state['message'])
        self.assertEqual(counts, [state['inspections']] * 2)
        self.provider.assert_called_once(); self.legacy.assert_not_called()

    def test_panel_rejects_stale_call_before_inspection(self):
        # Each case has an actual pending panel job and a syntactically valid read.
        for changes, tick in [({'session':'old'}, {}), ({'job':'j0'}, {}), ({'project':'old'}, {}),
                              ({'expires':1}, {}), ({'revision':99}, {}), ({}, {'project':'other'}),
                              ({}, {'cancel':True}), ({}, {'playing':5}), ({}, {'advance':121})]:
            with self.subTest(changes=changes, tick=tick):
                frame, job = self.start_panel('Inspect routing')
                before = frame()['inspections']
                path = self.w.directory / 'j2.companion-call.json'
                env = dict(job=job['id'], session='session', project=job['snapshot']['project'], ticket='f'*32,
                           expires=int(worker.time.time())+30, request=dict(id='read',name='hc_tracks',arguments={}))
                worker.atomic_json(path, env | changes)
                state = frame(**tick)
                self.assertEqual(state['inspections'], before)
                self.assertFalse(path.exists())
                self.assertFalse(state['edits'])

    def test_mismatched_or_contradictory_receipt_stops_worker(self):
        from test_mcp_loop import call, message
        for key in ('session','job','project','ticket','id','data_project','error'):
            with self.subTest(field=key):
                frame, job = self.start_panel('Inspect routing')
                self.provider.reset_mock(); self.bridge.list_tools.return_value = offered('transport_get_state')
                self.provider.side_effect = [dict(status='completed',output=[t]) for t in
                                             [call('hc_tracks','read'),message('Wrong success')]]
                def poll(_):
                    frame(); path = self.w.directory / 'j2.companion-reply.json'
                    env = json.loads(path.read_text())
                    if key=='data_project': env['receipt']['data']['project']='other'
                    elif key in ('id','error'): env['receipt'][key]='other'
                    else: env[key]='other'
                    worker.atomic_json(path, env)
                with patch.object(worker.time, 'sleep', side_effect=poll):
                    self.w.dispatch(job)
                result = json.loads((self.w.directory / 'j2.response.json').read_text())
                self.assertEqual(result['kind'], 'error', result)
                self.provider.assert_called_once(); self.legacy.assert_not_called(); frame()

    def test_disabled_and_non_boolean_toggle_never_launch(self):
        for value in (False, 'true'):
            self.w.config['reaper_mcp_enabled'] = value
            self.assertEqual(self.request('Mute track one')['message'], 'legacy')
        self.client.assert_not_called()

    def test_missing_account_and_api_key_do_not_launch(self):
        self.token.return_value = None
        self.assertIn('Sign in', self.request('Mute track one')['message'])
        self.w.config['fallback_provider'] = 'openrouter'
        with patch.object(worker.providers, 'api_key', return_value=''):
            self.assertIn('API key', self.request('Mute track one')['message'])
        self.client.assert_not_called()

    def test_recording_foreign_project_changed_revision_and_stale_panel_block_launch(self):
        for overrides in ({'recording': True}, {'project': 'other'}, {'revision': 3}, {'job': 'j0'}, {}):
            with self.subTest(overrides=overrides):
                live = dict(self.snapshot, job='j1', recording=False) | overrides
                self.guard.write_text(json.dumps(live))
                if not overrides:
                    os.utime(self.guard, (1, 1))
                self.assertEqual(self.request('Mute the track')['kind'], 'error')
        self.client.assert_not_called()
        self.provider.assert_not_called()

    def test_voice_transcription_counts_against_the_same_deadline(self):
        clock = self.enterContext(patch.object(worker.time, 'monotonic', return_value=1))
        def transcribe(*args):
            clock.return_value += worker.MCP_REQUEST_SECONDS
            return 'Mute the track'

        with patch.object(self.w.recorder, 'stop', return_value=b'audio'), \
             patch.object(worker.providers, 'transcribe', side_effect=transcribe):
            result = self.request('', kind='record_stop')
        self.assertIn('timed out', result['message'])
        self.client.assert_not_called()

    def test_guard_checked_again_after_model_turn(self):
        response = self.provider.return_value
        def change_project(*args, **kwargs):
            self.guard.write_text(json.dumps(dict(self.snapshot, project='other', recording=False)))
            return response
        self.provider.side_effect = change_project
        self.assertEqual(self.request('Mute track one')['kind'], 'error')
        self.bridge.call.assert_not_called()
        self.client.return_value.__exit__.assert_called_once()
        self.legacy.assert_not_called()

    def test_talk_recording_blocks_mcp_even_if_transport_is_stopped(self):
        with patch.object(worker.Recorder, 'stream', new_callable=PropertyMock, return_value=object()):
            self.assertEqual(self.request('Mute track one')['kind'], 'error')
        self.client.assert_not_called()

    def test_discussion_and_named_plugin_work_remain_with_legacy_planner(self):
        for text in ('How do I mute a track?', 'Insert Pro-Q 4 on the Kick track'):
            with self.subTest(text=text):
                self.assertEqual(self.request(text)['message'], 'legacy')
        self.client.assert_not_called()

    def test_cancel_during_write_is_uncertain_not_a_retry_or_native_plan(self):
        self.bridge.list_tools.return_value = offered('track_get_info', 'track_set_mute', 'transport_get_state')
        def response(name, **args):
            return {'status': 'completed', 'output': [{'type': 'function_call', 'name': name,
                    'call_id': name, 'arguments': json.dumps(args)}]}
        self.provider.side_effect = [response('track_get_info', track_index=0),
                                     response('track_set_mute', track_index=0, mute=True)]
        def rpc(name, args):
            if name == 'transport_get_state':
                return dict(recording=False, play_state=0)
            if name == 'track_set_mute':
                self.w.cancel_visual.set()
            return dict(index=0, name='Kick', mute=self.w.cancel_visual.is_set())
        self.bridge.call.side_effect = rpc
        result = self.request('Mute track one')
        self.assertEqual(result['kind'], 'error')
        self.assertIn('may have applied', result['message'])
        self.assertEqual([c.args[0] for c in self.bridge.call.call_args_list],
                         ['track_get_info', 'track_get_info', 'transport_get_state', 'track_set_mute'])
        self.client.assert_called_once()
        self.legacy.assert_not_called()
