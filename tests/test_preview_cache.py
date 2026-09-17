"""Preview latency and disk reuse through the real manager service."""
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


class PreviewCacheTests(unittest.TestCase):
    def test_stream_starts_before_completion_and_replay_uses_disk(self):
        self._check_preview(stop_early=False)

    def test_stopping_playback_still_caches_the_complete_stream(self):
        self._check_preview(stop_early=True)

    def test_draft_stream_and_custom_text_cache(self):
        self._check_preview(stop_early=False, draft=True)

    def _check_preview(self, stop_early, draft=False):
        synths = types.ModuleType("synthDrivers")
        synths.__path__ = [str(ROOT / "synthDrivers")]
        package = types.ModuleType("synthDrivers.maxlogic_xtts_v2")
        package.__path__ = [str(ROOT / "synthDrivers/maxlogic_xtts_v2")]
        played = []
        first_audio = threading.Event()
        release = threading.Event()
        completed = threading.Event()
        outcomes = []
        calls = []
        chunks = [b"\x01\x00" * 240, b"\x02\x00" * 240]

        class Player:
            def __init__(self, **kwargs): pass
            def stop(self): pass
            def idle(self): pass
            def close(self): pass
            def feed(self, data):
                played.append(bytes(data))
                first_audio.set()

        class Helper:
            _voices = ["test"]
            sample_rate = 24000
            def stream_synthesize_to_int16(self, *args, **kwargs):
                calls.append("stream")
                yield memoryview(chunks[0])
                if not release.wait(3):
                    raise RuntimeError("test did not release second chunk")
                yield memoryview(chunks[1])
            def stream_synthesize_preview_to_int16(self, text, **kwargs):
                self_test.assertEqual(text, "My custom audition.")
                self_test.assertEqual(kwargs["conditioning_path"], str(reference))
                self_test.assertEqual(kwargs["synthesis_settings"], {"temperature": .65})
                yield from self.stream_synthesize_to_int16(text)
            def synthesize_to_int16(self, *args, **kwargs):
                calls.append("buffered")
                release.wait(3)
                return memoryview(b"".join(chunks))
            def close(self): pass

        self_test = self
        boundary = {
            "synthDrivers": synths, "synthDrivers.maxlogic_xtts_v2": package,
            "addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
            "config": types.SimpleNamespace(conf={}),
            "nvwave": types.SimpleNamespace(WavePlayer=Player),
            "synthDriverHandler": types.SimpleNamespace(getSynth=lambda: None),
            "logHandler": types.SimpleNamespace(log=logging.getLogger("test")),
            "wx": types.SimpleNamespace(CallAfter=lambda fn, *a: fn(*a)),
        }
        def finish(status, error):
            outcomes.append((status, error))
            completed.set()

        with tempfile.TemporaryDirectory(prefix="xtts-preview-") as data, \
                patch.dict(os.environ, APPDATA=data), patch.dict(sys.modules, boundary), \
                patch.object(builtins, "_", lambda s: s, create=True):
            spec = importlib.util.spec_from_file_location(
                "preview_cache_service", ROOT / "globalPlugins/maxlogic_xtts_v2_manager/service.py"
            )
            service = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(service)
            reference = Path(data) / "reference.wav"
            reference.write_bytes(b"reference fixture")
            record = types.SimpleNamespace(voice_id="test", profile_path=None, metadata_path=None,
                reference_paths=[str(reference)], conditioning_path=None, metadata={}, source="test")
            preview_args = {}
            if draft:
                record.source = "draft"
                record.voice_id = "unsaved-draft"
                record.conditioning_path = str(reference)
                record.metadata = {"synthesisSettings": {"temperature": .65}}
                preview_args["sample_text"] = "My custom audition."
                initial_key = service._build_installed_preview_cache_payload(record, "en", "My custom audition.")
                record.metadata["synthesisSettings"] = {"temperature": .8}
                self.assertNotEqual(initial_key, service._build_installed_preview_cache_payload(record, "en", "My custom audition."))
                record.metadata["synthesisSettings"] = {"temperature": .65}
            with patch.object(service, "HelperEngineClient", lambda *a, **k: Helper()):
                service.play_installed_voice_sample(record, on_complete=finish, **preview_args)
                try:
                    self.assertTrue(first_audio.wait(0.5), "preview waits for the whole utterance")
                    self.assertFalse(completed.is_set())
                    if stop_early:
                        service.stop_preview()
                finally:
                    release.set()
                    self.assertTrue(completed.wait(4))
                self.assertEqual(outcomes, [("superseded" if stop_early else "completed", None)])
                self.assertEqual(b"".join(played), chunks[0] if stop_early else b"".join(chunks))
                self.assertEqual(calls, ["stream"])
                completed.clear()
                played.clear()
                service.close_preview_helper()
                with patch.object(service, "HelperEngineClient", side_effect=AssertionError("cache replay started a model")):
                    service.play_installed_voice_sample(record, on_complete=finish, **preview_args)
                    self.assertTrue(completed.wait(2))
                self.assertEqual(outcomes[-1], ("completed", None))
                self.assertEqual(b"".join(played), b"".join(chunks))
                if draft:
                    completed.clear()
                    # Different text must miss the old WAV; startup failure must not
                    # try importing the engine into NVDA's embedded Python.
                    with patch.object(service, "HelperEngineClient", side_effect=RuntimeError("helper unavailable")), \
                            patch.dict(sys.modules, {"synthDrivers.maxlogic_xtts_v2._engine": None}):
                        service.play_installed_voice_sample(record, on_complete=finish, sample_text="Changed text.")
                        self.assertTrue(completed.wait(2))
                    self.assertEqual(outcomes[-1], ("error", "helper unavailable"))
                service.close_preview_player()


if __name__ == "__main__":
    unittest.main()
