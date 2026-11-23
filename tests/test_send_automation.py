import json
import unittest
from heycomputer.protocol import action, validate_result, PlanError


class SendAutomationTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'tracks': [
            {'guid': 'source', 'selected': True, 'sends': [{'destination': 'bus', 'volume': .5}]},
            {'guid': 'bus', 'sends': []}]}

    def op(self, name='automation_points', target='source', **overrides):
        value = {'envelope': {'kind': 'send_volume', 'destination': 'bus'},
                 'points': [{'at': {'seconds': 2}, 'value': '-6 dB', 'shape': 'linear'}],
                 'replace': True}
        value.update(overrides)
        return action(name, target, json.dumps(value))

    def validate(self, *ops):
        return validate_result({'kind': 'plan', 'message': '', 'lua': '', 'actions': list(ops)}, self.snapshot)

    def test_existing_exact_source_destination_accepts_volume_points(self):
        self.validate(self.op())

    def test_send_points_use_the_same_bounded_db_values_as_track_volume(self):
        for value in ('13 dB', 'NaN dB', 'on', '0.5'):
            with self.subTest(value=value), self.assertRaises(PlanError):
                self.validate(self.op(points=[{'at': {'seconds': 2}, 'value': value, 'shape': 'linear'}]))
        for value in ('-inf dB', '-150 dB', '+12 dB'):
            self.validate(self.op(points=[{'at': {'seconds': 2}, 'value': value, 'shape': 'square'}]))

    def test_send_automation_rejects_routing_and_structural_batches(self):
        # Validate the combination guard directly; unrelated operation validation
        # must not conceal acceptance of an identity-invalidating batch.
        from heycomputer.automation import validate_combinations
        for name in ('send_create', 'send_set', 'send_delete', 'create_tracks',
                     'duplicate_tracks', 'folder_tracks', 'folder_created'):
            with self.subTest(op=name), self.assertRaises(PlanError):
                validate_combinations([self.op(), action(name, 'source', '{}')])
        validate_combinations([self.op(), action('rename', 'source', 'Claps')])

    def test_send_selector_requires_inspected_unique_existing_track_route(self):
        for target in ('selected', 'all'):
            with self.subTest(target=target), self.assertRaises(PlanError):
                self.validate(self.op(target=target))
        for envelope in ({'kind': 'send_volume'},
                         {'kind': 'send_volume', 'destination': 'missing'},
                         {'kind': 'send_volume', 'destination': 'selected'},
                         {'kind': 'send_volume', 'destination': 'bus', 'index': 0},
                         {'kind': 'send_volume', 'destination': 'bus', 'hardware': True}):
            with self.subTest(envelope=envelope), self.assertRaises(PlanError):
                self.validate(self.op(envelope=envelope))
        for sends in ([], [{'destination': 'bus'}, {'destination': 'bus'}]):
            self.snapshot['tracks'][0]['sends'] = sends
            with self.subTest(sends=sends), self.assertRaises(PlanError):
                self.validate(self.op())

    def test_send_clear_and_state_use_the_same_selector(self):
        self.validate(action('automation_clear', 'source', json.dumps({
            'envelope': {'kind': 'send_volume', 'destination': 'bus'},
            'start': {'seconds': 1}, 'end': {'seconds': 3}})))
        self.validate(action('automation_state', 'source', json.dumps({
            'envelope': {'kind': 'send_volume', 'destination': 'bus'}, 'visible': True})))

    def test_planner_help_advertises_exact_send_selector_and_clarifies_missing_values(self):
        from heycomputer.automation import HELP
        self.assertIn('"kind":"send_volume","destination":"exact existing destination track GUID"', HELP)
        self.assertNotIn('No master/take/send envelopes', HELP)
        self.assertIn('unspecified reduction amount or timing', HELP)


if __name__ == '__main__':
    unittest.main()
