import re

GREETING_TRIGGERS = ["hi", "hello", "hey", "thanks", "thank you", "bye", "goodbye"]

GENRE_KEYWORDS = {
    "romance": "Romance", "rom-com": "Romance", "rom com": "Romance",
    "comedy": "Comedy", "action": "Action", "adventure": "Adventure",
    "fantasy": "Fantasy", "horror": "Horror", "mystery": "Mystery",
    "sci-fi": "Sci-Fi", "sci fi": "Sci-Fi", "slice of life": "Slice of Life",
    "sports": "Sports", "supernatural": "Supernatural", "thriller": "Thriller",
    "drama": "Drama", "psychological": "Psychological",
}

SIMILAR_TO_PATTERN = re.compile(r"(?:like|similar to)\s+([a-zA-Z0-9\s]+)", re.IGNORECASE)

RECOMMENDATION_PHRASES = [
    "recommend", "recommendation", "suggest", "suggestion",
    "what should i watch", "start with", "new to anime", "beginner",
    "highest rated", "top rated", "best anime", "unique storyline",
]


def _is_recommendation_request(lower):
    if any(phrase in lower for phrase in RECOMMENDATION_PHRASES):
        return True
    if SIMILAR_TO_PATTERN.search(lower):
        return True
    if any(keyword in lower for keyword in GENRE_KEYWORDS):
        return True
    return False


def detect_intent(message):
    lower = message.lower().strip()

    if lower in GREETING_TRIGGERS or any(lower.startswith(g) for g in GREETING_TRIGGERS):
        return "general_chat"

    if _is_recommendation_request(lower):
        return "recommendation"

    return "anime_qa"

