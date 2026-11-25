import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from PIL import Image
from studio.images import acquire, normalize

class ImageTests(unittest.TestCase):
    def test_failed_search_leaves_scene_without_media_file(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            client = Mock()
            client.get.return_value.json.return_value = {}
            image = acquire(client, {"comfy_enabled": False, "light": True, "pexels": "", "pixabay": ""},
                            folder, {"query": "axolote"}, 1, set(), lambda: None)
            self.assertIsNone(image)
            self.assertFalse(list(folder.glob("*.jpg")))

    def test_local_library_fallback_for_relevant_jpeg(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            library = folder / "library"
            library.mkdir()
            source = library / "Giza pyramids sunset.jpg"
            Image.new("RGB", (440, 320), "#996544").save(source)
            client = Mock()
            client.get.return_value.json.return_value = {}
            image = acquire(client, {"media_library": str(library)}, folder,
                            {"keywords": ["Giza pyramids sunset"]}, 1, [], lambda: None)
            self.assertEqual(image["provider"], "Biblioteca local")
            with Image.open(folder / image["file"]) as normalized:
                self.assertEqual(normalized.size, (440, 320))

    def test_download_validation_rejects_small_or_corrupt_images(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "tiny.png"
            Image.new("RGBA", (50, 50)).save(source)
            with self.assertRaisesRegex(ValueError, "resolução"):
                normalize(source, folder / "new.jpg")
            source.write_bytes(b"corrupt")
            with self.assertRaises(OSError):
                normalize(source, folder / "new.jpg")

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
            with (patch("studio.images.commons", side_effect=search, __name__="commons") as commons,
                  patch("studio.images.cached_download", side_effect=download)):
                image = acquire(Mock(), settings, folder, {"query": "glass water",
                                "keywords": ["glass water", "boiling water"]},
                                1, [fingerprints(old)], lambda: None)
            self.assertEqual(commons.call_count, 2)
            self.assertEqual(image["search_query"], "boiling water")
            self.assertEqual(image["sha256"], fingerprints(new)["sha256"])

    def test_provider_fallback_rejects_reverse_scans_and_watermarked_metadata(self):
        from studio.images import fingerprints

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            source = folder / "giza.jpg"
            Image.new("RGB", (640, 480), "#494522").save(source)
            denied = requests.Response()
            denied.status_code = 403
            candidate = {"asset": "https://images.example/ok.jpg", "url": "https://images.example/ok",
                         "title": "Giza pyramids sunset", "provider": "Openverse",
                         "license": "CC0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"}
            rejected = [
                dict(candidate, url="https://images.example/back", title="Giza pyramids sunset - back - postcard"),
                dict(candidate, url="https://images.example/mark", tags="Watermarked preview"),
            ]
            with (patch("studio.images.commons", __name__="commons",
                        side_effect=requests.HTTPError(response=denied)) as commons,
                  patch("studio.images.openverse", __name__="openverse", return_value=[*rejected, candidate]),
                  patch("studio.images.cached_download", return_value=(source, fingerprints(source))) as download):
                media = acquire(Mock(), {}, folder, {"keywords": ["Giza pyramids sunset", "Egypt sunset"]},
                                1, [], lambda: None)
            commons.assert_called_once()
            download.assert_called_once()
            self.assertEqual(media["url"], candidate["url"])
