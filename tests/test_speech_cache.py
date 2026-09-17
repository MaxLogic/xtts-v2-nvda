"""The persistent speech cache is disposable: damage must not disable it."""
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"


class SpeechCacheTests(unittest.TestCase):
    def setUp(self):
        self._data = tempfile.TemporaryDirectory(prefix="xtts-speech-cache-test-")
        self.addCleanup(self._data.cleanup)
        patcher = patch.dict(os.environ, {"APPDATA": self._data.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(SYNTH_ROOT))
        self.addCleanup(sys.path.remove, str(SYNTH_ROOT))
        for name in ("_speech_cache", "_cache_settings", "_paths"):
            sys.modules.pop(name, None)
        from _speech_cache import SpeechCache
        self.SpeechCache = SpeechCache

    def _open(self):
        cache = self.SpeechCache(logging.getLogger("test"))
        self.addCleanup(cache.close)
        return cache

    def test_round_trip(self):
        cache = self._open()
        cache.put_audio("voice", 1.0, 1.0, "en-us", "hello", b"\x01\x00" * 10)
        self.assertEqual(cache.get_audio("voice", 1.0, 1.0, "en-us", "hello"), b"\x01\x00" * 10)
        self.assertIsNone(cache.get_audio("voice", 1.05, 1.0, "en-us", "hello"))

    def test_damaged_database_is_set_aside_and_recreated(self):
        first = self.SpeechCache(logging.getLogger("test"))
        db_path = first.db_path
        first.close()
        for suffix in ("-wal", "-shm"):
            if os.path.exists(db_path + suffix):
                os.remove(db_path + suffix)
        with open(db_path, "wb") as handle:
            handle.write(b"this is not a sqlite database" * 64)
        cache = self._open()
        cache.put_audio("voice", 1.0, 1.0, "en-us", "hello", b"\x01\x00" * 10)
        self.assertEqual(cache.get_audio("voice", 1.0, 1.0, "en-us", "hello"), b"\x01\x00" * 10)
        self.assertTrue(os.path.isfile(db_path + ".damaged"))


if __name__ == "__main__":
    unittest.main()
