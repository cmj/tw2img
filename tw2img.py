#!/usr/bin/env python3
"""
tw2img.py - render a tweet as PNG using Playwright

Requirements: playwright only needed for PNG output
    pip install playwright && playwright install chromium

Usage:
    tw2img.py <id|url|json|-> [output.png] [options]

Notes:
    --guest for no authentication, won't see conversation context
    --user <screen_name> to fetch latest tweet from user
    export TWITTER_AUTH_TOKEN=<auth_token>
    export TWITTER_CSRF_TOKEN=<x_csrf_token>
        or use random $(openssl rand -hex 16)
"""

import sys, json, re, os, argparse, asyncio, tempfile, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

TWEET_DETAIL_URL    = "https://x.com/i/api/graphql/xIYgDwjboktoFeXe_fgacw/TweetDetail"
TWEET_RESULT_URL    = "https://api.twitter.com/graphql/2Acdg-VztGlHX7MjX67Ysw/TweetResultByRestId"
GUEST_TOKEN_URL     = "https://api.twitter.com/1.1/guest/activate.json"

TWEET_DETAIL_VARS   = lambda id: {"focalTweetId": id, "with_rux_injections": True,
    "rankingMode": "Likes", "includePromotedContent": False, "withCommunity": True,
    "withQuickPromoteEligibilityTweetFields": False, "withBirdwatchNotes": True, "withVoice": True}
TWEET_DETAIL_FEAT   = {"rweb_video_screen_enabled": False, "profile_label_improvements_pcf_label_in_post_enabled": True,
    "creator_subscriptions_tweet_preview_api_enabled": True, "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False, "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True, "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True, "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True, "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True, "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True, "longform_notetweets_inline_media_enabled": False,
    "responsive_web_enhance_cards_enabled": False, "verified_phone_label_enabled": False}
TWEET_DETAIL_FTOG   = {"withArticleRichContentState": True, "withArticlePlainText": False,
    "withArticleSummaryText": True, "withGrokAnalyze": False}

TWEET_RESULT_FEAT   = {"creator_subscriptions_tweet_preview_api_enabled": True,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True, "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True, "tweet_awards_web_tipping_enabled": False,
    "creator_subscriptions_quote_tweet_preview_enabled": False, "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True, "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True, "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True, "verified_phone_label_enabled": False,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True, "responsive_web_enhance_cards_enabled": False}

USER_BY_SCREEN_NAME_URL = "https://x.com/i/api/graphql/laYnJPCAcVo0o6pzcnlVxQ/UserByScreenName"
USER_TWEETS_URL         = "https://x.com/i/api/graphql/fgsimYxdCfQmTI_dtJsTXw/UserTweets"

USER_BY_SCREEN_NAME_FEAT = {"hidden_profile_subscriptions_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True, "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True, "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True}

USER_TWEETS_FEAT = {"rweb_video_screen_enabled": False, "payments_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True, "rweb_tipjar_consumption_enabled": True,
    "verified_phone_label_enabled": False, "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False, "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True, "responsive_web_jetfuel_frame": False,
    "responsive_web_grok_share_attachment_enabled": True, "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True, "tweet_awards_web_tipping_enabled": False,
    "responsive_web_grok_show_grok_translated_post": False,
    "responsive_web_grok_analysis_button_from_backend": True,
    "creator_subscriptions_quote_tweet_preview_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True, "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True, "longform_notetweets_inline_media_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True, "responsive_web_enhance_cards_enabled": False}

def _req(url, headers, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def get_guest_token():
    req = urllib.request.Request(GUEST_TOKEN_URL, method="POST",
        headers={"Authorization": f"Bearer {BEARER}", "User-Agent": UA})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["guest_token"]

def auth_headers(auth_token, csrf_token):
    return {"Authorization": f"Bearer {BEARER}", "User-Agent": UA,
            "x-csrf-token": csrf_token, "x-twitter-active-user": "yes",
            "x-twitter-auth-type": "OAuth2Session", "x-twitter-client-language": "en",
            "Cookie": f"auth_token={auth_token}; ct0={csrf_token}"}

def guest_headers(guest_token):
    return {"Authorization": f"Bearer {BEARER}", "User-Agent": UA,
            "x-guest-token": guest_token}

def fetch_tweet_detail(tweet_id, auth_token, csrf_token):
    return _req(TWEET_DETAIL_URL, auth_headers(auth_token, csrf_token), {
        "variables": json.dumps(TWEET_DETAIL_VARS(tweet_id)),
        "features":  json.dumps(TWEET_DETAIL_FEAT),
        "fieldToggles": json.dumps(TWEET_DETAIL_FTOG),
    })

def fetch_tweet_result(tweet_id, headers):
    return _req(TWEET_RESULT_URL, headers, {
        "variables": json.dumps({"tweetId": tweet_id, "referrer": "home",
                                  "withCommunity": False, "includePromotedContent": False, "withVoice": False}),
        "features":  json.dumps(TWEET_RESULT_FEAT),
    })

def fetch_user_id(screen_name, headers):
    ubsn_headers = dict(headers)
    if "x-twitter-auth-type" in headers:
        ubsn_headers["Authorization"] = f"Bearer {BEARER}"
    data = _req(USER_BY_SCREEN_NAME_URL, ubsn_headers, {
        "variables": json.dumps({"screen_name": screen_name, "includePromotedContent": False, "withBirdwatchNotes": True, "withVoice": True}),
        "features":  json.dumps(USER_BY_SCREEN_NAME_FEAT),
    })
    return data["data"]["user"]["result"]["rest_id"]

def fetch_latest_tweet_id(user_id, headers):
    data = _req(USER_TWEETS_URL, headers, {
        "variables": json.dumps({"userId": user_id, "count": 20, "includePromotedContent": False,
                                  "withQuickPromoteEligibilityTweetFields": False, "withVoice": True}),
        "features":  json.dumps(USER_TWEETS_FEAT),
        "fieldToggles": json.dumps({"withArticlePlainText": False}),
    })
    instructions = data["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    for instr in instructions:
        if instr.get("type") != "TimelineAddEntries":
            continue
        for entry in instr.get("entries", []):
            eid = entry.get("entryId", "")
            if "pin" in eid or not eid.startswith("tweet-"):
                continue
            result = (entry.get("content", {}).get("itemContent", {})
                          .get("tweet_results", {}).get("result", {}))
            leg = result.get("legacy", {})
            if leg.get("retweeted_status_id_str") or leg.get("in_reply_to_status_id_str"):
                continue
            return result.get("rest_id")
    return None

def _parse_user(ur):
    res = ur.get("result", {})
    if not res: return {"name": "Unknown", "screen_name": "unknown", "avatar_url": ""}
    core = res.get("core", {})
    legacy = res.get("legacy", {})
    name = legacy.get("name") or core.get("name") or res.get("name", "Unknown")
    screen_name = legacy.get("screen_name") or core.get("screen_name") or res.get("screen_name", "unknown")
    av = res.get("avatar", {})
    avatar_url = av.get("image_url") or legacy.get("profile_image_url_https") or ""

    ver = res.get("verification", {}) or {}
    verified_type = ver.get("verified_type")

    if not verified_type:
        verified_type = legacy.get("verified_type") or res.get("verified_type")
    if not verified_type and ver.get("is_verified_business"):
        verified_type = "Business"

    return {
        "name":           name,
        "screen_name":    screen_name,
        "avatar_url":     avatar_url.replace("_normal", "_bigger"),
        "is_blue_verified": res.get("is_blue_verified", False),
        "verified_type":  verified_type,
    }

def _parse_tweet_result(result, user_parser):
    if not result or result.get("__typename") == "TweetTombstone":
        return None
    leg  = result.get("legacy", {})
    user = user_parser(result.get("core", {}).get("user_results", {}))

    quoted = None
    qt_res = result.get("quoted_status_result", {}).get("result") or \
             result.get("quoted_status_results", {}).get("result")
    if qt_res:
        quoted = _parse_tweet_result(qt_res, user_parser)

    rt_id = leg.get("retweeted_status_id_str")
    bw = result.get("birdwatch_pivot") or {}
    bw_note = bw.get("note", {}).get("text") or bw.get("subtitle", {}).get("text") or ""
    bw_ents = bw.get("note", {}).get("entities") or bw.get("subtitle", {}).get("entities") or []
    # Strip help.x.com links — appears as bare display URL (no protocol, may end with …)
    bw_note = re.sub(r'\s*https?://help\.x\.com\S*', '', bw_note)
    bw_note = re.sub(r'\s*help\.x\.com\S*', '', bw_note)

    return {
        "id":              result.get("rest_id"),
        "user":            user,
        "full_text":       leg.get("full_text", ""),
        "entities":        leg.get("entities", {}),
        "ext_entities":    leg.get("extended_entities") or leg.get("entities", {}),
        "created_at":      leg.get("created_at", ""),
        "reply_count":     leg.get("reply_count", 0),
        "retweet_count":   leg.get("retweet_count", 0),
        "quote_count":     leg.get("quote_count", 0),
        "like_count":      leg.get("favorite_count", 0),
        "view_count":      result.get("views", {}).get("count", 0),
        "source":          re.sub(r"<[^>]+>", "", result.get("source", "")),
        "source":          re.sub(r"(?i)^twitter\s+for\s+|^twitter\s*", "", re.sub(r"<[^>]+>", "", result.get("source", ""))),
        "in_reply_to_id":  leg.get("in_reply_to_status_id_str", ""),
        "in_reply_to_sn":  leg.get("in_reply_to_screen_name", ""),
        "is_rt":           bool(rt_id),
        "quoted":          quoted,
        "birdwatch":       bw_note,
        "birdwatch_ents":  bw_ents,
    }

def parse_tweet_detail(data, focal_id):
    instr   = data["data"]["threaded_conversation_with_injections_v2"]["instructions"]
    entries = next((i["entries"] for i in instr if i.get("type") == "TimelineAddEntries"), [])
    by_id = {}
    for e in entries:
        item   = e.get("content", {}).get("itemContent", {})
        result = item.get("tweet_results", {}).get("result", {})
        if not result: continue
        t = _parse_tweet_result(result, _parse_user)
        if t: by_id[t["id"]] = t

    chain = []
    cur   = by_id.get(focal_id)
    while cur:
        chain.insert(0, cur)
        parent_id = cur["in_reply_to_id"]
        cur = by_id.get(parent_id) if parent_id else None
    return chain if chain else list(by_id.values())

def parse_tweet_result_single(data):
    result = data["data"]["tweetResult"]["result"]
    if result.get("__typename") == "TweetTombstone":
        msg = result.get("tombstone", {}).get("text", {}).get("text", "This tweet is unavailable.")
        sys.exit(f"Error: {msg}")
    return [_parse_tweet_result(result, _parse_user)]

def fmt(n):
    n = int(n or 0)
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.1f}K"
    return str(n)

def rel_time(created_at):
    if not created_at: return ""
    dt   = datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y").replace(tzinfo=timezone.utc)
    diff = int((datetime.now(timezone.utc) - dt).total_seconds())
    if diff < 60:    return f"{diff}s"
    if diff < 3600:  return f"{diff//60}m"
    if diff < 86400: return f"{diff//3600}h"
    if diff < 86400*7: return f"{diff//86400}d"
    return dt.strftime("%b %d, %Y")

def abs_time(created_at):
    if not created_at: return ""
    dt = datetime.strptime(created_at, "%a %b %d %H:%M:%S +0000 %Y")
    return dt.strftime("%b %d, %Y · %I:%M %p UTC").replace(" 0", " ")

def linkify(text, entities):
    for u in entities.get("urls", []):
        text = text.replace(u["url"], f'<a href="{u["expanded_url"]}">{u["display_url"]}</a>')
    for m in entities.get("media", []):
        text = text.replace(m["url"], "")
    text = re.sub(r"#(\w+)", r'<a href="#">#\1</a>', text)
    text = re.sub(r"@(\w+)", r'<a href="#">@\1</a>', text)
    return text.strip()

def strip_all_lead_mentions(text, entities):
    mentions = sorted(entities.get("user_mentions", []), key=lambda x: x["indices"][0])
    reply_to_list = []
    current_pos = 0

    for m in mentions:
        start, end = m["indices"]
        if text[current_pos:start].strip() == "":
            reply_to_list.append(m["screen_name"])
            current_pos = end
        else:
            break

    return text[current_pos:].lstrip(), reply_to_list

def media_html(ext_entities):
    media_list = ext_entities.get("media", [])
    if not media_list:
        return ""

    parts = []
    for m in media_list:
        if m["type"] == "photo":
            parts.append(f'<div class="attachment"><img src="{m["media_url_https"]}"></div>')
        elif m["type"] in ("video", "animated_gif"):
            parts.append(f'''<div class="attachment video-wrap">
              <img src="{m["media_url_https"]}">
              <div class="play-btn"><div class="play-tri"></div></div>
            </div>''')

    if len(parts) == 4:
        return f'''<div class="media-grid-2x2">
            <div class="grid-item">{parts[0]}</div>
            <div class="grid-item">{parts[1]}</div>
            <div class="grid-item">{parts[2]}</div>
            <div class="grid-item">{parts[3]}</div>
        </div>'''
    else:
        return f'<div class="media-row">{"".join(parts)}</div>'
GLYPHS = {
    "comment": ("M1000 350q0-97-67-179t-182-130-251-48q-39 0-81 4-110-97-257-135-27-8-63-12-10-1-17 5t-10 16v1q-2 2 0 6t1 6 2 5l4 5t4 5 4 5q4 5 17 19t20 22 17 22 18 28 15 33 15 42q-88 50-138 123t-51 157q0 73 40 139t109 115 163 76 197 28q135 0 251-48t182-130 67-179z", 1000),
    "retweet": ("M714 11q0-7-5-13t-13-5h-535q-5 0-8 1t-5 4-3 4-2 7 0 6v335h-107q-15 0-25 11t-11 25q0 13 8 23l179 214q11 12 27 12t28-12l178-214q9-10 9-23 0-15-11-25t-25-11h-107v-214h321q9 0 14-6l89-108q4-5 4-11z m357 232q0-13-8-23l-178-214q-12-13-28-13t-27 13l-179 214q-8 10-8 23 0 14 11 25t25 11h107v214h-322q-9 0-14 7l-89 107q-4 5-4 11 0 7 5 12t13 6h536q4 0 7-1t5-4 3-5 2-6 1-7v-334h107q14 0 25-11t10-25z", 1071),
    "quote":   ("M18 685l335 0 0-334q0-140-98-238t-237-97l0 111q92 0 158 65t65 159l-223 0 0 334z m558 0l335 0 0-334q0-140-98-238t-237-97l0 111q92 0 158 65t65 159l-223 0 0 334z", 928),
    "heart":   ("M790 644q70-64 70-156t-70-158l-360-330-360 330q-70 66-70 158t70 156q62 58 151 58t153-58l56-52 58 52q62 58 150 58t152-58z", 860),
    "views":   ("M180 516l0-538-180 0 0 538 180 0z m250-138l0-400-180 0 0 400 180 0z m250 344l0-744-180 0 0 744 180 0z", 680),
    "group":   ("M0 106l0 134q0 26 18 32l171 80q-66 39-68 131 0 56 35 103 37 41 90 43 31 0 63-19-49-125 23-237-12-11-25-19l-114-55q-48-23-52-84l0-143-114 0q-25 0-27 34z m193-59l0 168q0 27 22 37l152 70 57 28q-37 23-60 66t-22 94q0 76 46 130t110 54 109-54 45-130q0-105-78-158l61-30 146-70q24-10 24-37l0-168q-2-37-37-41l-541 0q-14 2-24 14t-10 27z m473 330q68 106 22 231 31 19 66 21 49 0 90-43 35-41 35-103 0-82-65-131l168-80q18-10 18-32l0-134q0-32-27-34l-118 0 0 143q0 57-50 84l-110 53q-15 8-29 25z", 1000),
}

def icon_svg(name, size=13, color="currentColor"):
    d, adv = GLYPHS[name]
    w = adv * size / 1000
    return (f'<svg width="{w:.1f}" height="{size}" viewBox="0 0 {adv} 1000" '
            f'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle;flex-shrink:0">'
            f'<g transform="scale(1,-1) translate(0,-850)"><path d="{d}" fill="{color}"/></g></svg>')

def verified_svg(verified_type, is_blue):
    if is_blue and not verified_type:   fill, stroke = "#1d9bf0", "white"
    elif verified_type == "Business":   fill, stroke = "#e7b332", "black"
    elif verified_type == "Government": fill, stroke = "#829aab", "black"    
    else: return ""
    # 'stroke' is checkmark color, going with black instead of white for better contrast
    return (f'<svg width="12" height="12" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" '
            f'style="display:inline-block;vertical-align:middle;margin:0 0 4px 4px">'
            f'<circle cx="9" cy="9" r="9" fill="{fill}"/>'
            f'<polyline points="4.5,10 7.5,13 13.5,7" stroke="{stroke}" stroke-width="2.2" '
            f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

DARK_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    --bg:      #191919;
    --fg:      #FFFFFF;
    --grey:    #8899A6;
    --border:  #38444D;
    --link:    #80CEFF;
    --acc:     #2B608A;
    --play:    #3B5F78;
    --qt-bg:   #1e2732;
    --bw-bg:   #1c1f23;
    --bw-fg:   #5a6472;
    --bg-hover: #22262b;
    --accent:  #80CEFF;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
}
a { color: var(--link); text-decoration: none; }
"""

LIGHT_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
    --bg:      #ffffff;
    --fg:      #0f1419;
    --grey:    #536471;
    --border:  #cfd9de;
    --link:    #1d9bf0;
    --acc:     #cfd9de;
    --play:    #1d9bf0;
    --qt-bg:   #f7f9f9;
    --bw-bg:   #f0f2f4;
    --bw-fg:   #536471;
    --bg-hover: #e7eaed;
    --accent:  #1d9bf0;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 15px;
}
a { color: var(--link); text-decoration: none; }
"""

SHARED_CSS = """
.thread { padding: 0; }
.tweet-row { display: flex; padding: 12px 14px 0; }
.tweet-row:last-child { padding-bottom: 14px; }
.left-col { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; width: 46px; margin-right: 10px; }
.avatar { width: 46px; height: 46px; border-radius: 23px; display: block; }
.thread-line { width: 2px; flex: 1; min-height: 6px; background: var(--acc); margin: 3px 0; }
.right-col { flex: 1; overflow: hidden; padding-bottom: 12px; }
.tweet-row:last-child .right-col { padding-bottom: 0; }
.tweet-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 1px; }
.tweet-header-left { display: flex; align-items: center; flex-wrap: wrap; flex: 1; overflow: hidden; }
.tweet-header-left > * { margin-right: 0; }
.fullname { font-weight: 700; font-size: 15px; white-space: nowrap; }
.username { color: var(--grey); font-size: 14px; white-space: nowrap; padding-left: 4px; }
.tweet-time { color: var(--grey); font-size: 14px; white-space: nowrap; flex-shrink: 0; margin-left: 8px; }
.replying-to { color: var(--grey); font-size: 13px; margin-bottom: 3px; line-height: 1.4; }
.tweet-content { font-size: 15px; line-height: 1.5; margin: 4px 0 0; white-space: pre-wrap; word-wrap: break-word; }
.focal .tweet-content { font-size: 17px; }
.focal .tweet-date { color: var(--grey); font-size: 13px; margin-bottom: 0; padding-top: 6px; }
.stats { display: flex; align-items: center; color: var(--grey); font-size: 13px; padding-top: 8px; }
.stat { display: flex; white-space: nowrap; margin-right: 14px; }
.stat svg { margin: 3px 4px 3px 0; }
.source { margin-left: auto; font-size: 12px; }
.media-row { display: flex; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-row .attachment { flex: 1; }
.media-row .attachment + .attachment { margin-left: 3px; }
.media-grid-2x2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-2x2 .grid-item { position: relative; overflow: hidden; }
.media-grid-2x2 .grid-item img { width: 100%; height: 100%; object-fit: cover; }
.attachment img { width: 100%; display: block; }
.video-wrap { position: relative; }
.play-btn { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; }
.play-btn > div { width: 42px; height: 42px; border-radius: 21px; background: var(--play); display: flex; align-items: center; justify-content: center; }
.play-tri { width:0; height:0; border-top:9px solid transparent; border-bottom:9px solid transparent; border-left:16px solid white; margin-left:3px; }
.quote-block { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin: 6px 0; background: var(--qt-bg); overflow: hidden; }
.quote-header { display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 4px; }
.quote-header > * { margin-right: 4px; }
.quote-avatar { width: 20px; height: 20px; border-radius: 10px; display: inline-block; }
.quote-name { font-weight: 700; font-size: 14px; margin-right: 0; }
.quote-sn { color: var(--grey); font-size: 13px; padding-left: 4px; }
.quote-time { color: var(--grey); font-size: 13px; margin-left: auto; }
.quote-text { font-size: 14px; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
.quote-media { margin-top: 6px; border-radius: 8px; overflow: hidden; }
.quote-media img { width: 100%; display: block; }
.birdwatch { border: 1px solid var(--border); border-radius: 10px; margin: 6px 0; background: var(--bw-bg); overflow: hidden; }
.community-note-header { background-color: var(--bg-hover); font-weight: 700; font-size: 13px; padding: 6px 10px 8px; display: flex; align-items: center; gap: 12px; color: var(--fg); }
.community-note-header .icon-container { flex-shrink: 0; color: var(--accent); }
.community-note-text { font-size: 13px; line-height: 1.45; color: var(--fg); white-space: pre-line; padding: 6px 10px 10px; }
"""

def quote_block_html(qt):
    if not qt: return ""
    u    = qt["user"]
    text = linkify(qt["full_text"], qt["entities"])
    vicon = verified_svg(u["verified_type"], u["is_blue_verified"])
    time  = rel_time(qt["created_at"])
    media = ""
    mlist = qt["ext_entities"].get("media", [])
    if mlist:
        m = mlist[0]
        media = f'<div class="quote-media"><img src="{m["media_url_https"]}"></div>'
    return f"""<div class="quote-block">
  <div class="quote-header">
    <img class="quote-avatar" src="{u["avatar_url"]}">
    <span class="quote-name">{u["name"]}</span>{vicon}
    <span class="quote-sn">@{u["screen_name"]}</span>
    <span class="quote-time">{time}</span>
  </div>
  <div class="quote-text">{text}</div>
  {media}
</div>"""

def tweet_row_html(t, is_parent=False, no_source=False):
    u      = t["user"]
    vicon  = verified_svg(u["verified_type"], u["is_blue_verified"])
    grey   = "var(--grey)"

    clean_text, reply_to_sns = strip_all_lead_mentions(t["full_text"], t["entities"])

    if not reply_to_sns and t["in_reply_to_sn"]:
        reply_to_sns = [t["in_reply_to_sn"]]

    tweet_text  = linkify(clean_text, t["entities"])
    media_block = media_html(t["ext_entities"])
    time_str    = rel_time(t["created_at"])
    row_class   = "tweet-row" + ("" if is_parent else " focal")

    replying = ""
    if reply_to_sns and not is_parent:
        links = " ".join([f'<a href="#">@{sn}</a>' for sn in reply_to_sns])
        replying = f'<div class="replying-to">Replying to {links}</div>'

    qt_html = quote_block_html(t["quoted"]) if t.get("quoted") else ""
    bw_html = ""
    if t.get("birdwatch"):
        bw_text = t["birdwatch"]
        # Linkify non-help.x.com URLs in the note using entity indices (process in reverse)
        ents = sorted([e for e in t.get("birdwatch_ents", [])
                       if "help.x.com" not in (e.get("ref", {}).get("url") or "")],
                      key=lambda e: e.get("fromIndex", 0), reverse=True)
        for e in ents:
            start, end = e.get("fromIndex"), e.get("toIndex")
            ref = e.get("ref", {})
            url = ref.get("expandedUrl") or ref.get("url", "")
            if start is not None and end is not None and url:
                bw_text = bw_text[:start] + f'<a href="{url}">{url}</a>' + bw_text[end:]
        bw_html = f'''<div class="birdwatch">
          <div class="community-note-header"><span class="icon-container">{icon_svg("group", 13, "var(--accent)")}</span> Community Note</div>
          <div class="community-note-text">{bw_text}</div>
        </div>'''
    src = "" if no_source else f'<span class="source">{t["source"]}</span>'
    stats = f"""<div class="stats">
      <span class="stat">{icon_svg("comment", 13, grey)} {fmt(t["reply_count"])}</span>
      <span class="stat">{icon_svg("retweet", 13, grey)} {fmt(t["retweet_count"])}</span>
      <span class="stat">{icon_svg("quote",   13, grey)} {fmt(t["quote_count"])}</span>
      <span class="stat">{icon_svg("heart",   13, grey)} {fmt(t["like_count"])}</span>
      <span class="stat">{icon_svg("views",   13, grey)} {fmt(t["view_count"])}</span>
      {src}
    </div>"""
    return f"""<div class="{row_class}">
  <div class="left-col">
    <img class="avatar" src="{u["avatar_url"]}">
    {"<div class='thread-line'></div>" if is_parent else ""}
  </div>
  <div class="right-col">
    <div class="tweet-header">
      <div class="tweet-header-left">
        <span class="fullname">{u["name"]}</span>{vicon}<span class="username">@{u["screen_name"]}</span>
      </div>
      <span class="tweet-time">{time_str}</span>
    </div>
    {replying}
    <div class="tweet-content">{tweet_text}</div>
    {media_block}
    {qt_html}
    {bw_html}
    {"" if is_parent else f'<div class="tweet-date">{abs_time(t["created_at"])}</div>'}
    {stats}
  </div>
</div>"""

def build_html(tweets, light=False, no_source=False, css_path=None, width=598):
    if css_path and Path(css_path).exists():
        base_css = Path(css_path).read_text()
    else:
        base_css = (LIGHT_CSS if light else DARK_CSS) + SHARED_CSS
    rows = []
    for i, t in enumerate(tweets):
        rows.append(tweet_row_html(t, is_parent=(i < len(tweets)-1), no_source=no_source))
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
{base_css}
body {{ width: {width}px; }}
</style></head><body>
<div class="thread">{"".join(rows)}</div>
</body></html>"""

async def render_png(html, output_path, width=598, retina=True):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        sys.exit("Error: playwright is required for PNG rendering.\n"
                 "Install it with: pip install playwright && playwright install chromium")
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        context = await browser.new_context(viewport={'width': width, 'height': 800})
        page = await context.new_page()
        await page.set_content(html, wait_until="networkidle")
        if retina:
            await page.evaluate("document.body.style.zoom = '2.0'")
        await asyncio.sleep(0.5)
        thread = page.locator(".thread")
        await thread.screenshot(path=output_path)
        await browser.close()

async def main():
    p = argparse.ArgumentParser(description="Render tweet as PNG via Playwright")
    p.add_argument("input",       nargs="?", default=None, help="Tweet ID, URL, JSON file, or - for stdin")
    p.add_argument("output",      nargs="?", help="Output PNG (default: <screen_name>-<id>.png)")
    p.add_argument("--user",      default=None, help="Fetch latest tweet from this screen_name")
    p.add_argument("--light",     action="store_true", help="Render image in light mode")
    p.add_argument("--no-source", action="store_true", help="Hide the device name used (iPhone, etc)")
    p.add_argument("--no-context",action="store_true", help="Only show focal tweet, no thread")
    p.add_argument("--no-retina", action="store_true", help="Generate a 50%% smaller image")
    p.add_argument("--guest",     action="store_true", help="Guest mode (no account needed)")
    p.add_argument("--width",     type=int, default=598)
    p.add_argument("--css",       default=None, help="Supply custom Nitter or similar css file")
    p.add_argument("--html-only", action="store_true", help="Print HTML to stdout instead of rendering PNG")
    p.add_argument("--save-html", help="Save HTML to this file instead of rendering PNG")
    p.add_argument("--dump-json", action="store_true", help="Print raw API JSON to stdout and exit")
    p.add_argument("--auth-token",default=os.environ.get("TWITTER_AUTH_TOKEN"), help="or use envar TWITTER_AUTH_TOKEN")
    p.add_argument("--csrf-token",default=os.environ.get("TWITTER_CSRF_TOKEN"), help="or use envar TWITTER_CSRF_TOKEN")
    args = p.parse_args()

    if not args.user and not args.input:
        sys.exit("Error: provide a tweet ID/URL/file or use --user <screen_name>")
    inp  = args.input or ""

    data = None

    if args.user:
        if args.guest:
            gt = get_guest_token()
            headers = guest_headers(gt)
        else:
            if not args.auth_token or not args.csrf_token:
                sys.exit("Error: --auth-token/--csrf-token required (or use --guest)")
            headers = auth_headers(args.auth_token, args.csrf_token)
        user_id = fetch_user_id(args.user, headers)
        tweet_id = fetch_latest_tweet_id(user_id, headers)
        if not tweet_id:
            sys.exit(f"Error: no suitable tweet found for @{args.user}")
        if args.guest:
            data = fetch_tweet_result(tweet_id, headers)
        else:
            data = fetch_tweet_detail(tweet_id, args.auth_token, args.csrf_token)
        inp = tweet_id
    elif inp == "-":
        data = json.load(sys.stdin)
    elif os.path.isfile(inp):
        with open(inp) as f:
            data = json.load(f)
    else:
        m = re.search(r"(\d+)", inp)
        if not m: sys.exit(f"Error: cannot parse tweet ID from: {inp}")
        tweet_id = m.group(1)

        if args.guest:
            gt = get_guest_token()
            data = fetch_tweet_result(tweet_id, guest_headers(gt))
        else:
            if not args.auth_token or not args.csrf_token:
                sys.exit("Error: --auth-token/--csrf-token required (or use --guest)")
            data = fetch_tweet_detail(tweet_id, args.auth_token, args.csrf_token)

    if args.dump_json:
        print(json.dumps(data, indent=2))
        return

    fid = None
    if not os.path.isfile(inp):
        m = re.search(r"(\d+)", inp)
        if m: fid = m.group(1)

    if "threaded_conversation_with_injections_v2" in data.get("data", {}):
        if not fid:
            instr = data["data"]["threaded_conversation_with_injections_v2"]["instructions"]
            entries = next((i["entries"] for i in instr if i.get("type") == "TimelineAddEntries"), [])
            tweets_list = [e for e in entries if e["entryId"].startswith("tweet-")]
            if tweets_list: fid = tweets_list[-1]["entryId"].replace("tweet-", "")
        tweets = parse_tweet_detail(data, fid)
    else:
        tweets = parse_tweet_result_single(data)

    if args.no_context:
        tweets = [tweets[-1]]

    tweet_id = tweets[-1]["id"]
    user_name = tweets[-1]["user"]["screen_name"]
    output = args.output or f"{user_name}-{tweet_id}.png"

    html = build_html(tweets, light=args.light, no_source=args.no_source, css_path=args.css, width=args.width)

    if args.save_html:
        with open(args.save_html, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"HTML saved to {args.save_html}")
        return

    if args.html_only:
        print(html)
        return

    await render_png(html, output, width=args.width, retina=not args.no_retina)
    print(f"{output} saved")

if __name__ == "__main__":
    asyncio.run(main())
