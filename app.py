import uuid
from flask import Flask, request, jsonify, render_template
from services.resolver import resolve_anime, resolve_character
from services.llm_service import generate_answer, extract_query_info, classify_message
from services.wiki_service import fetch_wiki_context
from services.recommendation_service import (
    get_top_anime, get_similar_to, normalize_genre, normalize_tag, get_top_anime_by_tag,
    GENRE_ALIASES, KNOWN_TAGS, VALID_GENRES, get_picks
)
from werkzeug.exceptions import HTTPException
import os
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

app = Flask(__name__)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)   # on Render, the real visitor IP comes from the proxy header
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

@app.errorhandler(Exception)
def handle_error(e):
    if isinstance(e, HTTPException):
        if e.code == 429:
            return jsonify({"reply": "You're sending messages too fast. Please wait a moment and try again."}), 429
        return e
    app.logger.exception(e)
    return jsonify({"reply": "Something went wrong on my end. Please try again in a moment."}), 500

conversations = {}

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/new_chat", methods=["POST"])
def new_chat():
    thread_id = str(uuid.uuid4())
    conversations[thread_id] = {"history": [], "current_anime": None}
    return jsonify({"thread_id": thread_id})


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    thread_id = data.get("thread_id")
    user_message = data.get("message", "").strip()[:500]

    if not user_message:
        return jsonify({"error": "Empty message"}), 400

    if thread_id not in conversations:
        conversations[thread_id] = {"history": [], "current_anime": None}

    convo = conversations[thread_id]

    classification = classify_message(user_message)
    intent = classification.get("intent", "anime_qa")

    if intent == "general_chat":
        reply = "I'm all about anime. Ask me about characters, plots, lore or recommendations."
        convo["history"].append({"role": "user", "content": user_message})
        convo["history"].append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})

    if intent == "recommendation":
        rec_info = classification.get("rec_type", {"type": "general"})
        if rec_info.get("type") in (None, "general"):
            lowered = user_message.lower()
            for alias in sorted(list(GENRE_ALIASES) + list(KNOWN_TAGS) + [g.lower() for g in VALID_GENRES], key=len, reverse=True):
                if alias in lowered:
                    rec_info = {"type": "genre", "genre": alias}
                    break        

        if rec_info["type"] == "similar_to":
            ref_anime = resolve_anime(rec_info.get("reference"))
            wanted_genre = normalize_genre(rec_info.get("genre"))
            picks = get_similar_to(ref_anime, required_genre=wanted_genre) if ref_anime else []
            if ref_anime:
                label = f"{wanted_genre} " if wanted_genre else ""
                intro = f"If you liked {ref_anime['title']}, try out these {label}anime:"
            else:
                intro = "Here are some picks:"
        elif rec_info["type"] == "genre":
            raw = rec_info.get("genre")
            genre = normalize_genre(raw)
            if genre:
                picks = get_picks(genre=genre)
                intro = f"Here are some {genre} picks:"
            else:
                tag = normalize_tag(raw)
                if tag:
                    picks = get_picks(tag=tag)
                    intro = f"Here are some {tag} picks:"
                else:
                    picks = []
                    intro = "Here are some picks:"
        elif rec_info["type"] == "beginner":
            picks = get_picks(sort="POPULARITY_DESC")
            intro = "Since you're new to anime, here are some great starting points:"
        elif rec_info["type"] in ("top_rated", "general"):
            picks = get_picks(sort="SCORE_DESC")
            intro = "Here are some of the highest-rated anime:"
        else:
            picks = []
            intro = "Here are some picks:"

        if not picks:
            reply = "I couldn't find good recommendations for that — try naming a genre or an anime you like."
        else:
            bullets = "\n".join(f"• {p['title']} (Score: {p['score']}/100)" for p in picks)
            reply = f"{intro}\n{bullets}"
            if rec_info["type"] == "general":
                reply += "\n\n(Note: I can't judge subjective things like 'uniqueness' — these are just highly-rated overall.)"

        convo["history"].append({"role": "user", "content": user_message})
        convo["history"].append({"role": "assistant", "content": reply})
        return jsonify({"reply": reply})

    # intent == "anime_qa"
    query_info = extract_query_info(user_message)
    character_name = query_info.get("character")
    anime_name = query_info.get("anime")
    topic = query_info.get("topic") or user_message

    anime = resolve_anime(anime_name) if anime_name else None

    character = None
    others = []
    if character_name:
        found = resolve_character(character_name, anime=anime, hint_names=query_info.get("mentioned"))
        if found:
            character = found["best"]
            others = found["others"]
            if not anime:
                anime = character["anime_list"][0]   # the character's most popular anime
        elif not anime:
            anime = resolve_anime(character_name)    # the "character" may actually have been a show title

    if not anime and not character_name:
        anime = convo["current_anime"]   # follow-up like "what about his sister?"

    if not anime:
        if character_name:
            reply = (f"I couldn't find a character called \"{character_name}\". "
                     f"Try the full name, or add the anime, like \"{character_name} from <anime>\".")
        else:
            reply = "I couldn't figure out which anime or character you're asking about — try naming the show or a character."
        return jsonify({"reply": reply})

    convo["current_anime"] = anime

    page_query = character["name"] if character else topic

    wiki_context = None
    if character or topic.lower() != "overview":
        try:
            wiki_context = fetch_wiki_context(anime["title"], page_query, topic)
        except Exception as e:
            app.logger.warning("wiki lookup failed: %r", e)

    answer = generate_answer(user_message, anime, history=convo["history"],
                             wiki_context=wiki_context, character=character)

    if others:
        listed = ", ".join(f"{c['name']} ({c['anime_list'][0]['title']})" for c in [character] + others)
        example = f"{character_name} from {others[0]['anime_list'][0]['title']}"
        reply = (f"Sure! But I found multiple characters with that name: {listed}. "
                 f"Could you be more specific? You can ask again like \"{example}\". "
                 f"For now, here's the answer for {character['name']} ({anime['title']}):\n\n{answer}")
    else:
        reply = answer

    convo["history"].append({"role": "user", "content": user_message})
    convo["history"].append({"role": "assistant", "content": reply})

    return jsonify({"reply": reply})


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1", port=6001)