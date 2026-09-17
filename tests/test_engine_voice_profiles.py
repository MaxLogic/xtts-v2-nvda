"""Run in the helper environment: model boundary tests require NumPy/SoundFile."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
HAS_AUDIO = importlib.util.find_spec("numpy") is not None and importlib.util.find_spec("soundfile") is not None


@unittest.skipUnless(HAS_AUDIO, "run with .helper-venv/Scripts/python.exe")
class EngineProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import numpy as np
        import soundfile as sf
        cls.np, cls.sf = np, sf
        package = types.ModuleType("profile_engine_test")
        package.__path__ = [str(ROOT / "addon/synthDrivers/maxlogic_xtts_v2")]
        sys.modules[package.__name__] = package
        from importlib import import_module
        cls.Engine = import_module("profile_engine_test._engine").XTTSV2Engine

    def test_trim_preserves_rate_original_file_and_internal_pause(self):
        engine = self.Engine.__new__(self.Engine)
        np, sf = self.np, self.sf
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            source = Path(directory) / "stereo.wav"
            rate = 48000
            tone = .2 * np.sin(np.arange(rate) * (440 * 2 * np.pi / rate))
            signal = np.concatenate([np.zeros(rate), tone, np.zeros(rate // 2), tone, np.zeros(rate)])
            sf.write(source, np.column_stack([signal, signal]), rate)
            before = source.read_bytes()
            paths, cleanup = engine._prepare_cloning_references([str(source)], True)
            try:
                audio, actual_rate = sf.read(paths[0])
                self.assertEqual(actual_rate, rate)
                self.assertEqual(audio.ndim, 1)
                self.assertAlmostEqual(len(audio) / rate, 2.7, places=2)
                self.assertEqual(source.read_bytes(), before)
            finally:
                shutil.rmtree(cleanup)

    def test_balanced_style_takes_middle_of_every_recording(self):
        import torch
        from unittest.mock import patch
        engine = self.Engine.__new__(self.Engine)
        rate = 22050
        # Each recording is a constant value so the GPT input shows its origin.
        audio = {"a": torch.full((1, rate * 2), 1.0), "b": torch.full((1, rate * 100), 2.0), "c": torch.full((1, rate * 10), 3.0)}
        seen = {}
        def gpt_latents(mix, sr, length, chunk_length):
            seen.update(mix=mix, length=length, chunk=chunk_length)
            return torch.zeros(1, 4, 1)
        model = types.SimpleNamespace(device="cpu", get_gpt_cond_latents=gpt_latents,
            get_speaker_embedding=lambda clip, sr: torch.full((1, 3, 1), float(clip[0, 0])))
        engine.tts = types.SimpleNamespace(synthesizer=types.SimpleNamespace(tts_model=model))
        with patch("TTS.tts.models.xtts.load_audio", lambda path, sr: audio[path]):
            gpt, speaker = engine._balanced_conditioning_latents(["a", "b", "c"], max_ref_length=30, gpt_cond_len=12, gpt_cond_chunk_len=6, sound_norm_refs=False)
        mix = seen["mix"][0]
        self.assertEqual(seen["length"], -1)
        self.assertEqual(mix.shape[-1], rate * 12)
        for value, seconds in ((1.0, 2), (2.0, 5), (3.0, 5)):
            self.assertEqual(int((mix == value).sum()), rate * seconds)
        self.assertTrue(torch.allclose(speaker, torch.full((1, 3, 1), 2.0)))

    def test_both_render_paths_receive_profile_generation_settings(self):
        engine = self.Engine.__new__(self.Engine)
        calls = []
        np = self.np
        def inference(*args, **kwargs):
            calls.append(kwargs)
            return {"wav": np.zeros(48)}
        def stream(*args, **kwargs):
            calls.append(kwargs)
            yield np.zeros(48)
        engine.tts = types.SimpleNamespace(synthesizer=types.SimpleNamespace(tts_model=types.SimpleNamespace(inference=inference, inference_stream=stream)))
        engine.supports_streaming = lambda: True
        engine._get_or_create_voice_conditioning = lambda *args, **kwargs: dict(gpt_conditioning_latents=None, speaker_embedding=None)
        settings = dict(temperature=.65, top_p=.8, top_k=50, repetition_penalty=2, speed=1.2)
        engine._synthesize_from_references("test", [], speed=1.1, synthesis_settings=settings)
        list(engine._stream_synthesize_from_references("test", [], speed=1.1, synthesis_settings=settings))
        with tempfile.TemporaryDirectory(dir=ROOT / "tests") as directory:
            conditioning = Path(directory) / "draft.pth"
            conditioning.write_bytes(b"fixture")
            list(engine.stream_synthesize_preview_to_int16("custom preview", str(conditioning), cache_key="draft-test", synthesis_settings=settings))
        self.assertEqual(len(calls), 3)
        for call in calls[:2]:
            for key in ("temperature", "top_p", "top_k", "repetition_penalty"):
                self.assertEqual(call[key], settings[key])
            self.assertAlmostEqual(call["speed"], 1.32)
        self.assertEqual(calls[2]["temperature"], .65)
        self.assertEqual(calls[2]["speed"], 1.2)
        self.assertEqual(settings["speed"], 1.2)

    def test_cache_identity_changes_with_generation_settings(self):
        engine = self.Engine.__new__(self.Engine)
        engine.model_name = "test-model"
        record = types.SimpleNamespace(reference_paths=[], conditioning_path=None, metadata={})
        engine.voice_records = {"test": record}
        original = engine.get_voice_cache_key("test")
        record.metadata["synthesisSettings"] = {"temperature": .65}
        self.assertNotEqual(engine.get_voice_cache_key("test"), original)


if __name__ == "__main__":
    unittest.main()
