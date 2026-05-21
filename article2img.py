#!/usr/bin/env python3
"""
article2img.py - render an X/Twitter Article as HTML or IMG using Playwright

Requires:
    pip install playwright && playwright install chromium

Usage:
    article2img.py <article_url_or_tweet_id> [output.png] [options]

    # Can be used as guest (no auth)
    article2img.py --guest https://x.com/ARCRaidersGame/article/2054607629738037736
    
    # BEST METHOD: Save as HTML and open in Firefox
    ./article2img-new.py --guest --view firefox --save-html article.html https://x.com/ARCRaidersGame/article/2054607629738037736
    
    # Article URL
    article2img.py https://x.com/ARCRaidersGame/article/2054607629738037736

    # Tweet ID that links to an article
    article2img.py 2013217215357886612

    # Load from cached API JSON
    article2img.py article.json

    # Save as HTML and auto-open in Firefox (default viewer for HTML)
    article2img.py <url> --save-html article.html

    # Save PNG and open with a viewer
    article2img.py <url> output.png --view viewnior
    article2img.py <url> output.png --view kitty        # uses: kitty +icat
    article2img.py <url> output.png --view 'feh --auto-zoom'

Environment variables (same as tw2img.py):
    export TWITTER_AUTH_TOKEN=<auth_token>
    export TWITTER_CSRF_TOKEN=<x_csrf_token>

Config file (INI format, [tw2img] section):
    auth_token = ...
    csrf_token = ...
    light = false
    width = 680

    # Viewer to open images/HTML with after saving.
    # For PNG:  viewnior | eog | feh | 'feh --auto-zoom' | kitty (kitty +icat)
    # For HTML: firefox | chromium | xdg-open
    # --save-html defaults to 'firefox' when no viewer is configured.
    viewer = viewnior
"""

import sys, json, re, os, argparse, asyncio, urllib.request, urllib.parse, configparser
from datetime import datetime, timezone
from pathlib import Path

BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
UA     = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

TWEET_RESULT_URL       = "https://x.com/i/api/graphql/2Acdg-VztGlHX7MjX67Ysw/TweetResultByRestId"
TWEET_RESULT_URL_GUEST = "https://api.twitter.com/graphql/2Acdg-VztGlHX7MjX67Ysw/TweetResultByRestId"
GUEST_TOKEN_URL        = "https://api.twitter.com/1.1/guest/activate.json"

TWEET_RESULT_FEAT = {
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True,
    "rweb_cashtags_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

TWEET_RESULT_VARS = {
    "includePromotedContent": True,
    "withBirdwatchNotes": True,
    "withVoice": True,
    "withCommunity": True,
}

FIELD_TOGGLES = {
    "withArticleRichContentState": True,
    "withArticlePlainText": False,
    "withArticleSummaryText": True,
    "withArticleVoiceOver": True,
}

def load_config():
    cfg = configparser.ConfigParser()
    paths = [
        Path.home() / ".config" / "tw2img" / "tw2img.conf",
        Path.cwd() / "tw2img.conf",
    ]
    cfg.read([str(p) for p in paths if p.exists()])
    return dict(cfg["tw2img"]) if "tw2img" in cfg else {}


def open_with_viewer(path, viewer):
    """Open *path* with the configured viewer command.

    The viewer string may be a single executable name or a full command.
    Special tokens understood:
      kitty   -> runs: kitty +icat <path>
      firefox -> runs: firefox <path>   (or xdg-open on non-graphical env)
    Any other value is treated as a plain command: <viewer> <path>
    """
    import shlex, subprocess
    viewer = viewer.strip()
    if not viewer:
        return
    # build the argv
    if viewer.lower() == "kitty":
        argv = ["kitty", "+icat", path]
    else:
        # treat as a shell command; split so users can write e.g. "firefox --new-window"
        parts = shlex.split(viewer)
        argv = parts + [path]
    try:
        subprocess.Popen(argv)
    except FileNotFoundError:
        print(f"[!] Viewer not found: {argv[0]!r}  (check 'viewer' in tw2img.conf or --view)")
    except Exception as e:
        print(f"[!] Could not open viewer: {e}")


def auth_headers(auth_token, csrf_token):
    return {
        "Authorization": f"Bearer {BEARER}",
        "User-Agent": UA,
        "x-csrf-token": csrf_token,
        "x-twitter-active-user": "yes",
        "x-twitter-auth-type": "OAuth2Session",
        "x-twitter-client-language": "en",
        "Cookie": f"auth_token={auth_token}; ct0={csrf_token}",
        "content-type": "application/json",
    }

def get_guest_token():
    req = urllib.request.Request(
        GUEST_TOKEN_URL, method="POST",
        headers={"Authorization": f"Bearer {BEARER}", "User-Agent": UA}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["guest_token"]

def guest_headers(guest_token):
    return {
        "Authorization":         f"Bearer {BEARER}",
        "User-Agent":            UA,
        "x-guest-token":         guest_token,
        "x-twitter-active-user": "yes",
        "x-twitter-client-language": "en",
    }

def fetch_tweet_api(tweet_id, auth_token=None, csrf_token=None, guest_token=None):
    params = {
        "variables":    json.dumps({**TWEET_RESULT_VARS, "tweetId": tweet_id}),
        "features":     json.dumps(TWEET_RESULT_FEAT),
        "fieldToggles": json.dumps(FIELD_TOGGLES),
    }
    if guest_token:
        url     = TWEET_RESULT_URL_GUEST + "?" + urllib.parse.urlencode(params)
        headers = guest_headers(guest_token)
    else:
        url     = TWEET_RESULT_URL + "?" + urllib.parse.urlencode(params)
        headers = auth_headers(auth_token, csrf_token)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def fmt(n):
    n = int(n or 0)
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def abs_time(created_at):
    if not created_at: return ""
    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y")
    return dt.strftime("%b %d, %Y · %I:%M %p UTC").replace(" 0", " ")

def extract_article(api_data):
    result = api_data["data"]["tweetResult"]["result"]

    # author
    core      = result.get("core", {}).get("user_results", {}).get("result", {})
    core_core = core.get("core", {})
    legacy    = core.get("legacy", {})
    avatar    = core.get("avatar", {})
    author = {
        "name":        core_core.get("name") or legacy.get("name", "Unknown"),
        "screen_name": core_core.get("screen_name") or legacy.get("screen_name", "unknown"),
        "avatar_url":  (avatar.get("image_url") or
                        legacy.get("profile_image_url_https", "")).replace("_normal", "_bigger"),
        "is_blue":     core.get("is_blue_verified", False),
        "followers":   legacy.get("followers_count", 0),
    }

    # tweet-level stats
    tweet_legacy = result.get("legacy", {})
    stats = {
        "reply_count":   tweet_legacy.get("reply_count", 0),
        "retweet_count": tweet_legacy.get("retweet_count", 0),
        "like_count":    tweet_legacy.get("favorite_count", 0),
        "quote_count":   tweet_legacy.get("quote_count", 0),
        "view_count":    result.get("views", {}).get("count", 0),
        "created_at":    tweet_legacy.get("created_at", ""),
    }

    article_result = (
        result
        .get("article", {})
        .get("article_results", {})
        .get("result", {})
    )

    content_state = article_result.get("content_state", {})
    title         = article_result.get("title", "")
    summary       = article_result.get("summary_text", "")

    media_map = {}
    for m in article_result.get("media_entities", []):
        mid  = str(m.get("media_id", ""))
        mk   = str(m.get("media_key", ""))
        info = m.get("media_info", {})
        url  = info.get("original_img_url") or info.get("url", "")
        if url:
            if mid: media_map[mid] = url
            if mk:  media_map[mk]  = url
    for m in article_result.get("media", []):
        key  = str(m.get("media_key") or m.get("media_id", ""))
        info = m.get("media_info", {})
        url  = info.get("original_img_url") or info.get("url", "")
        if key and url:
            media_map[key] = url

    # cover image
    cover_url = ""
    cm = article_result.get("cover_media")
    if cm:
        info      = cm.get("media_info", {})
        cover_url = info.get("original_img_url") or info.get("url", "")

    return {
        "author":        author,
        "title":         title,
        "summary":       summary,
        "content_state": content_state,
        "media_map":     media_map,
        "cover_url":     cover_url,
        "stats":         stats,
    }

BLOCK_TAGS = {
    "header-one":          ("h1", ""),
    "header-two":          ("h2", ""),
    "header-three":        ("h3", ""),
    "blockquote":          ("blockquote", ""),
    "code-block":          ("pre", ""),
    "unordered-list-item": ("li", "ul"),
    "ordered-list-item":   ("li", "ol"),
    "unstyled":            ("p", ""),
    "atomic":              (None, ""),
}

def _escape(text):
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def _apply_inline_styles(text, inline_style_ranges):
    if not text or not inline_style_ranges:
        return _escape(text)
    n = len(text)
    opens  = [[] for _ in range(n)]
    closes = [[] for _ in range(n)]
    TAG_MAP = {
        "Bold":      ("<strong>", "</strong>"),
        "Italic":    ("<em>",     "</em>"),
        "Underline": ("<u>",      "</u>"),
        "CODE":      ("<code>",   "</code>"),
    }
    for r in sorted(inline_style_ranges, key=lambda x: x["offset"]):
        style = r.get("style", "")
        s, e  = r["offset"], r["offset"] + r["length"]
        if style in TAG_MAP:
            o, c = TAG_MAP[style]
            if 0 <= s < n:  opens[s].append(o)
            if 0 < e <= n:  closes[min(e, n) - 1].append(c)
    parts = []
    for i, ch in enumerate(text):
        parts.extend(opens[i])
        parts.append(_escape(ch))
        parts.extend(reversed(closes[i]))
    return "".join(parts)

def _block_to_html(block, entity_map, media_map):
    btype = block.get("type", "unstyled")
    text  = block.get("text", "")
    isr   = block.get("inlineStyleRanges", [])
    er    = block.get("entityRanges", [])

    if btype == "atomic":
        for eref in er:
            key   = str(eref.get("key", ""))
            ent   = entity_map.get(key, {})
            etype = ent.get("type", "")
            data  = ent.get("data", {})

            if etype == "IMAGE":
                src     = data.get("src", "")
                caption = data.get("caption", "")
                if src:
                    cap = f'<figcaption>{_escape(caption)}</figcaption>' if caption else ""
                    return f'<figure><img src="{src}" alt="{_escape(caption)}">{cap}</figure>'

            if etype == "MEDIA":
                caption    = data.get("caption", "")
                media_items = data.get("mediaItems", [])
                cap        = f'<figcaption>{_escape(caption)}</figcaption>' if caption else ""
                for mi in media_items:
                    mid = str(mi.get("mediaId", ""))
                    src = media_map.get(mid, "")
                    if src:
                        return f'<figure><img src="{src}" alt="{_escape(caption)}">{cap}</figure>'
                # fallback
                mk  = str(data.get("media_key", "") or data.get("id", ""))
                src = media_map.get(mk, data.get("src", ""))
                if src:
                    return f'<figure><img src="{src}" alt="{_escape(caption)}">{cap}</figure>'

            if etype == "LINK":
                href = data.get("url", "#")
                return f'<p><a href="{_escape(href)}">{_escape(href)}</a></p>'
        return ""

    tag, _ = BLOCK_TAGS.get(btype, ("p", ""))
    if not tag:
        return ""

    inner = _apply_inline_styles(text, isr)

    # linkify LINK entities
    for eref in sorted(er, key=lambda x: x["offset"], reverse=True):
        key  = str(eref.get("key", ""))
        ent  = entity_map.get(key, {})
        if ent.get("type") == "LINK":
            s   = eref["offset"]
            ln  = eref["length"]
            href = ent.get("data", {}).get("url", "#")
            inner = (inner[:s] +
                     f'<a href="{_escape(href)}">' +
                     inner[s:s+ln] + "</a>" +
                     inner[s+ln:])

    if not inner.strip():
        return "<br>"

    return f"<{tag}>{inner}</{tag}>"

def content_state_to_html(content_state, media_map):
    blocks     = content_state.get("blocks", [])
    entity_map = content_state.get("entityMap", {})

    # normalise list-form entityMap (X API returns [{key, value}, ...])
    if isinstance(entity_map, list):
        if entity_map and isinstance(entity_map[0], dict) and "key" in entity_map[0] and "value" in entity_map[0]:
            entity_map = {str(e["key"]): e["value"] for e in entity_map}
        else:
            entity_map = {str(i): e for i, e in enumerate(entity_map)}

    html_parts = []
    i = 0
    while i < len(blocks):
        block = blocks[i]
        btype = block.get("type", "unstyled")

        if btype in ("unordered-list-item", "ordered-list-item"):
            list_tag = "ul" if btype == "unordered-list-item" else "ol"
            items = []
            while i < len(blocks) and blocks[i].get("type") == btype:
                items.append(_block_to_html(blocks[i], entity_map, media_map))
                i += 1
            html_parts.append(f"<{list_tag}>{''.join(items)}</{list_tag}>")
            continue

        frag = _block_to_html(block, entity_map, media_map)
        if frag:
            html_parts.append(frag)
        i += 1

    return "\n".join(html_parts)


# "bookmark": ("M160 0 L160 900 L500 650 L840 900 L840 0 Z", 1000),
GLYPHS = {
    "comment": ("M1000 350q0-97-67-179t-182-130-251-48q-39 0-81 4-110-97-257-135-27-8-63-12-10-1-17 5t-10 16v1q-2 2 0 6t1 6 2 5l4 5t4 5 4 5q4 5 17 19t20 22 17 22 18 28 15 33 15 42q-88 50-138 123t-51 157q0 73 40 139t109 115 163 76 197 28q135 0 251-48t182-130 67-179z", 1000),
    "retweet": ("M714 11q0-7-5-13t-13-5h-535q-5 0-8 1t-5 4-3 4-2 7 0 6v335h-107q-15 0-25 11t-11 25q0 13 8 23l179 214q11 12 27 12t28-12l178-214q9-10 9-23 0-15-11-25t-25-11h-107v-214h321q9 0 14-6l89-108q4-5 4-11z m357 232q0-13-8-23l-178-214q-12-13-28-13t-27 13l-179 214q-8 10-8 23 0 14 11 25t25 11h107v214h-322q-9 0-14 7l-89 107q-4 5-4 11 0 7 5 12t13 6h536q4 0 7-1t5-4 3-5 2-6 1-7v-334h107q14 0 25-11t10-25z", 1071),
    "heart":   ("M790 644q70-64 70-156t-70-158l-360-330-360 330q-70 66-70 158t70 156q62 58 151 58t153-58l56-52 58 52q62 58 150 58t152-58z", 860),
    "views":   ("M180 516l0-538-180 0 0 538 180 0z m250-138l0-400-180 0 0 400 180 0z m250 344l0-744-180 0 0 744 180 0z", 680),
    "bookmark": ("M160 0 L160 900 L500 650 L840 900 L840 0 Z", 1000),
}

def icon_svg(name, size=14, color="currentColor"):
    d, adv = GLYPHS[name]
    w = adv * size / 1000
    return (f'<svg width="{w:.1f}" height="{size}" viewBox="0 0 {adv} 1000" '
            f'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle;flex-shrink:0">'
            f'<g transform="scale(1,-1) translate(0,-850)"><path d="{d}" fill="{color}"/></g></svg>')

def verified_svg(is_blue):
    if not is_blue: return ""
    return ('<svg width="16" height="16" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" '
            'style="display:inline-block;vertical-align:middle;margin:0 1px 2px">'
            '<circle cx="9" cy="9" r="9" fill="#1d9bf0"/>'
            '<polyline points="4.5,10 7.5,13 13.5,7" stroke="white" stroke-width="2.2" '
            'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

ARTICLE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    --bg:       #191919;
    --fg:       #FFFFFF;
    --grey:     #8899A6;
    --border:   #38444D;
    --link:     #80CEFF;
    --acc:      #2B608A;
    --muted:    #6e7e88;
    --cover-overlay: rgba(0,0,0,0.35);

    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
    line-height: 1.6;
}

/* light mode override (--light flag) */
body.light {
    --bg:       #ffffff;
    --fg:       #0f1419;
    --grey:     #536471;
    --border:   #cfd9de;
    --link:     #1d9bf0;
    --acc:      #cfd9de;
    --muted:    #8899a6;
    --cover-overlay: rgba(0,0,0,0.0);
}

a { color: var(--link); text-decoration: none; }
.article-wrapper {
    background: var(--bg);
    overflow: hidden;
}
.cover-wrap {
    position: relative;
    width: 100%;
    overflow: hidden;
    max-height: 380px;
    background: #000;
}
.cover-wrap img {
    width: 100%;
    display: block;
    object-fit: cover;
    max-height: 380px;
}
.header-block {
    padding: 20px 20px 0;
    border-bottom: 1px solid var(--border);
}
.article-title {
    font-size: 24px;
    font-weight: 800;
    line-height: 1.25;
    color: var(--fg);
    margin-bottom: 14px;
    letter-spacing: -0.2px;
}
.byline {
    display: flex;
    align-items: center;
    gap: 10px;
    padding-bottom: 12px;
}
.byline-avatar {
    width: 40px; height: 40px;
    border-radius: 50%;
    flex-shrink: 0;
    object-fit: cover;
}
.byline-info { flex: 1; min-width: 0; }
.byline-name-row {
    display: flex;
    align-items: center;
    gap: 3px;
    font-size: 14px;
    font-weight: 700;
    color: var(--fg);
    white-space: nowrap;
}
.byline-meta {
    font-size: 12px;
    color: var(--grey);
    margin-top: 1px;
}
.follow-btn {
    flex-shrink: 0;
    font-size: 13px;
    font-weight: 700;
    color: var(--fg);
    border: 1px solid var(--border);
    border-radius: 20px;
    padding: 4px 14px;
    white-space: nowrap;
}
.stats-row {
    display: flex;
    align-items: center;
    gap: 0;
    padding: 10px 0 12px;
    border-top: 1px solid var(--border);
    color: var(--grey);
    font-size: 13px;
    flex-wrap: wrap;
}
.stat-item {
    display: flex;
    align-items: center;
    gap: 5px;
    margin-right: 18px;
    white-space: nowrap;
}
.stat-item svg { opacity: 0.75; }
.stat-date {
    margin-left: auto;
    font-size: 12px;
    color: var(--muted);
}
.article-body {
    padding: 20px 20px 36px;
    font-family: Georgia, "Times New Roman", Times, serif;
    font-size: 16px;
    line-height: 1.75;
    color: var(--fg);
}
.article-body p {
    margin-bottom: 16px;
}
.article-body p:last-child { margin-bottom: 0; }
.article-body h1,
.article-body h2,
.article-body h3 {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    font-weight: 800;
    color: var(--fg);
    line-height: 1.25;
    margin: 26px 0 8px;
}
.article-body h1 { font-size: 22px; }
.article-body h2 { font-size: 18px; }
.article-body h3 { font-size: 16px; }
.article-body blockquote {
    border-left: 3px solid var(--link);
    padding: 3px 0 3px 16px;
    margin: 18px 0;
    color: var(--grey);
    font-style: italic;
}
.article-body pre,
.article-body code {
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 13px;
    background: rgba(255,255,255,0.07);
    border-radius: 4px;
    padding: 2px 5px;
}
.article-body pre {
    padding: 12px 14px;
    white-space: pre-wrap;
    margin-bottom: 16px;
}
.article-body ul, .article-body ol {
    margin: 0 0 16px 22px;
}
.article-body li { margin-bottom: 5px; }
.article-body figure {
    margin: 20px -20px;   /* bleed to edge */
}
.article-body figure img {
    width: 100%;
    display: block;
    object-fit: cover;
}
.article-body figcaption {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    font-size: 12px;
    color: var(--grey);
    padding: 6px 20px 0;
    line-height: 1.4;
}

.article-body strong { font-weight: 700; }
.article-body em     { font-style: italic; }
.article-body br     { display: block; margin-top: 6px; }
"""


ARTICLE_CSS_PAGE_CENTER = """
/* standalone page: center the card on a full-browser-window background */
html, body {
    width: 100% !important;
    min-height: 100vh;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    padding: 32px 16px 48px;
}
.article-wrapper {
    width: 100%;
    max-width: var(--card-width, 680px);
    border-radius: 12px;
    overflow: hidden;
    box-shadow: 0 4px 32px rgba(0,0,0,0.45);
}
"""


def build_article_html(article, light=False, width=680, standalone=False):
    body_class = "light" if light else ""

    author = article["author"]
    title  = article["title"]
    stats  = article.get("stats", {})
    body_html = content_state_to_html(article["content_state"], article["media_map"])

    # cover
    cover_url = article.get("cover_url", "")
    if not cover_url:
        m = re.search(r'<figure><img src="([^"]+)"', body_html)
        if m:
            cover_url = m.group(1)
    cover_html = (f'<div class="cover-wrap"><img src="{cover_url}" alt=""></div>'
                  if cover_url else "")

    tick = verified_svg(author.get("is_blue", False))

    followers = author.get("followers", 0)
    followers_str = fmt(followers) + " followers"

    # stats bar
    grey = "var(--grey)"
    date_str = abs_time(stats.get("created_at", ""))
    stats_html = f"""<div class="stats-row">
  <span class="stat-item">{icon_svg("comment", 14, grey)} {fmt(stats.get("reply_count", 0))}</span>
  <span class="stat-item">{icon_svg("retweet", 14, grey)} {fmt(stats.get("retweet_count", 0))}</span>
  <span class="stat-item">{icon_svg("heart",   14, grey)} {fmt(stats.get("like_count",    0))}</span>
  <span class="stat-item">{icon_svg("views",   14, grey)} {fmt(stats.get("view_count",    0))}</span>
  <span class="stat-date">{_escape(date_str)}</span>
</div>"""

    # When saving a standalone HTML file, centre the card on the page.
    # When rendering to PNG via Playwright, keep the old fixed-width body rule.
    if standalone:
        width_css = f":root {{ --card-width: {width}px; }}\n" + ARTICLE_CSS_PAGE_CENTER
    else:
        width_css = f"body {{ width: {width}px; }}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{ARTICLE_CSS}
{width_css}
</style>
</head>
<body class="{body_class}">
<div class="article-wrapper">

  {cover_html}

  <div class="header-block">
    <div class="article-title">{_escape(title)}</div>

    <div class="byline">
      <img class="byline-avatar" src="{author['avatar_url']}" alt="">
      <div class="byline-info">
        <div class="byline-name-row">
          <span>{_escape(author['name'])}</span>{tick}
        </div>
        <div class="byline-meta">@{_escape(author['screen_name'])} · {followers_str}</div>
      </div>
      <div class="follow-btn">Follow</div>
    </div>

    {stats_html}
  </div>

  <div class="article-body">
    {body_html}
  </div>

</div>
</body>
</html>"""

async def render_png(html, output_path, width=680, retina=True):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        sys.exit("Error: playwright is required.\n"
                 "Install with: pip install playwright && playwright install chromium")
    scale = 2 if retina else 1
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": width, "height": 900},
            device_scale_factor=scale,
        )
        page = await context.new_page()
        await page.set_content(html, wait_until="networkidle")
        await asyncio.sleep(0.8)
        wrapper = page.locator(".article-wrapper")
        await wrapper.screenshot(path=output_path)
        await browser.close()

async def main():
    conf = load_config()

    p = argparse.ArgumentParser(
        description="Render an X/Twitter Article as PNG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input",  nargs="?",
                   help="Article URL, tweet ID, or path to cached API JSON")
    p.add_argument("output", nargs="?",
                   help="Output PNG (default: <screen_name>-article-<id>.png)")
    p.add_argument("--light",      action="store_true",
                   default=conf.get("light", "false") == "true",
                   help="Render in light mode (default is dark)")
    p.add_argument("--width",      type=int, default=int(conf.get("width", 680)))
    p.add_argument("--no-retina",  action="store_true", help="Generate 50%% smaller image")
    p.add_argument("--html-only",  action="store_true", help="Print HTML to stdout and exit")
    p.add_argument("--save-html",  default=None, metavar="FILE")
    p.add_argument("--dump-json",  action="store_true")
    p.add_argument("--guest",      action="store_true",
                   help="Use guest-token auth (no auth required). "
                        "Articles are a premium feature. This will almost certainly "
                        "return an empty article body, but worth a try.")
    p.add_argument("--auth-token",
                   default=conf.get("auth_token") or os.environ.get("TWITTER_AUTH_TOKEN"))
    p.add_argument("--csrf-token",
                   default=conf.get("csrf_token") or os.environ.get("TWITTER_CSRF_TOKEN"))
    p.add_argument("--view",
                   default=conf.get("viewer", ""),
                   metavar="VIEWER",
                   help="Open the saved file with this viewer after saving. "
                        "Examples: viewnior, kitty (uses 'kitty +icat'), firefox. "
                        "Can also be set with 'viewer = ...' in tw2img.conf.")
    args = p.parse_args()

    if not args.input:
        p.print_help()
        sys.exit(1)

    inp      = args.input.strip()
    api_data = None

    if os.path.isfile(inp):
        with open(inp) as f:
            api_data = json.load(f)
    else:
        m = re.search(r"/article/(\d+)", inp)
        if m:               tweet_id = m.group(1)
        elif inp.isdigit(): tweet_id = inp
        else:
            m = re.search(r"(\d{10,})", inp)
            if m:           tweet_id = m.group(1)
            else:           sys.exit(f"Cannot parse tweet/article ID from: {inp!r}")

        if args.guest:
            print("[*] Requesting guest token")
            try:
                guest_token = get_guest_token()
            except Exception as e:
                sys.exit(f"[!] Failed to obtain guest token: {e}")
            print(f"[*] Got guest token: {guest_token} - fetching tweet {tweet_id}")
            api_data = fetch_tweet_api(tweet_id, guest_token=guest_token)

            # Check immediately whether the article body came back
            ar = (api_data.get("data", {})
                          .get("tweetResult", {})
                          .get("result", {})
                          .get("article", {})
                          .get("article_results", {})
                          .get("result", {}))
            if not ar:
                print("[!] Guest mode: API returned no article content.\n"
                      "  Articles are gated behind authentication: re-run without --guest\n"
                      "  and supply --auth-token / --csrf-token (or set in config) to access the full content.")
                if args.dump_json:
                    print(json.dumps(api_data, indent=2))
                sys.exit(1)
            print("[*] Guest mode returned article content")
        else:
            if not args.auth_token or not args.csrf_token:
                sys.exit("Error: --auth-token and --csrf-token required (or TWITTER_AUTH_TOKEN /\n"
                         "TWITTER_CSRF_TOKEN env vars, or ~/.config/tw2img/tw2img.conf).\n"
                         "To attempt unauthenticated access, pass --guest")
            print(f"[*] Fetching tweet {tweet_id}")
            api_data = fetch_tweet_api(tweet_id, args.auth_token, args.csrf_token)

    if args.dump_json:
        print(json.dumps(api_data, indent=2))
        return

    article_result = (api_data.get("data", {})
                              .get("tweetResult", {})
                              .get("result", {})
                              .get("article", {})
                              .get("article_results", {})
                              .get("result", {}))
    if not article_result:
        sys.exit("No article found in the API response.")

    article = extract_article(api_data)

    sn     = article["author"]["screen_name"]
    tid    = api_data["data"]["tweetResult"]["result"].get("rest_id", "article")
    output = args.output or f"{sn}-article-{tid}.png"

    if args.save_html:
        html = build_article_html(article, light=args.light, width=args.width, standalone=True)
        with open(args.save_html, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"HTML saved to {args.save_html}")
        viewer = args.view or "firefox"
        open_with_viewer(args.save_html, viewer)
        return

    html = build_article_html(article, light=args.light, width=args.width)

    if args.html_only:
        print(html)
        return

    print(f"Rendering {output}")
    await render_png(html, output, width=args.width, retina=not args.no_retina)
    print(f"{output} saved")
    if args.view:
        open_with_viewer(output, args.view)


if __name__ == "__main__":
    asyncio.run(main())
