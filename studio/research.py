import re
import unicodedata

import requests

WIKI = "https://pt.wikipedia.org/w/api.php"
CATEGORIES = {
    "Natureza": ("Axolote", "Sequoia-gigante", "Ornitorrinco", "Tardígrado"),
    "Ciência": ("Efeito Mpemba", "Câmara de nuvens", "Bioluminescência", "Fluido não newtoniano"),
    "Espaço": ("Encélado", "Titã (satélite)", "Vênus (planeta)", "Io (satélite)"),
    "História": ("Mecanismo de Anticítera", "Pedra de Roseta", "Exército de Terracota"),
    "Geografia": ("Lago Baical", "Salar de Uyuni", "Deserto do Atacama", "Ilha de Socotra"),
}

def key(value):
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(re.findall(r"[a-z0-9]+", normalized))

def session():
    client = requests.Session()
    client.headers["User-Agent"] = "CuriosityAIStudioFree/1.0 (desktop Wikimedia research client; Python requests)"
    return client

def research(client, topic, excluded=()):
    result = client.get(WIKI, params={
        "action": "query", "generator": "search", "gsrsearch": topic,
        "gsrnamespace": 0, "gsrlimit": 4, "prop": "extracts|info|pageprops",
        "inprop": "url", "explaintext": 1, "exintro": 1, "exchars": 4200, "exlimit": 4,
        "ppprop": "disambiguation", "format": "json", "formatversion": 2,
    }, timeout=25)
    result.raise_for_status()
    data = result.json()
    if "error" in data:
        raise RuntimeError(data["error"]["info"])
    pages = sorted(data.get("query", {}).get("pages", []), key=lambda item: item.get("index", 0))
    sources = []
    for page in pages:
        if "disambiguation" in page.get("pageprops", {}) or len(page.get("extract", "")) < 200:
            continue
        url = page["fullurl"]
        if page.get("lastrevid"):
            url = f'https://pt.wikipedia.org/w/index.php?oldid={page["lastrevid"]}'
        sources.append({"id": str(page["pageid"]), "title": page["title"],
                        "url": url, "text": " ".join(page["extract"].split())})
    return sources if sources and sources[0]["id"] not in excluded else []
