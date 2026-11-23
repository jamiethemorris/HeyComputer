import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from heycomputer import worker


class MCPWideRoutingTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(dir=Path(__file__).parent))
        self.worker = worker.Worker(Path(tmp) / 'session')
        self.worker.config.update(reaper_mcp_enabled=True, fallback_provider='openai', use_jev=False)
        self.mcp = self.enterContext(patch.object(self.worker, 'mcp_request',
                                                return_value={'kind': 'reply', 'message': 'MCP'}))
        self.enterContext(patch.object(worker.providers, 'api_key', return_value='fixture'))
        self.legacy = self.enterContext(patch.object(worker.providers, 'openai_plan',
                                                   return_value={'kind': 'reply', 'message': 'legacy'}))
        thread = self.enterContext(patch.object(worker.threading, 'Thread'))
        thread.side_effect = lambda target, args, daemon: Mock(start=lambda: target(*args))

    def request(self, text, *, kind='plan', **fields):
        self.worker.dispatch(dict(id='j1', kind=kind, text=text,
                                  snapshot={'project': 'fixture', 'timeline': {}}, **fields))
        return json.loads((self.worker.directory / 'j1.response.json').read_text())

    def test_spoken_requests_reach_mcp_intact(self):
        with patch.object(self.worker.recorder, 'stop', return_value=b'audio'), \
             patch.object(worker.providers, 'transcribe') as transcribe:
            for text in (
                'List the tempo changes in this project.',
                'How many MIDI notes are in the first MIDI item on track one?',
                'Split the first item on track one at five seconds.',
                'Delete the item on track one.',
                'Split the 2nd item on track 3 at five seconds.',
            ):
                with self.subTest(text=text):
                    transcribe.return_value = text
                    result = self.request('', kind='record_stop')
                    self.assertEqual(result['message'], 'MCP')
                    self.assertEqual(result['transcript'], text)
                    self.assertEqual(self.mcp.call_args.args[0], text)
        self.legacy.assert_not_called()

    def test_item_edit_reaches_mcp_intact(self):
        text = 'Split the selected item at the cursor and mute the right half'
        result = self.request(text)
        self.assertEqual(result['message'], 'MCP')
        self.assertEqual(self.mcp.call_args.args[0], text)
        self.legacy.assert_not_called()

    def test_new_tool_groups_reach_mcp(self):
        for text in (
            'List all media items',
            'Count the MIDI notes on track one',
            'Which MIDI channels are in the selected item?',
            'Quantize the MIDI notes to sixteenth notes',
            'Transpose the selected notes up an octave',
            'Rename the Verse marker to Intro',
            'Add a region from bar 5 to bar 9',
            'Set the tempo to 125 BPM',
            'Change the time signature to 7/8 at bar 9',
            'Apply the Drums track template',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.request(text)['message'], 'MCP')
                self.assertEqual(self.mcp.call_args.args[0], text)
        self.legacy.assert_not_called()

    def test_disabled_mcp_uses_legacy_for_new_tool_groups(self):
        self.worker.config['reaper_mcp_enabled'] = False
        text = 'Quantize the MIDI notes to sixteenth notes'
        self.assertEqual(self.request(text)['message'], 'legacy')
        self.legacy.assert_called_once()
        self.assertEqual(self.legacy.call_args.args[0], text)
        self.mcp.assert_not_called()

    def test_quick_commands_bypass_both_model_paths(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                self.worker.config['reaper_mcp_enabled'] = enabled
                result = self.request('save project')
                self.assertEqual(result['model'], 'Direct command')
                self.assertEqual([a['op'] for a in result['actions']], ['save'])
        self.mcp.assert_not_called()
        self.legacy.assert_not_called()

    def test_visual_request_stays_on_visual_path(self):
        self.worker.config['vision_enabled'] = True
        self.legacy.return_value = {'kind': 'vision', 'message': 'Use the MIDI editor'}
        with patch.object(self.worker, 'visual_plan', return_value={'kind': 'reply', 'message': 'visual'}) as visual:
            text = 'Open the MIDI editor and quantize the selected notes'
            self.assertEqual(self.request(text)['message'], 'visual')
            visual.assert_called_once()
            self.assertEqual(visual.call_args.args[0], text)
        self.legacy.assert_called_once()
        self.mcp.assert_not_called()

    def test_general_item_and_music_questions_do_not_access_the_project(self):
        for text in ('Duplicate the item on my shopping list', 'What is a time signature?',
                     'What does BPM mean?', 'What is a MIDI note?', 'What are MIDI channels?'):
            with self.subTest(text=text):
                self.assertEqual(self.request(text)['message'], 'legacy')
                self.assertEqual(self.legacy.call_args.args[0], text)
        self.mcp.assert_not_called()

    def test_non_mcp_requests_still_use_legacy(self):
        for text in (
            'Show the MIDI editor and select the MIDI notes',
            'What is MIDI?',
            'How do I split the selected item?',
            'How do I split the first item on track one?',
            'Explain what a tempo marker is',
            'Insert Pro-Q on the guitar track',
            'Move the meeting to Friday',
            'Move the item to the top of my shopping list',
            'Split the first item on my shopping list',
            'Delete the item from my shopping list',
            'Add an item to the grocery list',
            'Create a budget template for the Western region',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.request(text)['message'], 'legacy')
                self.assertEqual(self.legacy.call_args.args[0], text)
        self.mcp.assert_not_called()

    def test_clarification_keeps_legacy_context(self):
        history = [{'role': 'assistant', 'kind': 'reply', 'text': 'Which notes?'}]
        text = 'The selected MIDI notes'
        self.assertEqual(self.request(text, history=history)['message'], 'legacy')
        self.assertEqual(self.legacy.call_args.args[3], history)
        self.mcp.assert_not_called()
