import hashlib
import json
import logging
import random
import shutil
import tempfile
from pathlib import Path

import requests
from PIL import Image

from .decoration import artwork, video_asset
from .motion import MODES, plans_for, predict
from .images import (acquire, duplicate, fingerprints, generate_keywords, generic_graphic,
                     image_info, marked, placeholder, relevance, request_failure, reverse_scan)
from .research import CATEGORIES, key, research, session
from .script import SCENE_COUNT, generate, manual_lines, validate_scenes, words
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
            if mode in ("Vídeo", "Preparar"):
                project.update(media_review=False, status="Preparando cenas")
                store.save(project)
                prepare_images(client, store, project, settings, folder, progress, check)
                if any(scene.get("image_status") == "Imagem válida" for scene in project["scenes"]):
                    prepare_edit(client, store, project, settings, folder, progress, check)
                missing = missing_images(project, folder)
                project["media_review"] = not missing
                project["status"] = "Imagens pendentes" if missing else "Revisar cenas"
                store.save(project)
                progress(100, missing_message(missing) if missing else "14/14 cenas prontas para revisão e renderização.")
                return
            if mode not in ("Renderizar", "Prévia"):
                raise ValueError(f"Modo de produção desconhecido: {mode}")
            preview = mode == "Prévia"
            missing = missing_images(project, folder)
            if not preview and (missing or not project.get("media_review")):
                project.update(status="Imagens pendentes" if missing else "Revisar cenas", media_review=False)
                store.save(project)
                progress(100, missing_message(missing) if missing else "Prepare e revise as cenas na galeria.")
                return
            preview_index = settings.get("preview_index", 0)
            if preview and (preview_index < 0 or preview_index >= len(project["scenes"]) or preview_index + 1 in missing):
                raise ValueError("Selecione uma cena com imagem válida antes de abrir a prévia.")
            if not (binary("ffmpeg") and binary("ffprobe")):
                raise RuntimeError("Instale FFmpeg e FFprobe e adicione ambos ao PATH.")
            filters = run(["ffmpeg", "-hide_banner", "-filters"], check)
            available = {parts[1] for line in filters.splitlines()
                         if len(parts := line.split()) >= 3}
            missing = {"ass", "xfade", "overlay", "avgblur", "rgbashift", "sidechaincompress"} - available
            if missing:
                raise RuntimeError(f"Use uma compilação completa do FFmpeg com libass. "
                                   f"Filtros ausentes: {', '.join(sorted(missing))}.")
            validate_secondary(project, folder)
            if not preview:
                project["status"] = "Processando"
                store.save(project)
        audios, timings = [], []
        narrator = SceneNarrator(settings, folder)
        selected = [project["scenes"][preview_index]] if preview else project["scenes"]
        for index, scene in enumerate(selected, 1):
            check()
            progress(46 + round(index*20/14), f"Narrando cena {preview_index + 1:02d}" if preview else f"Narrando cena {index}/14")
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

def existing_image(scene, folder, key="image"):
    image = scene.get(key)
    if not image or not image.get("file") or placeholder(image) or marked(image):
        return None
    if image.get("provider") in {"Wikimedia Commons", "Openverse", "Pexels", "Pixabay"}:
        query = image.get("search_query", "")
        if not image.get("license") or not query or not relevance(image, query) or reverse_scan(image):
            LOGGER.info("Imagem salva rejeitada | licença ou relevância | %s", image.get("url"))
            return None
    path = folder / image["file"]
    try:
        image_info(path)
        return dict(image, **fingerprints(path))
    except (OSError, ValueError, Image.DecompressionBombError) as error:
        LOGGER.info("Arquivo de cena rejeitado | %s | %s", path, error)
        return None


def missing_images(project, folder):
    used, missing = [], []
    for index, scene in enumerate(project["scenes"], 1):
        image = existing_image(scene, folder)
        if image is None or duplicate(image, used):
            missing.append(index)
        else:
            used.append(image)
    return missing


def missing_message(missing):
    return "Selecione ou pesquise uma imagem válida para as cenas " + ", ".join(f"{n:02d}" for n in missing) + "."


def validate_secondary(project, folder):
    scenes = project["scenes"]
    used = [scene["image"] for scene in scenes if scene.get("image")]
    for scene in scenes:
        image = existing_image(scene, folder, "secondary_image")
        if image is None or duplicate(image, used):
            scene.pop("secondary_image", None)
        else:
            scene["secondary_image"] = image
            used.append(image)
    return used


def contextual_queries(topic, scene):
    stop = {"que", "para", "como", "sobre", "entre", "mais", "pela", "pelas", "uma", "foram",
            "essas", "estes", "ainda", "isso", "eles", "elas", "muito", "mesmo"}
    topic = "" if topic == "Roteiro manual" else topic
    tokens = [word for word in words(scene["text"]) if len(word) > 3 and word.lower() not in stop]
    groups = [" ".join(tokens[index:index + 3]) for index in range(0, min(len(tokens), 9), 3)]
    return list(dict.fromkeys(f"{topic} {group}".strip() for group in groups if group))


def ensure_keywords(client, store, project, settings, progress, check):
    scenes = project["scenes"]
    if not all(scene.get("keywords") for scene in scenes):
        progress(17, "Gerando consultas em inglês para as 14 cenas")
        try:
            queries = generate_keywords(client, settings, project, check)
        except (requests.RequestException, ValueError, KeyError, RuntimeError, AttributeError, TypeError) as error:
            check()
            LOGGER.warning("Consultas do Ollama indisponíveis | %s", request_failure(error))
        else:
            for scene, keywords in zip(scenes, queries, strict=True):
                scene["keywords"] = keywords
            store.save(project)


def search_scene(scene, topic):
    queries = scene.get("keywords") or contextual_queries(topic, scene)
    query = scene.get("query")
    if query in {topic, " ".join(scene["text"].split())}:
        query = None
    return dict(scene, keywords=queries, query=query)


def prepare_images(client, store, project, settings, folder, progress, check):
    ensure_keywords(client, store, project, settings, progress, check)
    scenes = project["scenes"]
    used, pending, unavailable = [], [], set()
    for index, scene in enumerate(scenes, 1):
        check()
        image = existing_image(scene, folder)
        if image and not duplicate(image, used):
            scene.update(image=image, image_status="Imagem válida")
            used.append(image)
        else:
            scene.pop("image", None)
            scene.pop("secondary_image", None)
            scene["image_status"] = "Imagem pendente"
            pending.append((index, scene))
    for index, scene in pending:
        check()
        progress(18 + index, f"Buscando imagem {index:02d}/{SCENE_COUNT} · {len(used)}/{SCENE_COUNT} válidas")
        image = acquire(client, settings, folder, search_scene(scene, project["topic"]), index, used, check,
                        cache=store.root / "cache-imagens", unavailable=unavailable)
        if image:
            scene.update(image=image, image_status="Imagem válida")
            used.append(image)
        else:
            scene["image_status"] = "Nenhuma imagem válida encontrada"
        store.save(project)
    used = validate_secondary(project, folder)
    if any(not scene.get("image") for scene in scenes):
        return
    for index, scene in enumerate(scenes, 1):
        if scene.get("secondary_image"):
            continue
        check()
        progress(32 + index, f"Buscando visual adicional · cena {index:02d}/{SCENE_COUNT}")
        query = search_scene(scene, project["topic"])
        query.update(keywords=query["keywords"][:2], query=None)
        candidate = acquire(client, settings, folder, query, index, used, check,
                            cache=store.root / "cache-imagens", optional=True, unavailable=unavailable)
        if candidate:
            scene["secondary_image"] = candidate
            used.append(candidate)
            store.save(project)


def prepare_edit(client, store, project, settings, folder, progress, check):
    mode = settings.get("edit_mode", "Leve")
    if mode not in MODES:
        mode = "Leve"
    library = settings.get("media_library", "")
    config = [2, mode, settings.get("model"), library, settings.get("online_graphics", True), bool(settings.get("intensity", 60)),
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
    for index, scene in enumerate(scenes):
        check()
        plan = scene["edit"]
        strength = settings.get("intensity", 60)
        if not scene.get("image"):
            continue
        query = plan["graphic"] or scene.get("image", {}).get("search_query", "")
        if strength and (mode != "Leve" or index % 4 == 0) and query:
            graphic = scene.get("graphic", {})
            if not graphic or generic_graphic(graphic) or not (folder / graphic["file"]).is_file():
                candidate = artwork(client, settings, query, folder, store.root / "cache-png",
                                    used_graphics, index, check)
                if candidate:
                    scene["graphic"] = candidate
                else:
                    scene.pop("graphic", None)
        if scene.get("secondary_image"):
            scene.pop("video", None)
        if strength and plan["media"] == "video" and "video" not in scene and not scene.get("secondary_image"):
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
        image = acquire(client, settings, folder, search_scene(scene, project["topic"]), row + 1, used, check,
                        cache=store.root / "cache-imagens", excluded=[previous] if previous else [])
        if image:
            scene.update(image=image, image_status="Imagem válida")
            validate_secondary(project, folder)
            prepare_edit(client, store, project, settings, folder, progress, check)
        elif not previous:
            scene.pop("image", None)
            scene["image_status"] = "Nenhuma imagem válida encontrada"
    missing = missing_images(project, folder)
    project.update(media_review=not missing and all(item.get("edit") for item in project["scenes"]),
                   status="Imagens pendentes" if missing else "Revisar cenas")
    store.save(project)
    if image:
        return "Revise a nova imagem."
    return "Nenhuma alternativa relevante encontrada." if previous else missing_message([row + 1])

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
