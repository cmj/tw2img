#!/usr/bin/env python3
"""
Convert an archive.org Wayback Machine page (which embeds a Twitter API v2
JSON blob inside a <pre> tag) into the internal tweet-dict shape that
tw2img.py's build_html()/render_png() expect, then render a PNG.

Usage:
    python3 wayback_convert.py <wayback.html> [output.png] [--html-only] [--light] ...
"""
import sys, os, re, json, html, argparse, asyncio
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tw2img as tw


def extract_pre_json(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    m = re.search(r"<pre[^>]*>(.*?)</pre>", raw, re.S)
    if not m:
        sys.exit("Error: no <pre> tag found in the given file.")
    text = html.unescape(m.group(1))
    return json.loads(text)


def _user_dict(u):
    """Build the internal user dict tw2img expects from a v2 `includes.users` entry."""
    if not u:
        return {"name": "Unknown", "screen_name": "unknown", "avatar_url": "",
                "is_blue_verified": False, "verified_type": None, "parody_label": None}
    avatar = (u.get("profile_image_url") or "").replace("_normal", "_bigger")
    verified_type = None
    if u.get("verified_type"):
        verified_type = u["verified_type"]
    elif u.get("verified"):
        verified_type = "Blue"
    return {
        "name": u.get("name", "Unknown"),
        "screen_name": u.get("username", "unknown"),
        "avatar_url": avatar,
        "is_blue_verified": bool(u.get("verified")),
        "verified_type": verified_type,
        "parody_label": None,
    }


def _media_item(m):
    """Convert a v2 `includes.media` entry into legacy extended_entities.media shape."""
    mtype = m.get("type", "photo")
    item = {
        "type": mtype,
        "media_url_https": m.get("url") or m.get("preview_image_url", ""),
        "sizes": {"large": {"w": m.get("width", 0), "h": m.get("height", 0)}},
        "original_info": {"width": m.get("width", 0), "height": m.get("height", 0)},
    }
    if mtype in ("video", "animated_gif"):
        variants = []
        for v in m.get("variants", []):
            variants.append({
                "content_type": v.get("content_type", ""),
                "url": v.get("url", ""),
                "bitrate": v.get("bit_rate", 0),
            })
        item["video_info"] = {
            "duration_millis": m.get("duration_ms", 0),
            "variants": variants,
        }
    return item


def build_tweet_dict(blob):
    d = blob["data"]
    includes = blob.get("includes", {})
    users_by_id = {u["id"]: u for u in includes.get("users", [])}
    media_by_key = {m["media_key"]: m for m in includes.get("media", [])}

    author = users_by_id.get(d.get("author_id"))
    user = _user_dict(author)

    v2_entities = d.get("entities", {}) or {}
    media_keys = set(d.get("attachments", {}).get("media_keys", []))

    # Split v2 "urls" into legacy "urls" (real links) vs "media" (t.co links
    # that point at attached media, which linkify() strips from the text).
    legacy_urls = []
    media_url_entries = []
    for u in v2_entities.get("urls", []):
        mk = u.get("media_key")
        if mk and mk in media_keys:
            media_url_entries.append({
                "url": u.get("url", ""),
                "media_url_https": (media_by_key.get(mk, {}).get("url")
                                     or media_by_key.get(mk, {}).get("preview_image_url", "")),
            })
        else:
            legacy_urls.append({
                "url": u.get("url", ""),
                "expanded_url": u.get("expanded_url", ""),
                "display_url": u.get("display_url", ""),
            })

    user_mentions = [
        {"screen_name": m.get("username", ""), "indices": [m.get("start", 0), m.get("end", 0)]}
        for m in v2_entities.get("mentions", [])
    ]

    entities = {
        "urls": legacy_urls,
        "user_mentions": user_mentions,
        "media": media_url_entries,
        "hashtags": [{"text": h.get("tag", "")} for h in v2_entities.get("hashtags", [])],
    }

    ext_media = [_media_item(media_by_key[mk]) for mk in media_keys if mk in media_by_key]
    ext_entities = {"media": ext_media}

    pm = d.get("public_metrics", {}) or {}

    nt = d.get("note_tweet") or {}
    full_text = nt.get("text") or d.get("text", "")

    created_at = d.get("created_at", "")
    if created_at:
        try:
            dt = datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
            created_at = dt.strftime("%a %b %d %H:%M:%S +0000 %Y")
        except ValueError:
            pass

    return {
        "id": d.get("id"),
        "user": user,
        "full_text": full_text,
        "entities": entities,
        "ext_entities": ext_entities,
        "media_attribution": None,
        "is_ai_media": False,
        "created_at": created_at,
        "reply_count": pm.get("reply_count", 0),
        "retweet_count": pm.get("retweet_count", 0),
        "quote_count": pm.get("quote_count", 0),
        "like_count": pm.get("like_count", 0),
        "view_count": pm.get("impression_count", 0),
        "source": "",
        "in_reply_to_id": "",
        "in_reply_to_sn": "",
        "lang": d.get("lang", ""),
        "is_rt": False,
        "rt_orig_sn": None,
        "quoted": None,
        "card": None,
        "poll": None,
        "birdwatch": "",
        "birdwatch_ents": [],
        "has_birdwatch_notes": False,
        "broadcast_card": None,
        "grok_question": "",
        "grok_answer": "",
        "rt_by_user": None,
    }


async def _amain():
    p = argparse.ArgumentParser(description="Render a Wayback-archived tweet JSON via tw2img")
    p.add_argument("input", help="Path to the saved Wayback Machine HTML page")
    p.add_argument("output", nargs="?", default=None, help="Output PNG path")
    p.add_argument("--light", action="store_true")
    p.add_argument("--no-source", action="store_true")
    p.add_argument("--width", type=int, default=598)
    p.add_argument("--no-retina", action="store_true")
    p.add_argument("--html-only", action="store_true")
    p.add_argument("--save-html", default=None, metavar="FILE")
    args = p.parse_args()

    blob = extract_pre_json(args.input)
    tweet = build_tweet_dict(blob)

    html_out = tw.build_html([tweet], light=args.light, no_source=True, width=args.width)

    if args.html_only:
        print(html_out)
        return

    if args.save_html:
        with open(args.save_html, "w", encoding="utf-8") as f:
            f.write(html_out)
        print(f"HTML saved to {args.save_html}")

    out = args.output or f"{tweet['user']['screen_name']}-{tweet['id']}.png"
    await tw.render_png(html_out, out, width=args.width, retina=not args.no_retina)
    print(f"{out} saved")


if __name__ == "__main__":
    asyncio.run(_amain())
