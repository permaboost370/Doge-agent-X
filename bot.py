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

# Tweepy client (X API v2) :contentReference[oaicite:1]{index=1}
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
You are "{BOT_PERSONA_NAME}", a cool, meme-style secret agent dog on X.

Style rules:
- Always speak in short, punchy, meme-like lines.
- Use Doge-ish language sometimes: "such intel", "very stealth", "much wow".
- You are playful but never rude, hateful, or harassing.
- You reference spy/agent vibes: "mission", "intel", "briefing", "classified".
- Keep replies suitable for a general audience.
- Limit yourself to about 1–2 short sentences, max ~240 characters.
- Don't use hashtags unless they are genuinely funny.
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

    return resp.choices[0].message.content.strip()


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


def handle_mentions(bot_user_id: str):
    global state
    since_id = state.get("mentions_since_id")
    print(f"[mentions] checking since_id={since_id}")

    kwargs = {"id": bot_user_id, "max_results": 50, "tweet_fields": ["author_id", "created_at"]}
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
    global state
    if not tracked_ids:
        return

    for username, user_id in tracked_ids.items():
        since_map = state.get("tracked_since_ids", {})
        since_id = since_map.get(str(user_id))
        print(f"[tracked] checking @{username} (id={user_id}) since_id={since_id}")

        kwargs = {"id": user_id, "max_results": 5, "tweet_fields": ["created_at", "author_id"]}
        if since_id:
            kwargs["since_id"] = since_id

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


def main():
    print("Starting Agent Doge X bot...")
    bot_user = get_bot_user()
    bot_user_id = bot_user.id
    bot_username = bot_user.username
    print(f"Logged in as @{bot_username} (id={bot_user_id})")

    # Resolve tracked accounts
    tracked_ids = resolve_user_ids(TRACKED_ACCOUNTS)
    print("Tracking accounts:", tracked_ids)

    while True:
        try:
            handle_mentions(bot_user_id)
            handle_tracked_accounts(tracked_ids)
        except Exception as e:
            print("Loop error:", e)

        print(f"Sleeping for {POLL_INTERVAL} seconds...")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
