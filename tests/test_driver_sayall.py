"""Say-all must not fall silent between lines or sentences.

NVDA's speech manager sends one utterance at a time and sends the next one
only when the synth reports the index that ends the current one. Each line
of a say-all is an index inside the utterance.
"""
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
    def __init__(self):
        self.calls = []

    def notify(self, **kwargs):
        self.calls.append((time.perf_counter(), kwargs))


class _WasapiPlayer:
    """Behaves like NVDA's WASAPI player (nvdaHelper/local/wasapi.cpp).

    There is no playback thread: onDone callbacks run only inside feed() and
    sync(), once playback has passed the end of their chunk. feed() waits while
    more than half of the 400 ms buffer is filled. stop() makes a waiting feed()
    return without playing its data, but a later feed() starts playing again.
    """
    HALF_BUFFER = 0.2

    def __init__(self, **kwargs):
        self._lock = threading.Lock()
        self._end = 0.0  # when the audio fed so far finishes playing
        self._callbacks = []
        self._stops = 0
        self.segments = []  # [start, end] of each fed chunk, cut short by stop()
        self.stops = 0

    def _fire_due(self):
        now = time.perf_counter()
        with self._lock:
            due = [callback for end, callback in self._callbacks if end <= now]
            self._callbacks = [(end, callback) for end, callback in self._callbacks if end > now]
        for callback in due:
            callback()

    def feed(self, data, size=None, onDone=None):
        stops = self._stops
        while self._end - time.perf_counter() > self.HALF_BUFFER:
            if self._stops != stops:
                return  # stop() woke this feed; the data is dropped
            self._fire_due()
            time.sleep(0.002)
        if self._stops != stops:
            return
        with self._lock:
            start = max(time.perf_counter(), self._end)
            self._end = start + len(data) / 2 / SAMPLE_RATE
            if data:
                self.segments.append([start, self._end])
            if onDone is not None:
                self._callbacks.append((self._end, onDone))
        self._fire_due()

    def sync(self):
        stops = self._stops
        while time.perf_counter() < self._end and self._stops == stops:
            self._fire_due()
            time.sleep(0.002)
        if self._stops == stops:
            self._fire_due()

    def idle(self):
        self.sync()

    def stop(self):
        with self._lock:
            now = time.perf_counter()
            self.segments = [[start, min(end, now)] for start, end in self.segments if start < now]
            self._end = now
            self._callbacks = []
            self._stops += 1
            self.stops += 1

    def close(self):
        pass

    def gaps(self):
        """Silences between the end of one piece of audio and the start of the next."""
        with self._lock:
            segments = sorted(self.segments)
        return [later[0] - earlier[1] for earlier, later in zip(segments, segments[1:]) if later[0] - earlier[1] > GAP_TOLERANCE]

    def seconds_played(self, until=None):
        until = time.perf_counter() if until is None else until
        with self._lock:
            return sum(max(0.0, min(end, until) - start) for start, end in self.segments)

    def finished_at(self):
        with self._lock:
            return max(end for __, end in self.segments) if self.segments else None


class _Command:
    pass


def load_driver():
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
        synthDoneSpeaking=_Notifier(),
        synthIndexReached=_Notifier(),
    )
    boundary = {
        "addonHandler": types.SimpleNamespace(initTranslation=lambda: None),
        "config": types.SimpleNamespace(conf={}),
        "nvwave": types.SimpleNamespace(WavePlayer=_WasapiPlayer),
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
    """Takes SYNTH_SECONDS per chunk and returns audio_seconds of sound."""
    sample_rate = SAMPLE_RATE
    voice_records = {}

    def __init__(self):
        self.audio_seconds = AUDIO_SECONDS

    def list_voices(self): return ["test"]
    def reload_voices(self, preferred_voice=None): return "test"
    def get_status(self): return {"providers": []}
    def set_voice(self, voice): pass
    def cancel(self, generation): return True
    def close(self): pass

    def stream_synthesize_to_int16(self, text, **kwargs):
        time.sleep(SYNTH_SECONDS)
        yield b"\x01\x00" * int(SAMPLE_RATE * self.audio_seconds)


class _StreamingEngine(_Engine):
    """Streams one piece per 20 characters, like the helper: each takes SYNTH_SECONDS and plays for AUDIO_SECONDS."""

    def stream_synthesize_to_int16(self, text, **kwargs):
        for __ in range(max(1, len(text) // 20)):
            time.sleep(SYNTH_SECONDS)
            yield b"\x01\x00" * int(SAMPLE_RATE * self.audio_seconds)


class SayAllTests(unittest.TestCase):
    def setUp(self):
        self.module, self.handler, self.commands, patcher = load_driver()
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
        first_line_end = self.driver._player.segments[0][0] + AUDIO_SECONDS
        reported = dict((number, at) for at, number in self.indexes())
        self.assertGreaterEqual(reported[1], first_line_end - 0.01, "index 1 was reported before its line was heard")
        self.assertLess(reported[1], first_line_end + GAP_TOLERANCE)

    def test_done_speaking_is_reported_when_the_audio_ends(self):
        self.driver.speak(["Only line.", self.index(1)])
        self.wait_until_done(timeout=5)
        done_at = self.handler.synthDoneSpeaking.calls[0][0]
        self.assertLess(done_at - self.driver._player.finished_at(), GAP_TOLERANCE, "done speaking came late")

    def test_the_last_index_of_a_short_line_is_reported(self):
        # Shorter than the lead time, so the index cannot be reported before the audio ends.
        self.driver.utteranceLeadSeconds = 1.0
        self.driver.speak(["Blank.", self.index(7)])
        self.wait_until_done(timeout=5)
        reported = dict((number, at) for at, number in self.indexes())
        self.assertIn(7, reported)
        self.assertLess(reported[7] - self.driver._player.finished_at(), GAP_TOLERANCE, "the index of a short line came late")

    def test_the_next_sentence_follows_without_a_gap(self):
        # Like NVDA: send the next utterance when the index that ends this one is reported.
        sentences = [["Sentence number %d." % n, self.index(n)] for n in (1, 2, 3)]
        self.driver.speak(sentences.pop(0))
        original = self.handler.synthIndexReached.notify

        def notify(**kwargs):
            original(**kwargs)
            if sentences:
                sentence = sentences.pop(0)
                threading.Thread(target=self.driver.speak, args=(sentence,)).start()

        self.handler.synthIndexReached.notify = notify
        self.wait_until_done()
        player = self.driver._player
        self.assertEqual(player.gaps(), [], "silence between sentences")
        self.assertAlmostEqual(player.seconds_played(), 3 * AUDIO_SECONDS, delta=0.05, msg="a sentence was cut off")
        self.assertLess(self.indexes()[-1][0], player.finished_at(), "the last index should come before the audio ends")

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
        # Nothing was fed, so the player may never have been created.
        self.assertEqual(self.driver._player.seconds_played() if self.driver._player else 0, 0)
        self.assertEqual(self.handler.synthDoneSpeaking.calls, [])

    def test_a_long_line_plays_on_after_its_first_words(self):
        # The short first chunk plays while the long rest is still being synthesized.
        # Waiting for the whole rest before playing it left seconds of silence after the first words.
        self.driver._engine = self.engine = _StreamingEngine()
        line = "- Closing during startup, by switching synths or exiting NVDA, stops the half-loaded helper instead of waiting for it."
        spoken_at = time.perf_counter()
        self.driver.speak([line, self.index(1)])
        self.wait_until_done()
        player = self.driver._player
        self.assertLess(player.segments[0][0] - spoken_at, SYNTH_SECONDS + GAP_TOLERANCE,
            "the line did not start when its first piece arrived")
        self.assertEqual(player.gaps(), [], "silence after the first words of the line")
        reported = dict((number, at) for at, number in self.indexes())
        self.assertAlmostEqual(player.finished_at() - reported[1], LEAD_SECONDS, delta=GAP_TOLERANCE,
            msg="the index that ends the line should still come the lead time before its end")

    def test_no_audio_is_heard_after_cancel(self):
        # Long chunks keep the driver inside feed() while the buffer drains, where cancel() lands.
        self.engine.audio_seconds = 1.5
        for attempt in range(3):
            self.driver.speak(["A long sentence number %d." % attempt, self.index(attempt)])
            time.sleep(SYNTH_SECONDS + 0.3)
            cancelled_at = time.perf_counter()
            self.driver.cancel()
            time.sleep(0.4)
            heard_after = self.driver._player.seconds_played() - self.driver._player.seconds_played(until=cancelled_at + 0.01)
            self.assertLess(heard_after, 0.02, "interrupted speech kept playing after cancel")


if __name__ == "__main__":
    unittest.main()
