import logging
import random
import shutil
import tempfile
from pathlib import Path

import requests

from .images import acquire, duplicate, fingerprints, generate_keywords
from .research import CATEGORIES, key, research, session
from .script import generate, validate_scenes
from .tools import binary, run
from .video import credits, render
from .voice import SceneNarrator

LOGGER = logging.getLogger(__name__)

class Interrupted(Exception):
    pass

def process(store, project, settings, mode, progress, check):
    folder = store.folder(project)
    try:
        if mode == "Renderizar":
            if not (binary("ffmpeg") and binary("ffprobe")):
                raise RuntimeError("Instale FFmpeg e FFprobe e adicione ambos ao PATH.")
            filters = run(["ffmpeg", "-hide_banner", "-filters"], check)
            if not any(line.split()[1:3] == ["ass", "V->V"] for line in filters.splitlines()):
                raise RuntimeError("A renderização de legendas exige FFmpeg com libass (filtro ass).")
        with session() as client:
            if not project["scenes"]:
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
            validate_scenes(project["scenes"], manual=True)
            if mode != "Renderizar":
                prepare_images(client, store, project, settings, folder, progress, check)
                return
            if not images_ready(project, folder):
                raise ValueError("Revise as 14 cenas e substitua imagens pendentes ou duplicadas.")
            project["status"] = "Processando"
            store.save(project)
        audios, timings = [], []
        narrator = SceneNarrator(settings, folder)
        for index, scene in enumerate(project["scenes"], 1):
            check()
            progress(46 + round(index*20/14), f"Narrando cena {index}/14")
            audio, cues = narrator.scene(scene["text"], index, check)
            audios.append(audio)
            timings.append(cues)
        out = render(project, folder, audios, timings, settings["light"], progress, check)
        (out / "creditos-e-publicacao.txt").write_text(credits(project), encoding="utf-8")
        project["status"] = "Pronto"
        store.save(project)
        progress(100, "Vídeo pronto para exportar")
    except Interrupted:
        project["status"] = "Interrompido"
        store.save(project)
        raise
    except Exception:
        project["status"] = "Erro recuperável"
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
        if not image or duplicate(image, used):
            return False
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

def prepare_images(client, store, project, settings, folder, progress, check):
    ensure_keywords(client, store, project, settings, progress, check)
    scenes = project["scenes"]
    used, pending = [], []
    for index, scene in enumerate(scenes, 1):
        image = existing_image(scene, folder)
        if image and not duplicate(image, used):
            scene["image"] = image
            used.append(image)
        else:
            pending.append((index, scene))
    for index, scene in pending:
        check()
        progress(18 + index * 2, f"Buscando imagem {index}/14")
        image = acquire(client, settings, folder, scene, index, used, check,
                        cache=store.root / "cache-imagens")
        scene["image"] = image
        if not image.get("placeholder"):
            used.append(image)
        store.save(project)
    project["status"] = "Revisar cenas"
    store.save(project)
    missing = sum(bool(scene["image"].get("placeholder")) for scene in scenes)
    label = f" · {missing} imagens pendentes" if missing else ""
    progress(46, f"Revise as cenas antes de renderizar{label}.")

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
