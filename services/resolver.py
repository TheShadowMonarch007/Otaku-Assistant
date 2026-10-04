import re
from concurrent.futures import ThreadPoolExecutor
from services.anilist_service import search_anime_by_title, search_anime_by_character, search_characters

# A same-name character only counts as "also possible" if it has at least this share of the
# top match's favourites. Keeps obscure namesakes from triggering a pointless "which one?".
NOTABLE_RATIO = 0.10
MAX_OTHERS = 3


def _words(text):
    return set(w.lower() for w in re.findall(r"[a-zA-Z]+", text) if len(w) > 2)


def _name_words(text):
    """Like _words, but keeps short words so names like 'L' work."""
    return set(w.lower() for w in re.findall(r"[a-zA-Z]+", text or ""))


def resolve_anime(entity):
    if not entity:
        return None

    entity_words = _words(entity)
    entity_norm = re.sub(r"[^a-z0-9]", "", entity.lower())

    with ThreadPoolExecutor(max_workers=2) as ex:
        title_future = ex.submit(search_anime_by_title, entity)
        char_future = ex.submit(search_anime_by_character, entity)
        title_results = title_future.result()
        character_results = char_future.result()

    def matches(a):
        for t in a.get("all_titles") or [a["title"]]:   # check english AND romaji
            if entity_words & _words(t):
                return True
            if not entity_words and entity_norm and re.sub(r"[^a-z0-9]", "", t.lower()) == entity_norm:
                return True
        return False

    confident_title_matches = [a for a in title_results if matches(a)]

    candidates = confident_title_matches + character_results
    if not candidates:
        return None

    return max(candidates, key=lambda a: a["popularity"] or 0)


def _name_matches(character, query_words, strict):
    for n in character["names"]:
        nw = _name_words(n)
        if (query_words <= nw) if strict else (query_words & nw):
            return True
    return False

def _hint_anime_ids(hint_names, exclude_words):
    """Anime ids that other characters named in the question appear in (e.g. 'orihime' -> Bleach)."""
    ids = set()
    for h in hint_names:
        hw = _name_words(h)
        if not hw or hw <= exclude_words:
            continue   # that's the main character again, not a second one
        for c in search_characters(h):
            if _name_matches(c, hw, strict=True):
                ids |= {a["id"] for a in c["anime_list"]}
                break   # results come sorted by favourites, so the first strict match is the most famous
    return ids

def resolve_character(name, anime=None, hint_names=None):
    """Find the character the user means.

    Returns None if nothing matches, else {"best": character, "others": [same-name characters]}.
    If `anime` is given, only characters appearing in that anime are considered (no ambiguity raised).
    """
    if not name:
        return None
    query_words = _name_words(name)
    if not query_words:
        return None

    candidates = search_characters(name)
    matches = [c for c in candidates if _name_matches(c, query_words, strict=True)]

    if not matches and len(query_words) > 1:
        # The full string can find nothing if AniList stores only part of the name:
        # also search each word on its own, merge, and re-filter
        seen = {c["id"] for c in candidates}
        for w in sorted(query_words, key=len, reverse=True):
            for c in search_characters(w):
                if c["id"] not in seen:
                    seen.add(c["id"])
                    candidates.append(c)
        matches = [c for c in candidates if _name_matches(c, query_words, strict=True)]

    if not matches:
        # AniList often stores only part of a name (e.g. "Levi" for "Levi Ackerman").
        # Rank partial matches: given name (first word typed) first, then profile-text support, then fame.
        first_word = _name_words(name.split()[0])

        def partial_rank(c):
            name_words = set().union(*(_name_words(n) for n in c["names"]))
            shared = query_words & name_words
            return (bool(first_word & shared), len(shared),
                    len(query_words & _name_words(c["description"])), c["favourites"])

        ranked = sorted((c for c in candidates if partial_rank(c)[1] > 0), key=partial_rank, reverse=True)
        if anime:
            ranked = [c for c in ranked if any(a["id"] == anime["id"] for a in c["anime_list"])]
        return {"best": ranked[0], "others": []} if ranked else None

    if anime:
        in_anime = [c for c in matches if any(a["id"] == anime["id"] for a in c["anime_list"])]
        if not in_anime:
            return None
        return {"best": max(in_anime, key=lambda c: c["favourites"]), "others": []}

    matches.sort(key=lambda c: c["favourites"], reverse=True)
    best = matches[0]
    if best["favourites"] <= 0:
        return {"best": best, "others": []}

    others = []
    seen_anime = {best["anime_list"][0]["id"]}   # one entry per anime, so alt forms of a character aren't listed twice
    for c in matches[1:]:
        if c["favourites"] < NOTABLE_RATIO * best["favourites"]:
            continue
        primary = c["anime_list"][0]["id"]
        if primary in seen_anime:
            continue
        seen_anime.add(primary)
        others.append(c)
        if len(others) >= MAX_OTHERS:
            break
        if others and hint_names:
            hint_ids = _hint_anime_ids(hint_names, query_words)
            if hint_ids:
                fits = [c for c in [best] + others if any(a["id"] in hint_ids for a in c["anime_list"])]
                if fits:
                    return {"best": fits[0], "others": fits[1:]}        
    return {"best": best, "others": others}