"""The driver sends XTTS whole sentences, as few requests as the model's text limit allows.

The helper streams audio as it is generated, so a small first chunk no longer
makes speech start sooner. Every chunk boundary costs a new request, which
needs about 0.8 s before its first audio, and it breaks the sentence melody.
"""
import re
import sys
import unittest
from unittest.mock import patch

from test_driver_sayall import _Engine, load_driver


class ChunkingTests(unittest.TestCase):
    def setUp(self):
        self.module, __, __, patcher = load_driver()
        self.addCleanup(patcher.stop)
        self.addCleanup(sys.modules.pop, "maxlogic_xtts_v2_sayall_under_test", None)
        with patch.object(self.module.SynthDriver, "_create_engine", lambda driver: _Engine()):
            self.driver = self.module.SynthDriver()
        self.addCleanup(self.driver.terminate)

    def chunks(self, text, language="en-us"):
        return self.driver._chunk_text_for_playback(text, language)

    def assert_nothing_lost(self, text, chunks):
        self.assertEqual(" ".join(chunks), re.sub(r"\s+", " ", text).strip())

    def test_a_line_under_the_limit_is_one_chunk(self):
        line = "- Closing during startup, by switching synths or exiting NVDA, stops the half-loaded helper instead of waiting for it."
        self.assertEqual(self.chunks(line), [line])

    def test_sentences_are_packed_up_to_the_limit_and_split_only_at_sentence_ends(self):
        text = " ".join("Sentence number %d says a few words about the weather today." % n for n in range(12))
        chunks = self.chunks(text)
        self.assert_nothing_lost(text, chunks)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 200)
            self.assertTrue(chunk.endswith("."), "split inside a sentence: %r" % chunk)
        # Packing: no two neighbours would have fitted into one chunk.
        for first, second in zip(chunks, chunks[1:]):
            self.assertGreater(len(first) + 1 + len(second.split(". ")[0]) + 1, 200)

    def test_a_sentence_over_the_limit_is_split_at_a_comma(self):
        text = ", ".join("clause number %d of one very long sentence" % n for n in range(10)) + "."
        chunks = self.chunks(text)
        self.assert_nothing_lost(text, chunks)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks[:-1]:
            self.assertLessEqual(len(chunk), 200)
            self.assertTrue(chunk.endswith(","), "split inside a clause: %r" % chunk)

    def test_the_limit_follows_the_language(self):
        polish = " ".join("Zdanie numer %d opowiada krótko o pogodzie na dziś." % n for n in range(12))
        self.assertLessEqual(max(len(chunk) for chunk in self.chunks(polish, "pl")), 179)
        chinese = "".join("这是第%d个句子，它讲的是今天的天气。" % n for n in range(12))
        chunks = self.chunks(chinese, "zh-cn")
        self.assertEqual("".join(chunks), chinese)
        self.assertLessEqual(max(len(chunk) for chunk in chunks), 65)
        self.assertTrue(all(chunk.endswith("。") for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
