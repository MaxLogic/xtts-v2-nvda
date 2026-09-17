"""The synth driver must tell the engine about cancellation without waiting for it."""
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


class _Notifier:
    def __init__(self):
        self.calls = []

    def notify(self, **kwargs):
        self.calls.append(kwargs)


class _Player:
    def __init__(self, **kwargs):
        self.fed = []

    def feed(self, data):
        self.fed.append(bytes(data))

    def idle(self): pass
    def stop(self): pass
    def close(self): pass


def load_driver():
    """Import the driver package with NVDA replaced at its module boundary."""
    class _Command:
        pass

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
        "nvwave": types.SimpleNamespace(WavePlayer=_Player),
        "synthDriverHandler": handler,
        "logHandler": types.SimpleNamespace(log=logging.getLogger("test")),
        "speech": speech,
        "speech.commands": commands,
    }
    patcher = patch.dict(sys.modules, boundary)
    patcher.start()
    spec = importlib.util.spec_from_file_location(
        "maxlogic_xtts_v2_under_test", ROOT / "synthDrivers/maxlogic_xtts_v2/__init__.py",
        submodule_search_locations=[str(ROOT / "synthDrivers/maxlogic_xtts_v2")],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module, handler, patcher


class _SlowEngine:
    """Synthesis that only ends when cancelled, like a long utterance on a slow machine."""
    sample_rate = 24000
    voice_records = {}

    def __init__(self):
        self.cancelled = []
        self.synthesizing = threading.Event()
        self._stop = threading.Event()
        self._active_generation = None

    def list_voices(self): return ["test"]
    def reload_voices(self, preferred_voice=None): return "test"
    def get_status(self): return {"providers": []}
    def set_voice(self, voice): pass
    def close(self): pass

    def cancel(self, generation):
        self.cancelled.append(generation)
        if self._active_generation is not None and generation >= self._active_generation:
            self._stop.set()
        return True

    def stream_synthesize_to_int16(self, text, **kwargs):
        self._active_generation = kwargs["generation"]
        self.synthesizing.set()
        self._stop.wait(10)
        # A real engine hands back whatever it had generated when it was cancelled.
        yield b"\x01\x00" * 2400


class DriverCancelTests(unittest.TestCase):
    def setUp(self):
        self.module, self.handler, patcher = load_driver()
        self.addCleanup(patcher.stop)
        self.addCleanup(sys.modules.pop, "maxlogic_xtts_v2_under_test", None)
        self.engine = _SlowEngine()
        with patch.object(self.module.SynthDriver, "_create_engine", lambda driver: self.engine):
            self.driver = self.module.SynthDriver()
        self.addCleanup(self.driver.terminate)

    def test_cancel_reaches_the_engine_while_it_is_synthesizing(self):
        self.driver.speak(["A long utterance that the user interrupts."])
        self.assertTrue(self.engine.synthesizing.wait(5))
        generation = self.driver._generation
        started = time.perf_counter()
        self.driver.cancel()
        self.assertLess(time.perf_counter() - started, 0.5, "cancel() waited for synthesis")
        self.assertIn(generation, self.engine.cancelled)
        self.assertEqual(self.handler.synthDoneSpeaking.calls, [])

    def test_audio_that_arrives_after_cancel_is_never_played(self):
        for attempt in range(5):
            self.engine.synthesizing.clear()
            self.engine._stop.clear()
            self.engine._active_generation = None
            self.driver.speak(["Text number %d that the user interrupts." % attempt])
            self.assertTrue(self.engine.synthesizing.wait(5))
            time.sleep(0.02)
            self.driver.cancel()
            time.sleep(0.15)
        self.assertEqual(self.driver._player.fed, [])


if __name__ == "__main__":
    unittest.main()
