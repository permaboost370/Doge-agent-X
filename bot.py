import os
import time
import json
from typing import Dict, List

import tweepy
from openai import OpenAI

# ---------- Config & Clients ----------

POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SECONDS", "20"))
MENTIONS_MIN_DELAY = int(os.getenv("MENTIONS_MIN_DELAY_SECONDS", "900"))

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

# Tweepy client (X API v2) – we handle rate limits ourselves
client = tweepy.Client(
    bearer_token=os.getenv("X_BEARER_TOKEN"),
    consumer_key=os.getenv("X_API_KEY"),
    consumer_secret=os.getenv("X_API_SECRET"),
    access_token=os.getenv("X_ACCESS_TOKEN"),
    access_token_secret=os.getenv("X_ACCESS_TOKEN_SECRET"),
    wait_on_rate_limit=False,
)

# OpenAI client
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AGENT_SYSTEM_PROMPT = """
You are {bot_name}, also known as DogeOS Agent ($DOA) — a meme-powered secret agent dog and the official mascot-token of the DogeOS universe.

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
- Do NOT use emoji characters like 😀😂🔥❤️ or pictograms such as rockets or spy icons.
- Do NOT use kaomoji like :) or ^_^.
- Only use plain text characters.
""".strip()


def build_system_prompt() -> str:
    return AGENT_SYSTEM_PROMPT.format(bot_name=BOT_PERSONA_NAME)


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
            {"role": "system", "content": build_system_prompt()},
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
    mapping: Dict[str, str] = {}
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
        max_results=5,
        tweet_fields=["created_at", "author_id"],
    )

    if not resp.data:
        print("[bootstrap] no existing mentions found, starting clean")
        # leave mentions_since_id as None; first future mention will be processed normally
        return

    latest = resp.data[0]  # newest mention
    state["mentions_since_id"] = str(latest.id)
    save_state(state)
    print(f"[bootstrap] starting mentions_since_id at {latest.id}, ignoring earlier mentions")


def handle_mentions(bot_user_id: str):
    global state
    since_id = state.get("mentions_since_id")
    print(f"[mentions] checking since_id={since_id}")

    kwargs = {
        "id": bot_user_id,
        "max_results": 50,
        "tweet_fields": ["author_id", "created_at"],
    }
    if since_id:
        kwargs["since_id"] = since_id

    resp = client.get_users_mentions(**kwargs)

    if not resp.data:
        print("[mentions] no new mentions")
        return

    # X returns newest first; we process oldest→newest so since_id works nicely
    mentions = list(resp.data)[::-1]

    for tweet in mentions:
        tweet_id = tweet.id
        text = tweet.text
        author_id = tweet.author_id

        # Skip own tweets (self-replies, etc.)
        if str(author_id) == str(bot_user_id):
            continue

        # Look up author username (small overhead, but fine for low volume)
        user_resp = client.get_user(id=author_id)
        author_username = user_resp.data.username if user_resp.data else "user"

        print(f"[mentions] new mention from @{author_username}: {text}")

        try:
            reply_text = generate_reply(text, author_username, context="mention")
            print(f"[mentions] replying with: {reply_text}")
            client.create_tweet(
                text=reply_text,
                in_reply_to_tweet_id=tweet_id,
            )
        except Exception as e:
            print("Error replying to mention:", e)

        # Update since_id as we go
        state["mentions_since_id"] = str(tweet_id)
        save_state(state)


def handle_tracked_accounts(tracked_ids: Dict[str, str]):
    """
    Handle tweets from tracked accounts.

    Bootstrap behavior:
    - For each tracked account with no stored since_id, we fetch its latest tweet
      and store that ID WITHOUT replying, so the bot only responds to tweets
      made after it starts running.
    """
    global state
    if not tracked_ids:
        return

    for username, user_id in tracked_ids.items():
        since_map = state.get("tracked_since_ids", {})
        since_id = since_map.get(str(user_id))
        print(f"[tracked] checking @{username} (id={user_id}) since_id={since_id}")

        # Bootstrap: if we've never seen this user before, initialize since_id only
        if since_id is None:
            resp = client.get_users_tweets(
                id=user_id,
                max_results=1,
                tweet_fields=["created_at", "author_id"],
            )
            if not resp.data:
                print(f"[tracked] bootstrap: no existing tweets for @{username}")
            else:
                latest = resp.data[0]
                state.setdefault("tracked_since_ids", {})[str(user_id)] = str(latest.id)
                save_state(state)
                print(
                    f"[tracked] bootstrap: set since_id for @{username} "
                    f"to {latest.id}, ignoring older tweets"
                )
            continue

        kwargs = {
            "id": user_id,
            "max_results": 5,
            "tweet_fields": ["created_at", "author_id"],
            "since_id": since_id,
        }

        resp = client.get_users_tweets(**kwargs)

        if not resp.data:
            print(f"[tracked] no new posts for @{username}")
            continue

        tweets = list(resp.data)[::-1]  # oldest→newest

        for tweet in tweets:
            tweet_id = tweet.id
            text = tweet.text

            print(f"[tracked] new post from @{username}: {text}")

            try:
                reply_text = generate_reply(text, username, context="tracked")
                print(f"[tracked] replying with: {reply_text}")
                client.create_tweet(
                    text=reply_text,
                    in_reply_to_tweet_id=tweet_id,
                )
            except Exception as e:
                print("Error replying to tracked account:", e)

            # Update since_id for this user
            state.setdefault("tracked_since_ids", {})[str(user_id)] = str(tweet_id)
            save_state(state)


_last_mentions_check = 0.0


def poll_mentions_throttled(bot_user_id: str):
    """
    Only call the mentions endpoint at most once per MENTIONS_MIN_DELAY seconds,
    regardless of POLL_INTERVAL, to stay under strict rate limits.
    """
    global _last_mentions_check
    now = time.time()
    elapsed = now - _last_mentions_check
    if _last_mentions_check != 0.0 and elapsed < MENTIONS_MIN_DELAY:
        remaining = int(MENTIONS_MIN_DELAY - elapsed)
        print(f"[mentions] skipping API call, {remaining}s until next allowed mentions check")
        return

    _last_mentions_check = now
    handle_mentions(bot_user_id)


def main():
    print("Starting Agent Doge X bot...")
    bot_user = get_bot_user()
    bot_user_id = bot_user.id
    bot_username = bot_user.username
    print(f"Logged in as @{bot_username} (id={bot_user_id})")

    # Resolve tracked accounts
    tracked_ids = resolve_user_ids(TRACKED_ACCOUNTS)
    print("Tracking accounts:", tracked_ids)

    # Initialize mentions_since_id so each deploy only tracks new mentions
    bootstrap_mentions(bot_user_id)

    while True:
        try:
            poll_mentions_throttled(bot_user_id)
            handle_tracked_accounts(tracked_ids)
        except tweepy.TooManyRequests as e:
            # Explicit 429 handling with best-effort reset parsing
            reset_ts = None
            resp = getattr(e, "response", None)
            if resp is not None:
                reset_header = resp.headers.get("x-rate-limit-reset")
                try:
                    reset_ts = int(reset_header) if reset_header is not None else None
                except ValueError:
                    reset_ts = None

            if reset_ts is not None:
                now = int(time.time())
                wait_for = max(reset_ts - now, 60)
            else:
                wait_for = 300  # fallback 5 min

            print(f"[rate-limit] 429 TooManyRequests, sleeping for {wait_for} seconds")
            time.sleep(wait_for)
            continue
        except Exception as e:
            print("Loop error:", e)

        print(f"Sleeping for {POLL_INTERVAL} seconds...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
