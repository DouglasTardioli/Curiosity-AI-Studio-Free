"""Sources and validation for transparent graphics and optional video plates."""
import hashlib
import io
import json
import logging
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image

from .images import commons, get, library_files, marked, openverse, relevance, request_failure

LOGGER = logging.getLogger(__name__)
PNG_LIMIT = 14 * 1024 * 1024

def transparent(source):
    with Image.open(source) as image:
        if image.format != "PNG" or min(image.size) < 96 or image.width * image.height > 16_000_000:
            raise ValueError("Resolução ou formato PNG inválido.")
        image.load()
        if "A" not in image.getbands() and "transparency" not in image.info:
            raise ValueError("PNG sem canal alfa.")
        rgba = image.convert("RGBA")
        histogram = rgba.getchannel("A").histogram()
        pixels = rgba.width * rgba.height
        if histogram[0] < pixels * .03 or sum(histogram[128:]) < pixels * .03:
            raise ValueError("PNG sem separação visível entre objeto e fundo.")
        return rgba

def pixabay_png(client, query, token):
    data = get(client, "https://pixabay.com/api/", params={"key": token, "q": query[:100],
               "image_type": "illustration", "safesearch": "true", "per_page": 8})
    for item in data.get("hits", []):
        yield {"asset": item["largeImageURL"], "url": item["pageURL"], "title": item["tags"],
               "author": item["user"], "provider": "Pixabay", "license": "Pixabay Content License",
               "license_url": "https://pixabay.com/service/license-summary/"}

def openclipart(client, query):
    response = client.get("https://openclipart.org/search/json/", params={"query": query}, timeout=(3, 5))
    response.raise_for_status()
    data = response.json()
    for item in data.get("payload", {}).get("cliparts", []):
        png = item.get("png")
        asset = (png.get("800") or png.get("400")) if isinstance(png, dict) else png
        if asset and urlsplit(asset).hostname == "openclipart.org":
            yield {"asset": asset, "url": item.get("detail_link"), "title": item.get("title") or "",
                   "author": item.get("user"), "provider": "Openclipart", "license": "CC0",
                   "license_url": "https://openclipart.org/share"}

def fetch(client, url, limit, check):
    if urlsplit(url).scheme not in {"https", "http"}:
        raise ValueError("URL de mídia inválida.")
    payload = io.BytesIO()
    with client.get(url, stream=True, timeout=(4, 10)) as response:
        LOGGER.info("Download de mídia HTTP %s | %s", response.status_code, url)
        response.raise_for_status()
        for chunk in response.iter_content(65536):
            check()
            if payload.tell() + len(chunk) > limit:
                raise ValueError("Mídia excede o limite de tamanho.")
            payload.write(chunk)
    payload.seek(0)
    return payload

def save_graphic(image, cache):
    image.thumbnail((768, 768))
    digest = hashlib.sha256(image.size[0].to_bytes(4, "big") + image.tobytes()).hexdigest()
    target = cache / f"{digest}.png"
    if not target.is_file():
        with io.BytesIO() as binary:
            image.save(binary, format="PNG")
            target.write_bytes(binary.getvalue())
    return target, digest

def artwork(client, settings, query, folder, cache, used, index, check):
    providers = [
        lambda: commons(client, query, graphics=True),
        lambda: (item for item in commons(client, query) if item["title"].lower().endswith(".png")),
        lambda: openverse(client, query, graphics=True),
        lambda: pixabay_png(client, query, settings["pixabay"]) if settings.get("pixabay") else (),
        lambda: openclipart(client, query),
    ]
    if not query or not settings.get("online_graphics", True):
        providers = []
    cache.mkdir(parents=True, exist_ok=True)
    record = None
    for path in library_files(settings, query, {".png"}):
        if marked({"title": path.stem}):
            continue
        try:
            cached, digest = save_graphic(transparent(path), cache)
        except (ValueError, OSError):
            continue
        if digest not in used:
            record = {"provider": "Biblioteca local", "title": path.name}
            break
    if record is None:
        for provider in providers:
            check()
            try:
                items = sorted((item for item in provider() if relevance(item, query) and not marked(item)),
                               key=lambda item: (item.get("license", "").upper() in {"CC0", "PDM", "PUBLIC DOMAIN"},
                                                 relevance(item, query)), reverse=True)
            except (ValueError, RuntimeError, requests.RequestException, KeyError, TypeError, AttributeError) as error:
                LOGGER.info("Busca de PNG indisponível | %s | %s", query, request_failure(error))
                continue
            for candidate in items[:3]:
                url = candidate["asset"]
                if url in used:
                    continue
                key = hashlib.sha256(url.encode("utf-8")).hexdigest()
                pointer = cache / f"{key}.json"
                try:
                    if pointer.is_file():
                        digest = json.loads(pointer.read_text(encoding="utf-8"))
                        cached = cache / f"{digest}.png"
                        if not cached.is_file():
                            pointer.unlink()
                    if not pointer.is_file():
                        cached, digest = save_graphic(transparent(fetch(client, url, PNG_LIMIT, check)), cache)
                        pointer.write_text(json.dumps(digest), encoding="utf-8")
                except (ValueError, OSError, requests.RequestException, Image.DecompressionBombError) as error:
                    LOGGER.info("PNG rejeitado | %s | %s", url,
                                request_failure(error) if isinstance(error, requests.RequestException) else error)
                    continue
                if digest not in used:
                    record = candidate
                    used.add(url)
                    break
            if record:
                break
    if not record:
        return None
    target = folder / f"grafico-{index:02d}-{digest[:12]}.png"
    used.add(digest)
    shutil.copyfile(cached, target)
    LOGGER.info("PNG selecionado | cena=%02d | consulta=%s | %s | %s", index + 1, query,
                record.get("url", record.get("title")), target)
    return dict(record, file=target.name, query=query, sha256=digest)

def video_asset(client, settings, query, folder, index, used, check):
    for path in library_files(settings, query, {".mp4", ".mov", ".webm"}):
        key = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
        if key not in used:
            used.add(key)
            target = folder / f"fundo-{index:02d}{path.suffix.lower()}"
            shutil.copyfile(path, target)
            return {"file": target.name, "title": path.name, "provider": "Biblioteca local", "cache_key": key}
    if not query or not settings.get("pixabay"):
        return None
    try:
        response = get(client, "https://pixabay.com/api/videos/", params={
            "key": settings["pixabay"], "q": query[:100], "per_page": 6, "safesearch": "true",
        })
        hits = [item for item in response.get("hits", []) if relevance({"title": item.get("tags", "")}, query)]
        for item in hits[:3]:
            url = item["pageURL"]
            if url in used:
                continue
            variants = [item["videos"].get(size, {}) for size in ("medium", "small")]
            variant = next((data for data in variants if data.get("url") and 0 < data.get("size", 0) < 24 * 1024 * 1024), None)
            if variant:
                target = folder / f"fundo-{index:02d}.mp4"
                target.write_bytes(fetch(client, variant["url"], 24 * 1024 * 1024, check).getvalue())
                used.add(url)
                return {"file": target.name, "url": url, "title": item["tags"], "author": item["user"],
                        "provider": "Pixabay", "license": "Pixabay Content License",
                        "license_url": "https://pixabay.com/service/license-summary/"}
    except (ValueError, OSError, requests.RequestException, KeyError) as error:
        LOGGER.info("Busca de vídeo indisponível | cena=%02d | consulta=%s | %s",
                    index + 1, query, request_failure(error))
        return None
    return None
