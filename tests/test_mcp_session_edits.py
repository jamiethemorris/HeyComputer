"""Offline dispatch contract; never instantiate the bridge client."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from heycomputer.mcp_catalog import LEGACY, WRITES, allowed_specs
from heycomputer.mcp_loop import MAX_EDITS, Session
from heycomputer.protocol import PlanError


SCHEMAS = json.loads(Path(__file__).with_name('mcp_083_schemas.json').read_text())
REV = 'project_get_change_count'
INFO = 'item_get_info'
STATE = 'transport_get_state'
DELETE = 'item_delete'
MUTE = 'item_set_mute'


class Fake:
    def __init__(self):
        self.calls = []
        self.revision = 11
        self.transport = dict(recording=False, play_state=0)
        self.items = [dict(index=i, track_index=0, name=str(i), mute=False) for i in range(2)]

    def call(self, name, args):
        self.calls.append(name)
        if name == REV:
            return {'change_count': self.revision}
        if name == STATE:
            return self.transport
        if name == INFO:
            return dict(self.items[args['item_index']])
        if name == DELETE:
            i = args['item_index']
            self.items.pop(i)
            self.revision += 1
            return dict(deleted_index=i, remaining_items=len(self.items))
        if name == MUTE:
            i = args['item_index']
            self.items[i]['mute'] = args['mute']
            self.revision += 1
            return dict(self.items[i])
        raise AssertionError(name)


class SessionEditTests(unittest.TestCase):
    def setUp(self):
        self.fake = Fake()
        tools = allowed_specs([dict(name=n, inputSchema=s) for n, s in SCHEMAS.items()])
        self.session = Session(self.fake, None, lambda: 3, tools)

    def dispatch(self, name, **args):
        return self.session.dispatch(name, args, 'fixture')

    def test_indexed_delete_needs_inspection_of_that_item(self):
        with self.assertRaisesRegex(PlanError, 'Inspect'):
            self.dispatch(DELETE, item_index=1)
        self.dispatch(INFO, item_index=0)
        with self.assertRaisesRegex(PlanError, 'Inspect'):
            self.dispatch(DELETE, item_index=1)
        self.assertNotIn(DELETE, self.fake.calls)

    def test_revision_alone_never_authorizes_any_pinned_wide_edit(self):
        self.dispatch(REV)
        for name in sorted(WRITES - LEGACY):
            schema = SCHEMAS[name]
            sample = {'integer': 0, 'number': 1.0, 'boolean': False, 'string': 'fixture'}
            args = {k: sample[schema['properties'][k]['type']] for k in schema.get('required', [])}
            with self.subTest(name=name), self.assertRaisesRegex(PlanError, 'Inspect'):
                self.dispatch(name, **args)
        self.assertEqual(self.fake.calls, [REV])

    def test_wide_send_edits_need_both_endpoints_of_the_inspected_send(self):
        for edit, extra in [('send_remove', {}), ('send_set_midi_channel',
                             dict(midi_source_channel=1, midi_dest_channel=2))]:
            with self.subTest(edit=edit):
                self.setUp()
                original = self.fake.call
                rows = [dict(index=0, dest_track_index=1), dict(index=1, dest_track_index=2)]
                def routing(name, args):
                    if name == 'track_get_info':
                        return dict(index=args['track_index'], name=str(args['track_index']))
                    if name == 'send_get_all':
                        return dict(send_count=len(rows), sends=rows)
                    if name == edit:
                        return dict(success=True)
                    return original(name, args)
                args = dict(track_index=0, send_index=1, **extra)
                with patch.object(self.fake, 'call', side_effect=routing) as client:
                    self.dispatch(REV)
                    self.dispatch('send_get_all', track_index=0)
                    self.dispatch('track_get_info', track_index=1)
                    with self.assertRaisesRegex(PlanError, 'Inspect'):
                        self.dispatch(edit, **dict(args, send_index=0))  # destination only
                    self.dispatch('track_get_info', track_index=0)
                    before = client.call_count
                    with self.assertRaisesRegex(PlanError, 'Inspect'):
                        self.dispatch(edit, **args)
                    self.assertEqual(client.call_count, before)
                    self.dispatch('track_get_info', track_index=2)
                    self.assertIn('receipt', self.dispatch(edit, **args))

    def test_transport_setters_need_evidence_of_the_specific_setting(self):
        project = 'project_get_info'
        bpm = ('transport_set_bpm', dict(bpm=100))
        meter = ('transport_set_time_signature', dict(numerator=3, denominator=4))
        project_settings = dict(bpm=120.5, time_sig_num=4)
        transport_settings = dict(project_settings, time_sig_den=4)
        for read, fields, (edit, args), allowed in [
            (STATE, {}, bpm, False), (project, dict(name='fixture'), meter, False),
            (project, dict(bpm=True), bpm, False), (STATE, dict(bpm=0), bpm, False),
            (project, project_settings, meter, False),
            (STATE, dict(time_sig_num=True, time_sig_den=4), meter, False),
            (STATE, transport_settings, meter, True), (STATE, transport_settings, bpm, True),
            (project, project_settings, bpm, True),
        ]:
            with self.subTest(read=read, fields=fields, edit=edit):
                self.setUp()
                original = self.fake.call
                def settings(name, params):
                    if name == read:
                        return dict(self.fake.transport, **fields) if read == STATE else fields
                    if name == edit:
                        return dict(success=True)
                    return original(name, params)
                with patch.object(self.fake, 'call', side_effect=settings) as client:
                    self.dispatch(read)
                    if allowed:
                        self.assertIn('receipt', self.dispatch(edit, **args))
                    else:
                        before = client.call_count
                        with self.assertRaisesRegex(PlanError, 'Inspect'):
                            self.dispatch(edit, **args)
                        self.assertEqual(client.call_count, before)

    def test_truncated_notes_only_authorize_observed_indices_on_that_item(self):
        self.dispatch(INFO, item_index=0)
        self.dispatch(INFO, item_index=1)
        original = self.fake.call
        def midi(name, args):
            if name == 'midi_get_notes':
                return dict(note_count=2, notes_returned=1, truncated=True, notes=[dict(index=0, pitch=60)])
            if name == 'midi_delete_note':
                self.fake.revision += 1
                return dict(deleted_index=args['note_index'])
            return original(name, args)
        with patch.object(self.fake, 'call', midi):
            self.dispatch('midi_get_notes', item_index=0, max_results=1)
            for item, note in [(1, 0), (0, 1)]:
                with self.assertRaisesRegex(PlanError, 'Inspect'):
                    self.dispatch('midi_delete_note', item_index=item, note_index=note)
            self.assertIn('receipt', self.dispatch('midi_delete_note', item_index=0, note_index=0))

    def test_receipt_is_not_verified_or_reusable_inspection(self):
        self.dispatch(INFO, item_index=1)
        result = self.dispatch(DELETE, item_index=1)
        self.assertEqual(result['receipt']['deleted_index'], 1)
        self.assertNotIn('verified', result)
        self.assertFalse(self.session.verified)
        self.assertIn(DELETE, self.session.receipts[0])
        self.assertIsNone(self.session.pending)
        self.assertEqual(self.fake.calls, [REV, INFO, REV, STATE, REV, DELETE, REV])
        with self.assertRaisesRegex(PlanError, 'Inspect'):
            self.dispatch(DELETE, item_index=0)

    def test_field_readback_not_receipt_verifies_item_mute(self):
        self.dispatch(INFO, item_index=0)
        feedback = self.dispatch(MUTE, item_index=0, mute=True)
        self.assertIs(feedback['verified']['mute'], True)
        self.assertEqual(self.fake.calls[-3:], [REV, INFO, REV])
        self.assertIn(MUTE, self.session.verified[0])

    def test_stale_or_malformed_revision_never_dispatches(self):
        for invalid in (12, True, 11.0, -1, None):
            with self.subTest(invalid=invalid):
                self.fake.revision = 11
                self.dispatch(INFO, item_index=0)
                self.fake.revision = invalid
                with self.assertRaises(PlanError):
                    self.dispatch(DELETE, item_index=0)
                self.assertNotIn(DELETE, self.fake.calls)
                self.assertIsNone(self.session.pending)

    def test_revision_change_during_transport_blocks_indexed_write(self):
        self.dispatch(INFO, item_index=0)
        original = self.fake.call
        def race(name, args):
            if name == STATE:
                self.fake.revision += 1
            return original(name, args)
        with patch.object(self.fake, 'call', race), self.assertRaisesRegex(PlanError, 'Project changed'):
            self.dispatch(DELETE, item_index=0)
        self.assertNotIn(DELETE, self.fake.calls)
        self.assertIsNone(self.session.pending)

    def test_revision_change_during_inspection_refuses_even_its_first_target(self):
        original = self.fake.call
        def race(name, args):
            result = original(name, args)
            if name == INFO:
                self.fake.revision += 1
            return result
        with patch.object(self.fake, 'call', race), self.assertRaisesRegex(PlanError, 'during inspection'):
            self.dispatch(INFO, item_index=0)
        with self.assertRaisesRegex(PlanError, 'Inspect'):
            self.dispatch(DELETE, item_index=0)
        self.assertNotIn(DELETE, self.fake.calls)

    def test_recording_unknown_state_and_cancellation_never_send(self):
        self.dispatch(INFO, item_index=0)
        for state in ({'recording': True, 'play_state': 5}, {'play_state': 0}, {'recording': 0, 'play_state': 0}):
            self.fake.transport = state
            with self.subTest(state=state), self.assertRaisesRegex(PlanError, 'transport state'):
                self.dispatch(DELETE, item_index=0)
        self.fake.transport = dict(recording=False, play_state=0)
        self.fake.calls.clear()
        def budget():
            if self.fake.calls == [STATE, REV]:
                raise RuntimeError('cancelled')
            return 3
        self.session.budget = budget
        with self.assertRaisesRegex(RuntimeError, 'cancelled'):
            self.dispatch(DELETE, item_index=0)
        self.assertNotIn(DELETE, self.fake.calls)
        self.assertIsNone(self.session.pending)

    def test_receipt_and_verified_edits_share_one_budget(self):
        for i in range(MAX_EDITS):
            self.fake.items.append(dict(self.fake.items[0], index=i+2))
        for i in range(MAX_EDITS):
            self.dispatch(INFO, item_index=0)
            if i % 2:
                self.dispatch(MUTE, item_index=0, mute=True)
            else:
                index = len(self.fake.items)-1
                self.dispatch(INFO, item_index=index)
                self.dispatch(DELETE, item_index=index)
        with self.assertRaisesRegex(PlanError, 'limit'):
            self.dispatch(DELETE, item_index=0)
        self.assertEqual(len(self.session.receipts) + len(self.session.verified), MAX_EDITS)

    def test_uncertain_write_keeps_pending_and_blocks_resubmission(self):
        for fault in ('lost', 'error', 'revision', 'readback', 'readback_race'):
            with self.subTest(fault=fault):
                self.setUp()
                self.dispatch(INFO, item_index=0)
                original = self.fake.call
                def broken(name, args):
                    result = original(name, args)
                    if name == INFO and fault == 'readback_race': self.fake.revision += 1
                    if name == MUTE:
                        if fault == 'lost': raise OSError('lost receipt')
                        if fault == 'error': return dict(success=False)
                        if fault == 'revision': self.fake.revision = False
                        if fault == 'readback': self.fake.items[0]['mute'] = 1  # not true
                    return result
                with patch.object(self.fake, 'call', broken), self.assertRaises((OSError, PlanError)):
                    self.dispatch(MUTE, item_index=0, mute=True)
                self.assertIn(MUTE, self.session.pending)
                with self.assertRaisesRegex(PlanError, 'do not retry'):
                    self.dispatch(MUTE, item_index=0, mute=True)
                self.assertEqual(self.fake.calls.count(MUTE), 1)
                self.assertFalse(self.session.verified or self.session.receipts)
