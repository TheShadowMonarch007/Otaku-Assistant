import math
import re
import requests
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor
from config import ANILIST_API_URL
from services.anilist_service import anilist_post, get_related_anime_ids, get_anime_tags, FRANCHISE_RELATION_TYPES

_SEASON_MARKER = re.compile(
    r"\s+(season\s*\d+|part\s*\d+|final season|the final|second season|third season|"
    r"\d+(st|nd|rd|th)\s*season|the\s+movie|movie|ova).*$",
    re.IGNORECASE
)


def _display_title(title):
    """Cosmetic only: shorten a title to its franchise name for display."""
    cleaned = _SEASON_MARKER.sub("", title).strip(" -–—:")
    if ":" in cleaned:
        head = cleaned.split(":")[0].strip()
        if len(head) >= 4:  # avoids chopping titles like "Re:ZERO" down to "Re"
            cleaned = head
    cleaned = re.sub(r"\s+(II|III|IV|VI|\d{1,2})$", "", cleaned).strip()
    return cleaned or title


VALID_GENRES = {"Action", "Adventure", "Comedy", "Drama", "Fantasy", "Horror", "Mystery",
                "Romance", "Sci-Fi", "Slice of Life", "Sports", "Supernatural", "Thriller", "Psychological",
                "Mecha", "Music", "Mahou Shoujo"}

GENRE_ALIASES = {
    "rom-com": "Romance", "rom com": "Romance", "romcom": "Romance", "romantic comedy": "Romance",
    "scary": "Horror", "spooky": "Horror", "creepy": "Horror",
    "funny": "Comedy", "comedic": "Comedy",
    "sad": "Drama", "emotional": "Drama", "tearjerker": "Drama",
    "fighting": "Action", "battle": "Action",
    "magic": "Fantasy", "magical": "Fantasy",
    "space": "Sci-Fi", "scifi": "Sci-Fi", "science fiction": "Sci-Fi",
    "chill": "Slice of Life", "relaxing": "Slice of Life", "wholesome": "Slice of Life",
    "sports": "Sports", "sport": "Sports",
    "mind bending": "Psychological", "dark": "Psychological", "mindfuck": "Psychological",
    "detective": "Mystery", "whodunit": "Mystery",
    "ghosts": "Supernatural", "spirits": "Supernatural",
    "robot": "Mecha", "robots": "Mecha", "giant robot": "Mecha", "giant robots": "Mecha",
    "magical girl": "Mahou Shoujo",
}

# Things that are AniList TAGS, not genres — need a different query field
KNOWN_TAGS = {
    "isekai": "Isekai", "harem": "Harem", "reverse harem": "Reverse Harem",
    "time travel": "Time Travel", "time-travel": "Time Travel",
    "military": "Military", "school": "School", "school life": "School",
    "vampire": "Vampire", "vampires": "Vampire",
    "zombie": "Zombie", "zombies": "Zombie",
    "post apocalyptic": "Post-Apocalyptic", "post-apocalyptic": "Post-Apocalyptic",
    "martial arts": "Martial Arts", "cooking": "Cooking",
    "video games": "Video Games", "gaming": "Video Games",
}

# Used ONLY to choose which tags to pull extra candidates from. These describe cast/demographic,
# not content, and exist on thousands of shows, so they'd flood the pool with unrelated anime.
POOL_SKIP_TAGS = {"Male Protagonist", "Female Protagonist", "Primarily Male Cast", "Primarily Female Cast",
                  "Ensemble Cast", "Shounen", "Seinen", "Shoujo", "Josei", "Anti-Hero",
                  "Primarily Adult Cast", "Primarily Teen Cast", "Primarily Child Cast"}


def normalize_genre(raw_genre):
    if not raw_genre:
        return None
    if raw_genre in VALID_GENRES:
        return raw_genre
    lower = raw_genre.lower()
    if lower in GENRE_ALIASES:
        return GENRE_ALIASES[lower]
    for valid in VALID_GENRES:
        if valid.lower() in lower or lower in valid.lower():
            return valid
    return None


def normalize_tag(raw_input):
    if not raw_input:
        return None
    lower = raw_input.lower().strip()

    # hand-made shortcuts first (e.g. "post apocalyptic" -> "Post-Apocalyptic")
    for key in sorted(KNOWN_TAGS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(key) + r"\b", lower):
            return KNOWN_TAGS[key]

    # then any AniList tag whose name appears in the text (full list, cached after the first call)
    data = anilist_post("query { MediaTagCollection { name isAdult } }", {})
    names = [t["name"] for t in data["data"]["MediaTagCollection"] if not t["isAdult"]]
    for name in sorted(names, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name.lower()) + r"\b", lower):
            return name
    return None


# ---------- AniList fetching (shared by genre and tag queries) ----------

_MEDIA_FIELDS = """
  id
  title { romaji english }
  genres
  episodes
  averageScore
  tags { name rank }
  relations { edges { relationType node { id type } } }
"""


def _to_result(media):
    related_ids = {
        e["node"]["id"] for e in media["relations"]["edges"]
        if e["relationType"] in FRANCHISE_RELATION_TYPES and e["node"]["type"] == "ANIME"
    }
    return {
        "id": media["id"],
        "title": media["title"]["english"] or media["title"]["romaji"],
        "genres": media["genres"],
        "episodes": media["episodes"],
        "score": media["averageScore"],
        "tags": {t["name"]: t["rank"] for t in (media.get("tags") or []) if t.get("rank") and t["rank"] >= 40},
        "related_ids": related_ids,
    }


def _fetch_media(arg_name, arg_value, sort, per_page):
    """arg_name is 'genre' or 'tag'."""
    graphql_query = f"""
    query (${arg_name}: String, $perPage: Int, $sort: [MediaSort]) {{
      Page(page: 1, perPage: $perPage) {{
        media(type: ANIME, isAdult: false, format_in: [TV, MOVIE, ONA], popularity_greater: 20000, {arg_name}: ${arg_name}, sort: $sort) {{          {_MEDIA_FIELDS}
        }}
      }}
    }}
    """
    variables = {arg_name: arg_value, "perPage": per_page, "sort": [sort]}
    data = anilist_post(graphql_query, variables)
    return [_to_result(m) for m in data["data"]["Page"]["media"]]


def get_top_anime(genre=None, sort="SCORE_DESC", per_page=5):
    return _fetch_media("genre", genre, sort, per_page)


def get_top_anime_by_tag(tag, sort="SCORE_DESC", per_page=5):
    """Get top-rated anime matching a specific AniList tag (e.g. Isekai, Mecha)."""
    return _fetch_media("tag", tag, sort, per_page)


# ---------- similarity helpers ----------

def _names_overlap(a, b, min_chars=8, min_ratio=0.5):
    """True if two titles share a long common substring (e.g. 'monogatari')."""
    a = re.sub(r"[^a-z0-9]", "", a.lower())
    b = re.sub(r"[^a-z0-9]", "", b.lower())
    if not a or not b:
        if a == b:
            return True
    return False
    m = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return m.size >= min_chars and m.size >= min_ratio * min(len(a), len(b))


def _franchise_grouper(candidates, anime_id, reference_related):
    """Union-find over relation edges: entries linked directly OR through a shared
    related ID end up with the same group root."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for rid in reference_related:
        union(anime_id, rid)
    for c in candidates:
        find(c["id"])
        for rid in c.get("related_ids", set()):
            union(c["id"], rid)
    return find


def _make_tag_weight(reference_pool):
    """Data-driven tag weights: a tag found on most of the pool says little about similarity
    (floor 0.25); a tag found on few says a lot (up to 1.0). No hand-made lists."""
    n = len(reference_pool)
    df = {}
    for c in reference_pool:
        for t in c.get("tags", {}):
            df[t] = df.get(t, 0) + 1

    def weight(tag):
        if n == 0:
            return 1.0
        idf = math.log((n + 1) / (df.get(tag, 0) + 1)) / math.log(n + 1)
        return 0.25 + 0.75 * idf

    return weight


def _tag_similarity(a, b, weight):
    """Weighted Jaccard over tag relevance ranks: 0 = nothing in common, 1 = identical."""
    if not a or not b:
        return 0.0
    inter = union = 0.0
    for k in set(a) | set(b):
        w = weight(k)
        inter += min(a.get(k, 0), b.get(k, 0)) * w
        union += max(a.get(k, 0), b.get(k, 0)) * w
    return inter / union if union else 0.0


# ---------- main recommender ----------

# Very common genres say little about similarity, so prefer the reference's rarer genres for the pool
COMMON_GENRES = {"Action", "Adventure", "Comedy", "Drama", "Fantasy"}

def get_similar_to(anime, required_genre=None, per_page=5, pool_size=25, debug=False):
    genres = anime.get("genres", [])
    if not genres:
        return []

    genre_list = [required_genre] if required_genre else sorted(genres, key=lambda g: g in COMMON_GENRES)[:2]

    # Independent AniList calls run in parallel
    with ThreadPoolExecutor(max_workers=len(genre_list) + 5) as ex:
        related_future = ex.submit(get_related_anime_ids, anime["id"])
        tags_future = ex.submit(get_anime_tags, anime["id"])
        genre_futures = [
            ex.submit(get_top_anime, genre=g, sort="SCORE_DESC", per_page=pool_size)
            for g in genre_list
        ]
        ref_tags = tags_future.result()
        genre_pools = [f.result() for f in genre_futures]
        reference_related = related_future.result() | {anime["id"]}

        # Choose pool tags by how common they are among similar-genre shows
        _gm = {}
        ref_name = _display_title(anime["title"])
        for p in genre_pools:
            for a in p:
                if a["id"] in reference_related or _names_overlap(_display_title(a["title"]), ref_name):
                    continue  # don't let the reference's own seasons inflate tag frequencies
                _gm.setdefault(a["id"], a)
        n = len(_gm)
        df = {}
        for a in _gm.values():
            for t in a.get("tags", {}):
                df[t] = df.get(t, 0) + 1

        ranked_tags = [t for t in sorted(ref_tags, key=ref_tags.get, reverse=True)
                       if t not in POOL_SKIP_TAGS]

        def _pool_score(t):
            freq = df.get(t, 0) / n if n else 0
            return ref_tags[t] * math.log(1 / max(freq, 0.01))

        usable = [t for t in ranked_tags
                  if n and df.get(t, 0) >= 2 and df.get(t, 0) / n <= 0.6 and ref_tags[t] >= 60]
        top_tags = sorted(usable, key=_pool_score, reverse=True)[:4] or ranked_tags[:3]

        tag_futures = [ex.submit(get_top_anime_by_tag, t, "SCORE_DESC", 40) for t in top_tags]
        tag_pools = [f.result() for f in tag_futures]

    # Genre pool is used to compute tag weights (it isn't biased toward the reference's tags)
    genre_merged = {}
    for p in genre_pools:
        for a in p:
            genre_merged.setdefault(a["id"], a)
    weight = _make_tag_weight(list(genre_merged.values()))

    merged = dict(genre_merged)
    for p in tag_pools:
        for a in p:
            merged.setdefault(a["id"], a)

    pool = [a for a in merged.values() if a["id"] not in reference_related]
    pool = [a for a in pool if (a["score"] or 0) >= 70]   # drops weak filler like a 59-rated show
    if required_genre:  # enforce the requested genre on EVERY candidate, including tag-pool ones
        pool = [a for a in pool if required_genre in a.get("genres", [])]

    find = _franchise_grouper(pool, anime["id"], reference_related)
    ref_genres = set(genres)

    scored = []
    for c in pool:
        cg = set(c.get("genres", []))
        genre_sim = len(cg & ref_genres) / len(cg | ref_genres) if (cg | ref_genres) else 0.0
        tag_sim = _tag_similarity(ref_tags, c.get("tags", {}), weight)
        quality = (c["score"] or 0) / 100
        if ref_tags:
            total = 0.60 * tag_sim + 0.30 * genre_sim + 0.10 * quality
        else:  # reference has no usable tags: fall back to genres
            total = 0.85 * genre_sim + 0.15 * quality
        scored.append((total, tag_sim, genre_sim, c))
    scored.sort(key=lambda t: t[0], reverse=True)

    if debug:
        print("REF:", anime["title"], genres, "| top pool tags:", top_tags, "| pool size:", len(pool))
        shown = 0
        for total, ts, gs, c in scored:
            if group_of_ref := (find(c["id"]) == find(anime["id"])):
                continue
            print(f"  {total:.3f} tag={ts:.2f} genre={gs:.2f}  {c['title']}")
            shown += 1
            if shown >= 25:
                break

    seen_groups = {find(anime["id"])}
    seen_names = [_display_title(anime["title"])]   # never recommend the reference's own franchise by name either
    results = []
    for _, _, _, c in scored:
        group = find(c["id"])
        if group in seen_groups:
            continue
        name = _display_title(c["title"])
        if any(_names_overlap(name, s) for s in seen_names):
            continue
        seen_groups.add(group)
        seen_names.append(name)
        results.append({**c, "title": name})
        if len(results) >= per_page:
            break
    return results

def get_picks(genre=None, tag=None, sort="SCORE_DESC", per_page=5, pool_size=40):
    """Top anime for a genre/tag/sort, with franchise duplicates and season suffixes removed."""
    if tag:
        pool = get_top_anime_by_tag(tag, sort, pool_size)
    else:
        pool = get_top_anime(genre=genre, sort=sort, per_page=pool_size)

    find = _franchise_grouper(pool, -1, set())   # no reference anime here, so just group the pool itself
    seen_groups, seen_names, results = set(), [], []
    for c in pool:
        group = find(c["id"])
        if group in seen_groups:
            continue
        name = _display_title(c["title"])
        if any(_names_overlap(name, s) for s in seen_names):
            continue
        seen_groups.add(group)
        seen_names.append(name)
        results.append({**c, "title": name})
        if len(results) >= per_page:
            break
    return results