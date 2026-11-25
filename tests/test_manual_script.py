from random import Random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

from studio.pipeline import process
from studio.script import approve_manual, invalid_manual_lines, manual_lines
from studio.store import Store


class ManualScriptTests(unittest.TestCase):
    def setUp(self):
        self.lines = ["  Você sabia que o polvo consegue mudar de cor?  "] + [
            f"Na cena {index}, o polvo muda de cor para se esconder."
            for index in range(2, 15)
        ]
        self.text = "\r\n".join(self.lines) + "\r\n"

    def test_validation_and_visual_markers(self):
        self.assertEqual(manual_lines(self.text), self.lines)
        self.assertEqual(invalid_manual_lines([13, 0, 19] + [10] * 12), [0, 1, 2, 14])
        for lines in (self.lines[:-1], ["gancho muito longo " * 5] + self.lines[1:],
                      [self.lines[0], "palavra " * 19] + self.lines[2:],
                      ["palavra"] * 14, ["palavra " * 13] * 14, self.lines[:1] + [""] + self.lines[2:]):
            with self.subTest(lines=lines), self.assertRaises(ValueError):
                approve_manual({"scenes": []}, "\n".join(lines))

    def test_pipeline_uses_approved_text_and_never_generates_script(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            project = store.new("Polvos", "Animais", manual_script=self.text,
                                title="Polvos", description="", hashtags="")
            with patch("studio.pipeline.generate") as generate, patch("studio.pipeline.research") as research:
                with self.assertRaisesRegex(ValueError, "Aprove"):
                    process(store, project, {}, "Vídeo", lambda *_: None, lambda: None)
                approve_manual(project, self.text)
                store.save(project)
                project = store.history()[0]
                folder = store.folder(project)
                for index, scene in enumerate(project["scenes"]):
                    image = Image.new("RGB", (360, 340), "#2d573f")
                    draw = ImageDraw.Draw(image)
                    for x in range(8):
                        for y in range(8):
                            if Random(index * 64 + x * 8 + y).getrandbits(1):
                                draw.rectangle((x * 45, y * 42, x * 45 + 20, y * 42 + 24), fill="white")
                    name = f"imagem-{index}.jpg"
                    image.save(folder / name)
                    scene.update(image={"file": name}, image_status="Imagem válida")
                with patch("studio.pipeline.binary", return_value=True), patch(
                    "studio.pipeline.run", return_value="\n".join(
                        f" T. {name} V->V" for name in
                        ("ass", "xfade", "overlay", "avgblur", "rgbashift", "sidechaincompress")
                    )
                ), patch("studio.pipeline.prepare_images") as images, patch(
                    "studio.pipeline.prepare_edit"
                ) as edit, patch("studio.pipeline.SceneNarrator") as narrator, patch(
                    "studio.pipeline.render", return_value=store.folder(project)
                ) as render:
                    narrator.return_value.scene.return_value = "audio.wav", []
                    narrator.return_value.word_timings = []
                    process(store, project, {"light": True}, "Renderizar", lambda *_: None, lambda: None)
                    narrator.assert_not_called()
                    process(store, project, {"light": True}, "Vídeo", lambda *_: None, lambda: None)
                    narrator.assert_not_called()
                    self.assertEqual(project["status"], "Revisar cenas")
                    self.assertTrue(project["media_review"])
                    process(store, project, {"light": True}, "Renderizar", lambda *_: None, lambda: None)
                generate.assert_not_called()
                research.assert_not_called()
                images.assert_called_once()
                edit.assert_called_once()
                self.assertEqual([call.args[0] for call in narrator.return_value.scene.call_args_list],
                                 self.lines)
                self.assertEqual([scene["text"] for scene in render.call_args.args[0]["scenes"]],
                                 self.lines)
                self.assertEqual(store.history()[0]["status"], "Pronto")
