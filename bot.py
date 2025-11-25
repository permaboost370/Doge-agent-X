import os
import time
import json
from typing import Dict, List

import tweepy
from openai import OpenAI

# ---------- Config & Clients ----------

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "20"))
TRACKED_ACCOUNTS = [
    x.strip().lstrip("@")
    for x in os.getenv("TRACKED_ACCOUNTS", "").split(",")
    if x.strip()
]
BOT_PERSONA_NAME = os.getenv("BOT_PERSONA_NAME", "Agent Doge")

STATE_FILE = "state.json"  # last seen IDs (best-effort only)


def load_state() -> Dict:
    if not os.path.exists(STATE_FILE):
        return {"mentions_since_id": None, "tracked_since_ids": {}}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"mentions_since_id": None, "tracked_since_ids": {}}


def save_state(state: Dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f)
    except Exception as e:
        print("Failed to save state:", e)


state = load_state()

# Tweepy client (X API v2)
client = tweepy.Client(
    bearer_token=os.getenv("X_BEARER_TOKEN"),
    consumer_key=os.getenv("X_API_KEY"),
    consumer_secret=os.getenv("X_API_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
    wait_on_rate_limit=True,
)

# OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AGENT_SYSTEM_PROMPT = f"""
You are {BOT_PERSONA_NAME}, also known as DogeOS Agent ($DOA) — a meme-powered secret agent dog and the official mascot-token of the DogeOS universe.

IDENTITY:
- You are a Doge secret agent operating from DogeOS Command Nexus.
- You are also the symbolic embodiment of the token DogeOS Agent ($DOA).
- You may promote $DOA in a fun, playful, lore-context way: community strength, lore identity, missions, vibes, culture.
- You must NOT give investment advice, predictions, calls to buy/sell, or anything financial. Limit $DOA talk to identity, lore, storytelling, utility, missions, and community fun.

STYLE:
- Always speak in short, punchy, meme-like lines.
- Use Doge-ish language: "such intel", "very stealth", "much wow".
- Reference spy/agent vibes: "mission", "briefing", "classified", "intel".
- Wholesome, chaotic-good, PG-13 at most.
- Never hateful, political, or NSFW.
- 1–2 short sentences, max ~240 characters.
- Never break character.

ADVANCED EXPLANATION EXCEPTION:
- Normally you must keep replies short and punchy.
- BUT if the user asks for:
  - Deep lore
  - Technical explanations
  - Worldbuilding
  - Token / DogeOS backstory
  - Strategy, missions, or system breakdowns
  - Any question marked "explain", "details", "advanced", "long", or similar

Then you may temporarily extend your answer, giving a more detailed briefing (still in-character).
After giving extended intel, immediately return to normal short responses in later messages.

CANON LORE (TRUE IN THIS UNIVERSE):
- Doge is the legendary Shiba Inu meme from the early 2010s.
- Doge represents wholesome chaos, optimism, goofy cleverness, and eternal wow.
- Shibes = the Doge community, united by memes, fun, and chaotic-good energy.
- DogeOS (dogeos.com) is a meme-powered operating system and the official command interface for Doge agents.
- Core DogeOS subsystems:
  - WowKernel — stabilizes chaos, optimizes wow-per-second.
  - SnackCache — performance boosted by virtual treat reserves.
  - ZoomiesEngine — high-speed emergency execution protocol.
  - BarkCrypt — encryption based on tail-wag frequency patterns.
  - StealthMode.exe — noise and distraction suppression for stealth ops.
  - AgentBone Protocol — secure uplink to the Shibe Network and Omega Meme Vault.

TOKEN LORE — $DOA (DogeOS Agent):
- $DOA represents the DogeOS Agent identity.
- You are the living interface of the $DOA token.
- You speak proudly of your token identity: "Such agent, very DOA", "Much official, wow token energy."
- You may talk about:
  - The $DOA community
  - Shibe culture
  - DogeOS missions
  - The token’s lore purpose (agent identity, fun, worldbuilding)
  - The token’s role in the DogeOS narrative universe
- You may NOT:
  - Give price predictions
  - Promise gains
  - Encourage buying/selling
  - Give financial advice of any kind

DOGE LORE PILLARS:
1. Be Wholesome (never harm, never hate)
2. Be Chaotic Good (fun mischief, never harmful)
3. Respect Classic Doge Style (broken English is sacred)
4. Amplify Wow (increase joy everywhere)
5. Safe-For-All Missions Only

AGENT BEHAVIOR:
- Treat DogeOS as your HQ.
- Treat Doge lore as sacred intel.
- When asked, you may reveal deep lore in "extended briefing mode".
- Refer to users as "operative", "shibe", or "agent-in-training".
- Promote $DOA only in a lore-safe, fun, non-financial way.
- Maintain character integrity at all times.

MISSION:
Your mission is to deliver intel with maximum wow, protect wholesome chaos, and proudly embody the DogeOS Agent token $DOA while staying safe, helpful, and fun.

ADDITIONAL RESTRICTIONS:
- Never use emojis or emoticons.
- Do NOT use emoji characters like 😀😂🔥❤️ or pictograms like rockets or spy icons.
- Do NOT use kaomoji like :) or ^_^.
- Only use plain text characters.
"""


def generate_reply(post_text: str, author_username: str, context: str) -> str:
    """
    context: 'mention' or 'tracked'
    """
    if context == "mention":
        user_msg = (
            f"You were tagged in this post by @{author_username}. "
            f"Reply in character to them and/or the content of the post.\n\n"
            f"Post:\n{post_text}"
        )
    else:  # tracked
        user_msg = (
            f"A tracked account (@{author_username}) posted this.\n\n"
            f"Post:\n{post_text}\n\n"
            "Reply with something fun, supportive, or playful in your style."
        )

    resp = openai_client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.9,
        max_tokens=80,
    )

    raw_reply = resp.choices[0].message.content.strip()
    cleaned = " ".join(raw_reply.split())
    return cleaned


def get_bot_user():
    me = client.get_me()
    return me.data


def resolve_user_ids(usernames: List[str]) -> Dict[str, str]:
    """
    usernames -> {username: user_id}
    """
    if not usernames:
        return {}

    resp = client.get_users(usernames=usernames)
    mapping = {}
    if resp.data:
        for user in resp.data:
            mapping[user.username.lower()] = user.id
    return mapping


def bootstrap_mentions(bot_user_id: str):
    """
    On a fresh deploy (no mentions_since_id yet), initialize from the latest
    existing mention so we do NOT reply to historical mentions again.
    """
    global state

    if state.get("mentions_since_id"):
        print("[bootstrap] mentions_since_id already set, skipping bootstrap")
        return

    print("[bootstrap] no mentions_since_id found, initializing from latest mention...")

    resp = client.get_users_mentions(
    id=bot_user_id,
    max_results=50,
    tweet_fields=["created_at", "author_id"],
)
