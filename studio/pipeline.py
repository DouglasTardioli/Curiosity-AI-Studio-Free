import random
import shutil
import tempfile
from pathlib import Path

from .images import acquire
from .research import CATEGORIES, key, research, session
from .script import generate, validate_scenes
from .tools import binary
from .video import credits, render
from .voice import narrate

class Interrupted(Exception):
    pass

def process(store, project, settings, mode, progress, check):
    folder = store.folder(project)
    try:
        if mode != "Roteiro" and not (binary("ffmpeg") and binary("ffprobe")):
            raise RuntimeError("Instale FFmpeg e FFprobe e adicione ambos ao PATH.")
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
                progress(12, "Escrevendo roteiro no Ollama")
                project.update(generate(client, settings, project["topic"], project["sources"], check))
                project["status"] = "Roteiro"
                store.save(project)
            if mode == "Roteiro":
                progress(100, "Roteiro salvo")
                return
            validate_scenes(project["scenes"])
            project["status"] = "Processando"
            store.save(project)
            used = {scene["image"]["url"] for scene in project["scenes"] if "image" in scene}
            for index, scene in enumerate(project["scenes"], 1):
                check()
                if scene.get("image") and (folder / scene["image"]["file"]).is_file():
                    continue
                progress(18 + index*2, f"Buscando imagem {index}/14")
                image = acquire(client, settings, folder, scene, index, used, check)
                used.add(image["url"])
                scene["image"] = image
                store.save(project)
        audios, timings = [], []
        for index, scene in enumerate(project["scenes"], 1):
            check()
            progress(46 + round(index*20/14), f"Narrando cena {index}/14")
            audio, duration = narrate(scene["text"], settings, folder, index, check)
            audios.append(audio)
            timings.append(duration)
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

def export(folder, destination):
    destination = Path(destination)
    outputs = (
        ("legendas.srt", destination.with_suffix(".srt")),
        ("creditos-e-publicacao.txt", destination.with_suffix(".creditos.txt")),
        ("video.mp4", destination),
    )
    with tempfile.TemporaryDirectory(prefix=".curiosity-", dir=destination.parent) as directory:
        staging = Path(directory)
        for source, _ in outputs:
            shutil.copy2(folder / "render" / source, staging / source)
        for source, target in outputs:
            (staging / source).replace(target)
