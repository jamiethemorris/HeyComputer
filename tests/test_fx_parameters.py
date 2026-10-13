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

if __name__ == '__main__': unittest.main()
