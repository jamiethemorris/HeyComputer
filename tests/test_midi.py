import copy
import json
import unittest
from unittest.mock import patch

from heycomputer.protocol import action, validate_result, PlanError
from heycomputer import providers
from routing_fixture import OPENROUTER_DEFAULTS as DEFAULTS


def op(name, target, value):
    return action(name, target, json.dumps(value) if isinstance(value, dict) else value)


class MidiTests(unittest.TestCase):
    def setUp(self):
        self.s = {'tracks': [{'guid': 'track', 'selected': True, 'folder_depth': 0}], 'plugins': [], 'inputs': [],
                  'timeline': {'play_state': 0}, 'midi_available': True,
                  'items': [{'guid': 'item', 'track': 'track', 'selected': True, 'take_type': 'midi',
                             'midi': {'notes': [{'index': 0}, {'index': 3}], 'preview_truncated': True}}]}

    def run_plan(self, actions):
        return validate_result(dict(kind='plan', actions=actions, message='', lua=''), self.s)

    def create(self, target='track'):
        return op('midi_create', target, {'start': {'bar': 1}, 'end': {'bar': 5}, 'name': 'Kick'})

    def pattern(self, target='item_result:1'):
        return op('midi_notes', target, {'notes': [{'at': {'bar': 1}, 'length_qn': .25, 'pitch': 36, 'velocity': 100}],
                                       'repeat_count': 16, 'every_qn': 1})

    def test_track_item_notes_color_and_rename_compose(self):
        self.run_plan([action('create_tracks', count=1), self.create('result:1'), self.pattern('item_result:2'),
                       op('item_color', 'item_result:2', '#22AA44'), op('item_name', 'item_result:2', 'Quarter kick'),
                       op('midi_edit', 'item_result:2', {'select': {}, 'set': {'velocity': 90}})])

    def test_item_references_are_backward_and_typed(self):
        for target in ('item_result:1', 'item_result:2', 'result:1', 'item_result:0'):
            with self.subTest(target=target), self.assertRaises(PlanError):
                self.run_plan([self.pattern(target), self.create()])
        with self.assertRaises(PlanError):
            self.run_plan([action('create_tracks', count=1), self.pattern()])

    def test_selected_and_all_midi_ignore_audio_items(self):
        self.s['items'].append({'guid': 'audio', 'selected': True, 'take_type': 'audio'})
        for target in ('selected', 'all', 'item'):
            self.run_plan([op('midi_edit', target, {'select': {'selected': True}, 'set': {'transpose': 12}})])
        with self.assertRaises(PlanError): self.run_plan([self.pattern('audio')])

    def test_empty_preview_does_not_block_explicit_whole_item_edits(self):
        self.s['items'][0]['midi']['notes'] = []
        self.run_plan([op('midi_edit', 'item', {'select': {}, 'set': {'quantize_qn': .25, 'strength': .5}})])

    def test_exact_indices_must_be_observed_and_used_before_changes(self):
        self.run_plan([op('midi_delete', 'item', {'select': {'indices': [3]}})])
        for ids in ([1], [-1], [0, 0], [True]):
            with self.assertRaises(PlanError): self.run_plan([op('midi_delete', 'item', {'select': {'indices': ids}})])
        with self.assertRaises(PlanError):
            self.run_plan([op('midi_edit', 'item', {'select': {}, 'set': {'velocity': 90}}),
                           op('midi_delete', 'item', {'select': {'indices': [0]}})])

    def test_malformed_values_and_unbounded_insertion_fail(self):
        for changes in ({'pitch': 128}, {'velocity': 0}, {'length_qn': -1}, {'muted': 1}, {'strength': .5},
                        {'pitch': 60, 'transpose': 12}, {'velocity': 90, 'velocity_delta': 1}, {'shift_qn': float('nan')}):
            with self.subTest(changes=changes), self.assertRaises(PlanError):
                self.run_plan([op('midi_edit', 'item', {'select': {}, 'set': changes})])
        a=self.pattern('item');v=json.loads(a['value']);v['repeat_count']=512;a['value']=json.dumps(v)
        with self.assertRaises(PlanError): self.run_plan([a]*9)
        with self.assertRaises(PlanError):
            self.run_plan([op('midi_delete', 'item', {'select': {'start': {'bar': 1}}})])

    def test_unavailable_locked_or_rolling_targets_fail_before_creation(self):
        for mode in ('locked', 'unavailable', 'playing', 'truncated'):
            with self.subTest(mode=mode):
                s=copy.deepcopy(self.s)
                if mode=='locked': self.s['items'][0]['locked']=True
                if mode=='unavailable': self.s['items'][0]['midi']['unavailable']='Unpool first.'
                if mode=='playing': self.s['timeline']['play_state']=1
                if mode=='truncated': self.s['items_truncated']=True
                with self.assertRaises(PlanError): self.run_plan([self.create(), self.pattern('all')])
                self.s=s

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.request_json')
    def test_planner_receives_native_midi_and_result_instructions(self, request, key):
        request.return_value={'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text',
                              'text': json.dumps(dict(kind='plan', actions=[self.create(), self.pattern()], message='', lua=''))}]}]}
        result=providers.openai_plan('Create a four-bar MIDI kick pattern',self.s,DEFAULTS,[])
        self.assertEqual(result['kind'],'plan')
        prompt=request.call_args.args[2]['instructions']
        self.assertIn('item_result:N',prompt)
        self.assertIn('quantize_qn',prompt)

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.jev_plan')
    @patch('heycomputer.providers.openai_plan', return_value={'kind': 'reply', 'message': 'fixture'})
    def test_midi_requests_skip_the_track_only_jev_recipe(self, fallback, jev, key):
        providers.plan('Transpose the selected notes up an octave',self.s,DEFAULTS,[])
        jev.assert_not_called();fallback.assert_called_once()
