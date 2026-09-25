#!/usr/bin/env python3
"""
v2_to_legacy.py - convert a Twitter API v2 tweet JSON (the kind the Wayback
Machine serves for archived tweets) into the internal GraphQL
"TweetResultByRestId" shape that tw2img.py actually parses.

Usage:
    python3 v2_to_legacy.py input_v2.json output_legacy.json
    python3 v2_to_legacy.py input_v2.json -            # print to stdout

Then feed the output straight into tw2img.py:
    python3 tw2img.py output_legacy.json out.png
"""
import sys, json
from datetime import datetime, timezone


def iso_to_legacy_date(iso):
    if not iso:
        return ""
    dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return dt.strftime("%a %b %d %H:%M:%S +0000 %Y")


def build_user_legacy(user):
    """v2 'user' object (from includes.users) -> legacy user 'result'."""
    if not user:
        return {"result": {}}
    return {
        "result": {
            "__typename": "User",
            "rest_id": user.get("id", ""),
            "is_blue_verified": user.get("verified", False),
            "legacy": {
                "name": user.get("name", ""),
                "screen_name": user.get("username", ""),
                "profile_image_url_https": user.get("profile_image_url", ""),
                "verified_type": None,
            },
        }
    }


def convert_entities(v2_entities, full_text):
    v2_entities = v2_entities or {}
    legacy = {"user_mentions": [], "urls": [], "hashtags": [], "symbols": []}

    for m in v2_entities.get("mentions", []):
        legacy["user_mentions"].append({
            "screen_name": m.get("username", ""),
            "name": m.get("username", ""),  # v2 doesn't give display name here
            "indices": [m.get("start", 0), m.get("end", 0)],
        })

    for u in v2_entities.get("urls", []):
        legacy["urls"].append({
            "url": u.get("url", ""),
            "expanded_url": u.get("expanded_url", u.get("unwound_url", "")),
            "display_url": u.get("display_url", ""),
            "indices": [u.get("start", 0), u.get("end", 0)],
        })

    for h in v2_entities.get("hashtags", []):
        legacy["hashtags"].append({
            "text": h.get("tag", ""),
            "indices": [h.get("start", 0), h.get("end", 0)],
        })

    return legacy


def find_included_tweet(includes, tweet_id):
    for t in (includes or {}).get("tweets", []):
        if t.get("id") == tweet_id:
            return t
    return None


def find_included_user(includes, user_id):
    for u in (includes or {}).get("users", []):
        if u.get("id") == user_id:
            return u
    return None


def convert_tweet(tweet, includes):
    """v2 tweet object -> legacy-shaped GraphQL tweet 'result'."""
    author = find_included_user(includes, tweet.get("author_id"))
    pm = tweet.get("public_metrics", {}) or {}

    in_reply_to_id = ""
    in_reply_to_sn = ""
    quoted_result = None
    for ref in tweet.get("referenced_tweets", []) or []:
        if ref.get("type") == "replied_to":
            in_reply_to_id = ref.get("id", "")
            parent = find_included_tweet(includes, in_reply_to_id)
            if parent:
                parent_author = find_included_user(includes, parent.get("author_id"))
                if parent_author:
                    in_reply_to_sn = parent_author.get("username", "")
        elif ref.get("type") == "quoted":
            qt = find_included_tweet(includes, ref.get("id"))
            if qt:
                quoted_result = convert_tweet(qt, includes)

    legacy = {
        "id_str": tweet.get("id", ""),
        "full_text": tweet.get("text", ""),
        "created_at": iso_to_legacy_date(tweet.get("created_at", "")),
        "reply_count": pm.get("reply_count", 0),
        "retweet_count": pm.get("retweet_count", 0),
        "quote_count": pm.get("quote_count", 0),
        "favorite_count": pm.get("like_count", 0),
        "lang": tweet.get("lang", ""),
        "entities": convert_entities(tweet.get("entities"), tweet.get("text", "")),
        "in_reply_to_status_id_str": in_reply_to_id,
        "in_reply_to_screen_name": in_reply_to_sn,
    }

    result = {
        "__typename": "Tweet",
        "rest_id": tweet.get("id", ""),
        "core": {"user_results": build_user_legacy(author)},
        "legacy": legacy,
        "views": {"count": pm.get("impression_count", 0)},
        "source": "",
    }
    if quoted_result:
        result["quoted_status_result"] = {"result": quoted_result}
    return result


def collect_reply_chain(tweet, includes):
    chain = [tweet]
    seen = {tweet.get("id")}
    cur = tweet
    while True:
        parent_id = None
        for ref in cur.get("referenced_tweets", []) or []:
            if ref.get("type") == "replied_to":
                parent_id = ref.get("id")
                break
        if not parent_id or parent_id in seen:
            break
        parent = find_included_tweet(includes, parent_id)
        if not parent:
            break
        chain.insert(0, parent)
        seen.add(parent_id)
        cur = parent
    return chain


def build_threaded_conversation(chain, includes):
    entries = []
    for t in chain:
        result = convert_tweet(t, includes)
        entries.append({
            "entryId": f"tweet-{t.get('id', '')}",
            "content": {"itemContent": {"tweet_results": {"result": result}}},
        })
    return {
        "data": {
            "threaded_conversation_with_injections_v2": {
                "instructions": [
                    {"type": "TimelineAddEntries", "entries": entries}
                ]
            }
        }
    }


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    in_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else "-"

    with open(in_path, encoding="utf-8") as f:
        wb = json.load(f)

    tweet = wb["data"]
    includes = wb.get("includes", {})

    chain = collect_reply_chain(tweet, includes)
    if len(chain) > 1:
        out = build_threaded_conversation(chain, includes)
    else:
        result = convert_tweet(tweet, includes)
        out = {"data": {"tweetResult": {"result": result}}}

    text = json.dumps(out, indent=2, ensure_ascii=False)
    if out_path == "-":
        print(text)
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
