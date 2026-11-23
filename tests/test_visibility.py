import json
import unittest
from unittest.mock import patch

from heycomputer import providers
from heycomputer.capabilities import quick_plan
from heycomputer.protocol import PlanError, action, validate_result


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.s = {'timeline': {'play_state': 0}, 'plugins': [], 'inputs': [],
                  'tracks': [{'guid': 'guitar', 'selected': True, 'visibility': {'tcp': True, 'mcp': False}},
                             {'guid': 'bass', 'selected': False, 'visibility': {'tcp': False, 'mcp': False}}],
                  'launch_actions': [{'id': '_SWSTL_HIDE', 'name': 'SWS: Hide selected track(s)'},
                                     {'id': '_SWSTL_SHOWALL', 'name': 'SWS: Show all tracks'}]}

    def validate(self, actions):
        return validate_result({'kind': 'plan', 'actions': actions, 'message': '', 'lua': ''}, self.s)

    @patch('heycomputer.providers.request_json', side_effect=AssertionError('No model needed'))
    @patch('heycomputer.providers.api_key', side_effect=AssertionError('No API key needed'))
    def test_common_visibility_commands_are_local_and_panel_specific(self, key, request):
        for text, target, value in (
                ('hide selected tracks', 'selected', {'tcp': False, 'mcp': False}),
                ('hide this track', 'selected', {'tcp': False, 'mcp': False}),
                ('show these tracks in MCP', 'selected', {'mcp': True}),
                ('Hide the selected track(s).', 'selected', {'tcp': False, 'mcp': False}),
                ('show all tracks', 'all', {'tcp': True, 'mcp': True}),
                ('please unhide selected tracks in the mixer', 'selected', {'mcp': True}),
                ('hide all tracks from MCP', 'all', {'mcp': False}),
                ('show selected tracks in TCP', 'selected', {'tcp': True}),
                ('show all tracks in the arrange view', 'all', {'tcp': True}),
                ('hide selected tracks in the track panel please', 'selected', {'tcp': False})):
            with self.subTest(text=text):
                result = providers.plan(text, self.s, {}, [])
                self.assertEqual(result['model'], 'Direct command')
                self.assertEqual(result['actions'], [action('track_visibility', target, json.dumps(value))])

    def test_visibility_and_creation_compose_with_track_results(self):
        self.validate([action('create_tracks', count=2),
                       action('track_visibility', 'result:1:2', json.dumps({'mcp': False})),
                       action('rename', 'result:1:2', 'Hidden mixer track')])
        self.validate([action('track_visibility', 'all', json.dumps({'tcp': True}))])

    def test_invalid_or_ambiguous_values_fail_validation(self):
        for v in ({}, {'tcp': 1}, {'mcp': 'false'}, {'tcp': None}, {'panel': 'both'}, {'tcp': False, 'toggle': True}):
            with self.subTest(v=v), self.assertRaises(PlanError):
                self.validate([action('track_visibility', 'selected', json.dumps(v))])
        with self.assertRaises(PlanError):
            self.validate([action('track_visibility', 'missing', '{"tcp":true}')])

    def test_local_parser_does_not_swallow_compound_or_named_requests(self):
        for text in ('hide selected tracks and mute them', 'show only guitar tracks', 'hide Guitar in the mixer'):
            self.assertIsNone(quick_plan(text, self.s))

    def test_explicit_sws_launches_still_use_the_installed_action(self):
        for text, command in (('run SWS hide selected tracks', '_SWSTL_HIDE'), ('run SWS show all tracks', '_SWSTL_SHOWALL')):
            self.assertEqual(quick_plan(text, self.s)['actions'], [action('launch_action', 'project', command)])

    def test_pointing_words_need_an_unambiguous_selection(self):
        self.s['tracks'][1]['selected'] = True
        self.assertIsNone(quick_plan('hide this track', self.s))
        self.assertEqual(quick_plan('hide these tracks', self.s)['actions'][0]['target'], 'selected')
        for t in self.s['tracks']:
            t['selected'] = False
        self.assertIsNone(quick_plan('hide this track', self.s))
        self.assertIsNone(quick_plan('show these tracks', self.s))
        self.assertIsNone(quick_plan('hide this track and mute it', self.s))

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.jev_plan')
    @patch('heycomputer.providers.openai_plan', return_value={'kind': 'reply', 'message': 'fixture'})
    def test_named_visibility_requests_skip_the_track_setup_recipe(self, fallback, jev, key):
        providers.plan('Hide Guitar in the mixer', self.s, {}, [])
        jev.assert_not_called(); fallback.assert_called_once()
