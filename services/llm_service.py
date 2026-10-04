import re
import requests
from config import GROQ_API_KEY, GROQ_MODEL
import json

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def generate_answer(user_question, anime_context, history=None, wiki_context=None, character=None):
    spoiler_instruction = ""
    if wiki_context or character:
        spoiler_instruction = (
            " Wrap story spoilers in double pipes so the app can blur them. This is REQUIRED whenever "
            "your answer reveals how the story turns out: who someone ends up with, who dies, who wins, "
            "secret identities, twists, final outcomes. It applies even when the user's question already "
            "hints at the outcome, because your answer is what confirms it. "
            "If the user asks a yes/no question about an outcome, the 'Yes' or 'No' IS the spoiler, so "
            "put it INSIDE the pipes with the details, and do not write any readable word that gives "
            "the answer away. "
            "Example: Q 'does he get the girl?' -> A '||Yes, they get married in the finale.||' "
            "For other spoilers, wrap only the specific phrase. Never wrap basic background (who a "
            "character is, their role, their personality). "
            "Text already wrapped in double pipes in the source is a known spoiler: keep those markers."
        )

    system_prompt = (
        "You are an anime expert assistant. Answer the user's question using "
        "ONLY the provided information (anime details, character profile, wiki lore). "
        "If the information doesn't answer the question, say so honestly instead of making things up, "
        "in a natural way like \"I don't have details on that\". Never mention 'the information provided' "
        "or 'the source material', because the user can't see it. "
        "Respond in plain conversational text. Do not use asterisks or any markdown formatting."
        + spoiler_instruction
    )

    context_text = (
        f"Title: {anime_context['title']}\n"
        f"Genres: {', '.join(anime_context['genres'])}\n"
        f"Episodes: {anime_context['episodes']}\n"
        f"Description: {anime_context['description']}"
    )
    if character:
        context_text += (
            f"\n\nCharacter: {character['name']}\n"
            f"Character profile: {(character['description'] or 'No description available.')[:2500]}"
        )
    if wiki_context:
        context_text += f"\n\nAdditional lore:\n{wiki_context}"

    messages = [{"role": "system", "content": system_prompt}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": f"Anime info:\n{context_text}\n\nQuestion: {user_question}"})

    payload = {"model": GROQ_MODEL, "messages": messages, "temperature": 0.2}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(GROQ_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]

def _parse_json_object(raw):
    """Pull the first {...} block out of an LLM reply (handles ```json fences and stray text)."""
    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def extract_query_info(user_message):
    """One LLM call: pull out the character name, anime title and a focused topic from the question."""
    system_prompt = (
        "Extract information from the user's anime question. Respond with ONLY a JSON object, no other text: "
        '{"character": "...", "anime": "...", "mentioned": ["..."], "topic": "..."}\n\n'
        "IMPORTANT: 'character', 'anime' and 'mentioned' must be names LITERALLY WRITTEN in the user's message "
        "(copy the spelling exactly as the user wrote it; the search handles typos itself). Do NOT guess, infer, or answer the question. "
        "'character' is the main character the question is about (e.g. 'levi', 'ichigo kurosaki', 'L'); "
        "'anime' is a show's title; 'mentioned' is a list of any OTHER character names written in the message "
        "(empty list if none). Use null for character or anime if not written in the message.\n"
        "Examples:\n"
        "'who is levi ackerman' -> character 'levi ackerman', anime null, mentioned []\n"
        "'who is ichigo from darling in the franxx' -> character 'ichigo', anime 'darling in the franxx', mentioned []\n"
        "'does ichigo end up with orihime' -> character 'ichigo', anime null, mentioned ['orihime']\n"
        "'who is the villain in naruto' -> character null (the villain's name is the ANSWER, never guess it), anime 'naruto', mentioned []\n"
        "'what is attack on titan about' -> character null, anime 'attack on titan', mentioned []\n\n"
        "'topic' is a short 2-5 word phrase describing what specific info is being asked about, "
        "suitable as a wiki search query (e.g. 'main antagonist', 'Bankai ability', 'wife'). "
        "If the user just asks who someone is, or says 'what about X' / 'tell me about X', the topic is "
        "'overview', NOT a word that merely appears in the sentence "
        "(in 'what about his friend erwin' the topic is 'overview', not 'friend')."
        " If the message only names a show or character with no question, the topic is also 'overview'. "
        "If it names several shows, put only the FIRST one in 'anime'."
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(GROQ_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]

    parsed = _parse_json_object(raw)
    if not isinstance(parsed, dict):
        return {"character": None, "anime": None, "mentioned": [], "topic": None}

    def clean(value):
        if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
            return value.strip()
        return None

    mentioned = parsed.get("mentioned")
    mentioned = [m.strip() for m in mentioned if isinstance(m, str) and m.strip()] if isinstance(mentioned, list) else []

    return {"character": clean(parsed.get("character")),
            "anime": clean(parsed.get("anime")),
            "mentioned": mentioned,
            "topic": clean(parsed.get("topic"))}

def extract_wiki_search_term(user_question, anime_title):
    """Ask the LLM for a concise, targeted wiki search phrase for this question."""
    system_prompt = (
        "Given a question about an anime, produce a short, precise search phrase "
        "(2-5 words) that would find the MOST RELEVANT wiki page or section to answer it. "
        "Focus on the core topic, not the whole question. For example, "
        "'who is the main villain in naruto?' -> 'Naruto main antagonist'. "
        "'what is ichigo's bankai?' -> 'Ichigo Kurosaki Bankai'. "
        "Respond with ONLY the search phrase, nothing else."
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Anime: {anime_title}\nQuestion: {user_question}"}
        ]
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(GROQ_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"].strip()

def pick_relevant_section(topic, section_titles):
    """Ask the LLM which wiki section title best matches the topic being asked about."""
    system_prompt = (
        "Given a topic and a list of wiki section titles, pick the ONE section title most likely "
        "to contain information about that topic. Respond with ONLY the exact section title text "
        "as it appears in the list, nothing else. If none seem relevant, respond with exactly: NONE"
    )
    titles_list = "\n".join(section_titles)

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Topic: {topic}\n\nSection titles:\n{titles_list}"}
        ]
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(GROQ_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    result = response.json()["choices"][0]["message"]["content"].strip()
    return None if result == "NONE" else result

def classify_recommendation_llm(user_message):
    """Ask the LLM to classify what kind of anime recommendation is being requested."""
    system_prompt = (
        "Classify this anime recommendation request. Respond with ONLY a JSON object: "
        '{"type": "similar_to", "reference": "anime name"} if they want anime similar to a specific one they liked '
        '(handle typos in their message), '
        '{"type": "genre", "genre": "Romance|Comedy|Action|Adventure|Fantasy|Horror|Mystery|Sci-Fi|Slice of Life|Sports|Supernatural|Thriller|Drama|Psychological"} '
        "if they asked for a specific genre, "
        '{"type": "beginner"} if they are new to anime / want a starting point, '
        '{"type": "top_rated"} if they want the highest-rated/best anime overall, '
        '{"type": "general"} for anything else vague or subjective (like "unique storyline").'
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(GROQ_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, AttributeError):
        return {"type": "general"}

def classify_message(user_message):
    """Single LLM call: determine intent, and if recommendation, classify its sub-type."""
    system_prompt = (
        "Classify the user's anime-chatbot message. Respond with ONLY a JSON object.\n"
        "First decide intent: 'general_chat' (greeting, thanks, small talk, or off-topic chatter that "
        "mentions NO anime title and NO character name), "
        "'recommendation' (wants anime suggestions - handle typos in their message), "
        "or 'anime_qa' (asks about, or even just names, a specific anime or character - "
        "a message that is only an anime title, even with no question, is anime_qa).\n\n"
        "If intent is 'recommendation', also include a 'rec_type' field:\n"
        '  {"type": "genre", "genre": "the genre or theme they asked for, in their own words (e.g. isekai, mecha, rom-com, scary)"} '
            "if they mention ANY genre, theme or mood (romance, rom-com, comedy, horror, sad, wholesome, etc.), even with no anime named, "
        "if similar to one they liked, "        '  {"type": "beginner"}, {"type": "top_rated"}, or {"type": "general"}.\n\n'
        'Respond like: {"intent": "recommendation", "rec_type": {"type": "similar_to", "reference": "One Piece"}} '
        'or {"intent": "anime_qa"} or {"intent": "general_chat"}'
    )

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
    }
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    response = requests.post(GROQ_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"]

    try:
        return json.loads(raw)
    except (json.JSONDecodeError, AttributeError):
        return {"intent": "anime_qa"}

if __name__ == "__main__":
    from services.resolver import resolve_anime
    anime = resolve_anime("bleach")
    answer = generate_answer("What is this anime about?", anime)
    print(answer)
