import copy
import json
import unittest
from unittest.mock import patch

from heycomputer import providers
from heycomputer.protocol import action, validate_result, PlanError
from heycomputer.settings import DEFAULTS


class ItemRepeatTests(unittest.TestCase):
    def setUp(self):
        self.s = {'tracks': [{'guid': 'kick', 'item_count': 8}, {'guid': 'vocal', 'item_count': 2, 'item_end': 140}],
                  'plugins': [], 'inputs': [], 'timeline': {'play_state': 0}, 'item_repeat_available': True,
                  'items': [{'guid': 'sample', 'track': 'kick', 'take_type': 'audio', 'start': 0, 'length': .2},
                            {'guid': 'voice', 'track': 'vocal', 'take_type': 'audio', 'start': 0, 'length': 140}]}

    def repeat(self, **kwargs):
        return action('item_repeat', 'sample', json.dumps(kwargs or {'every_qn': 2, 'until_track': 'vocal'}))

    def validate(self, *actions):
        return validate_result(dict(kind='plan', actions=list(actions), message='', lua=''), self.s)

    def test_extend_to_track_item_or_explicit_range(self):
        self.validate(self.repeat())
        self.validate(self.repeat(every_qn=.5, until_item='voice'))
        self.validate(self.repeat(every_qn=2, start={'bar': 3}, end={'bar': 20}))
        self.validate(action('item_gain', 'sample', json.dumps({'db': -6, 'relative': True})), self.repeat())

    def test_malformed_or_unknown_targets_fail(self):
        for params in ({'every_qn': True, 'until_track': 'vocal'}, {'every_qn': 0, 'until_track': 'vocal'},
                       {'every_qn': float('nan'), 'until_item': 'voice'}, {'every_qn': 2},
                       {'every_qn': 2, 'until_item': 'unknown'}, {'every_qn': 2, 'until_track': 'selected'},
                       {'every_qn': 2, 'until_track': []}, {'every_qn': 2, 'until_item': 'voice', 'end': {'bar': 5}},
                       {'every_qn': 2, 'until_track': 'vocal', 'trim': True}):
            with self.subTest(params=params), self.assertRaises(PlanError): self.validate(self.repeat(**params))
        for target in ('selected', 'all', 'missing', 'item_result:1'):
            a=self.repeat(); a['target']=target
            with self.assertRaises(PlanError): self.validate(a)

    def test_locked_midi_empty_endpoint_and_old_panel_fail(self):
        original=copy.deepcopy(self.s)
        for mode in ('locked', 'midi', 'empty', 'old', 'playing'):
            self.s=copy.deepcopy(original)
            if mode=='locked': self.s['items'][0]['locked']=True
            if mode=='midi': self.s['items'][0]['take_type']='midi'
            if mode=='empty': self.s['tracks'][1]['item_count']=0
            if mode=='old': self.s.pop('item_repeat_available')
            if mode=='playing': self.s['timeline']['play_state']=1
            with self.subTest(mode=mode), self.assertRaises(PlanError): self.validate(self.repeat())

    def test_track_endpoint_works_with_truncated_item_preview(self):
        self.s['items_truncated']=True
        self.s['items']=self.s['items'][:1]
        self.validate(self.repeat())

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.request_json')
    def test_native_plan_with_vision_enabled_receives_endpoint_context(self, request, key):
        result=dict(kind='plan', actions=[self.repeat()], message='', lua='')
        request.return_value={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps(result)}]}]}
        actual=providers.openai_plan('Extend the kicks every half note to the end of the vocal below',self.s,
                                    dict(DEFAULTS,vision_enabled=True),[])
        self.assertEqual(actual,result)
        payload=request.call_args.args[2]
        self.assertIn('Do not zoom',payload['instructions'])
        self.assertIn('half note=2',payload['instructions'])
        self.assertIn('until_track',payload['instructions'])
        self.assertEqual(json.loads(payload['input'])['project']['tracks'][1]['item_end'],140)

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.jev_plan')
    @patch('heycomputer.providers.openai_plan', return_value={'kind': 'reply', 'message': 'fixture'})
    def test_sample_repetition_skips_track_setup_router(self, fallback, jev, key):
        providers.plan('Repeat this sample to the end',self.s,DEFAULTS,[])
        jev.assert_not_called(); fallback.assert_called_once()
