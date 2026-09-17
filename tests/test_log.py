"""The helper log must not grow without limit."""
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"


class HelperLogTests(unittest.TestCase):
    def test_a_large_log_is_rotated_when_a_helper_starts(self):
        data = tempfile.TemporaryDirectory(prefix="xtts-log-test-")
        self.addCleanup(data.cleanup)
        patcher = patch.dict(os.environ, {"APPDATA": data.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.path.insert(0, str(SYNTH_ROOT))
        self.addCleanup(sys.path.remove, str(SYNTH_ROOT))
        for name in ("_log", "_paths"):
            sys.modules.pop(name, None)
        import _log
        log_path = _log.get_helper_log_path()
        with open(log_path, "w", encoding="utf-8") as handle:
            handle.write("x" * (_log.MAX_HELPER_LOG_BYTES + 1))
        logger = logging.getLogger("xtts-log-test")
        _log.configure_helper_file_logger(logger)
        for handler in list(logger.handlers):
            self.addCleanup(handler.close)
        logger.warning("first line after rotation")
        self.assertLess(os.path.getsize(log_path), 1024)
        self.assertGreater(os.path.getsize(_log.get_previous_helper_log_path()), _log.MAX_HELPER_LOG_BYTES)


if __name__ == "__main__":
    unittest.main()
