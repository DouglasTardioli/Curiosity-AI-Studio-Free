import hashlib
import json
import logging
import random
import shutil
import tempfile
from pathlib import Path

import requests

from .decoration import artwork, fallback_plate, video_asset
from .motion import MODES, plans_for, predict
from .images import acquire, duplicate, fingerprints, generate_keywords
from .research import CATEGORIES, key, research, session
from .script import generate, manual_lines, validate_scenes
from .tools import binary, run
from .video import credits, render
from .voice import SceneNarrator

LOGGER = logging.getLogger(__name__)

class Interrupted(Exception):
    pass

def process(store, project, settings, mode, progress, check):
    manual = "manual_script" in project
    if manual:
        if not project.get("manual_approved"):
            raise ValueError("Aprove o roteiro manual antes de gerar o vídeo.")
        validate_scenes(project["scenes"])
        if [scene["text"] for scene in project["scenes"]] != manual_lines(project["manual_script"]):
            raise ValueError("O roteiro manual foi alterado. Aprove a versão atual antes de continuar.")
    folder = store.folder(project)
    previous_status = project["status"]
    try:
        with session() as client:
            if not manual and not project["scenes"]:
                progress(4, "Pesquisando na Wikipédia")
                history = [item for item in store.history() if item["id"] != project["id"]]
                excluded = {item["sources"][0]["id"] for item in history if item["sources"]}
                topics = {key(item["topic"]) for item in history if item["sources"]}
                if not project["sources"]:
                    custom = bool(project["topic"])
                    candidates = [project["topic"]] if custom else random.sample(
                        CATEGORIES[project["category"]], len(CATEGORIES[project["category"]]))
                    for candidate in candidates:
                        check()
                        if key(candidate) in topics:
                            continue
                        sources = research(client, candidate, excluded)
                        if sources:
                            project.update(topic=candidate, sources=sources)
                            store.save(project)
                            break
                    else:
                        raise ValueError("Tema já usado ou sem fonte na Wikipédia. Escolha outro tema ou categoria.")
                check()
                project.update(generate(client, settings, project["topic"], project["sources"], check, progress))
                project["status"] = "Revisão pendente" if project.get("script_review") else "Roteiro"
                store.save(project)
            if project.get("script_review"):
                progress(16, "Rascunho salvo. Revise e aprove o roteiro para continuar.")
                return
            if mode == "Roteiro":
                progress(100, "Roteiro salvo")
                return
            if not manual:
                validate_scenes(project["scenes"], manual=True)
            if mode in ("Vídeo", "Renderizar", "Prévia"):
                if not (binary("ffmpeg") and binary("ffprobe")):
                    raise RuntimeError("Instale FFmpeg e FFprobe e adicione ambos ao PATH.")
                filters = run(["ffmpeg", "-hide_banner", "-filters"], check)
                available = {parts[1] for line in filters.splitlines()
                             if len(parts := line.split()) >= 3}
                missing = {"ass", "xfade", "overlay", "avgblur", "rgbashift", "sidechaincompress"} - available
                if missing:
                    raise RuntimeError(f"Use uma compilação completa do FFmpeg com libass. "
                                       f"Filtros ausentes: {', '.join(sorted(missing))}.")
            preview = mode == "Prévia"
            if preview:
                if not existing_image(project["scenes"][0], folder):
                    prepare_images(client, store, project, settings, folder, progress, check, count=1)
            elif not images_ready(project, folder):
                prepare_images(client, store, project, settings, folder, progress, check)
            if mode == "Preparar":
                progress(46, "Cenas disponíveis para revisão opcional.")
                return
            prepare_edit(client, store, project, settings, folder, progress, check, preview)
            if not preview:
                project["status"] = "Processando"
                store.save(project)
        audios, timings = [], []
        narrator = SceneNarrator(settings, folder)
        selected = project["scenes"][:1] if preview else project["scenes"]
        for index, scene in enumerate(selected, 1):
            check()
            progress(46 + round(index*20/14), f"Narrando cena {index}/14")
            audio, cues = narrator.scene(scene["text"], index, check)
            audios.append(audio)
            timings.append(cues)
            scene["word_timings"] = narrator.word_timings
        out = render({**project, "scenes": selected}, folder, audios, timings, settings["light"],
                     progress, check, settings={**settings, "draft": preview})
        if preview:
            progress(100, "Prévia de cinco segundos pronta.")
            return
        (out / "creditos-e-publicacao.txt").write_text(credits(project, settings.get("music")), encoding="utf-8")
        project["status"] = "Pronto"
        store.save(project)
        progress(100, "Vídeo pronto para exportar")
    except Interrupted:
        project["status"] = previous_status if mode == "Prévia" else "Interrompido"
        store.save(project)
        raise
    except Exception:
        project["status"] = previous_status if mode == "Prévia" else "Erro recuperável"
        store.save(project)
        raise

def existing_image(scene, folder):
    image = scene.get("image")
    if not image or image.get("placeholder") or image.get("provider") == "Cartão gráfico":
        return None
    path = folder / image["file"]
    if not path.is_file():
        return None
    try:
        return dict(image, **fingerprints(path))
    except (OSError, ValueError):
        return None

def images_ready(project, folder):
    if len(project["scenes"]) != 14:
        return False
    used = []
    for scene in project["scenes"]:
        image = existing_image(scene, folder)
        if not image or (not image.get("generated") and duplicate(image, used)):
            return False
        if not image.get("generated"):
            used.append(image)
    return True

def ensure_keywords(client, store, project, settings, progress, check):
    scenes = project["scenes"]
    if not all(scene.get("keywords") for scene in scenes):
        progress(17, "Gerando consultas em inglês para as 14 cenas")
        try:
            queries = generate_keywords(client, settings, project, check)
        except (requests.RequestException, ValueError, KeyError, RuntimeError) as error:
            check()
            LOGGER.warning("Consultas do Ollama indisponíveis: %s", type(error).__name__)
        else:
            for scene, keywords in zip(scenes, queries, strict=True):
                scene["keywords"] = keywords
            store.save(project)

def prepare_images(client, store, project, settings, folder, progress, check, count=14):
    ensure_keywords(client, store, project, settings, progress, check)
    scenes = project["scenes"][:count]
    used, pending = [], []
    for index, scene in enumerate(scenes, 1):
        image = existing_image(scene, folder)
        if image and (image.get("generated") or not duplicate(image, used)):
            scene["image"] = image
            if not image.get("generated"):
                used.append(image)
        else:
            pending.append((index, scene))
    for index, scene in pending:
        check()
        progress(18 + index * 2, f"Buscando imagem {index}/14")
        image = acquire(client, settings, folder, scene, index, used, check,
                        cache=store.root / "cache-imagens")
        if image.get("placeholder"):
            (folder / image["file"]).unlink(missing_ok=True)
            image = fallback_plate(folder, scene, project["topic"], index)
        else:
            used.append(image)
        scene["image"] = image
        store.save(project)
    project["status"] = "Revisar cenas"
    store.save(project)
    progress(46, "Imagens e ilustrações prontas.")

def prepare_edit(client, store, project, settings, folder, progress, check, preview=False):
    mode = settings.get("edit_mode", "Leve")
    if mode not in MODES:
        mode = "Leve"
    library = settings.get("media_library", "")
    config = [mode, settings.get("model"), library, settings.get("online_graphics", True), bool(settings.get("intensity", 60)),
              bool(settings.get("pixabay")), settings.get("ollama")]
    signature = hashlib.sha256(json.dumps(
        [config, [scene["text"] for scene in project["scenes"]]], ensure_ascii=False
    ).encode("utf-8")).hexdigest()
    if project.get("edit_signature") != signature:
        progress(47, "Planejando movimentos, elementos e transições")
        try:
            predicted = predict(client, project, settings, mode, check) if settings.get("intensity", 60) else []
        except (requests.RequestException, ValueError, KeyError, RuntimeError, AttributeError, TypeError) as error:
            check()
            LOGGER.info("Plano determinístico aplicado: %s", type(error).__name__)
            predicted = []
        for scene, plan in zip(project["scenes"], plans_for(project, mode, predicted), strict=True):
            scene["edit"] = plan
            scene.pop("graphic", None)
            scene.pop("video", None)
        project["edit_signature"] = signature
        store.save(project)
    used_graphics, used_videos = set(), set()
    scenes = project["scenes"]
    for scene in scenes:
        if scene.get("graphic"):
            for field in ("sha256", "asset"):
                if scene["graphic"].get(field):
                    used_graphics.add(scene["graphic"][field])
        for field in ("url", "cache_key"):
            if scene.get("video", {}).get(field):
                used_videos.add(scene["video"][field])
    for index, scene in enumerate(scenes[:1] if preview else scenes):
        check()
        plan = scene["edit"]
        strength = settings.get("intensity", 60)
        if strength and (mode != "Leve" or index % 4 == 0):
            if not scene.get("graphic") or not (folder / scene["graphic"]["file"]).is_file():
                scene["graphic"] = artwork(client, settings, plan["graphic"], folder,
                                           store.root / "cache-png", used_graphics, index,
                                           plan["highlight"], mode, check)
        if strength and plan["media"] == "video" and "video" not in scene:
            candidate = video_asset(client, settings, plan["graphic"], folder, index, used_videos, check)
            if candidate:
                from .tools import probe
                try:
                    valid = any(stream.get("codec_type") == "video" and stream.get("width", 0) >= 240
                                for stream in probe(folder / candidate["file"], check)["streams"])
                except (RuntimeError, ValueError, KeyError):
                    valid = False
                if valid:
                    scene["video"] = candidate
        store.save(project)

def retry_image(store, project, settings, row, progress, check):
    folder = store.folder(project)
    scene = project["scenes"][row]
    used = [image for index, other in enumerate(project["scenes"]) if index != row
            if (image := existing_image(other, folder))]
    previous = existing_image(scene, folder)
    with session() as client:
        ensure_keywords(client, store, project, settings, progress, check)
        image = acquire(client, settings, folder, scene, row + 1, used, check,
                        cache=store.root / "cache-imagens", excluded=[previous] if previous else [])
    if image.get("placeholder") and previous:
        (folder / image["file"]).unlink()
        return "Nenhuma alternativa relevante encontrada. A imagem atual foi mantida."
    scene["image"] = image
    project["status"] = "Revisar cenas"
    store.save(project)
    return "Revise a nova imagem." if not image.get("placeholder") else "Selecione uma imagem local para esta cena."

def export(folder, destination):
    destination = Path(destination)
    outputs = [
        ("legendas.srt", destination.with_suffix(".srt")),
        ("creditos-e-publicacao.txt", destination.with_suffix(".creditos.txt")),
        ("video.mp4", destination),
    ]
    if (folder / "render" / "legendas.ass").is_file():
        outputs.insert(1, ("legendas.ass", destination.with_suffix(".ass")))
    with tempfile.TemporaryDirectory(prefix=".curiosity-", dir=destination.parent) as directory:
        staging = Path(directory)
        for source, _ in outputs:
            shutil.copy2(folder / "render" / source, staging / source)
        for source, target in outputs:
            (staging / source).replace(target)
