"""Sounds announce when the speech engine starts loading and when it is ready."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"


class LoadingSoundTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory(prefix="xtts-sounds-test-")
        self.addCleanup(root.cleanup)
        self.played = []
        nvwave = types.SimpleNamespace(playWaveFile=lambda path, asynchronous=True: self.played.append((path, asynchronous)))
        patcher = patch.dict(sys.modules, {"nvwave": nvwave})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = patch.dict(os.environ, {"APPDATA": root.name})
        env.start()
        self.addCleanup(env.stop)
        sys.path.insert(0, str(SYNTH_ROOT))
        self.addCleanup(sys.path.remove, str(SYNTH_ROOT))
        sys.modules.pop("_loading_sounds", None)
        import _loading_sounds
        self.sounds = _loading_sounds
        self.custom = os.path.join(root.name, "custom.wav")
        with open(self.custom, "wb") as handle:
            handle.write(b"RIFF")

    def test_both_sounds_play_by_default_from_the_add_on(self):
        for kind in (self.sounds.LOADING, self.sounds.READY):
            self.assertTrue(self.sounds.play_loading_sound(kind))
            path = self.played[-1][0]
            self.assertTrue(os.path.isfile(path), "the add-on has no %s sound" % kind)
            self.assertEqual(os.path.dirname(path), str(SYNTH_ROOT / "sounds"))

    def test_a_disabled_sound_does_not_play(self):
        settings = self.sounds.load_sound_settings()
        settings[self.sounds.READY]["enabled"] = False
        self.sounds.save_sound_settings(settings)
        self.assertFalse(self.sounds.play_loading_sound(self.sounds.READY))
        self.assertTrue(self.sounds.play_loading_sound(self.sounds.LOADING))
        self.assertEqual(len(self.played), 1)

    def test_a_chosen_file_plays_instead_and_a_missing_one_falls_back(self):
        settings = self.sounds.load_sound_settings()
        settings[self.sounds.LOADING]["path"] = self.custom
        self.sounds.save_sound_settings(settings)
        self.sounds.play_loading_sound(self.sounds.LOADING)
        self.assertEqual(self.played[-1][0], self.custom)
        os.remove(self.custom)
        self.sounds.play_loading_sound(self.sounds.LOADING)
        self.assertEqual(self.played[-1][0], self.sounds.default_sound_path(self.sounds.LOADING))

    def test_a_damaged_settings_file_means_the_defaults(self):
        with open(self.sounds.get_sound_settings_path(), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(self.sounds.load_sound_settings(), self.sounds.DEFAULT_SOUND_SETTINGS)
        with open(self.sounds.get_sound_settings_path(), "w", encoding="utf-8") as handle:
            json.dump({"loading": {"enabled": "no", "path": 5}}, handle)
        settings = self.sounds.load_sound_settings()
        self.assertEqual(settings["loading"]["path"], "")
        self.assertTrue(settings["ready"]["enabled"])

    def test_waiting_plays_the_sound_to_the_end_before_returning(self):
        self.sounds.play_loading_sound(self.sounds.READY, wait=True)
        self.assertEqual(self.played[-1][1], False)
        self.sounds.play_loading_sound(self.sounds.READY)
        self.assertEqual(self.played[-1][1], True)

    def test_only_wav_files_nvda_can_play_are_accepted(self):
        self.assertTrue(self.sounds.is_playable_wave(self.sounds.default_sound_path(self.sounds.READY)))
        self.assertFalse(self.sounds.is_playable_wave(self.custom), "a broken WAV file was accepted")
        self.assertFalse(self.sounds.is_playable_wave(self.custom + ".missing"))

    def test_a_failing_player_does_not_raise(self):
        sys.modules["nvwave"].playWaveFile = lambda *args, **kwargs: 1 / 0
        self.assertFalse(self.sounds.play_loading_sound(self.sounds.LOADING))


if __name__ == "__main__":
    unittest.main()
