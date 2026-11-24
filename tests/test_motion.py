import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from studio.captions import ass
from studio.decoration import local_graphic, transparent
from studio.motion import default_plan, event_moment, plans_for, validate_plan

class MotionTests(unittest.TestCase):
    def test_plan_only_uses_known_effects_and_limits(self):
        base = default_plan(0, "Água quente", [], "Viral")
        plan = validate_plan({"camera": "; movie", "transition": "custom", "at": -50,
                              "scale_to": float("inf"), "rotation": 200}, base)
        self.assertEqual(plan["camera"], base["camera"])
        self.assertEqual(plan["transition"], base["transition"])
        self.assertEqual(plan["scale_to"], base["scale_to"])
        self.assertEqual(plan["at"], 1.7)
        self.assertEqual(plan["rotation"], 25)
        scenes = [{"text": "Água quente"}]*3
        plans = plans_for({"scenes": scenes}, "Viral", [base]*3)
        self.assertNotEqual(plans[0]["entrance"], plans[1]["entrance"])

    def test_real_transparency_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "element.png"
            fake = Image.new("RGBA", (192, 192), (190, 190, 190, 255))
            draw = ImageDraw.Draw(fake)
            for x in range(0, 192, 24):
                for y in range(0, 192, 24):
                    if (x+y) % 48:
                        draw.rectangle((x, y, x+23, y+23), fill=(230, 230, 230, 255))
            fake.save(path)
            with self.assertRaises(ValueError):
                transparent(path)
            local_graphic(path, "Açúcar e água", 0)
            self.assertEqual(transparent(path).getpixel((0, 0))[3], 0)

    def test_real_word_boundary_wins_over_estimate(self):
        plan = {"at": 2.2, "highlight": "água"}
        cues = [(1.4, 3.4, ["Uma água fria"])]
        self.assertEqual(event_moment(cues, plan, [(2.55, "água")]), 2.55)
        document = ass(cues, "água", .6, [(2.55, "água")])
        self.assertIn(r"\t(1150,1220,\c&H0054E4FF&)", document)
        self.assertIn("água", document)

    def test_missing_remote_images_are_replaced_without_user_input(self):
        from unittest.mock import Mock, patch
        from studio.pipeline import images_ready, prepare_images
        from studio.store import Store

        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            project = store.new("Efeito Mpemba", "Ciência")
            project["scenes"] = [{"text": f"Água e gelo na cena {index}", "keywords": ["water ice"]}
                                 for index in range(14)]
            folder = store.folder(project)
            with patch("studio.pipeline.acquire", return_value={"file": "missing.jpg", "placeholder": True}):
                prepare_images(Mock(), store, project, {}, folder, lambda *_: None, lambda: None)
            self.assertTrue(images_ready(project, folder))
            self.assertEqual(len({scene["image"]["file"] for scene in project["scenes"]}), 14)
            self.assertTrue(all(scene["image"]["license"] == "CC0" for scene in project["scenes"]))
