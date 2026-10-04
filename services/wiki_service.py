import re
import requests
import html as html_lib
from services.llm_service import pick_relevant_section

KNOWN_WIKI_OVERRIDES = {
    "one piece": "onepiece",
    "jujutsu kaisen": "jujutsu-kaisen",
    "demon slayer": "kimetsu-no-yaiba",
    "attack on titan": "attackontitan",
}


def _slugify_candidates(title):
    """Generate a broader set of plausible Fandom subdomain slugs from an anime title."""
    lower = title.lower().strip()
    no_punct = re.sub(r"[^a-z0-9\s]", "", lower)
    words = no_punct.split()

    candidates = []

    if lower in KNOWN_WIKI_OVERRIDES:
        candidates.append(KNOWN_WIKI_OVERRIDES[lower])

    joined = no_punct.replace(" ", "")
    dashed = no_punct.replace(" ", "-")

    candidates += [
        joined,                          # "attackontitan"
        dashed,                          # "attack-on-titan"
        f"{joined}wiki",                 # "attackontitanwiki"
        f"{joined}-wiki",                # "attack-on-titan-wiki"
        f"{joined}anime",                # "attackontitananime"
    ]

    # Try dropping common filler words ("the", "no", season numbers) — many wikis
    # use a shortened core name, e.g. "Dragon Ball Super" -> just "dragonball"
    filler = {"the", "no", "season", "part", "final", "movie"}
    core_words = [w for w in words if w not in filler and not w.isdigit()]
    if core_words and core_words != words:
        candidates.append("".join(core_words))
        candidates.append("-".join(core_words))

    # Try just the first word alone — catches cases like "Dragon Ball Super" -> "dragonball"
    # matching only the base franchise name
    if len(words) > 1:
        candidates.append("".join(words[:2]))  # first two words, e.g. "dragonball"

    seen = set()
    unique_candidates = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            unique_candidates.append(c)
    return unique_candidates


def find_wiki_subdomain(anime_title):
    """Try each candidate slug, return the first one that's a real, working Fandom wiki."""
    for slug in _slugify_candidates(anime_title):
        test_url = f"https://{slug}.fandom.com/api.php"
        try:
            response = requests.get(
                test_url,
                params={"action": "query", "meta": "siteinfo", "format": "json"},
                timeout=5
            )
            if response.status_code == 200 and "query" in response.json():
                return slug
        except requests.RequestException:
            continue
    return None

def search_wiki(subdomain, query, limit=1):
    """Search a specific Fandom wiki for pages matching the query. Returns page titles."""
    url = f"https://{subdomain}.fandom.com/api.php"
    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "format": "json"
    }
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    data = response.json()
    return [result["title"] for result in data["query"]["search"]]


def get_wiki_page_extract(subdomain, page_title, max_chars=1500):
    """Fetch a clean plain-text extract of a wiki page's intro section (rendered HTML)."""
    url = f"https://{subdomain}.fandom.com/api.php"
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "text",
        "section": 0,
        "redirects": 1,
        "format": "json"
    }
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    data = response.json()

    rendered_html = data.get("parse", {}).get("text", {}).get("*", "")
    if not rendered_html:
        return ""

    return _clean_wiki_html(rendered_html)[:max_chars]


def _clean_wiki_html(raw_html):
    """Strip infoboxes/tables/scripts/citations entirely, then strip remaining tags."""
    raw_html = re.sub(r"<table.*?</table>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r"<aside.*?</aside>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r"<style.*?</style>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r"<script.*?</script>", "", raw_html, flags=re.DOTALL)
    raw_html = re.sub(r"<sup.*?</sup>", "", raw_html, flags=re.DOTALL)

    text = re.sub(r"<[^>]+>", " ", raw_html)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text)
    text = text.split("↑")[0]  # cut off at the first footnote citation marker
    return text.strip()


def get_wiki_sections(subdomain, page_title):
    """Get the list of section titles on a wiki page."""
    url = f"https://{subdomain}.fandom.com/api.php"
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "sections",
        "format": "json"
    }
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    data = response.json()
    sections = data.get("parse", {}).get("sections", [])
    return [{"index": s["index"], "title": s["line"]} for s in sections]


def get_wiki_page_extract(subdomain, page_title, section="0", max_chars=1500):
    """Fetch a clean plain-text extract of a specific wiki page section."""
    url = f"https://{subdomain}.fandom.com/api.php"
    params = {
        "action": "parse",
        "page": page_title,
        "prop": "text",
        "section": section,
        "redirects": 1,
        "format": "json"
    }
    response = requests.get(url, params=params, timeout=5)
    response.raise_for_status()
    data = response.json()

    rendered_html = data.get("parse", {}).get("text", {}).get("*", "")
    if not rendered_html:
        return ""

    return _clean_wiki_html(rendered_html)[:max_chars]


def fetch_wiki_context(anime_title, page_query, topic):
    """Full pipeline: find the wiki, search for the specific page, pick the best section, return its text."""
    subdomain = find_wiki_subdomain(anime_title)
    if not subdomain:
        return None

    page_titles = search_wiki(subdomain, page_query)
    if not page_titles:
        return None

    page_title = page_titles[0]
    sections = get_wiki_sections(subdomain, page_title)

    section_index = "0"
    if sections:
        section_titles = [s["title"] for s in sections]
        chosen_title = pick_relevant_section(topic, section_titles)
        if chosen_title:
            for s in sections:
                if s["title"] == chosen_title:
                    section_index = s["index"]
                    break

    return get_wiki_page_extract(subdomain, page_title, section=section_index)

if __name__ == "__main__":
    context = fetch_wiki_context("Bleach", "Ichigo bankai")
    print(context)