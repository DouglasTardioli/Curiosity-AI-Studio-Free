import html
import io
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlparse

import requests
from PIL import Image, ImageDraw, ImageOps

COMMONS = "https://commons.wikimedia.org/w/api.php"

def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).split())

def get(client, url, **kwargs):
    response = client.get(url, timeout=(5, 25), **kwargs)
    response.raise_for_status()
    return response.json()

def commons(client, query):
    data = get(client, COMMONS, params={
        "action": "query", "generator": "search", "gsrsearch": query + " filetype:bitmap",
        "gsrnamespace": 6, "gsrlimit": 10, "prop": "imageinfo",
        "iiprop": "url|extmetadata", "iiurlwidth": 1280, "format": "json", "formatversion": 2,
    })
    for page in sorted(data.get("query", {}).get("pages", []), key=lambda item: item.get("index", 0)):
        for info in page.get("imageinfo", []):
            meta = {k: v["value"] for k, v in info.get("extmetadata", {}).items()}
            license_name = meta.get("LicenseShortName", "")
            license_url = meta.get("LicenseUrl", "")
            author = clean(meta.get("Artist", ""))
            if meta.get("Restrictions", "").strip():
                continue
            if not (license_name in {"CC0", "Public domain"} or
                    (re.fullmatch(r"CC BY [1-4]\.0", license_name) and author
                     and urlparse(license_url).hostname == "creativecommons.org")):
                continue
            yield {"asset": info.get("thumburl") or info["url"], "url": info["descriptionurl"],
                   "title": page["title"], "author": author, "license": license_name,
                   "license_url": license_url, "provider": "Wikimedia Commons"}

def pexels(client, query, token):
    data = get(client, "https://api.pexels.com/v1/search", headers={"Authorization": token},
               params={"query": query, "per_page": 8, "locale": "pt-BR", "orientation": "portrait"})
    for photo in data.get("photos", []):
        yield {"asset": photo["src"]["large"], "url": photo["url"], "author": photo["photographer"],
               "title": photo.get("alt", ""), "provider": "Pexels", "license": "Pexels License",
               "license_url": "https://www.pexels.com/license/"}

def pixabay(client, query, token):
    data = get(client, "https://pixabay.com/api/", params={
        "key": token, "q": query[:100], "lang": "pt", "image_type": "photo", "safesearch": "true",
        "per_page": 8})
    for photo in data.get("hits", []):
        yield {"asset": photo["largeImageURL"], "url": photo["pageURL"], "author": photo["user"],
               "title": photo["tags"], "provider": "Pixabay", "license": "Pixabay Content License",
               "license_url": "https://pixabay.com/service/license-summary/"}

def normalize(source, target):
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
        image.thumbnail((1400, 2100))
        background = Image.new("RGB", image.size, "#111827")
        background.paste(image, mask=image.getchannel("A"))
        # Unique target names allow SQLite to keep referencing the last complete asset.
        background.save(target, "JPEG", quality=92)

def download(client, url, target):
    payload = io.BytesIO()
    with client.get(url, stream=True, timeout=(5, 25)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(65536):
            payload.write(chunk)
            if payload.tell() > 24 * 1024 * 1024:
                raise ValueError("Imagem excede 24 MB.")
    payload.seek(0)
    normalize(payload, target)

def comfy(client, settings, prompt, target, check):
    base = settings["comfy"].rstrip("/")
    workflow = json.loads(Path(settings["workflow"]).read_text(encoding="utf-8-sig"))
    workflow = json.loads(json.dumps(workflow, ensure_ascii=False).replace(
        "{{PROMPT}}", json.dumps(prompt, ensure_ascii=False)[1:-1]))
    response = client.post(base + "/prompt", json={"prompt": workflow}, timeout=20)
    response.raise_for_status()
    pid = response.json()["prompt_id"]
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline:
        check()
        entry = get(client, base + "/history/" + pid).get(pid)
        if entry:
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError("O ComfyUI falhou ao executar o workflow.")
            for node in entry.get("outputs", {}).values():
                for image in node.get("images", []):
                    download(client, base + "/view?" + urlencode({
                        key: image[key] for key in ("filename", "subfolder", "type")}), target)
                    return {"provider": "ComfyUI", "author": "", "title": prompt,
                            "license": "Verifique a licença do checkpoint", "url": ""}
            raise RuntimeError("O workflow precisa de um nó SaveImage.")
        time.sleep(2)
    raise TimeoutError("O ComfyUI não retornou uma imagem no prazo.")

def card(target, index):
    image = Image.new("RGB", (720, 1280), "#101726")
    draw = ImageDraw.Draw(image)
    for radius in range(360, 30, -15):
        color = (18 + index, 33 + (360-radius)//9, 53 + (360-radius)//5)
        draw.ellipse((360-radius, 550-radius, 360+radius, 550+radius), outline=color, width=5)
    draw.text((360, 550), "CURIOSITY", fill="#d7f7f4", anchor="mm", font_size=46)
    image.save(target, "JPEG", quality=90)

def acquire(client, settings, folder, scene, index, used, check):
    target = folder / f"imagem-{index:02d}-{uuid.uuid4().hex[:8]}.jpg"
    notes = []
    if settings["comfy_enabled"] and not settings["light"]:
        try:
            result = comfy(client, settings, scene["prompt"], target, check)
            return dict(result, file=target.name)
        except (ValueError, RuntimeError, OSError, requests.RequestException, KeyError) as error:
            check()
            notes.append(f"ComfyUI: {error}")
    providers = [(commons, ())]
    if settings["pexels"]:
        providers.append((pexels, (settings["pexels"],)))
    if settings["pixabay"]:
        providers.append((pixabay, (settings["pixabay"],)))
    for provider, args in providers:
        try:
            for candidate in provider(client, scene["query"], *args):
                check()
                if candidate["url"] in used:
                    continue
                try:
                    download(client, candidate.pop("asset"), target)
                except (OSError, ValueError, requests.RequestException):
                    continue
                return dict(candidate, file=target.name, notes=notes)
        except requests.RequestException:
            # Avoid persisting URLs containing API tokens.
            notes.append(f"{provider.__name__}: serviço indisponível.")
    card(target, index)
    return {"file": target.name, "provider": "Cartão gráfico", "title": "Visual de reserva",
            "author": "Curiosity AI Studio Free", "url": "", "license": "Arte do aplicativo",
            "notes": notes}
