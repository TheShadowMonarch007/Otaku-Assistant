# Otaku Assistant

A chatbot for anime fans. Ask about plots, characters and lore, or get recommendations that go beyond "top rated". Answers are grounded in live data from AniList and Fandom wikis, and story spoilers are blurred until you click them.

<img width="1366" height="687" alt="image" src="https://github.com/user-attachments/assets/fa3b8caf-6892-4175-8d94-ca67c042f586" />


**Live demo:** [[LIVE_DEMO_URL]](https://otaku-assistant.onrender.com/)

> Built with no paid APIs: free AniList GraphQL data, Fandom wikis, and a small open model on Groq.

## What it does

- **Anime Q&A.** "What is Attack on Titan about?", "Who is the villain in Naruto?"
- **Character lookup.** "Who is Levi Ackerman?" works from the character's AniList profile. If a name belongs to several characters ("Ichigo"), the bot says so, lists the options, and still answers for the best-known one. Naming the anime ("Ichigo from Darling in the FranXX") or another character from the same show ("Does Ichigo end up with Orihime?") picks the right one.
- **Recommendations.**
  - By genre or theme: "suggest a mecha anime", "recommend something scary", "an isekai anime".
  - Similar to something you liked: "anime like Horimiya".
  - Beginner picks and top-rated lists.
- **Lore from Fandom wikis.** Deeper questions pull a relevant wiki section, chosen by the model from the page's real section titles.
- **Spoiler protection.** Outcomes (who dies, who ends up together) are wrapped so the UI blurs them. The answer itself is hidden, not just the details.
- **Conversation memory** per chat thread, so follow-ups like "what about his friend Erwin?" work.

## How it works

```
User message
    |
    v
classify_message (1 LLM call) --> general_chat --> canned reply
    |
    +--> recommendation --> genre / tag / similar-to / top-rated --> AniList --> ranked picks
    |
    +--> anime_qa
            |
            v
         extract_query_info (1 LLM call): character, anime, other names mentioned, topic
            |
            v
         resolve_anime / resolve_character --> AniList
            |
            v
         fetch_wiki_context --> Fandom wiki (best effort)
            |
            v
         generate_answer (1 LLM call), grounded in AniList + wiki context
```

## Tech stack

- **Backend:** Python, Flask, Gunicorn
- **LLM:** Groq (`openai/gpt-oss-20b`)
- **Data:** AniList GraphQL API, Fandom MediaWiki API
- **Frontend:** vanilla JavaScript, HTML, CSS (no framework)
- **Other:** Flask-Limiter for rate limiting, `ThreadPoolExecutor` for parallel API calls

## Project structure

```
anime-chatbot/
├── app.py                        Flask routes and orchestration only
├── config.py                     API URLs and settings, loaded from .env
├── services/
│   ├── anilist_service.py        AniList queries, with an on-disk cache and 429 retry
│   ├── llm_service.py            all Groq calls: classify, extract, answer, wiki section pick
│   ├── resolver.py               turns a name into an anime or character record
│   ├── recommendation_service.py genre / tag / similar-to ranking and de-duplication
│   └── wiki_service.py           Fandom wiki discovery, search and extraction
├── templates/index.html
└── static/{css/styles.css, js/chat.js}
```

## Run it locally

You need Python 3 and a free Groq API key.

```powershell
git clone https://github.com/TheShadowMonarch007/Otaku-Assistant.git
cd Otaku-Assistant
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create a `.env` file in the project root:

```
GROQ_API_KEY=your_key_here
```

Then run:

```powershell
python app.py
```

and open http://127.0.0.1:6001. Set `FLASK_DEBUG=1` first if you want auto-reload while developing.

For production the app is served with:

```
gunicorn app:app --workers 1 --threads 4 --timeout 120
```

One worker is deliberate: conversation state lives in memory, and separate workers would each have their own copy.

## Design decisions

- **LLM-based routing instead of regex.** Intent classification and entity extraction are model calls. Hand-written regex and stopword rules kept failing on messy, typo-heavy questions.
- **Franchise de-duplication from AniList relations.** Title-text tricks (stripping "Season 2") can't handle titles like "Re:ZERO" or subtitle-only sequels. Relations, merged by connectivity, can. A title-similarity check catches franchises that AniList links incompletely, such as Monogatari.
- **Data-driven similarity.** Similar-to ranking blends tag overlap (weighted by how rare each tag is among comparable shows), genre overlap and rating. This is what keeps a wholesome romance from being matched with a heavy drama that only shares two genres. Nothing in it is hard-coded per anime.
- **Character disambiguation by fame.** Same-name characters are ranked by AniList favourites. Others are only mentioned if they have at least 10% of the top match's favourites, so "Levi" doesn't trigger a pointless question but "Ichigo" does.
- **Respecting free APIs.** All AniList traffic goes through one function with an on-disk cache and retry on HTTP 429. Independent requests run in parallel.
- **Spoilers as a UI feature.** The model wraps spoilers in `||double pipes||`, and the frontend renders them as click-to-reveal blur boxes. AniList's own `~!spoiler!~` tags are converted to the same format.

## Known limitations

- **Spoiler tagging is a model judgment.** It usually works but is not guaranteed.
- **Wiki retrieval is best effort.** Fandom has no cross-wiki search API, so the wiki is found by guessing slugs, which has gaps for obscure titles and for some long titles. Wiki structure also varies a lot between wikis.
- **Answers can still contain mistakes.** A small open model sometimes garbles details even when given the source text.
- **Recommendations come from metadata, not taste.** Tags and genres can't fully capture tone, so some picks will be debatable. I checked the ranking by hand across 20 well-known titles, which is not a rigorous evaluation.
- **Alternate spellings AniList doesn't index** (for example "Rivaille" for Levi) can't be resolved.
- **One anime per question.** Comparing two shows is not supported yet.
- **Chats live in memory** and reset when the server restarts. The AniList cache is also cleared on redeploy.

## Possible next steps

- Compare two anime or two characters
- Persistent chat storage and a pre-warmed cache on startup
- A "did you mean" follow-up that remembers the original question
- A proper offline evaluation set for recommendations

## Credits

Anime and character data from [AniList](https://anilist.co). Lore from [Fandom](https://www.fandom.com) wikis. Language model served by [Groq](https://groq.com). This is an unofficial fan project and is not affiliated with any of them.
