import os
import tempfile
import unittest
import wave
from pathlib import Path

from studio.images import card
from studio.tools import probe
from studio.video import render
from studio.voice import SAMPLE_RATE, SCENE_SAMPLES

@unittest.skipUnless(os.getenv("CURIOSITY_TEST_RENDER") == "1", "Render completo opcional")
class VideoTests(unittest.TestCase):
    def test_real_full_length_mp4(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            card(folder / "cena.jpg", 1)
            audio_path = folder / "audio.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(SCENE_SAMPLES*2))
            scenes = [{"text": "Você sabia que o axolote preserva suas brânquias externas quando adulto?", "image": {
                "file": "cena.jpg", "provider": "Cartão gráfico"}} for _ in range(14)]
            check = lambda: None
            out = render({"scenes": scenes}, folder, [audio_path]*14, [4.35]*14,
                         True, lambda *args: None, check)
            info = probe(out / "video.mp4", check)
            self.assertEqual(float(info["format"]["duration"]), 70.0)
            self.assertEqual({stream["codec_type"] for stream in info["streams"]},
                             {"video", "audio", "subtitle"})
