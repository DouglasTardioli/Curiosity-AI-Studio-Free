import json
import tempfile
import unittest
from unittest.mock import MagicMock

from studio.research import key
from studio.script import generate, validate_scenes
from studio.store import Store

class ScriptTests(unittest.TestCase):
    def test_scene_limits(self):
        scenes = [{"text": "palavra " * 11} for _ in range(14)]
        validate_scenes(scenes)
        scenes[0]["text"] *= 2
        with self.assertRaises(ValueError):
            validate_scenes(scenes)

    def test_history(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            project = store.new("Saturno", "Espaço")
            project["sources"] = [{"id": "42"}]
            store.save(project)
            self.assertEqual(store.history(), [project])
        self.assertEqual(key("TITÃ (satélite)"), "tita satelite")

    def test_generator_retries_excerpt_that_is_not_in_source(self):
        scene = {"text": "Você sabia que algumas espécies mantêm características juvenis quando se tornam adultas?",
                 "query": "axolote", "prompt": "Fotografia vertical de um axolote", "source_id": "1",
                 "evidence": "Trecho fabricado pelo modelo."}
        payload = {"title": "O axolote", "description": "Curiosidades sobre o axolote",
                   "hashtags": "#Axolote", "scenes": [scene]*14}
        invalid = json.dumps(payload)
        scene["evidence"] = "mantém características juvenis"
        valid = json.dumps(payload)
        client = MagicMock()
        response = client.post.return_value.__enter__.return_value
        response.iter_lines.side_effect = [[json.dumps({"response": value}).encode()] for value in (invalid, valid)]
        settings = {"ollama": "http://localhost:11434", "model": "qwen2.5:3b", "light": True}
        sources = [{"id": "1", "text": "O axolote mantém características juvenis na fase adulta."}]
        result = generate(client, settings, "Axolote", sources)
        self.assertEqual(result["scenes"][0]["evidence"], scene["evidence"])
        self.assertEqual(client.post.call_count, 2)
