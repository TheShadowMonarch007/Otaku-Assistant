import hashlib
import json
import os
import re
import threading
import time
import requests
from config import ANILIST_API_URL


FRANCHISE_RELATION_TYPES = {"PREQUEL", "SEQUEL", "PARENT", "SIDE_STORY", "ALTERNATIVE",
                             "SUMMARY", "SPIN_OFF", "COMPILATION", "CONTAINS"}

_CACHE_TTL = 24 * 3600
_CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "anilist_cache.json")
_CACHE_LOCK = threading.Lock()
try:
    with open(_CACHE_FILE, "r", encoding="utf-8") as f:
        _CACHE = json.load(f)
except (OSError, ValueError):
    _CACHE = {}


def anilist_post(graphql_query, variables):
    """Single entry point for every AniList call: cached on disk, and retries politely on 429."""
    key = hashlib.md5((graphql_query + json.dumps(variables, sort_keys=True)).encode()).hexdigest()
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]

    for _ in range(4):
        response = requests.post(
            ANILIST_API_URL,
            json={"query": graphql_query, "variables": variables},
            timeout=15,
        )
        if response.status_code == 429:
            try:
                wait = int(response.headers.get("Retry-After", 5))
            except ValueError:
                wait = 5
            time.sleep(min(wait, 30) + 1)
            continue
        response.raise_for_status()
        data = response.json()
        with _CACHE_LOCK:
            _CACHE[key] = [time.time(), data]
            try:
                with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(_CACHE, f)
            except OSError:
                pass
        return data

    raise RuntimeError("AniList is rate limiting us (429 after several retries)")


def _clean_description(text):
    """Strip HTML tags and normalize whitespace from AniList description text."""
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def search_anime_by_title(query, per_page=5):
    """Search AniList for anime matching a title string. Returns a list of results."""
    graphql_query = """
    query ($search: String, $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        media(search: $search, type: ANIME) {
          id
          title { romaji english }
          description
          genres
          episodes
          popularity
        }
      }
    }
    """
    data = anilist_post(graphql_query, {"search": query, "perPage": per_page})

    results = []
    for media in data["data"]["Page"]["media"]:
        results.append({
            "all_titles": [t for t in (media["title"]["english"], media["title"]["romaji"]) if t],            "id": media["id"],
            "title": media["title"]["english"] or media["title"]["romaji"],
            "description": _clean_description(media["description"]),
            "genres": media["genres"],
            "episodes": media["episodes"],
            "popularity": media["popularity"],
        })
    return results


def search_anime_by_character(query, per_page=5):
    graphql_query = """
    query ($search: String, $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        characters(search: $search) {
          name { full }
          media(perPage: 3) {
            nodes { id title { romaji english } description genres episodes popularity }
          }
        }
      }
    }
    """
    data = anilist_post(graphql_query, {"search": query, "perPage": per_page})

    characters = data["data"]["Page"]["characters"]
    if not characters:
        return []

    query_words = set(w.lower() for w in re.findall(r"[a-zA-Z]+", query) if len(w) > 2)

    results = []
    for character in characters:
        name_words = set(w.lower() for w in re.findall(r"[a-zA-Z]+", character["name"]["full"] or ""))
        if not (query_words & name_words):
            continue  # this candidate's name doesn't actually match the query
        for media in character["media"]["nodes"]:
            results.append({
                "id": media["id"],
                "title": media["title"]["english"] or media["title"]["romaji"],
                "description": _clean_description(media["description"]),
                "genres": media["genres"],
                "episodes": media["episodes"],
                "popularity": media["popularity"],
            })
    return results


def get_related_anime_ids(anime_id):
    """Fetch AniList IDs directly franchise-related to this anime (sequels, side stories, etc.)."""
    graphql_query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) {
        relations {
          edges { relationType node { id type } }
        }
      }
    }
    """
    data = anilist_post(graphql_query, {"id": anime_id})
    edges = data.get("data", {}).get("Media", {}).get("relations", {}).get("edges", [])
    return {
        edge["node"]["id"] for edge in edges
        if edge["relationType"] in FRANCHISE_RELATION_TYPES and edge["node"]["type"] == "ANIME"
    }


def get_anime_tags(anime_id):
    """Return {tag_name: relevance_rank} for an anime, keeping only reasonably relevant tags."""
    graphql_query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) { tags { name rank } }
    }
    """
    data = anilist_post(graphql_query, {"id": anime_id})
    tags = data.get("data", {}).get("Media", {}).get("tags", []) or []
    return {t["name"]: t["rank"] for t in tags if t.get("rank") and t["rank"] >= 40}

def _clean_character_description(text):
    """AniList marks spoilers as ~!like this!~ ; convert to the ||spoiler|| format the frontend blurs."""
    if not text:
        return ""
    text = re.sub(r"~!(.*?)!~", r"||\1||", text, flags=re.DOTALL)
    text = text.replace("__", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def search_characters(query, per_page=10):
    """Search AniList for characters by name. Each result lists the anime they appear in, most popular first."""
    graphql_query = """
    query ($search: String, $perPage: Int) {
      Page(page: 1, perPage: $perPage) {
        characters(search: $search, sort: FAVOURITES_DESC) {
          id
          name { full alternative }
          description
          favourites
          media(type: ANIME, sort: POPULARITY_DESC, perPage: 5) {
            nodes { id title { romaji english } description genres episodes popularity }
          }
        }
      }
    }
    """
    data = anilist_post(graphql_query, {"search": query, "perPage": per_page})

    results = []
    for ch in data["data"]["Page"]["characters"]:
        anime_list = [{
            "id": m["id"],
            "title": m["title"]["english"] or m["title"]["romaji"],
            "description": _clean_description(m["description"]),
            "genres": m["genres"],
            "episodes": m["episodes"],
            "popularity": m["popularity"],
        } for m in ch["media"]["nodes"]]
        if not anime_list:
            continue  # manga-only character, nothing to anchor an anime answer on
        results.append({
            "id": ch["id"],
            "name": ch["name"]["full"],
            "names": [ch["name"]["full"] or ""] + (ch["name"]["alternative"] or []),
            "description": _clean_character_description(ch["description"]),
            "favourites": ch["favourites"] or 0,
            "anime_list": anime_list,
        })
    return results

if __name__ == "__main__":
    results = search_anime_by_character("luffy")
    for r in results:
        print(r["id"], r["title"])