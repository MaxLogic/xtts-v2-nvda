"""Say-all must not fall silent between lines or sentences.

NVDA's speech manager sends one utterance at a time and sends the next one
only when the synth reports the index that ends the current one. Each line
of a say-all is an index inside the utterance.
"""
import collections
import importlib.util
import logging
from pathlib import Path
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1] / "addon"
SAMPLE_RATE = 24000
# The fake engine needs this long for each chunk, and each chunk plays for AUDIO_SECONDS.
SYNTH_SECONDS = 0.12
AUDIO_SECONDS = 0.25
LEAD_SECONDS = 0.2
# Anything shorter is scheduling noise, not an audible gap.
GAP_TOLERANCE = 0.05


class _Notifier:
    def __init__(self, clock):
        self.calls = []
        self._clock = clock

    def notify(self, **kwargs):
        self.calls.append((self._clock(), kwargs))


class _TimedPlayer:
    """Plays fed audio in real time and runs onDone like nvwave.WavePlayer."""

    def __init__(self, **kwargs):
        self._lock = threading.Condition()
        self._pending = collections.deque()
        self._busy = False
        self.segments = []  # (start, end, byte count)
        self.stops = 0
        threading.Thread(target=self._play, daemon=True).start()

    def feed(self, data, size=None, onDone=None):
        with self._lock:
            self._pending.append((bytes(data), onDone))
            self._lock.notify_all()

    def _play(self):
        while True:
            with self._lock:
                while not self._pending:
                    self._busy = False
                    self._lock.notify_all()
                    self._lock.wait()
                data, on_done = self._pending.popleft()
                self._busy = True
            start = time.perf_counter()
            time.sleep(len(data) / 2 / SAMPLE_RATE)
            with self._lock:
                self.segments.append((start, time.perf_counter(), len(data)))
            if on_done is not None:
                on_done()

    def sync(self):
        with self._lock:
            while self._pending or self._busy:
                self._lock.wait(0.01)

    def idle(self):
        self.sync()

    def stop(self):
        with self._lock:
            self._pending.clear()
            self.stops += 1

    def close(self):
        pass

    def gaps(self):
        """Silences between the end of one piece of audio and the start of the next."""
        with self._lock:
            segments = list(self.segments)
        return [later[0] - earlier[1] for earlier, later in zip(segments, segments[1:]) if later[0] - earlier[1] > GAP_TOLERANCE]

    def audio_bytes(self):
        with self._lock:
            return sum(size for __, __, size in self.segments)

    def finished_at(self):
        with self._lock:
            return self.segments[-1][1] if self.segments else None


class _Command:
    pass


def load_driver(clock):
    class _BaseDriver:
        @staticmethod
        def VoiceSetting(): return "voice"
        @staticmethod
        def RateSetting(): return "rate"
        @staticmethod
        def VolumeSetting(): return "volume"
        def __init__(self): pass
        def terminate(self): pass

    commands = types.ModuleType("speech.commands")
    for name in ("BreakCommand", "IndexCommand", "LangChangeCommand", "RateCommand", "VolumeCommand"):
        setattr(commands, name, type(name, (_Command,), {}))
    speech = types.ModuleType("speech")
    speech.commands = commands
    handler = types.SimpleNamespace(
        SynthDriver=_BaseDriver,
        VoiceInfo=lambda *args: args,
        synthDoneSpeaking=_Notifier(clock),
        synthIndexReached=_Notifier(clock),
    )
    boundary = {
        "addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
        "config": types.SimpleNamespace(conf={}),
        "nvwave": types.SimpleNamespace(WavePlayer=_TimedPlayer),
        "synthDriverHandler": handler,
        "logHandler": types.SimpleNamespace(log=logging.getLogger("test")),
        "speech": speech,
        "speech.commands": commands,
    }
    patcher = patch.dict(sys.modules, boundary)
    patcher.start()
    spec = importlib.util.spec_from_file_location(
        "maxlogic_xtts_v2_sayall_under_test", ROOT / "synthDrivers/maxlogic_xtts_v2/__init__.py",
        submodule_search_locations=[str(ROOT / "synthDrivers/maxlogic_xtts_v2")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, handler, commands, patcher


class _Engine:
    """Takes SYNTH_SECONDS per chunk and returns AUDIO_SECONDS of sound."""
    sample_rate = SAMPLE_RATE
    voice_records = {}

    def __init__(self):
        self.requests = []

    def list_voices(self): return ["test"]
    def reload_voices(self, preferred_voice=None): return "test"
    def get_status(self): return {"providers": []}
    def set_voice(self, voice): pass
    def cancel(self, generation): return True
    def close(self): pass

    def stream_synthesize_to_int16(self, text, **kwargs):
        self.requests.append((time.perf_counter(), text))
        time.sleep(SYNTH_SECONDS)
        yield b"\x01\x00" * int(SAMPLE_RATE * AUDIO_SECONDS)


class SayAllTests(unittest.TestCase):
    def setUp(self):
        self.module, self.handler, self.commands, patcher = load_driver(time.perf_counter)
        self.addCleanup(patcher.stop)
        self.addCleanup(sys.modules.pop, "maxlogic_xtts_v2_sayall_under_test", None)
        self.engine = _Engine()
        with patch.object(self.module.SynthDriver, "_create_engine", lambda driver: self.engine):
            self.driver = self.module.SynthDriver()
        self.driver.utteranceLeadSeconds = LEAD_SECONDS
        self.addCleanup(self.driver.terminate)

    def index(self, number):
        command = self.commands.IndexCommand()
        command.index = number
        return command

    def indexes(self):
        return [(at, call["index"]) for at, call in self.handler.synthIndexReached.calls]

    def wait_until_done(self, count=1, timeout=10):
        deadline = time.perf_counter() + timeout
        while len(self.handler.synthDoneSpeaking.calls) < count and time.perf_counter() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.handler.synthDoneSpeaking.calls), count, "the driver never finished speaking")

    def test_lines_in_one_utterance_play_without_gaps(self):
        self.driver.speak(["First line.", self.index(1), "Second line.", self.index(2), "Third line.", self.index(3)])
        self.wait_until_done()
        self.assertEqual(self.driver._player.gaps(), [], "silence between lines of one utterance")
        self.assertEqual([number for __, number in self.indexes()], [1, 2, 3])

    def test_a_line_index_is_reported_when_its_line_has_been_heard(self):
        self.driver.speak(["First line.", self.index(1), "Second line.", self.index(2)])
        self.wait_until_done()
        player = self.driver._player
        first_line_end = player.segments[0][1]
        reported = dict((number, at) for at, number in self.indexes())
        self.assertGreaterEqual(reported[1], first_line_end - 0.01, "index 1 was reported before its line was heard")
        self.assertLess(reported[1], first_line_end + GAP_TOLERANCE)

    def test_the_next_sentence_follows_without_a_gap(self):
        # Like NVDA: send the next utterance when the index that ends this one is reported.
        sentences = [["Sentence number %d." % n, self.index(n)] for n in (1, 2, 3)]
        self.driver.speak(sentences.pop(0))

        def push_next(**kwargs):
            if sentences:
                self.driver.speak(sentences.pop(0))

        original = self.handler.synthIndexReached.notify

        def notify(**kwargs):
            original(**kwargs)
            threading.Thread(target=push_next, kwargs=kwargs).start()

        self.handler.synthIndexReached.notify = notify
        self.wait_until_done()
        player = self.driver._player
        self.assertEqual(player.gaps(), [], "silence between sentences")
        self.assertEqual(player.audio_bytes() // int(SAMPLE_RATE * AUDIO_SECONDS * 2), 3, "a sentence was cut off")
        last_index_at = self.indexes()[-1][0]
        self.assertLess(last_index_at, player.finished_at(), "the last index should come before the audio ends")

    def test_speech_sent_before_the_previous_ends_is_queued_not_cut_off(self):
        self.driver.speak(["Earlier speech.", self.index(1)])
        self.driver.speak(["Later speech.", self.index(2)])
        self.wait_until_done()
        self.assertEqual(self.driver._player.stops, 0, "the earlier speech was stopped")
        self.assertEqual([number for __, number in self.indexes()], [1, 2])
        self.assertEqual(len(self.handler.synthDoneSpeaking.calls), 1)

    def test_cancel_drops_queued_speech_and_its_indexes(self):
        self.driver.speak(["Earlier speech.", self.index(1)])
        self.driver.speak(["Later speech.", self.index(2)])
        time.sleep(SYNTH_SECONDS / 2)
        self.driver.cancel()
        time.sleep(SYNTH_SECONDS + AUDIO_SECONDS + LEAD_SECONDS + 0.2)
        self.assertEqual(self.indexes(), [])
        self.assertEqual(self.driver._player.audio_bytes(), 0)
        self.assertEqual(self.handler.synthDoneSpeaking.calls, [])


if __name__ == "__main__":
    unittest.main()
