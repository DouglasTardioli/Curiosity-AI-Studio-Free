import os
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from studio.images import card
from studio.motion import (CAMERAS, ENTRANCES, EXITS, SOUNDS, TRANSITIONS, default_plan,
                           graphic_canvas_size, overlay_filters)
from studio.sound import track
from studio.tools import CommandError, probe, run
from studio.video import graphic_file, render, scene_clip
from studio.voice import SAMPLE_RATE, SCENE_SAMPLES

CHECK = lambda: None
TEXT = "A água congela? Açúcar também afeta a solução."


def make_media(folder):
    Image.new("RGB", (721, 395), "#284a76").save(folder / "wide.jpg")
    Image.new("RGB", (379, 721), "#486a35").save(folder / "tall.png")
    for name, size in (("wide-alpha.png", (311, 177)), ("tall-alpha.png", (195, 393))):
        sprite = Image.new("RGBA", size)
        ImageDraw.Draw(sprite).rectangle((size[0] // 4, 22, 3 * size[0] // 4, size[1] - 20),
                                         fill=(246, 95, 20, 190))
        sprite.save(folder / name)


@unittest.skipUnless(os.getenv("CURIOSITY_TEST_RENDER") == "1", "Render completo opcional")
class VideoTests(unittest.TestCase):
    def test_real_full_length_mp4(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            make_media(folder)
            audio_path = folder / "audio.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(SCENE_SAMPLES*2))
            scenes = [
                {"text": TEXT, "image": {"file": ("wide.jpg", "tall.png")[index % 2]},
                 "graphic": {"file": ("wide-alpha.png", "tall-alpha.png")[index % 2]}}
                for index in range(14)
            ]
            with self.assertNoLogs("studio.video", level="WARNING"):
                out = render({"scenes": scenes}, folder, [audio_path] * 14, [4.35] * 14,
                             True, lambda *_: None, CHECK,
                             settings={"edit_mode": "Viral", "intensity": 80})
            info = probe(out / "video.mp4", CHECK)
            self.assertEqual(float(info["format"]["duration"]), 70.0)
            self.assertEqual({stream["codec_type"] for stream in info["streams"]},
                             {"video", "audio", "subtitle"})
            self.assert_video(out / "video.mp4", (1080, 1920), 2100)
            durations = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                             "-show_entries", "packet=duration", "-of", "csv=p=0",
                             str(out / "video.mp4")], CHECK).splitlines()
            self.assertEqual(durations, ["1000"] * 2100)
            clips = sorted(out.glob("clip-*.mp4"))
            self.assertEqual(len(clips), 14)
            for clip in clips:
                self.assert_video(clip, (1080, 1920), 150)

    def test_animated_preview_with_graphic_audio_and_accents(self):
        from studio.decoration import local_graphic

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            card(folder / "cena.jpg", 1)
            text = "A água congela? Açúcar também afeta a solução."
            local_graphic(folder / "elemento.png", "Água", 1)
            audio_path = folder / "audio.wav"
            with wave.open(str(audio_path), "wb") as audio:
                audio.setparams((1, 2, SAMPLE_RATE, 0, "NONE", "not compressed"))
                audio.writeframes(bytes(SCENE_SAMPLES*2))
            plan = default_plan(0, text, [], "Viral")
            plan.update(camera="shake", entrance="triumph", sound="impact")
            scene = {"text": text, "edit": plan, "image": {"file": "cena.jpg"},
                     "graphic": {"file": "elemento.png"}}
            check = lambda: None
            out = render({"scenes": [scene]}, folder, [audio_path], [4.6], True,
                         lambda *args: None, check,
                         settings={"edit_mode": "Viral", "intensity": 80, "draft": True})
            info = probe(out / "video.mp4", check)
            video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"], video["nb_frames"]), (540, 960, "150"))
            self.assertEqual(float(info["format"]["duration"]), 5)
            self.assertIn("Açúcar", (out / "legendas.ass").read_text(encoding="utf-8"))
            self.assertTrue((out / "efeitos.wav").is_file())

    def assert_video(self, path, size, frames):
        stream = next(item for item in probe(path, CHECK)["streams"] if item["codec_type"] == "video")
        self.assertEqual((stream["width"], stream["height"]), size)
        self.assertEqual((stream["pix_fmt"], stream["sample_aspect_ratio"], stream["time_base"]),
                         ("yuv420p", "1:1", "1/30000"))
        self.assertEqual((stream["r_frame_rate"], stream["avg_frame_rate"]), ("30/1", "30/1"))
        self.assertEqual(int(stream["nb_frames"]), frames)
        self.assertEqual(stream["duration_ts"], frames * 1000)

    def test_each_effect_and_transition_without_fallback(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            make_media(folder)
            run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=306x190:r=12:d=1",
                 "-vf", "setsar=4/3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(folder / "broll.mp4")], CHECK)
            scene = {"text": TEXT, "image": {"file": "wide.jpg"}, "video": {"file": "broll.mp4"}}
            settings = {"light": True, "draft": True, "edit_mode": "Viral", "intensity": 90}
            cues = [(1.0, 4.3, [TEXT])]
            plan = default_plan(0, TEXT, [], "Viral")
            cases = (
                [dict(entrance=name, camera="ken-burns", rotation=25,
                      exit=EXITS[index % len(EXITS)]) for index, name in enumerate(ENTRANCES)]
                + [dict(camera=name, media="video") for name in CAMERAS]
                + [dict(transition=name) for name in TRANSITIONS]
            )
            for index, effect in enumerate(cases):
                scene["graphic"] = {"file": ("wide-alpha.png", "tall-alpha.png")[index % 2]}
                following = {"file": "tall.png"} if "transition" in effect else None
                with self.subTest(effect=effect), self.assertNoLogs("studio.video", level="WARNING"):
                    clip = scene_clip(folder, folder, scene, following, cues, dict(plan, **effect),
                                      index, settings, CHECK)
                    self.assert_video(clip, (540, 960), 150)

    def test_alpha_stays_on_fixed_canvas_during_animation(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            make_media(folder)
            source = folder / "composition.png"
            graphic_file(folder / "wide-alpha.png", source, 120)
            canvas = graphic_canvas_size(120)
            frame_size = canvas * canvas * 4
            plan = dict(default_plan(0, TEXT, [], "Viral"), entrance="triumph", rotation=25)
            graph, _ = overlay_filters(120, plan, 2.2, .9)
            output = folder / "alpha.rgba"
            run(["ffmpeg", "-v", "error", "-filter_threads", "1", "-i", str(source), "-vf", graph,
                 "-frames:v", "150", "-pix_fmt", "rgba", "-f", "rawvideo", str(output)], CHECK)
            raw = output.read_bytes()
            self.assertEqual(len(raw), 150 * frame_size)
            alpha = [raw[index * frame_size + 3:(index + 1) * frame_size:4] for index in (0, 71, 90, 149)]
            self.assertEqual(max(alpha[0]), 0)
            self.assertEqual(max(alpha[-1]), 0)
            for frame in alpha[1:3]:
                self.assertEqual(frame[0], 0)
                self.assertGreater(max(frame), 0)
                self.assertLess(max(frame), 255)
            self.assertNotEqual(sum(alpha[1]), sum(alpha[2]))

    def test_recovers_from_isolated_effect_and_logs_diagnostics(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            make_media(folder)
            scene = {"text": TEXT, "image": {"file": "wide.jpg"}, "graphic": {"file": "tall-alpha.png"}}
            settings = {"light": True, "draft": True, "edit_mode": "Viral", "intensity": 90}
            plan = dict(default_plan(0, TEXT, [], "Viral"), entrance="bounce", rotation=25)
            graphs = []

            def fail_complex_effects(command, *args, **kwargs):
                graph = command[command.index("-filter_complex") + 1]
                graphs.append(graph)
                if "[graphic]" in graph:
                    raise CommandError("início da mensagem\n" + "x" * 2000 + "\nInvalid argument")
                return run(command, *args, **kwargs)

            with patch("studio.video.run", side_effect=fail_complex_effects), self.assertLogs(
                "studio.video", level="INFO"
            ) as logs:
                clip = scene_clip(folder, folder, scene, {"file": "tall.png"}, [],
                                  plan, 0, settings, CHECK)
            self.assert_video(clip, (540, 960), 150)
            self.assertEqual(len(graphs), 3)
            self.assertIn("rotate=", graphs[0])
            self.assertNotIn("rotate=", graphs[1])
            self.assertIn("xfade=transition=fade", graphs[1])
            self.assertIn("zoompan=", graphs[2])
            self.assertNotIn("[graphic]", graphs[2])
            log = "\n".join(logs.output)
            for fragment in ("Cena 1", "entrada=bounce", "FFmpeg=", "filter_complex=", "195", "393",
                             "rgba", "início da mensagem", "Invalid argument", "zoom suave"):
                self.assertIn(fragment, log)

    def test_each_sound_effect_fits_its_scene(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            names = [name for name in SOUNDS if name != "none"]
            output = folder / "sounds.wav"
            track(output, [(name, 2.2) for name in names], .85, folder / "cache", CHECK)
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getnframes(), len(names) * SCENE_SAMPLES)
                for name in names:
                    with self.subTest(sound=name):
                        self.assertTrue(any(audio.readframes(SCENE_SAMPLES)))
