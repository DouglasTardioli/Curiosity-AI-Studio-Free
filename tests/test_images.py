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
