"""Real profile publication with a controlled model boundary."""
import importlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class CloningTests(unittest.TestCase):
    def setUp(self):
        package = types.ModuleType("clone_test_package")
        package.__path__ = [str(ROOT / "addon/synthDrivers/maxlogic_xtts_v2")]
        self.modules = patch.dict(sys.modules, {"clone_test_package": package})
        self.modules.start()
        self.clone = importlib.import_module("clone_test_package._cloning")
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "tests")
        self.env = patch.dict(os.environ, {"APPDATA": self.temp.name})
        self.env.start()
        self.paths = []
        for name in ("one", "two"):
            folder = Path(self.temp.name) / name
            folder.mkdir()
            path = folder / "same.wav"
            path.write_bytes(name.encode())
            self.paths.append(str(path))

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
        self.modules.stop()

    def test_multiple_recordings_and_settings_survive_publication(self):
        calls = []
        def model(paths, target, options):
            calls.append(([Path(p).read_bytes() for p in paths], options))
            Path(target).write_bytes(b"conditioning")
        settings = dict(max_ref_length=15, gpt_cond_len=10, gpt_cond_chunk_len=5, sound_norm_refs=True)
        record = self.clone.create_voice(self.paths, "My voice", "pl", settings, model)
        self.assertEqual(calls, [([b"one", b"two"], settings)])
        self.assertEqual(record.metadata["cloneSettings"], settings)
        self.assertEqual(record.metadata["language"], "pl")
        self.assertEqual(len(record.reference_paths), 2)
        self.assertEqual(Path(record.conditioning_path).read_bytes(), b"conditioning")
        with self.assertRaises(self.clone.DuplicateVoiceError):
            self.clone.create_voice(self.paths, "My voice", "en", {}, model)
        self.assertEqual(len(calls), 1)

    def test_model_failure_does_not_publish_or_leave_staging_files(self):
        def fail(*args): raise RuntimeError("model failed")
        with self.assertRaisesRegex(RuntimeError, "model failed"):
            self.clone.create_voice(self.paths, "Failed", "en", {}, fail)
        self.assertFalse(Path(self.clone.get_user_voice_profile_dir("Failed")).exists())
        self.assertEqual(list(Path(self.clone.get_temp_dir()).iterdir()), [])

    def test_invalid_lengths_rejected_before_model_call(self):
        with self.assertRaises(self.clone.VoiceStoreError):
            self.clone.create_voice(self.paths, "Invalid", "en", {"gpt_cond_chunk_len": 7}, lambda *args: self.fail("model called"))

    def test_generation_preset_is_saved_separately_from_conditioning(self):
        presets = importlib.import_module("clone_test_package._voice_presets")
        seen = []
        def model(paths, target, options):
            seen.append(options)
            Path(target).write_bytes(b"conditioning")
        settings = dict(presets.GENERATION_PRESETS[1][1])
        record = self.clone.create_voice(self.paths, "Preset", "en", {"trim_silence": True}, model, settings)
        self.assertEqual(record.metadata["synthesisSettings"], settings)
        self.assertNotIn("temperature", seen[0])
        self.assertTrue(seen[0]["trim_silence"])
        for key, preset in presets.GENERATION_PRESETS:
            self.assertEqual(presets.generation_settings(preset), preset)
        for invalid in ({"temperature": float("nan")}, {"top_p": 2}, {"top_k": 2.5}):
            with self.assertRaises(ValueError):
                presets.generation_settings(invalid)


if __name__ == "__main__":
    unittest.main()
