import os
from dotenv import load_dotenv

load_dotenv()

# AniList — no key required, public GraphQL endpoint
ANILIST_API_URL = "https://graphql.anilist.co"

# Groq — needed later for llm_service.py
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Fandom wiki base — we'll likely append the specific wiki subdomain per-anime later
FANDOM_API_SUFFIX = "/api.php"

GROQ_MODEL = "openai/gpt-oss-20b"