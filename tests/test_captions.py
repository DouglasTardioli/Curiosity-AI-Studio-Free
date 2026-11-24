import unittest

from studio.captions import ass, blocks, scene_cues, srt

class CaptionTests(unittest.TestCase):
    def test_accented_text_survives_utf8_ass_and_srt(self):
        text = "Água à pressão e água já: ângulo, é, você, líquido, ó, avô, põe, úmido e açúcar."
        groups = blocks(text)
        self.assertGreater(len(groups), 1)
        self.assertTrue(all(len(lines) <= 2 for lines in groups))
        self.assertEqual(" ".join(line for group in groups for line in group), text)
        timing = [(i, i + 1, lines) for i, lines in enumerate(groups)]
        cues = list(scene_cues([{"text": text}]*2, [timing, timing]))
        self.assertEqual(cues[len(groups)][0], 5)
        for encoded in (srt(cues), ass(cues)):
            result = encoded.encode("utf-8").decode("utf-8")
            for character in "Ááàâãéêíóôõúç":
                self.assertIn(character, result)
