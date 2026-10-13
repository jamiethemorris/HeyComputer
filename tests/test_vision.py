import base64
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from heycomputer import vision, providers
from heycomputer.protocol import PlanError, validate_result, RESPONSE_SCHEMA
from heycomputer.settings import DEFAULTS
from heycomputer.vision import Window, Frame, StaleWindow
from heycomputer.worker import Worker, atomic_json


WINDOW = Window(11, 101, 'REAPER', (-1400, 120, 1000, 600), 'reaper:101:launch')
FRAME = Frame(WINDOW, b'jpeg-fixture', 1600, 960, [])
CONFIG = dict(DEFAULTS, vision_enabled=True)


def step(op, **kwargs):
    return dict(op=op, x=0, y=0, end_x=0, end_y=0, text='', target=0, amount=0, memory=[]) | kwargs


def response(result):
    return {'status': 'completed', 'output': [{'type': 'message', 'content': [
        {'type': 'output_text', 'text': json.dumps(result)}]}]}


class FakeDesktop:
    def __init__(self):
        self.calls = []
        self.frames = []
        self.cleaned = False
        self.stale = False
        self.escaped = False

    def require_permissions(self):
        pass

    def escape_pressed(self):
        return self.escaped

    def windows(self):
        return [WINDOW]

    def capture(self, window, check):
        check()
        self.frames.append(FRAME)
        return FRAME

    def assert_current(self, window):
        if self.stale:
            self.stale = False
            raise StaleWindow('Moved')

    def execute(self, action, frame, check):
        check()
        self.calls.append(action)

    def clear(self):
        self.cleaned = True

    def follow_window(self, current, check=lambda: None):
        return current


class VisionTests(unittest.TestCase):
    def run_visual(self, ask, backend=None, cancel=None, guard=lambda: None):
        backend = backend or FakeDesktop()
        progress = Mock()
        result = vision.run('set plugin mix to 30%', [], CONFIG, backend,
                            cancel or threading.Event(), guard, progress, ask=ask)
        self.assertTrue(backend.cleaned)
        self.assertEqual(progress.call_args.args, ('',))
        return result, backend

    def test_retina_resizing_and_negative_monitor_coordinates(self):
        self.assertEqual(FRAME.point(800, 480), (-900, 420))
        other = Frame(Window(1, 1, '', (300, -900, 800, 500), 'id'), b'', 800, 500, [])
        self.assertEqual(other.point(400, 250), (700, -650))
        for point in [(-1, 0), (1600, 1), (0, 960), (float('nan'), 0), (True, 0)]:
            with self.assertRaises(PlanError):
                FRAME.point(*point)

    def test_rejects_arbitrary_windows_chords_and_out_of_bounds_drags(self):
        for action in [step('window', target=999), step('key', text='cmd+tab'),
                       step('type', text='text\n'), step('drag', end_x=1600),
                       step('scroll', amount=1.5), step('scroll', amount=True), step('ax_set', target=1),
                       step('click', x=float('inf')), step('click', target=True)]:
            with self.subTest(action=action), self.assertRaises(PlanError):
                vision.validate_step(action, FRAME, [WINDOW])

    def test_oversized_scroll_is_bounded_then_observed_again(self):
        for amount, expected in [(100, 10), (-1200, -10), (3, 3), (0, 0)]:
            with self.subTest(amount=amount):
                action = step('scroll', amount=amount)
                ask = Mock(side_effect=[action, step('done', text='Verified')])
                result, backend = self.run_visual(ask)
                self.assertEqual(result['kind'], 'visual_done')
                self.assertEqual(backend.calls[0]['amount'], expected)
                self.assertEqual(len(backend.frames), 2)
                self.assertEqual(action['amount'], amount)

    def test_prepared_plugin_is_initial_target_and_setup_is_not_repeated(self):
        backend = FakeDesktop()
        backend.prepared_window = Mock(return_value=WINDOW.id)
        ask = Mock(return_value=step('done', text='Verified'))
        vision.run('Set shelf 500 Hz -3 dB', [], CONFIG, backend, threading.Event(), lambda: None,
                   Mock(), ask=ask, opened_fx={'guid':'fx1','title':'plugin'})
        backend.prepared_window.assert_called_once()
        self.assertIn('ALREADY finished', ask.call_args.args[0])
        self.assertEqual(backend.calls, [])

    def test_native_preparation_waits_for_receipt_before_visual_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(Path(tmp) / 'session')
            worker.config = CONFIG
            prepared = {'kind':'vision', 'message':'Set shelf 500 Hz -3 dB', 'actions':[{'op':'open_fx'}], 'lua':''}
            def result():
                path = worker.directory / 'j1.response.json'
                deadline = time.monotonic() + 3
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                value = json.loads(path.read_text()); path.unlink()
                return value
            with patch('heycomputer.worker.providers.plan', return_value=prepared), patch.object(worker, 'visual_plan') as visual:
                worker.dispatch({'id':'j1','kind':'plan','text':'original full request','snapshot':{'project':'p'},'history':[]})
                self.assertEqual(result()['kind'], 'visual_prepare')
                visual.assert_not_called()
                visual.return_value = {'kind':'visual_done','message':'Verified'}
                worker.dispatch({'id':'j1','kind':'visual_continue','snapshot':{'project':'p','revision':2},'opened_fx':{'guid':'fx1'}})
                self.assertEqual(result()['kind'], 'visual_done')
                self.assertEqual(visual.call_args.args[0], 'Set shelf 500 Hz -3 dB')
                self.assertEqual(visual.call_args.args[3]['revision'], 2)
                self.assertIsNone(worker.visual_preparation)
                worker.dispatch({'id':'j1','kind':'visual_continue','snapshot':{'project':'p'},'opened_fx':{'guid':'fx1'}})
                self.assertEqual(result()['kind'], 'error')
                self.assertEqual(visual.call_count, 1)

    def test_cancelled_model_response_never_executes(self):
        cancel = threading.Event()
        def ask(*args):
            cancel.set()
            return step('click', x=5, y=5)
        result, backend = self.run_visual(ask, cancel=cancel)
        self.assertEqual(backend.calls, [])
        self.assertIn('cancelled', result['message'])

    def test_changed_geometry_discards_action_and_recaptures(self):
        backend = FakeDesktop()
        def ask(*args):
            if len(backend.frames) == 1:
                backend.stale = True
                return step('click', x=10, y=10)
            return step('reply', text='Which control?')
        result, backend = self.run_visual(ask, backend)
        self.assertEqual(len(backend.frames), 2)
        self.assertEqual(backend.calls, [])
        self.assertEqual(result['kind'], 'reply')

    def test_done_requires_observation_after_input(self):
        ask = Mock(side_effect=[step('click', x=15, y=15), step('done', text='Mix is 30%.')])
        result, backend = self.run_visual(ask)
        self.assertEqual(result['kind'], 'visual_done')
        self.assertEqual(len(backend.frames), 2)
        self.assertEqual(len(backend.calls), 1)

    def test_error_after_input_never_claims_no_changes(self):
        ask = Mock(side_effect=[step('click'), PlanError('Service unreachable. No project changes were made.')])
        result, _ = self.run_visual(ask)
        self.assertIn('may have changed', result['message'])
        self.assertNotIn('No project changes', result['message'])

    def test_blocked_first_action_does_not_claim_previous_changes(self):
        backend = FakeDesktop()
        backend.input_sent = False
        backend.execute = Mock(side_effect=vision.VisualStopped('Obstructed'))
        result, _ = self.run_visual(Mock(return_value=step('click')), backend)
        self.assertIn('No visual inputs were sent', result['message'])
        self.assertNotIn('Earlier visual actions', result['message'])

    def test_step_limit_and_escape(self):
        result, backend = self.run_visual(Mock(return_value=step('wait')))
        self.assertEqual(len(backend.frames), 20)
        self.assertIn('20-step', result['message'])
        backend = FakeDesktop()
        backend.escaped = True
        result, backend = self.run_visual(Mock(), backend)
        self.assertEqual(backend.frames, [])
        self.assertIn('Escape', result['message'])

    def test_guard_stops_before_capture(self):
        def guard():
            raise vision.VisualStopped('Project changed')
        result, backend = self.run_visual(Mock(), guard=guard)
        self.assertEqual(backend.frames, [])
        self.assertIn('Project changed', result['message'])

    def test_disabled_mode_never_captures(self):
        backend = FakeDesktop()
        with self.assertRaises(PlanError):
            vision.run('test', [], DEFAULTS, backend, threading.Event(), lambda: None, Mock(), ask=Mock())
        self.assertEqual(backend.frames, [])

    def test_deadline_after_network_response_discards_action(self):
        with patch('heycomputer.vision.time.monotonic', return_value=0) as clock:
            def ask(*args):
                clock.return_value = 181
                return step('click')
            result, backend = self.run_visual(ask)
        self.assertEqual(backend.calls, [])
        self.assertIn('three-minute', result['message'])

    def test_permission_denial_never_captures_or_calls_model(self):
        backend = FakeDesktop()
        backend.require_permissions = Mock(side_effect=PlanError('Permission needed'))
        ask = Mock()
        result, backend = self.run_visual(ask, backend)
        ask.assert_not_called()
        self.assertEqual(backend.frames, [])
        self.assertIn('Permission needed', result['message'])

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.request_json')
    def test_one_in_memory_window_image_uses_existing_endpoint(self, request, key):
        request.return_value = response(step('done', text='Verified'))
        vision.next_step('test', [], CONFIG, FRAME, [WINDOW], [])
        endpoint, _, payload = request.call_args.args
        self.assertEqual(endpoint, DEFAULTS['fallback_endpoint'])
        self.assertEqual(payload['model'], DEFAULTS['fallback_model'])
        self.assertFalse(payload['store'])
        content = payload['input'][0]['content']
        self.assertEqual(len(content), 2)
        self.assertEqual(base64.b64decode(content[1]['image_url'].split(',')[1]), FRAME.jpeg)
        context = json.loads(content[0]['text'])
        self.assertEqual(context['allowed_windows'], [{'id': 11, 'title': 'REAPER'}])
        self.assertNotIn('bounds', context['window'])

    @patch('heycomputer.providers.api_key', return_value='fixture')
    @patch('heycomputer.providers.request_json')
    def test_visual_fallback_is_opt_in_and_schema_is_not_mutated(self, request, key):
        result = dict(kind='vision', message='', lua='', actions=[])
        request.return_value = response(result)
        self.assertEqual(providers.openai_plan('adjust visible plugin', {}, CONFIG, []), result)
        self.assertIn('vision', request.call_args.args[2]['text']['format']['schema']['properties']['kind']['enum'])
        self.assertNotIn('vision', RESPONSE_SCHEMA['properties']['kind']['enum'])
        with self.assertRaises(PlanError):
            providers.openai_plan('adjust plugin', {}, DEFAULTS, [])
        with self.assertRaises(PlanError):
            validate_result(result | {'actions': [{'op': 'anything'}]}, {}, allow_vision=True)

    def test_worker_guard_and_session_cancellation(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(Path(tmp) / 'session')
            worker.desktop = FakeDesktop()
            worker.active_id = 'j2'
            atomic_json(worker.directory / 'visual-cancel.json', {'id': 'j1'})
            worker.poll_visual_cancel()
            self.assertFalse(worker.cancel_visual.is_set())
            atomic_json(worker.directory / 'visual-cancel.json', {'id': 'j2'})
            worker.poll_visual_cancel()
            self.assertTrue(worker.cancel_visual.is_set())
            worker.cancel_visual.clear()
            atomic_json(worker.directory / 'visual-guard.json', {'project': 'different', 'revision': 1, 'recording': False})
            result = worker.visual_plan('test', [], CONFIG, {'project': 'original', 'revision': 1}, 'j2')
            self.assertIn('changed project', result['message'])
            self.assertEqual(worker.desktop.frames, [])
            self.assertFalse(worker.visual_active)
            self.assertNotIn('jpeg', ''.join(p.read_text() for p in worker.directory.glob('*.json')))

    def test_worker_dispatch_runs_visual_fallback_and_keeps_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker = Worker(Path(tmp) / 'session')
            worker.config = CONFIG
            with patch('heycomputer.worker.providers.plan', return_value={'kind':'vision'}), \
                 patch.object(worker, 'visual_plan', return_value={'kind':'visual_done','message':'Mix is 30%.','visual':True}) as visual:
                worker.dispatch({'id':'j1','kind':'plan','text':'set mix','snapshot':{},'history':[]})
                response_path = worker.directory / 'j1.response.json'
                deadline = time.monotonic() + 3
                while not response_path.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                result = json.loads(response_path.read_text())
                self.assertEqual(result['kind'], 'visual_done')
                self.assertEqual(result['transcript'], 'set mix')
                visual.assert_called_once()


class MacPolicyTests(unittest.TestCase):
    def backend(self):
        # Dependency injection exercises policy without OS permissions or input.
        from heycomputer.vision_macos import MacDesktop
        b = object.__new__(MacDesktop)
        b.Q = Mock(kCGWindowOwnerPID='pid', kCGWindowNumber='id', kCGWindowBounds='bounds',
                   kCGWindowAlpha='alpha', kCGWindowLayer='layer', kCGWindowName='title', kCGWindowOwnerName='app')
        b.allowed_hosts, b.offered_hosts, b.elements = {}, {}, {}
        b._identity = lambda app: app
        b._mouse_window_at = Mock(return_value=90)
        b._apps = lambda: {101: ('reaper-identity', False), 202: ('host-identity', True)}
        b._rows = lambda: [
            {'id': wid, 'pid': pid, 'title': title, 'bounds': dict(X=0,Y=0,Width=800,Height=600)}
            for wid,pid,title in [(1,101,'REAPER'), (2,202,'Plugin'), (3,303,'Private unrelated app')]]
        return b

    def test_unrelated_apps_never_reach_allowed_window_list(self):
        b = self.backend()
        self.assertEqual([w.id for w in b.windows()], [1])
        self.assertEqual([w.id for w in b.windows(candidates=True)], [1,2])
        b.approve_host(2, True)
        self.assertEqual([w.id for w in b.windows()], [1,2])
        b.allowed_hosts[2] = 'old-process'
        self.assertEqual([w.id for w in b.windows()], [1])
        with self.assertRaises(PlanError):
            b.approve_host(3, True)

    def test_assistant_windows_are_excluded_under_both_product_names(self):
        b = self.backend()
        rows = b._rows()
        rows += [{'id': 4 + i, 'pid': 101, 'title': title,
                  'bounds': dict(X=0, Y=0, Width=800, Height=600)}
                 for i, title in enumerate(('HeyComputer', 'ReaJev'))]
        b._rows = lambda: rows
        self.assertEqual([w.id for w in b.windows()], [1])

    def test_capture_uses_exact_window_filter_and_no_desktop_fallback(self):
        b = self.backend()
        b.require_permissions = lambda: None
        b.assert_current = lambda w: None
        b._focus = lambda w, check: None
        b._controls = lambda w: []
        b.A, b.SC = Mock(), Mock()
        target = Mock()
        target.windowID.return_value = 11
        target.owningApplication.return_value.processID.return_value = 101
        content = Mock()
        content.windows.return_value = [target]
        b.SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_.side_effect = lambda a,c,cb: cb(content,None)
        b.SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_.side_effect = lambda f,c,cb: cb('cgimage',None)
        b.A.NSBitmapImageRep.alloc.return_value.initWithCGImage_.return_value.representationUsingType_properties_.return_value = b'jpeg'
        b.Q.CGImageGetWidth.return_value = 1600
        b.Q.CGImageGetHeight.return_value = 960
        frame = b.capture(WINDOW, lambda: None)
        b.SC.SCContentFilter.alloc.return_value.initWithDesktopIndependentWindow_.assert_called_once_with(target)
        config = b.SC.SCStreamConfiguration.alloc.return_value.init.return_value
        config.setIgnoreShadowsSingleWindow_.assert_called_once_with(True)
        config.setIncludeChildWindows_.assert_called_once_with(False)
        self.assertEqual(frame.jpeg, b'jpeg')
        target.owningApplication.return_value.processID.return_value = 999
        with self.assertRaises(PlanError):
            b.capture(WINDOW, lambda: None)
        self.assertEqual(b.SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_.call_count, 1)

    def test_obscuring_window_blocks_input(self):
        b = self.backend()
        b._rows = lambda: [{'id': 90, 'bounds': dict(X=-1400,Y=120,Width=1000,Height=600)}]
        with self.assertRaises(vision.VisualStopped):
            b._unobscured(WINDOW, (-900, 420))

    def test_overlay_requires_positive_target_hit_test(self):
        b = self.backend()
        b._rows = lambda: [{'id': 90, 'app': 'Overlay app', 'bounds': dict(X=-1400,Y=120,Width=1000,Height=600)}]
        with self.assertRaisesRegex(vision.VisualStopped, r'Overlay app.*#90'):
            b._unobscured(WINDOW, (-900, 420))
        b._mouse_window_at = Mock(return_value=WINDOW.id)
        b._unobscured(WINDOW, (-900, 420))
        b._mouse_window_at.assert_called_once_with((-900, 420))

    def test_unidentified_or_other_reaper_window_blocks_input(self):
        b = self.backend()
        for wid in (0, 12):
            b._mouse_window_at.return_value = wid
            with self.assertRaises(vision.VisualStopped):
                b._unobscured(WINDOW, (-900, 420))
        b._mouse_window_at.return_value = WINDOW.id
        with self.assertRaises(vision.VisualStopped):
            b._unobscured(WINDOW, (0, 0))

    def test_native_mouse_hit_test_uses_primary_display_and_never_skips_windows(self):
        b = self.backend()
        del b._mouse_window_at
        b.A = Mock()
        b.Q.CGDisplayBounds.return_value.size.height = 1080
        native = b.A.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_
        native.return_value = 11
        self.assertEqual(b._mouse_window_at((-900, 420)), 11)
        native.assert_called_once_with((-900, 660), 0)
        b._mouse_window_at((100, -500))
        native.assert_called_with((100, 1580), 0)
        b.Q.CGDisplayBounds.return_value.size.height = 0
        with self.assertRaises(vision.VisualStopped):
            b._mouse_window_at((100, 100))

    def test_prepared_window_requires_unique_exact_title(self):
        b = self.backend()
        b.windows = Mock(return_value=[WINDOW])
        b._ax_window = Mock(return_value='root')
        self.assertEqual(b.prepared_window({'title':'REAPER'}), WINDOW.id)
        with self.assertRaises(vision.VisualStopped):
            b.prepared_window({'title':'Other plugin'})
        b.windows.return_value = [WINDOW, WINDOW]
        with self.assertRaises(vision.VisualStopped):
            b.prepared_window({'title':'REAPER'})

    def test_focus_transition_retries_but_never_ignores_a_foreign_window(self):
        b = self.backend()
        b._focused_window = Mock(side_effect=[StaleWindow('pending'), WINDOW.id])
        check = Mock()
        with patch('heycomputer.vision_macos.time.sleep'):
            self.assertEqual(b.follow_window(5, check), WINDOW.id)
        self.assertEqual(check.call_count, 2)
        b._focused_window = Mock(side_effect=vision.VisualStopped('Foreign window'))
        with self.assertRaises(vision.VisualStopped):
            b.follow_window(5)
        b._focused_window.assert_called_once()

    def test_elevated_dialog_requires_accessible_window_identity(self):
        b = self.backend()
        b._rows = lambda: [{'id':11,'pid':101,'layer':3,'title':'Routing','bounds':dict(X=0,Y=0,Width=400,Height=300)}]
        b._ax_window = Mock(return_value=None)
        self.assertEqual(b.windows(), [])
        b._ax_window.return_value = 'root'
        self.assertEqual([w.id for w in b.windows()], [11])

    def test_direct_accessibility_action_does_not_use_mouse_occlusion(self):
        b = self.backend()
        b.AX = Mock()
        b.elements = {1: 'button'}
        b._element_in_window = Mock(return_value=True)
        b._front = Mock()
        b.assert_current = Mock()
        b._unobscured = Mock(side_effect=AssertionError('Mouse check used for AX'))
        b.AX.AXUIElementPerformAction.return_value = 0
        b.execute(step('ax_press', target=1), FRAME, lambda: None)
        b._unobscured.assert_not_called()
        b.AX.AXUIElementPerformAction.assert_called_once_with('button','AXPress')
        self.assertTrue(b.input_sent)
        b._element_in_window.return_value = False
        with self.assertRaisesRegex(PlanError, 'no longer belongs'):
            b.execute(step('ax_press', target=1), FRAME, lambda: None)
        self.assertEqual(b.AX.AXUIElementPerformAction.call_count, 1)

    def test_hit_testing_rejects_other_process_and_same_process_wrong_window(self):
        b = self.backend()
        b.AX = Mock()
        b.AX.AXUIElementGetPid.return_value = (0, 999)
        self.assertFalse(b._element_in_window('element', WINDOW))
        b.AX.AXUIElementGetPid.return_value = (0, WINDOW.pid)
        b._attr = Mock(return_value='root')
        b._ax_bounds = Mock(return_value=(0,0,100,100))
        b._ax_window = Mock(return_value='unique-root')
        self.assertFalse(b._element_in_window('element', WINDOW))
        b._ax_bounds.return_value = WINDOW.bounds
        self.assertTrue(b._element_in_window('element', WINDOW))
        b._ax_window.return_value = None
        self.assertFalse(b._element_in_window('element', WINDOW))

    def test_cancel_mid_drag_always_releases_mouse_button(self):
        b = self.backend()
        b._front = Mock()
        b.assert_current = Mock()
        b._unobscured = Mock()
        Q = b.Q
        Q.kCGMouseButtonLeft = 0
        Q.kCGEventLeftMouseDown = 1
        Q.kCGEventLeftMouseUp = 2
        Q.kCGEventLeftMouseDragged = 6
        Q.CGEventCreateMouseEvent.side_effect = lambda _,kind,p,button: (kind,p)
        checks = 0
        def check():
            nonlocal checks
            checks += 1
            if checks == 4:
                raise vision.VisualStopped('cancelled')
        with self.assertRaises(vision.VisualStopped):
            b.execute(step('drag', x=10,y=10,end_x=100,end_y=100), FRAME, check)
        posted = [call.args[1][0] for call in Q.CGEventPost.call_args_list]
        self.assertEqual(posted, [1,6,2])


if __name__ == '__main__':
    unittest.main()
