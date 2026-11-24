import json
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from studio.pipeline import process
from studio.research import key
from studio.script import generate, validate_scenes, words
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
        payload["scenes"] = [dict(scene, text="Você sabia que o axolote mantém traços juvenis?")] + [scene]*13
        payload["scenes"][0]["evidence"] = "mantém características juvenis"
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

    def test_python_word_count_handles_accents_and_hyphens(self):
        self.assertEqual(words("Café, verde-azulado e d’água! _ cafe\u0301"), [
            "Café", "verde-azulado", "e", "d’água", "café"])

    def generate_with(self, lengths, corrections=(), flexible=False):
        scene = {"query": "axolote", "prompt": "Fotografia vertical",
                 "source_id": "1", "evidence": "O axolote mantém características juvenis"}
        initial = {"title": "Axolote", "description": "Sobre o axolote", "hashtags": "#Axolote",
                   "scenes": [dict(scene, text=" ".join(["axolote"] * size)) for size in lengths]}
        client = MagicMock()
        response = client.post.return_value.__enter__.return_value
        response.iter_lines.side_effect = [
            [json.dumps({"response": json.dumps(reply)}).encode()] for reply in (initial, *corrections)]
        settings = {"ollama": "http://localhost:11434", "model": "qwen2.5:3b",
                    "light": True, "flexible_script": flexible}
        progress = MagicMock()
        draft = generate(client, settings, "Axolote", [{"id": "1", "text": scene["evidence"]}],
                         progress=progress)
        return draft, [call.kwargs["json"] for call in client.post.call_args_list], progress

    def test_only_hook_is_rewritten(self):
        hook = "Você sabia de tudo que um axolote faz?"
        draft, calls, progress = self.generate_with([15] + [12]*13, [{"text": hook}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["format"]["required"], ["text"])
        self.assertEqual(calls[1]["options"]["temperature"], 0.3)
        self.assertEqual(calls[1]["options"]["num_gpu"], 0)
        self.assertIn("171 palavras", calls[1]["prompt"])
        self.assertEqual(draft["scenes"][0]["text"], hook)
        self.assertEqual(draft["scenes"][1]["text"], " ".join(["axolote"]*12))
        self.assertIn("apenas gancho", progress.call_args_list[1].args[1])

    def test_flexible_count_uses_one_call(self):
        draft, calls, _ = self.generate_with([8] + [10]*13, flexible=True)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("script_review", draft)

    def test_three_failed_corrections_leave_reviewable_draft(self):
        corrections = [{"texts": ["Axolote"]*14}]*3
        with self.assertLogs("studio.script", level="WARNING") as logs:
            draft, calls, _ = self.generate_with([8] + [10]*13, corrections)
        self.assertEqual(len(calls), 4)
        self.assertIn("138 palavras", calls[1]["prompt"])
        self.assertIn("138 palavras", draft["script_review"][0])
        self.assertIn("rodada 4/4", logs.output[-1])
        validate_scenes(draft["scenes"], manual=True)

    def test_length_retry_preserves_metadata_and_evidence(self):
        corrected = ["Axolote " * 8] + ["Axolote " * 12]*13
        draft, calls, _ = self.generate_with([8] + [10]*13, [{"texts": corrected}])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["format"]["required"], ["texts"])
        self.assertEqual(draft["title"], "Axolote")
        self.assertEqual(draft["scenes"][0]["source_id"], "1")
        self.assertNotIn("script_review", draft)

    def test_pending_draft_survives_video_request_without_starting_media(self):
        with tempfile.TemporaryDirectory() as root:
            store = Store(root)
            project = store.new("Axolote", "Natureza")
            project["sources"] = [{"id": "1"}]
            draft = {"scenes": [{"text": "Axolote"}]*14, "script_review": ["Faixa inválida."]}
            with (patch("studio.pipeline.binary", return_value=True),
                  patch("studio.pipeline.generate", return_value=draft) as generate_mock,
                  patch("studio.pipeline.acquire") as acquire_mock):
                process(store, project, {}, "Vídeo", lambda *_: None, lambda: None)
                reopened = store.history()[0]
                self.assertEqual(reopened["status"], "Revisão pendente")
                self.assertEqual(reopened["scenes"], draft["scenes"])
                process(store, reopened, {}, "Vídeo", lambda *_: None, lambda: None)
                generate_mock.assert_called_once()
                acquire_mock.assert_not_called()
