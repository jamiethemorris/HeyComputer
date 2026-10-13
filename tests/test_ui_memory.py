import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from heycomputer.ui_memory import UIMemory, observations, scope_for
from heycomputer.vision import Frame, Window, run, VisualStopped
from test_vision import FakeDesktop, step, CONFIG


def frame(title='VST3: Pro-Q 4 (FabFilter) - Track 12 "Private title" [3/3]',
          bounds=(200, 300, 950, 595), pixels=(1600, 1002)):
    return Frame(Window(11, 101, title, bounds, 'identity'), b'jpeg', *pixels, [])


NOTE = dict(control='Filter shape', note='Left side of the selected-band panel; only visible with a band selected.',
            has_position=False, x=0, y=0)


class MemoryTests(unittest.TestCase):
    def test_scope_ignores_instance_track_and_screen_location_but_keeps_product_version(self):
        f = frame()
        scope = scope_for(f)
        self.assertEqual(scope, 'plugin:vst3: pro-q 4 (fabfilter)')
        self.assertEqual(scope_for(frame(title='VST3: Pro-Q 4 (FabFilter) - Track 5 "Other" [1/1]')), scope)
        self.assertNotEqual(scope_for(frame(title='VST3: Pro-Q 3 (FabFilter) - Track 5 "Other" [1/1]')), scope)
        self.assertNotEqual(scope_for(frame(title='AU: Pro-Q 4 (FabFilter) - Track 5 "Other" [1/1]')), scope)
        self.assertEqual(scope_for(frame(title='Secret song [modified] - REAPER v7.78')), 'reaper:main:7.78')
        self.assertIsNone(scope_for(frame(title='Routing for Private track')))

    def test_window_relative_coordinates_survive_move_and_retina_but_not_resize(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UIMemory(Path(tmp)/'ui-memory.json')
            f = frame()
            note = dict(NOTE, control='Output gain', note='Fixed top-right output control', has_position=True, x=800, y=501)
            self.assertTrue(memory.save(observations(scope_for(f), f, [note])))
            moved = frame(bounds=(-1400, -100, 950, 595), pixels=(950, 595))
            hint = memory.hints(scope_for(moved), moved)[0]
            self.assertEqual(hint['approximate_image_point'], [475, 298])
            resized = frame(bounds=(0, 0, 1200, 800))
            hint = memory.hints(scope_for(resized), resized)[0]
            self.assertNotIn('approximate_image_point', hint)
            self.assertEqual(hint['note'], note['note'])
            data = memory.path.read_text()
            self.assertNotIn('Private title', data)
            self.assertNotIn('jpeg', data)
            self.assertNotIn('-1400', data)

    def test_proq_band_coordinates_are_suppressed_even_in_legacy_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UIMemory(Path(tmp)/'ui-memory.json'); f = frame(); scope = scope_for(f)
            records = observations(scope, f, [dict(NOTE, has_position=True, x=200, y=400)])
            self.assertIsNone(records[0]['position'])
            self.assertIn('moves', records[0]['anchor'])
            # Simulate an old, incorrectly recorded point at the SAME window size.
            records[0].pop('anchor')
            records[0]['position'] = dict(x=.2,y=.4,window_width=950,window_height=595)
            memory.save(records)
            hint = memory.hints(scope, f)[0]
            self.assertNotIn('approximate_image_point', hint)
            self.assertIn('selected EQ point', hint['anchor'])

    def test_anchored_controls_in_other_plugins_never_get_fixed_points(self):
        f = frame(title='CLAP: Synth - Track 1 "Synth" [1/1]')
        entry = observations(scope_for(f), f, [dict(NOTE, anchor='floating oscillator inspector', has_position=True, x=200,y=300)])[0]
        self.assertIsNone(entry['position'])
        self.assertEqual(entry['anchor'], 'floating oscillator inspector')

    def test_location_miss_gets_fresh_image_without_input_then_corrects_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UIMemory(Path(tmp)/'ui-memory.json'); f = frame(); scope = scope_for(f)
            memory.save(observations(scope, f, [NOTE]))
            backend = FakeDesktop()
            backend.windows = lambda: [f.window]
            captures = []
            def capture(window, check):
                captures.append(1)
                return f
            backend.capture = capture
            count = 0
            def ask(*args):
                nonlocal count
                count += 1
                if count == 1:
                    self.assertEqual(len(f.ui_memory['hints']), 1)
                    return step('reobserve', text='Filter shape')
                self.assertEqual(f.ui_memory['hints'], [])
                return step('done', text='Verified', memory=[dict(NOTE,note='Left of FREQ in the current selected-band panel.')])
            result = run('test', [], CONFIG, backend, threading.Event(), lambda: None, Mock(), ask=ask, memory=memory)
            self.assertEqual(result['kind'], 'visual_done')
            self.assertEqual(len(captures), 2)
            self.assertEqual(backend.calls, [])
            self.assertEqual(memory.hints(scope,f)[0]['note'], 'Left of FREQ in the current selected-band panel.')

    def test_generic_fixed_control_can_be_relearned_as_moving_after_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UIMemory(Path(tmp)/'ui-memory.json')
            f=frame(title='CLAP: Example synth - Track 1 "Synth" [1/1]'); scope=scope_for(f)
            old=dict(NOTE,control='Cutoff',note='Visible cutoff knob',has_position=True,x=400,y=300)
            memory.save(observations(scope,f,[old]))
            self.assertIn('approximate_image_point', memory.hints(scope,f)[0])
            backend=FakeDesktop(); backend.windows=lambda:[f.window]; backend.capture=lambda w,c:f
            corrected=dict(old,anchor='selected oscillator inspector',note='Cutoff is beneath the filter label inside the moving inspector.',has_position=False)
            ask=Mock(side_effect=[step('reobserve',text='Cutoff'),step('done',text='Verified',memory=[corrected])])
            result=run('test',[],CONFIG,backend,threading.Event(),lambda:None,Mock(),ask=ask,memory=memory)
            self.assertEqual(result['kind'],'visual_done')
            hint=UIMemory(memory.path).hints(scope,f)[0]
            self.assertEqual(hint['anchor'],'selected oscillator inspector')
            self.assertNotIn('approximate_image_point',hint)
            self.assertEqual(backend.calls,[])

    def test_recovery_is_bounded_and_does_not_delete_memory_on_failed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UIMemory(Path(tmp)/'ui-memory.json'); f=frame(); scope=scope_for(f)
            memory.save(observations(scope, f, [NOTE]))
            original = memory.path.read_text()
            backend = FakeDesktop(); backend.windows=lambda:[f.window]; backend.capture=lambda w,c:f
            result = run('test', [], CONFIG, backend, threading.Event(), lambda: None, Mock(),
                         ask=Mock(return_value=step('reobserve')), memory=memory)
            self.assertEqual(result['kind'], 'error')
            self.assertIn('two fresh observations', result['message'])
            self.assertEqual(backend.calls, [])
            self.assertEqual(memory.path.read_text(), original)

    def test_notes_persist_merge_and_can_be_edited_or_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'ui-memory.json'
            f = frame(); scope = scope_for(f)
            memory = UIMemory(path)
            self.assertTrue(memory.save(observations(scope, f, [NOTE])))
            self.assertTrue(memory.save(observations(scope, f, [dict(NOTE, note='Updated note')])))
            other = frame(title='AU: Other plugin - Track 1 "Other" [1/1]')
            memory.save(observations(scope_for(other), other, [NOTE]))
            reloaded = UIMemory(path)
            self.assertEqual(len(reloaded.hints(scope, f)), 1)
            self.assertEqual(reloaded.hints(scope, f)[0]['note'], 'Updated note')
            data = json.loads(path.read_text())
            data['entries'] = [e for e in data['entries'] if e['scope'] != scope]
            path.write_text(json.dumps(data))
            self.assertEqual(memory.hints(scope, f), [])
            self.assertEqual(len(memory.hints(scope_for(other), other)), 1)

    def test_corrupt_file_is_ignored_and_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'ui-memory.json'
            path.write_text('my unfinished edit')
            memory = UIMemory(path); f = frame()
            self.assertEqual(memory.hints(scope_for(f), f), [])
            self.assertFalse(memory.save(observations(scope_for(f), f, [NOTE])))
            self.assertEqual(path.read_text(), 'my unfinished edit')

    def test_invalid_or_outside_window_observations_are_not_saved(self):
        f = frame(); scope = scope_for(f)
        for bad in [dict(NOTE, has_position=True, x=-1), dict(NOTE, note='x'*401),
                    dict(NOTE, has_position=True, x=float('nan')), dict(NOTE, control=''),
                    dict(NOTE, has_position=1), dict(NOTE, note='a\nb')]:
            self.assertEqual(observations(scope, f, [bad]), [])
        self.assertEqual(observations(None, f, [NOTE]), [])

    def test_success_learns_and_reuses_hints_without_an_extra_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = UIMemory(Path(tmp)/'ui-memory.json')
            f = frame()
            backend = FakeDesktop()
            backend.windows = lambda: [f.window]
            backend.capture = lambda window, check: f
            ask = Mock(return_value=step('done', text='Verified', memory=[NOTE]))
            result = run('test', [], CONFIG, backend, threading.Event(), lambda: None, Mock(), ask=ask, memory=memory)
            self.assertTrue(result['memory_saved'])
            ask.assert_called_once()
            ask = Mock(return_value=step('done', text='Verified'))
            run('test again', [], CONFIG, backend, threading.Event(), lambda: None, Mock(), ask=ask, memory=memory)
            self.assertEqual(ask.call_args.args[3].ui_memory['hints'][0]['control'], 'Filter shape')

    def test_failed_cancelled_reply_and_disabled_runs_do_not_learn(self):
        for outcome in ('failure', 'cancel', 'reply', 'disabled'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                memory = UIMemory(Path(tmp)/'ui-memory.json')
                f = frame(); backend = FakeDesktop()
                backend.windows = lambda: [f.window]
                backend.capture = lambda window, check: f
                cancel = threading.Event()
                config = dict(CONFIG, ui_memory_enabled=outcome != 'disabled')
                calls = 0
                def ask(*args):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        if outcome == 'cancel':
                            cancel.set()
                        return step('wait', memory=[NOTE])
                    if outcome == 'failure':
                        raise VisualStopped('failed')
                    return step('reply' if outcome == 'reply' else 'done', text='Finished')
                run('test', [], config, backend, cancel, lambda: None, Mock(), ask=ask, memory=memory)
                self.assertFalse(memory.path.exists())


if __name__ == '__main__':
    unittest.main()
