"""Sources and validation for transparent graphics and optional video plates."""
import hashlib
import io
import json
import random
import shutil
import textwrap
from pathlib import Path
from urllib.parse import urlsplit

import requests
from PIL import Image, ImageDraw, ImageFont

from .captions import FONT
from .images import commons, get, relevance, terms

GENERATED = {"provider": "Gerado localmente", "license": "CC0", "license_url": "https://creativecommons.org/publicdomain/zero/1.0/"}
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

def openverse(client, query):
    data = get(client, "https://api.openverse.org/v1/images/", params={
        "q": query, "extension": "png", "license": "cc0,pdm,by", "page_size": 8,
    })
    for item in data.get("results", []):
        code = item.get("license")
        if code not in {"cc0", "pdm", "by"} or not item.get("license_url") or (code == "by" and not item.get("creator")):
            continue
        yield {"asset": item["url"], "url": item.get("foreign_landing_url"),
               "title": item.get("title") or "", "author": item.get("creator"), "provider": "Openverse",
               "license": code.upper(), "license_url": item["license_url"]}

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
        response.raise_for_status()
        for chunk in response.iter_content(65536):
            check()
            if payload.tell() + len(chunk) > limit:
                raise ValueError("Mídia excede o limite de tamanho.")
            payload.write(chunk)
    payload.seek(0)
    return payload

def local_graphic(target, text, index, particles=False):
    image = Image.new("RGBA", (512, 512))
    draw = ImageDraw.Draw(image)
    accent = ((99, 228, 245), (255, 207, 105), (161, 242, 196), (191, 156, 255))[index % 4]
    rng = random.Random(index)
    if particles:
        for _ in range(42):
            x, y = rng.randrange(24, 488), rng.randrange(24, 488)
            r = rng.randrange(2, 7)
            draw.ellipse((x-r, y-r, x+r, y+r), fill=(*accent, rng.randrange(70, 205)))
    draw.ellipse((72, 72, 440, 440), fill=(11, 20, 38, 218), outline=(*accent, 255), width=7)
    draw.arc((100, 100, 412, 412), 175, 304, fill=(*accent, 210), width=7)
    draw.arc((100, 100, 412, 412), 353, 90, fill=(*accent, 210), width=7)
    font = ImageFont.truetype(str(FONT), 43)
    words = text.upper().split()[:3]
    label = "\n".join(word[:12] for word in words) or f"{index+1:02}"
    draw.multiline_text((256, 251), label, font=font, fill="white", anchor="mm", align="center", spacing=5)
    image.save(target)

def library_files(settings, query, extensions):
    location = settings.get("media_library")
    requested = terms(query)
    if not location or not Path(location).is_dir() or not requested:
        return []
    return sorted(path for path in Path(location).iterdir()
                  if path.suffix.lower() in extensions and terms(path.stem) & requested)

def save_graphic(image, cache):
    image.thumbnail((768, 768))
    digest = hashlib.sha256(image.size[0].to_bytes(4, "big") + image.tobytes()).hexdigest()
    target = cache / f"{digest}.png"
    if not target.is_file():
        with io.BytesIO() as binary:
            image.save(binary, format="PNG")
            target.write_bytes(binary.getvalue())
    return target, digest

def artwork(client, settings, query, folder, cache, used, index, fallback, mode, check):
    providers = [
        lambda: commons(client, query, graphics=True),
        lambda: (item for item in commons(client, query) if item["title"].lower().endswith(".png")),
        lambda: openverse(client, query),
        lambda: pixabay_png(client, query, settings["pixabay"]) if settings.get("pixabay") else (),
        lambda: openclipart(client, query),
    ]
    if mode == "Leve" or not query or not settings.get("online_graphics", True):
        providers = []
    cache.mkdir(parents=True, exist_ok=True)
    record = None
    for path in library_files(settings, query, {".png"}):
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
                items = sorted((item for item in provider() if relevance(item, query)),
                               key=lambda item: (item.get("license", "").upper() in {"CC0", "PDM", "PUBLIC DOMAIN"},
                                                 relevance(item, query)), reverse=True)
            except (ValueError, RuntimeError, requests.RequestException, KeyError, TypeError, AttributeError):
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
                except (ValueError, OSError, requests.RequestException, Image.DecompressionBombError):
                    continue
                if digest not in used:
                    record = candidate
                    used.add(url)
                    break
            if record:
                break
    target = folder / f"grafico-{index:02d}.png"
    if record:
        used.add(digest)
        shutil.copyfile(cached, target)
    else:
        local_graphic(target, fallback, index, mode == "Cinematográfico")
        record = dict(GENERATED, title="Elemento vetorial rasterizado pelo aplicativo")
    return dict(record, file=target.name, query=query, **({"sha256": digest} if record.get("asset") or record.get("provider") == "Biblioteca local" else {}))

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
    except (ValueError, OSError, requests.RequestException, KeyError):
        return None
    return None

def fallback_plate(folder, scene, topic, index):
    suffix = hashlib.sha256(scene["text"].encode("utf-8")).hexdigest()[:12]
    target = folder / f"ilustracao-{index:02d}-{suffix}.jpg"
    if not target.is_file():
        base = ((13, 37, 62), (35, 18, 61), (14, 53, 48), (60, 31, 28))[index % 4]
        image = Image.new("RGB", (720, 1280), base)
        draw = ImageDraw.Draw(image)
        rng = random.Random(index + 84)
        for step in range(14):
            x, y = rng.randrange(-100, 640), rng.randrange(-40, 1050)
            size = rng.randrange(40, 265)
            color = tuple(min(255, value + 7 + step) for value in base)
            draw.rounded_rectangle((x, y, x + size, y + size), 15, fill=color)
        accent = (197, 236, 244)
        draw.line((65, 400, 650, 400), fill=accent, width=3)
        draw.text((67, 357), f"CENA {index:02d}", font=ImageFont.truetype(str(FONT), 25), fill=accent)
        lines = "\n".join(textwrap.wrap(scene["text"], width=21)[:7])
        draw.multiline_text((65, 440), lines, font=ImageFont.truetype(str(FONT), 42),
                            fill="white", spacing=12)
        draw.text((65, 1060), topic[:35], font=ImageFont.truetype(str(FONT), 26), fill=accent)
        image.save(target, quality=92)
    return dict(GENERATED, file=target.name, title=f"Ilustração tipográfica da cena {index:02d}", generated=True)
