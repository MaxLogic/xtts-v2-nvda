"""Busy operations must report an interruption, not "list index out of range"."""
import builtins
import importlib.util
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch


UI_PATH = Path(__file__).resolve().parents[1] / "addon/globalPlugins/maxlogic_xtts_v2_manager/_ui.py"


class _Window(object):
    def __init__(self):
        self.enabled = True
        self._operation_busy = False

    def IsEnabled(self): return self.enabled
    def Enable(self, enable=True): self.enabled = enable
    def Disable(self): self.enabled = False
    def IsBeingDeleted(self): return False
    def IsActive(self): return True


class _EventLoop(object):
    """Runs queued calls until Exit, or ends after a moment as when NVDA exits its main loop."""
    pending = []

    def __init__(self):
        self.exited = False

    def Run(self):
        deadline = time.monotonic() + 0.5
        while not self.exited and time.monotonic() < deadline:
            while self.pending:
                self.pending.pop(0)()
            time.sleep(0.01)
        return 0

    def Exit(self, code=0):
        self.exited = True


class _ButtonBusy(object):
    def __init__(self, button): pass
    def stop(self): pass


def load_ui(top):
    class _Base(object):
        pass

    wx = types.SimpleNamespace(
        Accessible=_Base, TextCtrl=_Base, Panel=_Base, Button=_Base,
        Window=types.SimpleNamespace(FindFocus=lambda: None),
        GetTopLevelParent=lambda window: top,
        GUIEventLoop=_EventLoop,
        CallAfter=lambda fn, *args: _EventLoop.pending.append(lambda: fn(*args)),
    )
    patcher = patch.dict(sys.modules, {"wx": wx, "gui": types.SimpleNamespace(),
        "ui": types.SimpleNamespace(message=lambda text: None)})
    patcher.start()
    spec = importlib.util.spec_from_file_location("maxlogic_xtts_v2_ui_under_test", UI_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ButtonBusy = _ButtonBusy
    return module, patcher


class RunBusyTests(unittest.TestCase):
    def setUp(self):
        self.top = _Window()
        self.ui, patcher = load_ui(self.top)
        self.addCleanup(patcher.stop)
        translate = patch.object(builtins, "_", lambda text: text, create=True)
        translate.start()
        self.addCleanup(translate.stop)
        self.release = threading.Event()
        self.addCleanup(self.release.set)
        _EventLoop.pending[:] = []

    def test_an_interrupted_operation_reports_that_it_did_not_finish(self):
        def slow_work():
            self.release.wait(5)
            return "clone"

        with self.assertRaises(RuntimeError) as raised:
            self.ui.run_busy(object(), "Cloning...", slow_work, button=object())
        self.assertIn("did not finish", str(raised.exception))
        self.assertTrue(self.top.enabled, "the manager stayed disabled")
        self.assertFalse(self.top._operation_busy)

    def test_a_finished_operation_returns_its_result(self):
        self.release.set()
        self.assertEqual(self.ui.run_busy(object(), "Cloning...", lambda: "clone", button=object()), "clone")


if __name__ == "__main__":
    unittest.main()
