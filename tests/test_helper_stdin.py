"""The helper's stdin reader must never wait inside a read.

On Windows, a thread blocked in ReadFile on stdin stopped the main thread from
loading numpy's C extension. The helper then never finished loading the model,
and cloning a voice failed after hours of waiting. The real hang needs the
helper's own Python and numpy, so this test checks the rule that prevents it:
read only the bytes that are already waiting.
"""
import importlib.util
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"


class _ReadsForbidden(object):
    """A stdin whose only safe use is its file descriptor."""

    def __init__(self, fd):
        self._fd = fd

    def fileno(self):
        return self._fd

    def _blocking_read(self, *args):
        raise AssertionError("the reader waited inside a blocking read")

    __iter__ = readline = read = _blocking_read


@unittest.skipUnless(os.name == "nt", "the helper only runs on Windows")
class HelperStdinTests(unittest.TestCase):
    def setUp(self):
        appdata = tempfile.TemporaryDirectory(prefix="xtts-stdin-test-")
        self.addCleanup(appdata.cleanup)
        for patcher in (patch.dict(os.environ, APPDATA=appdata.name), patch.object(sys, "path", [str(SYNTH_ROOT)] + sys.path)):
            patcher.start()
            self.addCleanup(patcher.stop)
        spec = importlib.util.spec_from_file_location("helper_process_under_test", SYNTH_ROOT / "_helper_process.py")
        self.helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.helper)
        for handler in list(self.helper.LOGGER.handlers):
            self.helper.LOGGER.removeHandler(handler)
            handler.close()

        read_fd, self.write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.active_reads = []
        real_read = os.read

        def read(fd, size):
            self.active_reads.append(fd)
            try:
                return real_read(fd, size)
            finally:
                self.active_reads.remove(fd)

        patcher = patch.object(self.helper.os, "read", read)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.requests = queue.Queue()
        self.cancel_state = self.helper._CancelState()
        self.reader = threading.Thread(target=self.helper._read_requests,
            args=(_ReadsForbidden(read_fd), self.requests, self.cancel_state), daemon=True)
        self.reader.start()

    def _close_client_end(self):
        if self.write_fd is not None:
            os.close(self.write_fd)
            self.write_fd = None

    def tearDown(self):
        self._close_client_end()
        self.reader.join(2)

    def test_the_reader_only_reads_bytes_that_are_waiting(self):
        time.sleep(0.2)
        self.assertEqual(self.active_reads, [], "the reader waits inside a read while the pipe is empty")
        self.assertTrue(self.requests.empty())

        os.write(self.write_fd, b'{"op": "cancel", "generation": 5}\n{"op": "synthesize", "id": 1')
        time.sleep(0.1)
        os.write(self.write_fd, b'}\n')
        self.assertEqual(self.requests.get(timeout=2).strip(), '{"op": "synthesize", "id": 1}')
        self.assertTrue(self.cancel_state.is_cancelled(5))
        self.assertFalse(self.cancel_state.is_cancelled(6))

        time.sleep(0.2)
        self.assertEqual(self.active_reads, [], "the reader waits inside a read after a request")
        self.assertTrue(self.requests.empty())

    def test_a_closed_client_stops_the_reader_and_cancels_everything(self):
        self._close_client_end()
        self.assertIsNone(self.requests.get(timeout=2))
        self.assertTrue(self.cancel_state.is_cancelled(99))


if __name__ == "__main__":
    unittest.main()
