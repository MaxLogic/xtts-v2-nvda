"""The manager's speech engine is expensive to load and expensive to keep."""
import builtins
import importlib.util
import logging
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "addon"


class Helper:
    sample_rate = 24000

    def __init__(self):
        self._voices = ["test"]
        self.closed = threading.Event()
        self.reloads = []

    def reload_voices(self, preferred_voice=None):
        self.reloads.append(preferred_voice)

    def stream_synthesize_to_int16(self, text, **kwargs):
        yield memoryview(b"\x01\x00" * 240)

    def close(self):
        self.closed.set()


class Player:
    def __init__(self, **kwargs): pass
    def stop(self): pass
    def idle(self): pass
    def close(self): pass
    def feed(self, data): pass


class PreviewHelperLifecycleTests(unittest.TestCase):
    def setUp(self):
        synths = types.ModuleType("synthDrivers")
        synths.__path__ = [str(ROOT / "synthDrivers")]
        package = types.ModuleType("synthDrivers.maxlogic_xtts_v2")
        package.__path__ = [str(ROOT / "synthDrivers/maxlogic_xtts_v2")]
        boundary = {
            "synthDrivers": synths, "synthDrivers.maxlogic_xtts_v2": package,
            "addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
            "config": types.SimpleNamespace(conf={}),
            "nvwave": types.SimpleNamespace(WavePlayer=Player),
            "synthDriverHandler": types.SimpleNamespace(getSynth=lambda: None),
            "logHandler": types.SimpleNamespace(log=logging.getLogger("test")),
            "wx": types.SimpleNamespace(CallAfter=lambda fn, *a: fn(*a)),
        }
        data = tempfile.TemporaryDirectory(prefix="xtts-preview-lifecycle-")
        self.addCleanup(data.cleanup)
        for patcher in (patch.dict(os.environ, APPDATA=data.name), patch.dict(sys.modules, boundary),
                patch.object(builtins, "_", lambda s: s, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location(
            "preview_lifecycle_service", ROOT / "globalPlugins/maxlogic_xtts_v2_manager/service.py"
        )
        self.service = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.service)
        self.helper = Helper()
        self.service._preview_helper = self.helper
        self.addCleanup(self.service.close_preview_helper)
        self.addCleanup(self.service.close_preview_player)

    def _play(self, text):
        completed = threading.Event()
        outcomes = []
        record = types.SimpleNamespace(voice_id="test", profile_path=None, metadata_path=None,
            reference_paths=[], conditioning_path=None, metadata={}, source="user")

        def finish(status, error):
            outcomes.append((status, error))
            completed.set()

        self.service.play_installed_voice_sample(record, on_complete=finish, sample_text=text)
        self.assertTrue(completed.wait(4))
        self.assertEqual(outcomes, [("completed", None)])

    def test_a_voice_store_change_rescans_voices_but_keeps_the_loaded_model(self):
        self._play("Before the install.")
        self.assertEqual(self.helper.reloads, [])
        self.service._invalidate_preview_helper("local-install")
        self.assertFalse(self.helper.closed.is_set(), "the loaded model was thrown away")
        self._play("After the install.")
        self.assertEqual(self.helper.reloads, ["test"])
        self._play("No further change.")
        self.assertEqual(self.helper.reloads, ["test"])

    def test_the_helper_is_released_after_the_manager_has_been_closed_for_a_while(self):
        self.service.release_preview_helper_later(delay_seconds=0.05)
        self.assertTrue(self.helper.closed.wait(2), "the model stayed in memory after the manager closed")
        self.assertIsNone(self.service._preview_helper)

    def test_reopening_the_manager_keeps_the_helper(self):
        self.service.release_preview_helper_later(delay_seconds=0.2)
        self.service.prepare_preview_runtime_async()
        self.assertFalse(self.helper.closed.wait(0.5), "the helper was closed under a reopened manager")


if __name__ == "__main__":
    unittest.main()
