"""macOS 14+ REAPER-only capture/input. No desktop capture API or disk images.

SCContentFilter(desktopIndependentWindow:) excludes occluding applications.
Coordinates use Quartz global points throughout (including negative displays).
"""
from pathlib import Path
import platform
import threading
import time

from .protocol import PlanError
from .vision import Window, Frame, StaleWindow, VisualStopped, KEYS


def prepare_window_server():
    """Initialize AppKit's WindowServer connection on the helper MAIN thread.

    Without NSApplication initialization, the native mouse hit-test returns 0
    even though Quartz can enumerate windows. No windows or menus are created.
    """
    if platform.system() != "Darwin":
        return
    import AppKit as A
    if not A.NSThread.isMainThread():
        raise PlanError("Initialize visual control on the helper's main thread.")
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyProhibited)


class MacDesktop:
    def __init__(self):
        if platform.system() != "Darwin" or int(platform.mac_ver()[0].split('.')[0]) < 14:
            raise PlanError("Visual control requires macOS 14 or later.")
        try:
            import AppKit as A
            import Quartz as Q
            import ApplicationServices as AX
            import ScreenCaptureKit as SC
        except ImportError:
            raise PlanError("Run scripts/setup.sh again to install the visual-control libraries, then reopen HeyComputer.") from None
        self.A, self.Q, self.AX, self.SC = A, Q, AX, SC
        self.allowed_hosts = {}  # Session-only approvals: window ID -> process identity
        self.offered_hosts = {}
        self.elements = {}
        self.focused = None
        self.owner = None
        self.input_sent = False

    def permissions(self, prompt=False):
        screen = bool(self.Q.CGPreflightScreenCaptureAccess())
        access = bool(self.AX.AXIsProcessTrusted())
        if prompt:
            if not screen:
                self.Q.CGRequestScreenCaptureAccess()
            if not access:
                self.AX.AXIsProcessTrustedWithOptions({self.AX.kAXTrustedCheckOptionPrompt: True})
        return {"screen": screen, "accessibility": access}

    def require_permissions(self):
        p = self.permissions()
        if not all(p.values()):
            raise PlanError("Allow Screen Recording and Accessibility for the HeyComputer Python helper in macOS Privacy & Security, then reopen HeyComputer. Use Check permissions in Settings.")

    def _apps(self):
        apps = self.A.NSWorkspace.sharedWorkspace().runningApplications()
        roots = [a for a in apps if a.bundleIdentifier() == "com.cockos.reaper"]
        if len(roots) != 1:
            raise PlanError("Open exactly one REAPER application instance for visual control.")
        root = roots[0]
        identity = self._identity(root)
        if self.owner is not None and self.owner != identity:
            raise PlanError("REAPER restarted. Reopen HeyComputer before using visual control.")
        self.owner = identity
        bundle = Path(str(root.bundleURL().path())).resolve()
        allowed = {int(root.processIdentifier()): (root, False)}
        for app in apps:
            if app.processIdentifier() in allowed or not app.executableURL():
                continue
            path = Path(str(app.executableURL().path())).resolve()
            # A process name alone never authorizes a host. It must ship inside
            # the actual running REAPER bundle, then its window is approved locally.
            if path.is_relative_to(bundle) and path.name.startswith("reaper_host"):
                allowed[int(app.processIdentifier())] = (app, True)
        return allowed

    @staticmethod
    def _identity(app):
        return f"{app.executableURL().path()}:{app.processIdentifier()}:{app.launchDate()}"

    def _rows(self):
        return self.Q.CGWindowListCopyWindowInfo(self.Q.kCGWindowListOptionOnScreenOnly | self.Q.kCGWindowListExcludeDesktopElements, self.Q.kCGNullWindowID) or []

    def windows(self, candidates=False):
        apps, windows = self._apps(), []
        for row in self._rows():
            pid = int(row.get(self.Q.kCGWindowOwnerPID, 0))
            if pid not in apps:
                continue
            app, host = apps[pid]
            wid = int(row[self.Q.kCGWindowNumber])
            identity = self._identity(app)
            if host and not candidates and self.allowed_hosts.get(wid) != identity:
                continue
            b = row[self.Q.kCGWindowBounds]
            bounds = tuple(float(b[k]) for k in ("X", "Y", "Width", "Height"))
            if min(bounds[2:]) < 40 or row.get(self.Q.kCGWindowAlpha, 1) <= 0:
                continue
            title = str(row.get(self.Q.kCGWindowName, ""))[:300]
            if title in ("HeyComputer", "ReaJev"):
                continue
            window = Window(wid, pid, title, bounds, identity, host)
            # Floating REAPER dialogs can use elevated window levels. Accept
            # them only when AXWindows identifies a unique actual window;
            # menus/popovers without an AX window remain excluded.
            if row.get(self.Q.kCGWindowLayer, 0) != 0 and self._ax_window(window) is None:
                continue
            windows.append(window)
        if candidates:
            self.offered_hosts = {w.id: w.identity for w in windows if w.host}
        return windows

    def approve_host(self, wid, enabled):
        offered = self.offered_hosts.get(wid)
        match = next((w for w in self.windows(candidates=True) if w.id == wid and w.host), None)
        if not match or offered != match.identity:
            raise PlanError("That plugin-host window changed. Refresh windows and select it again.")
        if enabled:
            self.allowed_hosts[wid] = match.identity
        else:
            self.allowed_hosts.pop(wid, None)

    def status(self, prompt=False):
        p = self.permissions(prompt)
        windows = self.windows(candidates=True) if p["screen"] else []
        return {**p, "hosts": [{**w.public(), "allowed": self.allowed_hosts.get(w.id) == w.identity}
                              for w in windows if w.host]}

    def assert_current(self, window):
        live = next((w for w in self.windows() if w.id == window.id and w.identity == window.identity), None)
        if live is None:
            raise VisualStopped("The allowed target window closed or changed owner.")
        if live.bounds != window.bounds:
            raise StaleWindow("Window geometry changed.")

    def _attr(self, element, name):
        error, value = self.AX.AXUIElementCopyAttributeValue(element, name, None)
        return value if error == 0 else None

    def _ax_bounds(self, element):
        pos, size = self._attr(element, "AXPosition"), self._attr(element, "AXSize")
        if pos is None or size is None:
            return None
        okp, p = self.AX.AXValueGetValue(pos, self.AX.kAXValueCGPointType, None)
        oks, s = self.AX.AXValueGetValue(size, self.AX.kAXValueCGSizeType, None)
        return (p.x, p.y, s.width, s.height) if okp and oks else None

    def _ax_window(self, window):
        app = self.AX.AXUIElementCreateApplication(window.pid)
        matches = []
        for element in self._attr(app, "AXWindows") or []:
            bounds = self._ax_bounds(element)
            if bounds and all(abs(a-b) < 2 for a, b in zip(bounds, window.bounds)):
                matches.append(element)
        # Fail closed on ambiguous windows, not an arbitrary window in the app.
        return matches[0] if len(matches) == 1 else None

    def _front(self, window):
        front = self.A.NSWorkspace.sharedWorkspace().frontmostApplication()
        if not front or int(front.processIdentifier()) != window.pid:
            raise VisualStopped("Focus moved away from the target application. Visual control stopped.")
        app = self.AX.AXUIElementCreateApplication(window.pid)
        focused = self._attr(app, "AXFocusedWindow")
        bounds = self._ax_bounds(focused) if focused is not None else None
        if not bounds or any(abs(a-b) >= 2 for a, b in zip(bounds, window.bounds)) or self._ax_window(window) is None:
            raise VisualStopped("The target window no longer has keyboard focus.")

    def prepared_window(self, opened_fx):
        title = opened_fx.get("title", "")
        if title:
            matches = [w for w in self.windows() if w.title == title]
            if len(matches) == 1 and self._ax_window(matches[0]) is not None:
                return matches[0].id
            raise VisualStopped("The exact plugin window opened by REAPER is unavailable or ambiguous.")
        # Without JS_ReaScriptAPI, require the focused allowed window to name
        # both the plugin and its track. Never guess from the largest window.
        current = self.follow_window(None)
        window = next(w for w in self.windows() if w.id == current)
        name = opened_fx.get("name", "").partition(":")[2].strip() or opened_fx.get("name", "")
        track = opened_fx.get("track_name", "")
        if not name or not track or name.casefold() not in window.title.casefold() or track.casefold() not in window.title.casefold():
            raise VisualStopped("Cannot verify the plugin window opened by REAPER. Install JS_ReaScriptAPI for exact window-title handoff.")
        return current

    def follow_window(self, current, check=lambda: None):
        # AX focus and the WindowServer list may update in different frames.
        for attempt in range(5):
            check()
            try:
                return self._focused_window()
            except StaleWindow:
                if attempt == 4:
                    raise VisualStopped("Cannot safely identify the new focused REAPER window after waiting for it to appear.") from None
                time.sleep(.1)

    def _focused_window(self):
        front = self.A.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            raise VisualStopped("The target application lost focus.")
        pid = int(front.processIdentifier())
        allowed = [w for w in self.windows() if w.pid == pid]
        if not allowed:
            raise VisualStopped("Focus moved to a window that is not allowed. Visual control stopped.")
        focused = self._attr(self.AX.AXUIElementCreateApplication(pid), "AXFocusedWindow")
        bounds = self._ax_bounds(focused) if focused is not None else None
        matches = [w for w in allowed if bounds and all(abs(a-b)<2 for a,b in zip(bounds,w.bounds))]
        if not matches:
            raise StaleWindow("The focused window has not appeared in the allowed window list.")
        if len(matches) > 1:
            raise VisualStopped("More than one allowed window matches the focused window; visual control stopped.")
        self.focused = matches[0].id
        return self.focused

    def _focus(self, window, check):
        if self.focused == window.id:
            self._front(window)
            return
        element = self._ax_window(window)
        if element is None:
            raise PlanError("Cannot identify this window safely for input. Bring it forward and try again.")
        app = self.A.NSRunningApplication.runningApplicationWithProcessIdentifier_(window.pid)
        app.activateWithOptions_(self.A.NSApplicationActivateIgnoringOtherApps)
        self.AX.AXUIElementPerformAction(element, "AXRaise")
        time.sleep(.15)
        check()
        self.assert_current(window)
        self._front(window)
        self.focused = window.id

    def _wait(self, start, check):
        done, result = threading.Event(), []
        def callback(value, error):
            result.extend((value, error))
            done.set()
        start(callback)
        until = time.monotonic() + 8
        while not done.wait(.05):
            check()
            if time.monotonic() > until:
                raise PlanError("macOS window capture timed out.")
        check()
        if result[1] is not None or result[0] is None:
            raise PlanError("macOS could not capture the allowed window. Check Screen Recording permission.")
        return result[0]

    def capture(self, window, check):
        self.require_permissions()
        check()
        self.assert_current(window)
        self._focus(window, check)
        content = self._wait(lambda cb: self.SC.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(True, True, cb), check)
        target = next((w for w in content.windows() if int(w.windowID()) == window.id
                       and w.owningApplication() is not None and int(w.owningApplication().processID()) == window.pid), None)
        if target is None:
            raise PlanError("The allowed window cannot be captured.")
        filt = self.SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(target)
        config = self.SC.SCStreamConfiguration.alloc().init()
        if not hasattr(config, "setIgnoreShadowsSingleWindow_"):
            raise PlanError("Update macOS to capture window coordinates without shadow offsets.")
        config.setIgnoreShadowsSingleWindow_(True)
        config.setShowsCursor_(False)
        if hasattr(config, "setIncludeChildWindows_"):
            config.setIncludeChildWindows_(False)
        scale = min(2.0, 1600 / max(window.bounds[2:]))
        width, height = [max(1, round(n * scale)) for n in window.bounds[2:]]
        config.setWidth_(width)
        config.setHeight_(height)
        # EXACTLY one selected window. Never call CGDisplayCreateImage, desktop
        # screenshot commands, or a display-based content filter as a fallback.
        cgimage = self._wait(lambda cb: self.SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(filt, config, cb), check)
        self.assert_current(window)
        rep = self.A.NSBitmapImageRep.alloc().initWithCGImage_(cgimage)
        jpeg = bytes(rep.representationUsingType_properties_(self.A.NSBitmapImageFileTypeJPEG, {self.A.NSImageCompressionFactor: .75}))
        if not jpeg or len(jpeg) > 2_000_000:
            raise PlanError("Window image exceeded the upload limit.")
        width, height = int(self.Q.CGImageGetWidth(cgimage)), int(self.Q.CGImageGetHeight(cgimage))
        if abs(width / height - window.bounds[2] / window.bounds[3]) > .02:
            raise PlanError("Window image geometry does not match its input bounds.")
        return Frame(window, jpeg, width, height, self._controls(window))

    def _controls(self, window):
        self.elements = {}
        root = self._ax_window(window)
        if root is None:
            return []
        result, queue, visited = [], [(root, 0)], 0
        while queue and visited < 200:
            element, depth = queue.pop(0)
            visited += 1
            role = str(self._attr(element, "AXRole") or "")
            if self._attr(element, "AXSubrole") == "AXSecureTextField":
                continue
            operations = []
            error, names = self.AX.AXUIElementCopyActionNames(element, None)
            if error == 0 and "AXPress" in (names or []):
                operations.append("ax_press")
            error, writable = self.AX.AXUIElementIsAttributeSettable(element, "AXValue", None)
            if error == 0 and writable and role == "AXTextField":
                operations.append("ax_set")
            if operations and self._attr(element, "AXEnabled") is not False:
                key = len(result) + 1
                label = str(self._attr(element, "AXTitle") or self._attr(element, "AXDescription") or "")[:160]
                value = self._attr(element, "AXValue")
                result.append({"id": key, "role": role, "label": label,
                               "value": str(value)[:160] if isinstance(value, (str, int, float)) else "",
                               "operations": operations})
                self.elements[key] = element
            if depth < 6:
                queue.extend((c, depth + 1) for c in (self._attr(element, "AXChildren") or [])[:100])
        return result

    def escape_pressed(self):
        return bool(self.Q.CGEventSourceKeyState(self.Q.kCGEventSourceStateCombinedSessionState, 53))

    def _element_in_window(self, element, window):
        error, pid = self.AX.AXUIElementGetPid(element, None)
        if error != 0 or pid != window.pid:
            return False
        root = self._attr(element, "AXWindow")
        # REAPER's custom controls may expose only their parent chain.
        current = element
        for _ in range(16):
            if root is not None:
                break
            if current is None:
                return False
            if self._attr(current, "AXRole") == "AXWindow":
                root = current
                break
            current = self._attr(current, "AXParent")
        bounds = self._ax_bounds(root) if root is not None else None
        return bool(bounds and all(abs(a-b)<2 for a,b in zip(bounds,window.bounds))
                    and self._ax_window(window) is not None)

    def _mouse_window_at(self, point):
        # AppKit uses global bottom-left points, Quartz/AX use top-left points.
        # Flip about the PRIMARY display, not the active display: this also
        # handles monitors left of, above, or below the primary display.
        height = float(self.Q.CGDisplayBounds(self.Q.CGMainDisplayID()).size.height)
        if height <= 0:
            raise VisualStopped("macOS display geometry is unavailable. Reopen HeyComputer.")
        cocoa_point = (point[0], height - point[1])
        # Unlike rectangle enumeration or AX hit testing, this uses mouse-down
        # rules and excludes transparent pixels / windows that ignore input.
        # Zero means search from the TOP, never search underneath a real blocker.
        return int(self.A.NSWindow.windowNumberAtPoint_belowWindowWithWindowNumber_(cocoa_point, 0))

    def _unobscured(self, window, point):
        x, y = point
        left, top, width, height = window.bounds
        if not (left <= x < left + width and top <= y < top + height):
            raise VisualStopped("The input target is outside the captured window.")
        wid = self._mouse_window_at(point)
        if wid == window.id:
            return
        if wid <= 0:
            raise VisualStopped("macOS could not identify the window that would receive this input. Reopen HeyComputer and try again.")
        row = next((r for r in self._rows() if int(r[self.Q.kCGWindowNumber]) == wid), {})
        app = str(row.get(self.Q.kCGWindowOwnerName, "another application"))[:100]
        raise VisualStopped(f"A {app} window (#{wid}) would receive input intended for {window.title or 'REAPER'}, at screen point ({x:.0f}, {y:.0f}). Move that window or overlay away from the target and try again. No input was sent to that window.")

    def execute(self, step, frame, check):
        Q, window, op = self.Q, frame.window, step["op"]
        def ready(point=None):
            check()
            self.assert_current(window)
            self._front(window)
            if point is not None:
                self._unobscured(window, point)
        ready()
        if op in ("ax_press", "ax_set"):
            element = self.elements.get(step["target"])
            if element is None:
                raise PlanError("Accessibility control expired.")
            if not self._element_in_window(element, window):
                raise PlanError("The accessibility control no longer belongs to the captured window.")
            # AXPress/AXValue target this exact element, not the screen point.
            # Applying the physical mouse occlusion check here rejects valid
            # controls under transparent overlays unnecessarily.
            ready()
            self.input_sent = True
            if op == "ax_press":
                error = self.AX.AXUIElementPerformAction(element, "AXPress")
            else:
                error = self.AX.AXUIElementSetAttributeValue(element, "AXValue", step["text"])
            if error != 0:
                raise PlanError("The accessibility control rejected the action.")
            return
        if op in ("key", "type"):
            # Prevent input if a menu/popover became the key target since capture.
            app = self.AX.AXUIElementCreateApplication(window.pid)
            focus = self._attr(app, "AXFocusedUIElement")
            if focus is not None and self._attr(focus, "AXSubrole") == "AXSecureTextField":
                raise PlanError("Visual control cannot type into password fields.")
            if op == "type":
                for offset in range(0, len(step["text"]), 32):
                    ready()
                    value = step["text"][offset:offset + 32]
                    event = Q.CGEventCreateKeyboardEvent(None, 0, True)
                    Q.CGEventKeyboardSetUnicodeString(event, len(value.encode("utf-16-le"))//2, value)
                    up = Q.CGEventCreateKeyboardEvent(None, 0, False)
                    try:
                        self.input_sent = True
                        Q.CGEventPost(Q.kCGHIDEventTap, event)
                    finally:
                        Q.CGEventPost(Q.kCGHIDEventTap, up)
            else:
                parts = step["text"].split("+")
                code = KEYS[parts[-1]]
                flags = (Q.kCGEventFlagMaskCommand if "cmd" in parts else 0) | (Q.kCGEventFlagMaskShift if "shift" in parts else 0)
                down = Q.CGEventCreateKeyboardEvent(None, code, True)
                up = Q.CGEventCreateKeyboardEvent(None, code, False)
                Q.CGEventSetFlags(down, flags)
                Q.CGEventSetFlags(up, 0)
                try:
                    ready()
                    self.input_sent = True
                    Q.CGEventPost(Q.kCGHIDEventTap, down)
                finally:
                    Q.CGEventPost(Q.kCGHIDEventTap, up)
            return
        point = frame.point(step["x"], step["y"])
        ready(point)
        def mouse(kind, p, button=Q.kCGMouseButtonLeft, clicks=1):
            event = Q.CGEventCreateMouseEvent(None, kind, p, button)
            Q.CGEventSetIntegerValueField(event, Q.kCGMouseEventClickState, clicks)
            self.input_sent = True
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        if op == "scroll":
            mouse(Q.kCGEventMouseMoved, point)
            ready(point)
            event = Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, step["amount"])
            Q.CGEventPost(Q.kCGHIDEventTap, event)
        elif op == "drag":
            end = frame.point(step["end_x"], step["end_y"])
            self._unobscured(window, end)
            last = point
            try:
                mouse(Q.kCGEventLeftMouseDown, point)
                for n in range(1, 21):
                    dest = tuple(a + (b-a)*n/20 for a,b in zip(point, end))
                    ready(dest)
                    mouse(Q.kCGEventLeftMouseDragged, dest)
                    last = dest
                    time.sleep(.02)
            finally:
                mouse(Q.kCGEventLeftMouseUp, last)
        else:
            right = op == "right_click"
            button = Q.kCGMouseButtonRight if right else Q.kCGMouseButtonLeft
            down = Q.kCGEventRightMouseDown if right else Q.kCGEventLeftMouseDown
            up = Q.kCGEventRightMouseUp if right else Q.kCGEventLeftMouseUp
            for n in range(1, 3 if op == "double_click" else 2):
                ready(point)
                try:
                    mouse(down, point, button, n)
                finally:
                    mouse(up, point, button, n)

    def clear(self):
        self.elements = {}
        self.input_sent = False
        self.focused = None
