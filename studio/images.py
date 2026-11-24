import hashlib
import html
import io
import json
import shutil
import unicodedata
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlsplit, urlunsplit

import requests
from PIL import Image, ImageDraw, ImageOps

from .research import session
from .script import STRING, object_schema, request_json, scene_array

COMMONS = "https://commons.wikimedia.org/w/api.php"

def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).split())

def get(client, url, **kwargs):
    response = client.get(url, timeout=(5, 25), **kwargs)
    response.raise_for_status()
    return response.json()

def commons(client, query, *, graphics=False):
    suffix = " filetype:drawing" if graphics else " filetype:bitmap"
    extensions = r"\.(?:svg|png)$" if graphics else r"\.(?:jpe?g|png|webp|tiff?)$"
    data = get(client, COMMONS, params={
        "action": "query", "generator": "search", "gsrsearch": query + suffix,
        "gsrnamespace": 6, "gsrlimit": 20, "prop": "imageinfo",
        "iiprop": "url|size|extmetadata", "iiurlwidth": 640 if graphics else 1600, "format": "json", "formatversion": 2,
    })
    for page in sorted(data.get("query", {}).get("pages", []), key=lambda item: item.get("index", 0)):
        if not re.search(extensions, page["title"], re.IGNORECASE):
            continue
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
                   "tags": clean(meta.get("ImageDescription", ""))[:1000],
                   "width": info.get("thumbwidth", info.get("width", 0)),
                   "height": info.get("thumbheight", info.get("height", 0)),
                   "license_url": license_url, "provider": "Wikimedia Commons"}

def pexels(client, query, token):
    data = get(client, "https://api.pexels.com/v1/search", headers={"Authorization": token},
               params={"query": query, "per_page": 8, "locale": "en-US"})
    for photo in data.get("photos", []):
        yield {"asset": photo["src"]["large"], "url": photo["url"], "author": photo["photographer"],
               "title": photo.get("alt", ""), "width": photo.get("width", 0),
               "height": photo.get("height", 0), "provider": "Pexels", "license": "Pexels License",
               "license_url": "https://www.pexels.com/license/"}

def pixabay(client, query, token):
    data = get(client, "https://pixabay.com/api/", params={
        "key": token, "q": query[:100], "lang": "en", "image_type": "photo", "safesearch": "true",
        "per_page": 8})
    for photo in data.get("hits", []):
        yield {"asset": photo["largeImageURL"], "url": photo["pageURL"], "author": photo["user"],
               "title": photo["tags"], "width": photo.get("imageWidth", 0),
               "height": photo.get("imageHeight", 0), "provider": "Pixabay", "license": "Pixabay Content License",
               "license_url": "https://pixabay.com/service/license-summary/"}

def normalize(source, target):
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
        image.thumbnail((1400, 2100))
        background = Image.new("RGB", image.size, "#111827")
        background.paste(image, mask=image.getchannel("A"))
        # Unique target names allow SQLite to keep referencing the last complete asset.
        background.save(target, "JPEG", quality=92)

def download(client, url, target, check=lambda: None):
    payload = io.BytesIO()
    with client.get(url, stream=True, timeout=(5, 25)) as response:
        response.raise_for_status()
        for chunk in response.iter_content(65536):
            check()
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

def generate_keywords(client, settings, project, check):
    schema = object_schema({"queries": scene_array({
        "type": "array", "items": STRING, "minItems": 4, "maxItems": 4,
    })})
    texts = [scene["text"] for scene in project["scenes"]]
    prompt = (
        "For each of the 14 Portuguese narration lines, produce four distinct English photo "
        "search phrases (2 to 5 words each), in the same order. Start with the most specific "
        "visible subject. The other three should depict different concrete objects or activities "
        "mentioned or implied by that line. Avoid unrelated stock images, abstract terms, "
        "and repeating the same subject in every scene. Use plain English.\n"
        f"Topic: {project['topic']}\nLines: {json.dumps(texts, ensure_ascii=False)}"
    )
    queries = request_json(client, settings, prompt, schema, 1000, check)["queries"]
    if not isinstance(queries, list) or len(queries) != len(texts):
        raise ValueError("Ollama não devolveu consultas para todas as cenas.")
    result = []
    for group in queries:
        if not isinstance(group, list) or not all(isinstance(value, str) for value in group):
            raise ValueError("Consultas de imagem inválidas.")
        cleaned = list(dict.fromkeys(" ".join(value.lower().split()) for value in group if value.strip()))
        if len(cleaned) < 4:
            raise ValueError("Ollama devolveu menos de quatro consultas distintas.")
        result.append(cleaned[:4])
    return result

def canonical(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))

def fingerprints(path):
    with Image.open(path) as image:
        small = image.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        pixels = small.load()
        bits = sum((pixels[x, y] > pixels[x + 1, y]) << (y * 8 + x)
                   for y in range(8) for x in range(8))
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"sha256": digest,
            "dhash": f"{bits:016x}"}

def duplicate(candidate, used):
    for other in used:
        if isinstance(other, str):
            if candidate.get("url") and canonical(candidate["url"]) == canonical(other):
                return True
            continue
        for field in ("url", "asset_url"):
            if candidate.get(field) and other.get(field) and canonical(candidate[field]) == canonical(other[field]):
                return True
        if candidate.get("sha256") and candidate["sha256"] == other.get("sha256"):
            return True
        if candidate.get("dhash") and other.get("dhash"):
            if (int(candidate["dhash"], 16) ^ int(other["dhash"], 16)).bit_count() <= 5:
                return True
    return False

def terms(value):
    plain = unicodedata.normalize("NFKD", value.lower())
    plain = "".join(char for char in plain if not unicodedata.combining(char))
    return {word.removesuffix("s") for word in re.findall(r"[a-z0-9]+", plain)
            if len(word) > 2 and word not in {"the", "and", "with", "for", "from", "photo", "image", "vertical"}}

def relevance(candidate, query):
    requested = terms(query)
    raw_title = candidate.get("title", "").removeprefix("File:")
    title = terms(re.sub(r"\.[a-z0-9]+$", "", raw_title, flags=re.IGNORECASE))
    tags = terms(candidate.get("tags", ""))
    matches = requested & (title | tags)
    if len(matches) < min(2, len(requested)) or not matches:
        return 0
    visible_matches = len(requested & title)
    # Descriptions can mention incidental objects. Prefer concise titles identifying the subject.
    return (visible_matches / len(requested), visible_matches / max(1, len(title)),
            len(matches) / len(requested))

def quality(candidate):
    width, height = candidate.get("width", 0), candidate.get("height", 0)
    return (height >= width > 0, width >= 900 and height >= 1200,
            -abs(width / height - 9 / 16) if height else -2, width * height)

def cached_download(candidate, cache, check):
    asset = candidate["asset"]
    location = cache / (hashlib.sha256(asset.encode("utf-8")).hexdigest() + ".jpg")
    if not location.is_file():
        temporary = location.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with session() as client:
                download(client, asset, temporary, check)
            temporary.replace(location)
        finally:
            temporary.unlink(missing_ok=True)
    check()
    with Image.open(location) as image:
        if min(image.size) < 300:
            raise ValueError("Imagem pequena demais para revisão.")
    return location, fingerprints(location)

def acquire(client, settings, folder, scene, index, used, check, *, cache=None, excluded=()):
    target = folder / f"imagem-{index:02d}-{uuid.uuid4().hex[:8]}.jpg"
    notes = []
    if settings["comfy_enabled"] and not settings["light"]:
        try:
            result = comfy(client, settings, scene["prompt"], target, check)
            result.update(fingerprints(target))
            if not duplicate(result, used):
                return dict(result, file=target.name)
            target.unlink()
        except (ValueError, RuntimeError, OSError, requests.RequestException, KeyError) as error:
            check()
            notes.append(f"ComfyUI: {error}")
    providers = [(commons, ())]
    if settings["pexels"]:
        providers.append((pexels, (settings["pexels"],)))
    if settings["pixabay"]:
        providers.append((pixabay, (settings["pixabay"],)))
    cache = cache or folder / "cache-imagens"
    cache.mkdir(parents=True, exist_ok=True)
    queries = list(dict.fromkeys([*scene.get("keywords", []), scene["query"]]))
    seen = set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for query in filter(None, queries):
            candidates = []
            for provider, args in providers:
                check()
                try:
                    for candidate in provider(client, query, *args):
                        candidate.update(asset_url=candidate["asset"], search_query=query)
                        if (canonical(candidate["url"]) not in seen
                                and not duplicate(candidate, used) and not duplicate(candidate, excluded)
                                and relevance(candidate, query)):
                            seen.add(canonical(candidate["url"]))
                            candidates.append(candidate)
                except (requests.RequestException, ValueError, KeyError):
                    if provider.__name__ not in notes:
                        notes.append(provider.__name__)
            candidates.sort(key=lambda item: (
                relevance(item, query)[0],
                item.get("license") in {"Public domain", "CC0", "Pexels License", "Pixabay Content License"},
                relevance(item, query)[1:], quality(item)), reverse=True)
            for offset in range(0, min(9, len(candidates)), 3):
                batch = candidates[offset:offset + 3]
                futures = [pool.submit(cached_download, item, cache, check) for item in batch]
                for candidate, future in zip(batch, futures):
                    check()
                    try:
                        location, hashes = future.result()
                    except (OSError, ValueError, requests.RequestException, Image.DecompressionBombError):
                        continue
                    candidate.update(hashes)
                    if duplicate(candidate, used) or duplicate(candidate, excluded):
                        continue
                    shutil.copyfile(location, target)
                    return dict(candidate, file=target.name, notes=notes)
    card(target, index)
    return {"file": target.name, "provider": "Cartão gráfico", "title": "Imagem relevante pendente",
            "author": "Curiosity AI Studio Free", "url": "", "license": "Arte do aplicativo",
            "placeholder": True, "notes": notes}
