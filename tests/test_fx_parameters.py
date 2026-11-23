import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from heycomputer.protocol import validate_result, action, PlanError
from heycomputer.worker import Worker


class ParametersTests(unittest.TestCase):
    def setUp(self):
        self.catalog = {'track': 't1', 'guid': 'fx1', 'parameters': [
            {'index': 2, 'name': 'Band 1 Frequency', 'direct': True}]}
        self.snapshot = {'project': 'p', 'tracks': [{'guid': 't1', 'fx_instances': [{'guid': 'fx1'}]}],
                         'fx_parameters': [self.catalog]}
        self.value = {'fx': 'fx1', 'index': 2, 'name': 'Band 1 Frequency', 'value': '300 Hz', 'exact': False}

    def plan(self, value=None):
        return {'kind': 'plan', 'actions': [action('set_fx_param', 't1', json.dumps(value or self.value))], 'message': '', 'lua': ''}

    def test_only_inspected_parameter_identity_is_accepted(self):
        validate_result(self.plan(), self.snapshot)
        for changes in ({'fx': 'other'}, {'index': True}, {'name': 'Gain'}, {'exact': 'yes'}, {'value': ''}, {'extra': 1}):
            with self.assertRaises(PlanError):
                validate_result(self.plan(dict(self.value, **changes)), self.snapshot)
        with self.assertRaises(PlanError):
            validate_result(self.plan(), dict(self.snapshot, fx_parameters=[]))

    def test_inspection_requires_remaining_task_and_exact_instance(self):
        result = {'kind': 'inspect', 'actions': [action('inspect_fx', 't1', 'fx1')], 'message': 'Set 300 Hz exactly', 'lua': ''}
        validate_result(result, self.snapshot)
        with self.assertRaises(PlanError): validate_result(dict(result, message=''), self.snapshot)
        with self.assertRaises(PlanError): validate_result(dict(result, kind='plan'), self.snapshot)
        with self.assertRaises(PlanError): validate_result(dict(result, lua='code'), self.snapshot)

    def response(self, worker, job):
        path = worker.directory / (job['id'] + '.response.json')
        path.unlink(missing_ok=True)
        worker.dispatch(job)
        deadline = time.monotonic() + 3
        while not path.exists() and time.monotonic() < deadline: time.sleep(.01)
        return json.loads(path.read_text())

    def test_inspect_then_plan_is_one_use_and_does_not_call_visual_model(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory)/'session')
            preparation = {'kind': 'inspect', 'actions': [action('inspect_fx', 't1', 'fx1')],
                           'message': 'Set 300 Hz exactly; then open this instance', 'lua': '', 'model': 'fixture'}
            with patch('heycomputer.worker.providers.plan', return_value=preparation), \
                 patch('heycomputer.worker.providers.openai_plan', return_value=self.plan()) as planner, \
                 patch.object(worker, 'visual_plan') as visual:
                r = self.response(worker, {'id':'j1','kind':'plan','text':'test','snapshot':dict(self.snapshot, fx_parameters=None)})
                self.assertEqual(r['kind'],'parameter_prepare')
                r = self.response(worker, {'id':'j1','kind':'parameter_continue','snapshot':self.snapshot})
                self.assertEqual(r['kind'],'plan')
                self.assertIn('exactly',planner.call_args.args[0])
                visual.assert_not_called()
                r = self.response(worker, {'id':'j1','kind':'parameter_continue','snapshot':self.snapshot})
                self.assertEqual(r['kind'],'error')

    def test_guitar_delay_send_reaches_inspection_through_real_planner(self):
        text = ('Put a send on my guitar track with Echo Boy set to 100% wet and '
                'set it to an eighth note ping pong delay, eighth note on both sides.')
        remaining = 'Set 100% wet and ping pong delay, eighth note on both sides.'
        snapshot = {'project': 'p', 'timeline': {'play_state': 0},
                    'tracks': [{'guid': 'guitar', 'selected': True}],
                    'plugins': [{'id': 'VST3: EchoBoy', 'name': 'VST3: EchoBoy'}]}
        preparation = {'kind': 'inspect', 'message': remaining, 'lua': '', 'actions': [
            action('create_tracks', value='Guitar Delay', count=1),
            action('add_fx', 'created', 'VST3: EchoBoy'),
            action('send_create', 'guitar', json.dumps({'destination': 'first_created'})),
            action('inspect_fx', 'created', 'last_added')]}
        response = {'status': 'completed', 'output': [{'type': 'message', 'content': [
            {'type': 'output_text', 'text': json.dumps(preparation)}]}]}
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory) / 'session')
            worker.config['use_jev'] = False
            with patch('heycomputer.providers.api_key', return_value='fixture'), \
                 patch('heycomputer.providers.request_json', return_value=response) as request, \
                 patch.object(worker, 'visual_plan') as visual:
                result = self.response(worker, {'id': 'delay', 'kind': 'plan',
                                               'text': text, 'snapshot': snapshot})
            self.assertEqual(result['kind'], 'parameter_prepare', result)
            self.assertEqual(result['actions'], preparation['actions'])
            assert worker.parameter_preparation is not None
            self.assertEqual(worker.parameter_preparation['text'], remaining)
            self.assertEqual(json.loads(request.call_args.args[2]['input'])['request'], text)
            visual.assert_not_called()

    def test_inspection_loop_is_bounded_and_visual_fallback_preserves_task(self):
        with tempfile.TemporaryDirectory() as directory:
            worker = Worker(Path(directory)/'session')
            worker.parameter_preparation = {'id':'j1','created':time.time(),'project':'p','text':'300 Hz exactly'}
            result = {'kind':'vision','actions':[action('open_fx','t1','fx1')], 'message':'Set 300 Hz exactly','lua':''}
            with patch('heycomputer.worker.providers.openai_plan',return_value=result):
                r=self.response(worker,{'id':'j1','kind':'parameter_continue','snapshot':self.snapshot})
            self.assertEqual(r['kind'],'visual_prepare')
            self.assertEqual(worker.visual_preparation['text'],'Set 300 Hz exactly')
            worker.parameter_preparation={'id':'j2','created':time.time(),'project':'p','text':'test'}
            with patch('heycomputer.worker.providers.openai_plan',return_value=dict(result,kind='inspect')):
                r=self.response(worker,{'id':'j2','kind':'parameter_continue','snapshot':self.snapshot})
            self.assertEqual(r['kind'],'error')

    def test_send_setup_allowed_before_inspection_and_visual_open(self):
        snapshot = dict(self.snapshot, timeline={'play_state': 0})
        snapshot['tracks'] = self.snapshot['tracks'] + [{'guid': 'guitar'}]
        for kind, terminal in (('inspect', 'inspect_fx'), ('vision', 'open_fx')):
            for op in ('send_create', 'send_set'):
                with self.subTest(kind=kind, op=op):
                    result = {'kind': kind, 'message': 'Set the delay to 100% wet.', 'lua': '',
                              'actions': [action(op, 'guitar', json.dumps({
                                  'destination': 't1', 'volume_db': -12})),
                                  action(terminal, 't1', 'fx1')]}
                    self.assertEqual(validate_result(result, snapshot, allow_vision=True), result)

    def test_send_preparation_preserves_validation_and_operation_limits(self):
        snapshot = dict(self.snapshot, timeline={'play_state': 0})
        snapshot['tracks'] = self.snapshot['tracks'] + [{'guid': 'guitar'}]
        for kind, terminal in (('inspect', 'inspect_fx'), ('vision', 'open_fx')):
            invalid = [action('send_create', 'missing', '{"destination":"t1"}'),
                       action('send_create', 'guitar', '{"destination":"missing"}'),
                       action('send_create', 'guitar', '{"destination":"created"}'),
                       action('send_create', 'guitar', '{"destination":"result:3"}'),
                       action('send_create', 'guitar', '{"destination":"t1","volume_db":99}'),
                       action('send_create', 'guitar', '{"destination":"t1","mode":"hardware"}'),
                       action('send_set', 'guitar', '{"destination":"t1"}'),
                       action('send_delete', 'guitar', '{"destination":"t1"}'),
                       action('transport', 'project', 'record'),
                       action('set_fx_param', 't1', json.dumps(self.value))]
            for operation in invalid:
                with self.subTest(kind=kind, operation=operation), self.assertRaises(PlanError):
                    validate_result({'kind': kind, 'message': 'Set delay.', 'lua': '',
                                     'actions': [operation, action(terminal, 't1', 'fx1')]},
                                    snapshot, allow_vision=True)

if __name__ == '__main__': unittest.main()
