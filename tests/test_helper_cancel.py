"""Cancelled speech must stop in the helper, not only in the player.

Runs the real client against the real helper process. Only the speech engine is
replaced, so no model or synthesis dependency is needed.
"""
import logging
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"
HELPER_FILES = ("_helper_process.py", "_hot_text_cache.py", "_log.py", "_speech_cache.py", "_cache_settings.py", "_paths.py")

FAKE_ENGINE = r'''
import os
import time


class _Audio(object):
    def __init__(self, data):
        self._data = data

    def tobytes(self):
        return self._data


class XTTSV2Engine(object):
    sample_rate = 24000
    current_voice = "test"

    def __init__(self, package_root):
        # Stands in for loading the model.
        time.sleep(float(os.environ.get("FAKE_ENGINE_LOAD_SECONDS", "0")))

    def list_voices(self):
        return ["test"]

    def get_status(self):
        return {"providers": ["fake"]}

    def supports_streaming(self):
        return True

    def get_voice_cache_key(self, voice):
        return voice

    def synthesize_to_int16(self, text, **kwargs):
        return _Audio(b"\x01\x00" * 8)

    def stream_synthesize_to_int16(self, text, **kwargs):
        for index in range(200):
            time.sleep(0.01)
            yield _Audio(b"\x01\x00" * 8)

    def close(self):
        pass
'''


class HelperCancelTests(unittest.TestCase):
    def setUp(self):
        self._root = tempfile.TemporaryDirectory(prefix="xtts-cancel-test-", ignore_cleanup_errors=True)
        self.addCleanup(self._root.cleanup)
        package_root = os.path.join(self._root.name, "package")
        os.mkdir(package_root)
        for name in HELPER_FILES:
            shutil.copyfile(SYNTH_ROOT / name, os.path.join(package_root, name))
        with open(os.path.join(package_root, "_engine.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_ENGINE)
        patcher = patch.dict(os.environ, {
            "APPDATA": os.path.join(self._root.name, "appdata"),
            "MAXLOGIC_XTTS_V2_HELPER_PYTHON": sys.executable,
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(SYNTH_ROOT))
        self.addCleanup(sys.path.remove, str(SYNTH_ROOT))
        from _helper_client import HelperEngineClient
        self.client = HelperEngineClient(package_root, logging.getLogger("test"), skip_prewarm=True)
        self.addCleanup(self.client.close)

    def test_cancel_stops_a_running_stream_and_frees_the_helper(self):
        text = "A sentence that is still being synthesized when the user presses control."
        received = []
        first_chunk = threading.Event()

        def consume():
            for chunk in self.client.stream_synthesize_to_int16(text, voice="test", generation=1):
                received.append(bytes(chunk))
                first_chunk.set()

        worker = threading.Thread(target=consume, daemon=True)
        started = time.perf_counter()
        worker.start()
        self.assertTrue(first_chunk.wait(5))
        self.assertTrue(self.client.cancel(1))
        worker.join(5)
        self.assertFalse(worker.is_alive(), "the cancelled request kept the helper busy")
        self.assertLess(len(received), 100, "the helper synthesized the whole cancelled utterance")
        self.assertLess(time.perf_counter() - started, 1.5)
        # Partial audio must never be cached as the complete utterance.
        self.assertEqual(self.client.get_cache_stats()["persistent"]["entryCount"], 0)

    def test_requests_from_a_cancelled_generation_are_dropped(self):
        self.client.cancel(3)
        chunks = list(self.client.stream_synthesize_to_int16("Stale text.", voice="test", generation=3))
        self.assertEqual(chunks, [])
        self.assertEqual(len(self.client.synthesize_to_int16("Stale text.", voice="test", generation=2)), 0)
        chunks = list(self.client.stream_synthesize_to_int16("Fresh text.", voice="test", generation=4))
        self.assertEqual(len(chunks), 200)

    def test_a_closed_client_does_not_start_another_helper(self):
        self.client.close()
        with self.assertRaises(RuntimeError):
            self.client.get_cache_stats()
        self.assertIsNone(self.client._process)



class HelperBackgroundStartTests(unittest.TestCase):
    """Selecting the synthesizer must not wait for the model to load."""
    LOAD_SECONDS = 1.5

    def setUp(self):
        self._root = tempfile.TemporaryDirectory(prefix="xtts-start-test-", ignore_cleanup_errors=True)
        self.addCleanup(self._root.cleanup)
        self.package_root = os.path.join(self._root.name, "package")
        os.mkdir(self.package_root)
        for name in HELPER_FILES:
            shutil.copyfile(SYNTH_ROOT / name, os.path.join(self.package_root, name))
        with open(os.path.join(self.package_root, "_engine.py"), "w", encoding="utf-8") as handle:
            handle.write(FAKE_ENGINE)
        patcher = patch.dict(os.environ, {
            "APPDATA": os.path.join(self._root.name, "appdata"),
            "MAXLOGIC_XTTS_V2_HELPER_PYTHON": sys.executable,
            "FAKE_ENGINE_LOAD_SECONDS": str(self.LOAD_SECONDS),
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(SYNTH_ROOT))
        self.addCleanup(sys.path.remove, str(SYNTH_ROOT))
        from _helper_client import HelperEngineClient
        self.client_class = HelperEngineClient

    def test_the_client_returns_before_the_helper_is_ready(self):
        started = time.perf_counter()
        client = self.client_class(self.package_root, logging.getLogger("test"), skip_prewarm=True, wait_until_ready=False)
        self.addCleanup(client.close)
        self.assertLess(time.perf_counter() - started, 0.5, "the caller waited for the model to load")
        self.assertFalse(client.is_ready)
        # A request waits for the helper instead of failing.
        self.assertEqual(len(client.synthesize_to_int16("Early text.", voice="test")), 16)
        self.assertTrue(client.is_ready)

    def test_closing_during_startup_does_not_wait_or_leave_a_helper(self):
        client = self.client_class(self.package_root, logging.getLogger("test"), skip_prewarm=True, wait_until_ready=False)
        deadline = time.perf_counter() + 2
        while client._process is None and time.perf_counter() < deadline:
            time.sleep(0.01)
        process = client._process
        self.assertIsNotNone(process, "the helper never started")
        started = time.perf_counter()
        client.close()
        self.assertLess(time.perf_counter() - started, 0.5, "close() waited for the model to load")
        self.assertIsNotNone(process.wait(timeout=2), "the helper kept running after close()")
        time.sleep(self.LOAD_SECONDS)
        self.assertIsNone(client._process, "a closed client started another helper")


if __name__ == "__main__":
    unittest.main()
