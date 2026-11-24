import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PIL import Image
from studio.images import acquire, normalize

class ImageTests(unittest.TestCase):
    def test_reservation_card_and_normalization(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            client = Mock()
            client.get.return_value.json.return_value = {}
            image = acquire(client, {"comfy_enabled": False, "light": True, "pexels": "", "pixabay": ""},
                            folder, {"query": "axolote"}, 1, set(), lambda: None)
            self.assertEqual(image["provider"], "Cartão gráfico")
            target = folder / "manual.jpg"
            normalize(folder / image["file"], target)
            with Image.open(target) as normalized:
                self.assertEqual(normalized.size, (720, 1280))

    def test_relevance_ranks_subject_above_incidental_match(self):
        from studio.images import relevance
        query = "ice cubes water"
        relevant = {"title": "File:Ice cubes on water.jpg"}
        incidental = {"title": "File:Pear vodka bottle and glass with ice cube.jpg",
                      "tags": "a pear vodka advertisement with water"}
        self.assertGreater(relevance(relevant, query), relevance(incidental, query))
        self.assertFalse(relevance({"title": "File:Wooden table.jpg"}, query))

    def test_duplicate_urls_digests_and_visual_hash(self):
        from studio.images import duplicate
        existing = {"url": "https://example.com/photo?id=tracking",
                    "sha256": "same", "dhash": "1234567890abcdef"}
        self.assertTrue(duplicate({"url": "https://example.com/photo/"}, [existing]))
        self.assertTrue(duplicate({"sha256": "same"}, [existing]))
        self.assertTrue(duplicate({"dhash": "1234567890abcde0"}, [existing]))
        self.assertFalse(duplicate({"dhash": "edcba9876f543210"}, [existing]))

    def test_search_moves_to_another_query_after_duplicate(self):
        from unittest.mock import patch
        from studio.images import fingerprints

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            old, new = folder / "old.jpg", folder / "new.jpg"
            gradient = Image.linear_gradient("L").rotate(90)
            gradient.save(old)
            Image.eval(gradient, lambda value: 255 - value).save(new)
            first = {"asset": "https://example.com/old.jpg", "url": "https://example.com/old",
                     "title": "glass water"}
            second = {"asset": "https://example.com/new.jpg", "url": "https://example.com/new",
                      "title": "boiling water"}
            def search(client, query):
                return [first] if query == "glass water" else [second]
            def download(candidate, cache, check):
                path = old if candidate["url"] == first["url"] else new
                return path, fingerprints(path)
            settings = {"comfy_enabled": False, "light": True, "pexels": "", "pixabay": ""}
            with (patch("studio.images.commons", side_effect=search) as commons,
                  patch("studio.images.cached_download", side_effect=download)):
                image = acquire(Mock(), settings, folder, {"query": "glass water",
                                "keywords": ["glass water", "boiling water"]},
                                1, [fingerprints(old)], lambda: None)
            self.assertEqual(commons.call_count, 2)
            self.assertEqual(image["search_query"], "boiling water")
            self.assertEqual(image["sha256"], fingerprints(new)["sha256"])
