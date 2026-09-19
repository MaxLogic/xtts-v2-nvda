"""XTTS decoding with a CUDA graph must choose exactly the tokens Coqui's own decoding chooses.

Needs the real model and a CUDA GPU, so it runs only with the helper's Python:
    .helper-venv\\Scripts\\python.exe -m unittest discover -s tests -p test_fast_gpt.py
"""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


SYNTH_ROOT = Path(__file__).resolve().parents[1] / "addon/synthDrivers/maxlogic_xtts_v2"

# Coqui TTS and soundfile are installed only in the helper's Python.
HELPER_PYTHON = all(importlib.util.find_spec(name) for name in ("TTS", "soundfile", "torch"))
if HELPER_PYTHON:
    import torch
    CUDA = torch.cuda.is_available()
else:
    CUDA = False

TEXTS = (
    ("Closing during startup, by switching synths or exiting NVDA, stops the half-loaded helper.", "en"),
    ("Zapisane ustawienia głosu zostały wczytane.", "pl"),
)


@unittest.skipUnless(CUDA, "needs PyTorch with a CUDA GPU; run with the helper's Python")
class FastDecodingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(SYNTH_ROOT))
        import _helper_process  # noqa: F401  Sets up the helper's import paths.
        from _engine import XTTSV2Engine
        cls.engine = XTTSV2Engine(str(SYNTH_ROOT))
        cls.model = cls.engine.tts.synthesizer.tts_model
        cls.gpt = cls.model.gpt
        # Conditioning for the first installed voice, taken from a normal request.
        captured = {}
        original = cls.model.inference_stream

        def recording(*args, **kwargs):
            captured.setdefault("call", (args, kwargs))
            return original(*args, **kwargs)

        with patch.object(cls.model, "inference_stream", recording):
            list(cls.engine.stream_synthesize_to_int16("Warm up.", voice=cls.engine.current_voice))
        cls.args, cls.kwargs = captured["call"]

    def tokens(self, text, language, fast, seed=0, stop_after=None, **override):
        chosen = []
        get_generator = self.engine.fast_get_generator if fast else self.engine.coqui_get_generator

        def spy(*args, **kwargs):
            for token, latent in get_generator(*args, **kwargs):
                chosen.append(int(token))
                yield token, latent

        kwargs = dict(self.kwargs)
        kwargs.update(override)
        torch.manual_seed(seed)
        with patch.object(self.gpt, "get_generator", spy):
            stream = self.model.inference_stream(text, language, self.args[2], self.args[3], **kwargs)
            for number, __ in enumerate(stream):
                if stop_after is not None and number + 1 >= stop_after:
                    stream.close()
                    break
        return chosen

    def test_the_engine_decodes_with_a_cuda_graph(self):
        self.assertIsNotNone(self.engine.fast_decoding)

    def test_greedy_decoding_chooses_the_same_tokens(self):
        for text, language in TEXTS:
            with self.subTest(language=language):
                expected = self.tokens(text, language, fast=False, top_k=1)
                self.assertEqual(self.tokens(text, language, fast=True, top_k=1), expected)

    def test_sampling_with_the_same_seed_chooses_the_same_tokens(self):
        text, language = TEXTS[0]
        self.assertEqual(self.tokens(text, language, fast=True, seed=5), self.tokens(text, language, fast=False, seed=5))

    def test_a_cancelled_request_does_not_affect_the_next(self):
        text, language = TEXTS[0]
        expected = self.tokens(text, language, fast=False, top_k=1)
        self.tokens(TEXTS[1][0], TEXTS[1][1], fast=True, top_k=1, stop_after=1)
        self.assertEqual(self.tokens(text, language, fast=True, top_k=1), expected)

    def test_a_failing_graph_falls_back_to_coqui_decoding(self):
        text, language = TEXTS[0]
        expected = self.tokens(text, language, fast=False, top_k=1)
        with patch.object(type(self.engine.fast_decoding), "get_generator", side_effect=RuntimeError("capture failed")):
            fallback = self.engine.fast_decoding
            try:
                self.assertEqual(self.tokens(text, language, fast=True, top_k=1), expected)
                self.assertIsNone(self.engine.fast_decoding, "the failing graph was not turned off")
            finally:
                self.engine.fast_decoding = fallback


class SwitchTests(unittest.TestCase):
    def test_the_environment_can_turn_the_graph_off(self):
        sys.path.insert(0, str(SYNTH_ROOT))
        try:
            import _engine
        except Exception as error:
            self.skipTest("the engine needs the helper's Python: %s" % error)
        with patch.dict(os.environ, {"MAXLOGIC_XTTS_V2_CUDA_GRAPH": "0"}):
            self.assertFalse(_engine.cuda_graph_decoding_enabled(use_gpu=True))
        with patch.dict(os.environ, {"MAXLOGIC_XTTS_V2_CUDA_GRAPH": ""}):
            self.assertTrue(_engine.cuda_graph_decoding_enabled(use_gpu=True))
            self.assertFalse(_engine.cuda_graph_decoding_enabled(use_gpu=False))


if __name__ == "__main__":
    unittest.main()
