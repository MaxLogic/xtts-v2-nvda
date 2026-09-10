"""Cache maintenance must work without loading any speech-model dependencies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CacheHelperTests(unittest.TestCase):
    def test_cache_only_process_without_site_packages(self):
        script = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2/_helper_process.py"
        with tempfile.TemporaryDirectory(prefix="xtts-cache-test-") as data:
            env = dict(os.environ, APPDATA=data, MAXLOGIC_XTTS_V2_HELPER_MODE="cache")
            result = subprocess.run(
                [sys.executable, "-S", str(script)], env=env,
                input='{"op":"get_cache_stats","id":1}\n',
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            messages = [json.loads(line) for line in result.stdout.splitlines()]
            self.assertEqual(len(messages), 2, result.stdout)
            self.assertTrue(messages[0]["ok"], messages[0])
            self.assertEqual(messages[0]["mode"], "cache")
            self.assertTrue(messages[1]["ok"], messages[1])
            self.assertEqual(messages[1]["persistent"]["entryCount"], 0)


if __name__ == "__main__":
    unittest.main()
