import shutil
import tempfile
import unittest
import wave
from pathlib import Path

from studio.voice import SCENE_SAMPLES, fit_audio, fit_blocks, subtitles
from studio.tools import run

@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg necessário")
class VoiceTests(unittest.TestCase):
    def test_long_audio_is_compressed_and_padded(self):
        with tempfile.TemporaryDirectory() as root:
            raw, output = (Path(root) / name for name in ("raw.wav", "fit.wav"))
            run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=d=8", str(raw)], lambda: None)
            duration = fit_audio(raw, output, lambda: None)
            self.assertAlmostEqual(duration, 4.65, delta=0.08)
            with wave.open(str(output)) as audio:
                self.assertEqual(audio.getnframes(), SCENE_SAMPLES)
            self.assertIn("00:00:05,000 --> 00:00:09", subtitles(
                [{"text": "Uma cena."}, {"text": "Outra cena."}], [duration, duration]))

    def test_cue_boundaries_follow_actual_audio_blocks(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            files = [folder / name for name in ("short.wav", "long.wav")]
            for path, seconds in zip(files, (1, 3), strict=True):
                run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=d={seconds}", str(path)],
                    lambda: None)
            output = folder / "output.wav"
            cues = fit_blocks(files, [["Água"], ["Congelação"]], output, folder, lambda: None)
            self.assertEqual(cues[0][0], 0)
            self.assertEqual(cues[0][1], cues[1][0])
            self.assertGreater(cues[-1][1] - cues[-1][0], 2.5 * cues[0][1])
            self.assertLess(cues[-1][1], 5)
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getnframes(), SCENE_SAMPLES)
