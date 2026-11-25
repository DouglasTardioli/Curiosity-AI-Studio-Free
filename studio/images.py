import hashlib
import html
import io
import json
import logging
import shutil
import unicodedata
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlparse, urlsplit, urlunsplit

import requests
from PIL import Image, ImageOps

from .research import session
from .script import STRING, object_schema, request_json, scene_array

LOGGER = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF"}
MIN_EDGE = 300
COMMONS = "https://commons.wikimedia.org/w/api.php"

def clean(value):
    return " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).split())

def get(client, url, **kwargs):
    response = client.get(url, timeout=(5, 25), **kwargs)
    LOGGER.info("Consulta HTTP %s | %s", response.status_code, url)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise ValueError(data["error"].get("code", "Erro do provedor"))
    return data

def commons(client, query, *, graphics=False):
    suffix = "" if graphics else " filetype:bitmap"
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
                LOGGER.info("Commons rejeitou %s | restrições de reutilização", info["descriptionurl"])
                continue
            if not (license_name in {"CC0", "Public domain"} or
                    (re.fullmatch(r"CC BY(?:-SA)? (?:[1-4]\.0|2\.5)", license_name) and author
                     and urlparse(license_url).hostname == "creativecommons.org")):
                LOGGER.info("Commons rejeitou %s | licença=%s ou atribuição incompleta",
                            info["descriptionurl"], license_name)
                continue
            yield {"asset": info.get("thumburl") or info["url"], "original_asset": info["url"],
                   "url": info["descriptionurl"],
                   "title": page["title"], "author": author, "license": license_name,
                   "tags": clean(meta.get("ImageDescription", ""))[:1000],
                   "width": info.get("thumbwidth", info.get("width", 0)),
                   "height": info.get("thumbheight", info.get("height", 0)),
                   "license_url": license_url, "provider": "Wikimedia Commons"}

def openverse(client, query, *, graphics=False):
    params = {"q": query, "license": "cc0,pdm,by,by-sa", "page_size": 15}
    if graphics:
        params["extension"] = "png"
    data = get(client, "https://api.openverse.org/v1/images/", params=params)
    licenses = {"cc0": "CC0", "pdm": "Public domain", "by": "CC BY", "by-sa": "CC BY-SA"}
    for item in data.get("results", []):
        code = item.get("license")
        url = item.get("foreign_landing_url") or item.get("url")
        license_url = item.get("license_url") or ""
        if (not url or not item.get("url") or code not in licenses
                or urlparse(license_url).hostname != "creativecommons.org"
                or (code in {"by", "by-sa"} and not item.get("creator"))):
            LOGGER.info("Openverse rejeitou %s | licença, URL ou atribuição incompleta", url)
            continue
        yield {"asset": item["url"], "url": url, "title": item.get("title") or "",
               "author": item.get("creator") or "", "provider": "Openverse", "license": licenses[code],
               "license_url": license_url, "width": item.get("width") or 0, "height": item.get("height") or 0,
               "tags": " ".join(tag.get("name", "") for tag in item.get("tags") or [])}


def validate_source(image):
    if image.format not in IMAGE_FORMATS or min(image.size) < MIN_EDGE or image.width * image.height > 45_000_000:
        raise ValueError(f"Formato ou resolução inválida: {image.format} {image.size}.")
    image.load()


def image_info(path):
    with Image.open(path) as image:
        validate_source(image)
        return image.size


def library_files(settings, query, extensions):
    location = settings.get("media_library")
    requested = terms(query)
    if not location or not Path(location).is_dir() or not requested:
        return []
    try:
        return sorted(path for path in Path(location).iterdir()
                      if path.is_file() and path.suffix.lower() in extensions and terms(path.stem) & requested)
    except OSError as error:
        LOGGER.warning("Biblioteca local %s: %s", location, error)
        return []


def placeholder(record):
    provider = record.get("provider")
    name = Path(record.get("file", "")).name
    return (record.get("placeholder") or record.get("generated") or provider == "Cartão gráfico"
            or (record.get("title") or "").startswith("Ilustração tipográfica da cena")
            or any(re.fullmatch(r"ilustracao-\d{2}-[a-f0-9]{12}\.jpg", filename, re.IGNORECASE)
                   for filename in (name, record.get("title") or "")))


def generic_graphic(record):
    return (record.get("provider") == "Gerado localmente"
            or record.get("title") == "Elemento vetorial rasterizado pelo aplicativo")


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
        validate_source(original)
        image = ImageOps.exif_transpose(original).convert("RGBA")
        image.thumbnail((1400, 2100))
        background = Image.new("RGB", image.size, "#111827")
        background.paste(image, mask=image.getchannel("A"))
        # Unique target names allow SQLite to keep referencing the last complete asset.
        background.save(target, "JPEG", quality=92)

def download(client, url, target, check=lambda: None):
    if urlsplit(url).scheme not in {"http", "https"}:
        raise ValueError("URL de imagem inválida.")
    payload = io.BytesIO()
    with client.get(url, stream=True, timeout=(4, 12)) as response:
        LOGGER.info("Download HTTP %s | %s", response.status_code, url)
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

def generate_keywords(client, settings, project, check):
    schema = object_schema({"queries": scene_array({
        "type": "array", "items": STRING, "minItems": 4, "maxItems": 4,
    })})
    texts = [scene["text"] for scene in project["scenes"]]
    prompt = (
        "For each of the 14 Portuguese narration lines, produce four distinct English photo "
        "search phrases (3 to 7 words each), one group per scene in order. Start with the most specific "
        "visible subject. The other three should depict different concrete objects or activities "
        "mentioned or implied by that line. Include visual context from the topic where needed and "
        "avoid unrelated stock images, abstract terms, "
        "and repeating the same subject in every scene. Use plain English.\n"
        f"Topic (infer the subject from the lines when unspecified): {project['topic']}\nLines: {json.dumps(texts, ensure_ascii=False)}"
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
    aliases = {"gizeh": "giza", "gizah": "giza", "gize": "giza", "piramide": "pyramid",
               "egito": "egypt", "egipcio": "egyptian", "queops": "khufu", "cheops": "khufu"}
    return {aliases.get(word.removesuffix("s"), word.removesuffix("s")) for word in re.findall(r"[a-z0-9]+", plain)
            if len(word) > 2 and word not in {"the", "and", "with", "for", "from", "photo", "image", "vertical"}}

def relevance(candidate, query):
    requested = terms(query)
    raw_title = (candidate.get("title") or "").removeprefix("File:")
    title = terms(re.sub(r"\.[a-z0-9]+$", "", raw_title, flags=re.IGNORECASE))
    tags = terms(candidate.get("tags") or "")
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
    if location.is_file():
        try:
            image_info(location)
        except (ValueError, OSError, Image.DecompressionBombError) as error:
            LOGGER.warning("Cache de imagem inválido | %s | %s", location, error)
            location.unlink(missing_ok=True)
        else:
            check()
            return location, fingerprints(location)
    if not location.is_file():
        temporary = location.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            with session() as client:
                try:
                    download(client, asset, temporary, check)
                except requests.HTTPError as error:
                    original = candidate.get("original_asset")
                    if not original or canonical(original) == canonical(asset) or error.response.status_code not in {400, 404}:
                        raise
                    LOGGER.warning("Miniatura indisponível | %s | tentando a imagem original", asset)
                    download(client, original, temporary, check)
            temporary.replace(location)
        finally:
            temporary.unlink(missing_ok=True)
    check()
    image_info(location)
    LOGGER.info("Imagem validada no cache | %s | %s", asset, location)
    return location, fingerprints(location)

def marked(candidate):
    return bool(re.search(r"watermark|marca[- ]d[’' ]?[áa]gua|stock preview", (candidate.get("title") or "")
                          + " " + (candidate.get("tags") or ""), re.IGNORECASE))


def reverse_scan(candidate):
    title = Path(candidate.get("title") or "").stem
    return bool(re.search(r"[-—,(]\s*(?:back|verso|reverse)\s*(?:[-—,)]|$)", title, re.IGNORECASE))


def request_failure(error):
    response = getattr(error, "response", None)
    return f"{type(error).__name__} HTTP {response.status_code}" if response is not None else type(error).__name__


def provider_candidates(client, provider, args, queries, unavailable, check):
    for query in queries:
        check()
        LOGGER.info("Busca | provedor=%s | consulta=%s", provider.__name__, query)
        try:
            yield query, list(provider(client, query, *args))
        except (requests.RequestException, ValueError, KeyError, TypeError) as error:
            LOGGER.warning("Busca falhou | provedor=%s | consulta=%s | %s",
                           provider.__name__, query, request_failure(error))
            response = getattr(error, "response", None)
            if (isinstance(error, (requests.ConnectionError, requests.Timeout))
                    or (response is not None and response.status_code in {401, 403, 429})):
                unavailable.add(provider.__name__)
                return


def acquire(client, settings, folder, scene, index, used, check, *, cache=None, excluded=(), optional=False,
            unavailable=None):
    target = folder / f"imagem-{index:02d}-{uuid.uuid4().hex[:8]}.jpg"
    unavailable = unavailable if unavailable is not None else set()
    providers = [(commons, ()), (openverse, ())]
    for provider, name in ((pexels, "pexels"), (pixabay, "pixabay")):
        if settings.get(name):
            providers.append((provider, (settings[name],)))
    cache = cache or folder / "cache-imagens"
    cache.mkdir(parents=True, exist_ok=True)
    queries = list(dict.fromkeys(filter(None, [*scene.get("keywords", []), scene.get("query")])))
    excluded = [*used, *excluded]
    seen = set()
    with ThreadPoolExecutor(max_workers=3) as pool:
        for provider, args in providers:
            if provider.__name__ in unavailable:
                continue
            check()
            for query, results in provider_candidates(client, provider, args, queries, unavailable, check):
                check()
                candidates = []
                for candidate in results:
                    candidate.update(asset_url=candidate["asset"], search_query=query)
                    url = canonical(candidate["url"])
                    if url in seen or duplicate(candidate, excluded):
                        LOGGER.info("Imagem rejeitada | duplicada | %s", candidate["url"])
                    elif marked(candidate):
                        LOGGER.info("Imagem rejeitada | marca-d’água nos metadados | %s", candidate["url"])
                    elif reverse_scan(candidate):
                        LOGGER.info("Imagem rejeitada | verso de digitalização | %s", candidate["url"])
                    elif not relevance(candidate, query):
                        LOGGER.info("Imagem rejeitada | relevância insuficiente | %s | %s", query, candidate["url"])
                    else:
                        candidates.append(candidate)
                        seen.add(url)
                candidates.sort(key=lambda item: (relevance(item, query),
                                item.get("license") in {"Public domain", "CC0"}, quality(item)), reverse=True)
                for offset in range(0, min(6 if optional else 12, len(candidates)), 3):
                    batch = candidates[offset:offset + 3]
                    futures = [pool.submit(cached_download, item, cache, check) for item in batch]
                    for candidate, future in zip(batch, futures):
                        check()
                        try:
                            location, hashes = future.result()
                        except (OSError, ValueError, requests.RequestException, Image.DecompressionBombError) as error:
                            LOGGER.warning("Download rejeitado | %s | %s | %s", candidate["asset"],
                                           request_failure(error), str(error) if isinstance(error, ValueError) else "")
                            continue
                        candidate.update(hashes)
                        if duplicate(candidate, excluded):
                            LOGGER.info("Imagem rejeitada | conteúdo duplicado | %s", candidate["asset"])
                            continue
                        shutil.copyfile(location, target)
                        LOGGER.info("Imagem selecionada | cena=%02d | %s | %s", index, candidate["url"], target)
                        return dict(candidate, file=target.name)
    for query in queries:
        for path in library_files(settings, query, IMAGE_EXTENSIONS):
            result = {"provider": "Biblioteca local", "title": path.stem, "search_query": query}
            if not relevance(result, query) or placeholder(result) or marked(result) or reverse_scan(result):
                continue
            check()
            try:
                normalize(path, target)
                result.update(fingerprints(target))
                if not duplicate(result, excluded):
                    LOGGER.info("Imagem local selecionada | cena=%02d | %s | %s", index, path, target)
                    return dict(result, file=target.name)
            except (ValueError, OSError, Image.DecompressionBombError) as error:
                LOGGER.warning("Imagem local rejeitada | %s | %s", path, error)
            target.unlink(missing_ok=True)
    if not optional and settings.get("comfy_enabled") and not settings.get("light"):
        try:
            result = comfy(client, settings, scene["prompt"], target, check)
            image_info(target)
            result.update(fingerprints(target))
            if not duplicate(result, excluded):
                return dict(result, file=target.name)
        except (ValueError, RuntimeError, OSError, requests.RequestException, KeyError) as error:
            check()
            LOGGER.warning("ComfyUI indisponível | cena=%02d | %s", index, request_failure(error))
        target.unlink(missing_ok=True)
    LOGGER.info("Imagem %s indisponível | cena=%02d | consultas=%s",
                "adicional" if optional else "principal", index, queries)
    return None
