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

    def test_draft_is_private_until_saved_and_discard_removes_only_draft(self):
        store = importlib.import_module("clone_test_package._voice_store")
        calls = []
        def model(paths, target, options):
            calls.append(target)
            Path(target).write_bytes(b"draft conditioning")
        draft = self.clone.create_draft(self.paths, "pl", {}, model)
        self.assertEqual(store.list_user_voice_records(), [])
        self.assertEqual(draft.metadata["language"], "pl")
        self.assertEqual([Path(p).read_bytes() for p in draft.reference_paths], [b"one", b"two"])
        saved = self.clone.save_draft(draft, "Chosen name")
        self.assertEqual(saved.voice_id, "Chosen_name")
        self.assertEqual(saved.display_name, "Chosen name")
        self.assertEqual(Path(saved.conditioning_path).read_bytes(), b"draft conditioning")
        self.assertEqual(len(calls), 1)
        self.clone.discard_draft(draft)
        self.clone.discard_draft(draft)
        self.assertFalse(Path(draft.conditioning_path).exists())
        self.assertTrue(Path(saved.conditioning_path).exists())
        self.assertEqual(list(Path(self.clone.get_temp_dir()).iterdir()), [])

    def test_saved_voice_can_be_deleted_from_the_user_voice_store(self):
        store = importlib.import_module("clone_test_package._voice_store")
        def model(paths, target, options):
            Path(target).write_bytes(b"conditioning")
        saved = self.clone.create_voice(self.paths, "Bad clone", "en", {}, model)
        profile_dir = Path(saved.metadata_path).parent
        self.assertTrue(profile_dir.is_dir())
        removed_paths = store.remove_user_voice(saved.voice_id)
        self.assertFalse(profile_dir.exists())
        self.assertIn(str(profile_dir), removed_paths)
        self.assertEqual(store.list_user_voice_records(), [])
        with self.assertRaisesRegex(store.VoiceStoreError, "User voice not found"):
            store.remove_user_voice(saved.voice_id)

    def test_failed_save_preserves_draft_and_overwrite_requires_explicit_choice(self):
        def model(paths, target, options):
            Path(target).write_bytes(b"first")
        draft = self.clone.create_draft(self.paths, "en", {}, model)
        try:
            saved = self.clone.save_draft(draft, "Existing")
            Path(draft.conditioning_path).write_bytes(b"replacement")
            with self.assertRaises(self.clone.DuplicateVoiceError):
                self.clone.save_draft(draft, "Existing")
            self.assertEqual(Path(saved.conditioning_path).read_bytes(), b"first")
            self.assertEqual(Path(draft.conditioning_path).read_bytes(), b"replacement")
            # An actual filesystem collision also leaves the draft available for retry.
            collision = Path(self.clone.get_user_voice_profile_dir("Blocked"))
            collision.write_bytes(b"unrelated")
            with self.assertRaises((OSError, self.clone.VoiceStoreError)):
                self.clone.save_draft(draft, "Blocked")
            self.assertEqual(collision.read_bytes(), b"unrelated")
            self.assertEqual(Path(draft.conditioning_path).read_bytes(), b"replacement")
            replaced = self.clone.save_draft(draft, "Existing", overwrite=True)
            self.assertEqual(Path(replaced.conditioning_path).read_bytes(), b"replacement")
        finally:
            self.clone.discard_draft(draft)

    def test_discarded_draft_cannot_be_saved(self):
        def model(paths, target, options):
            Path(target).write_bytes(b"conditioning")
        draft = self.clone.create_draft(self.paths, "en", {}, model)
        self.clone.discard_draft(draft)
        with self.assertRaises(self.clone.VoiceStoreError):
            self.clone.save_draft(draft, "Too late")

    def test_failed_overwrite_restores_installed_voice_and_preserves_draft(self):
        store = importlib.import_module("clone_test_package._voice_store")
        def model(paths, target, options):
            Path(target).write_bytes(b"original")
        draft = self.clone.create_draft(self.paths, "en", {}, model)
        try:
            saved = self.clone.save_draft(draft, "Existing")
            original_metadata = Path(saved.metadata_path).read_bytes()
            Path(draft.conditioning_path).write_bytes(b"replacement")
            replace = os.replace
            def fail_publication(source, destination):
                if Path(source).name == "payload":
                    raise PermissionError("injected publication failure")
                return replace(source, destination)
            with patch.object(store.os, "replace", side_effect=fail_publication):
                with self.assertRaisesRegex(PermissionError, "publication failure"):
                    self.clone.save_draft(draft, "Existing", overwrite=True)
            self.assertEqual(Path(saved.conditioning_path).read_bytes(), b"original")
            self.assertEqual(Path(saved.metadata_path).read_bytes(), original_metadata)
            self.assertEqual(Path(draft.conditioning_path).read_bytes(), b"replacement")
            self.clone.save_draft(draft, "Existing", overwrite=True)
            self.assertEqual(Path(saved.conditioning_path).read_bytes(), b"replacement")
        finally:
            self.clone.discard_draft(draft)
        self.assertEqual(list(Path(self.clone.get_temp_dir()).iterdir()), [])

    def test_generation_preset_is_saved_separately_from_conditioning(self):
        presets = importlib.import_module("clone_test_package._voice_presets")
        seen = []
        def model(paths, target, options):
            seen.append(options)
            Path(target).write_bytes(b"conditioning")
        settings = dict(presets.GENERATION_PRESETS[2][1])
        record = self.clone.create_voice(self.paths, "Preset", "en", {"trim_silence": True}, model, settings)
        self.assertEqual(record.metadata["synthesisSettings"], settings)
        self.assertNotIn("temperature", seen[0])
        self.assertTrue(seen[0]["trim_silence"])
        self.assertEqual(presets.CONDITIONING_PRESETS[0], ("model_config", (30, 30, 4)))
        self.assertEqual(presets.GENERATION_PRESETS[0][1]["repetition_penalty"], 5.0)
        for key, (max_ref, total, chunk) in presets.CONDITIONING_PRESETS:
            self.assertEqual(self.clone.validate_options(dict(max_ref_length=max_ref, gpt_cond_len=total, gpt_cond_chunk_len=chunk))["gpt_cond_chunk_len"], chunk)
        for key, preset in presets.GENERATION_PRESETS:
            self.assertEqual(presets.generation_settings(preset), preset)
        for invalid in ({"temperature": float("nan")}, {"top_p": 2}, {"top_k": 2.5}):
            with self.assertRaises(ValueError):
                presets.generation_settings(invalid)


if __name__ == "__main__":
    unittest.main()
