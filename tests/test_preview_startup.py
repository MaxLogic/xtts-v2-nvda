"""Exercise manager scheduling with a controlled slow external-helper boundary."""
import builtins
import importlib.util
import logging
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "addon"


class PreviewStartupTests(unittest.TestCase):
    def test_repeated_warmup_does_not_wait_for_helper_startup(self):
        synths = types.ModuleType("synthDrivers")
        synths.__path__ = [str(ROOT / "synthDrivers")]
        package = types.ModuleType("synthDrivers.maxlogic_xtts_v2")
        package.__path__ = [str(ROOT / "synthDrivers/maxlogic_xtts_v2")]
        boundary = {
            "synthDrivers": synths,
            "synthDrivers.maxlogic_xtts_v2": package,
            "addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
            "config": types.SimpleNamespace(conf={}),
            "nvwave": types.SimpleNamespace(),
            "synthDriverHandler": types.SimpleNamespace(getSynth=lambda: None),
            "logHandler": types.SimpleNamespace(log=logging.getLogger("test")),
            "wx": types.SimpleNamespace(CallAfter=lambda callback, *args: callback(*args)),
        }
        with patch.dict(sys.modules, boundary), patch.object(builtins, "_", lambda s: s, create=True):
            spec = importlib.util.spec_from_file_location(
                "preview_service", ROOT / "globalPlugins/maxlogic_xtts_v2_manager/service.py"
            )
            service = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(service)
            entered, release, returned = threading.Event(), threading.Event(), threading.Event()
            helpers = []
            completed = []
            startup_options = []

            def slow_helper(*args, **kwargs):
                startup_options.append(kwargs)
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test did not release helper")
                helper = types.SimpleNamespace(close=lambda: None)
                helpers.append(helper)
                return helper

            def another_page():
                service.prepare_preview_runtime_async()
                returned.set()

            with patch.object(service, "HelperEngineClient", slow_helper):
                self.assertTrue(service.prepare_preview_runtime_async(on_complete=completed.append))
                worker = service._preview_helper_thread
                self.assertTrue(entered.wait(2))
                caller = threading.Thread(target=another_page, daemon=True)
                caller.start()
                try:
                    self.assertTrue(returned.wait(0.5), "dialog construction waited for helper startup")
                finally:
                    release.set()
                    caller.join(2)
                    worker.join(2)
                    self.assertFalse(service.prepare_preview_runtime_async())
                    service.close_preview_helper()
                self.assertEqual(len(helpers), 1)
                self.assertEqual(completed, [None])
                self.assertTrue(startup_options[0]["skip_prewarm"])


if __name__ == "__main__":
    unittest.main()
