#!/usr/bin/env python3
"""
tw2img.py - render a tweet as PNG using Playwright

Requirements: playwright only needed for PNG output
    pip install playwright && playwright install chromium

Usage:
    tw2img.py <id|url|json|-> [output.png] [options]
    tw2img.py @username [output.png] [options]
    tw2img.py @username <1-20> [output.png] [options]

Notes:
    @username            implies --user; grabs the latest original tweet
    @username 3          grabs the 3rd most-recent original tweet (skips RTs/replies)
    @username 3 out.png  same, saves to out.png
    --guest for no authentication, won't see conversation context
    --user <screen_name> to fetch latest tweet from user
    export TWITTER_AUTH_TOKEN=<auth_token>
    export TWITTER_CSRF_TOKEN=<x_csrf_token>
        or use random $(openssl rand -hex 16)

Config file (INI format, [tw2img] section):
    Config is loaded in this order, later sources override earlier ones:
      1. ~/.config/tw2img/tw2img.conf   (user default, only if it exists)
      2. ./tw2img.conf                  (current directory, only if it exists)
      3. -c /path/to/custom.conf        (explicit override via -c / --config)
      4. CLI flags                      (always highest priority)
    Example:  tw2img.py 12345 -c ~/work/tw2img-work.conf --light

    Viewer settings (open saved file automatically):
      view   = true              # enable auto-open after saving
      viewer = viewnior          # PNG viewer  (default when --save-html not used)
      viewer = kitty +icat {}    # terminal inline display (kitty)
      viewer = eog               # GNOME image viewer
      viewer = firefox           # good default when saving as HTML (--save-html)
      viewer = xdg-open          # let the OS pick the right app
    Use {} as a placeholder for the filename; otherwise the path is appended.
"""

import sys, json, re, os, argparse, asyncio, tempfile, urllib.request, urllib.parse, configparser
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "tw2img" / "tw2img.conf"
CWD_CONFIG_PATH     = Path.cwd() / "tw2img.conf"

def load_config(extra_path=None):
    """Load and merge config files in priority order (later overrides earlier):
      1. ~/.config/tw2img/tw2img.conf   (user default, only if it exists)
      2. tw2img.conf in current directory (only if it exists)
      3. extra_path                     (supplied via -c / --config flag)
    Returns a merged dict of key->value strings from the [tw2img] section."""
    cfg = configparser.ConfigParser()
    sources = []
    if DEFAULT_CONFIG_PATH.exists():
        sources.append(DEFAULT_CONFIG_PATH)
    cwd_conf = Path.cwd() / "tw2img.conf"
    if cwd_conf.exists() and cwd_conf != DEFAULT_CONFIG_PATH:
        sources.append(cwd_conf)
    if extra_path:
        p = Path(extra_path).expanduser()
        if not p.exists():
            sys.exit(f"Error: config file not found: {p}")
        sources.append(p)
    cfg.read([str(s) for s in sources])
    return dict(cfg["tw2img"]) if "tw2img" in cfg else {}

def translate_text(text, source_lang, target_lang):
    """Translate *text* from *source_lang* to *target_lang* using deep-translator.

    Lazily imports deep-translator so it is not a hard requirement.
    Install with: pip install deep-translator

    Returns the translated string, or the original text on any error.
    source_lang / target_lang use BCP-47 / ISO 639-1 codes (e.g. 'ja', 'en', 'auto').
    Pass source_lang='auto' to let the library detect the language.
    """
    try:
        from deep_translator import GoogleTranslator
    except ImportError:
        sys.exit(
            "Error: deep-translator is required for translation.\n"
            "Install it with: pip install deep-translator"
        )
    try:
        translated = GoogleTranslator(source=source_lang, target=target_lang).translate(text)
        return translated or text
    except Exception as e:
        print(f"Warning: translation failed ({e}), using original text.", file=sys.stderr)
        return text


BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

TWEET_DETAIL_URL        = "https://x.com/i/api/graphql/xIYgDwjboktoFeXe_fgacw/TweetDetail"
TWEET_RESULT_URL        = "https://api.twitter.com/graphql/2Acdg-VztGlHX7MjX67Ysw/TweetResultByRestId"
USER_BY_SCREEN_NAME_URL = "https://x.com/i/api/graphql/laYnJPCAcVo0o6pzcnlVxQ/UserByScreenName"
USER_TWEETS_URL         = "https://x.com/i/api/graphql/fgsimYxdCfQmTI_dtJsTXw/UserTweets"
GUEST_TOKEN_URL         = "https://api.twitter.com/1.1/guest/activate.json"

TWEET_DETAIL_VARS   = lambda id: {"focalTweetId": id, "with_rux_injections": True,
    "rankingMode": "Likes", "includePromotedContent": False, "withCommunity": True,
    "withQuickPromoteEligibilityTweetFields": False, "withBirdwatchNotes": True, "withVoice": True}
TWEET_DETAIL_FEAT   = {"rweb_video_screen_enabled": False, "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False, "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False, "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False, "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True, "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True, "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True, "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True, "tweet_awards_web_tipping_enabled": False,
    "content_disclosure_indicator_enabled": True, "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True, "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True, "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True, "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True, "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": False,
    "responsive_web_enhance_cards_enabled": False}
TWEET_DETAIL_FTOG   = {"withArticleRichContentState": True, "withArticlePlainText": False,
    "withArticleSummaryText": True, "withArticleVoiceOver": True, "withGrokAnalyze": False,
    "withDisallowedReplyControls": False}

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

def open_with_viewer(path, viewer):
    """Open *path* with the configured viewer command.

    The viewer string may be:
      - A plain command name / path, e.g. ``viewnior`` or ``eog``
      - A command with a placeholder ``{}``, e.g. ``kitty +icat {}``
      - Multiple words without ``{}``, in which case the file is appended, e.g.
        ``firefox`` becomes ``firefox saved.html``

    The process is launched detached (fire-and-forget for GUI apps) or
    waited for in-place (terminal viewers such as ``kitty +icat``).
    """
    import shlex, subprocess
    viewer = viewer.strip()
    if "{}" in viewer:
        cmd = shlex.split(viewer.replace("{}", shlex.quote(str(path))))
    else:
        cmd = shlex.split(viewer) + [str(path)]

    # Terminal viewers (kitty +icat, chafa, viu, timg, etc) should run in-process so
    # their output appears in the current terminal; GUI apps are detached.
    terminal_hints = ("icat", "chafa", "viu", "catimg", "timg", "jp2a")
    is_terminal = any(h in viewer for h in terminal_hints)

    try:
        if is_terminal:
            subprocess.run(cmd)
        else:
            # Detach: don't wait, don't tie stdout/stderr to this process
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except FileNotFoundError:
        prog = cmd[0]
        print(f"Warning: viewer '{prog}' not found in PATH - skipping open.", file=sys.stderr)
    except Exception as e:
        print(f"Warning: could not open viewer: {e}", file=sys.stderr)


def _req(url, headers, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def resolve_output_path(path, mode):
    """Apply duplicate_files logic to *path*.

    mode values (from config/default):
      'overwrite'  - return path unchanged (default, existing file is replaced)
      'increment'  - if file exists, append -1, -2, ... before the extension
                     e.g. nasa-123.png -> nasa-123-1.png -> nasa-123-2.png
      'epoch'      - if file exists, append the current Unix epoch before the extension
                     e.g. nasa-123.png -> nasa-123-1779464539.png
    """
    import time as _time
    p = Path(path)
    if mode == "overwrite" or not p.exists():
        return path
    stem = p.stem
    suffix = p.suffix
    parent = p.parent
    if mode == "epoch":
        new_path = parent / f"{stem}-{int(_time.time())}{suffix}"
    else:  # increment
        counter = 1
        while True:
            new_path = parent / f"{stem}-{counter}{suffix}"
            if not new_path.exists():
                break
            counter += 1
    return str(new_path)

def resolve_url(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA}, method="HEAD")
        with urllib.request.urlopen(req) as r:
            return r.url
    except Exception:
        return url

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

def fetch_nth_tweet_id(user_id, headers, n=1):
    """Return the Nth original tweet (1-based) from UserTweets; skips RTs and replies."""
    data = _req(USER_TWEETS_URL, headers, {
        "variables": json.dumps({"userId": user_id, "count": 20, "includePromotedContent": False,
                                  "withQuickPromoteEligibilityTweetFields": False, "withVoice": True}),
        "features":  json.dumps(USER_TWEETS_FEAT),
        "fieldToggles": json.dumps({"withArticlePlainText": False}),
    })
    instructions = data["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    hits = []
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
            hits.append(result.get("rest_id"))
            if len(hits) >= n:
                return hits[-1]
    return hits[-1] if hits else None

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

def _extract_media_attribution(ext_entities, result=None, rt_result=None):
    """Return attribution user dict {name, screen_name, avatar_url, is_blue_verified}
    from additional_media_info.source_user (TweetResultByRestId) or
    card binding_values amplify_card_user_results (TweetDetail), or None."""

    def _user_from_result(res):
        if not res:
            return None
        leg = res.get("legacy", {})
        name        = leg.get("name", "")
        screen_name = leg.get("screen_name", "")
        avatar_url  = leg.get("profile_image_url_https", "").replace("_normal", "_bigger")
        is_blue     = res.get("is_blue_verified", False)
        ver         = res.get("verification", {}) or {}
        verified_type = ver.get("verified_type") or leg.get("verified_type") or res.get("verified_type")
        if not verified_type and ver.get("is_verified_business"):
            verified_type = "Business"
        if name and screen_name:
            return {"name": name, "screen_name": screen_name,
                    "avatar_url": avatar_url, "is_blue_verified": is_blue,
                    "verified_type": verified_type}
        return None

    # Gather all unique candidate media dictionaries to search through
    dicts_to_check = []
    if isinstance(ext_entities, dict):
        dicts_to_check.append(ext_entities)

    for r in [result, rt_result]:
        if isinstance(r, dict):
            tw = r.get("tweet") if "tweet" in r else r
            if isinstance(tw, dict):
                l = tw.get("legacy", {})
                if isinstance(l, dict):
                    if "extended_entities" in l and isinstance(l["extended_entities"], dict):
                        dicts_to_check.append(l["extended_entities"])
                    if "entities" in l and isinstance(l["entities"], dict):
                        dicts_to_check.append(l["entities"])

    # Path 1: additional_media_info.source_user on any found media item
    for d in dicts_to_check:
        for m in d.get("media", []):
            if isinstance(m, dict):
                src = (m.get("additional_media_info") or {}).get("source_user") or {}
                res = src.get("user_results", {}).get("result")
                u = _user_from_result(res)
                if u:
                    return u

    # Path 2: card binding_values amplify_card_user_results (TweetDetail)
    for r in [result, rt_result]:
        if isinstance(r, dict):
            tw = r.get("tweet") if "tweet" in r else r
            if isinstance(tw, dict):
                raw_card = (tw.get("card") or {}).get("legacy", {})
                if isinstance(raw_card, dict):
                    binding_values = raw_card.get("binding_values", [])
                    if isinstance(binding_values, list):
                        bv = {}
                        for b in binding_values:
                            if isinstance(b, dict) and "key" in b and "value" in b:
                                bv[b["key"]] = b["value"]
                        res = (bv.get("amplify_card_user_results", {})
                                 .get("user_value", {})
                                 .get("user_results", {})
                                 .get("result"))
                        u = _user_from_result(res)
                        if u:
                            return u

    return None

def _parse_tweet_result(result, user_parser):
    if not result or result.get("__typename") == "TweetTombstone":
        return None
    if "tweet" in result and not result.get("legacy"):
        result = result["tweet"]
    leg  = result.get("legacy", {})
    user = user_parser(result.get("core", {}).get("user_results", {}))

    quoted = None
    qt_res = result.get("quoted_status_result", {}).get("result") or \
             result.get("quoted_status_results", {}).get("result")
    if qt_res:
        if qt_res.get("__typename") == "TweetTombstone":
            tombstone_text = (qt_res.get("tombstone", {}).get("text", {}).get("text")
                              or "This tweet is unavailable.")
            permalink = leg.get("quoted_status_permalink", {})
            expanded = permalink.get("expanded", "")
            m_sn = re.search(r"(?:twitter|x)\.com/([^/]+)/status", expanded)
            sn = m_sn.group(1) if m_sn else ""
            quoted = {"__tombstone": True, "screen_name": sn, "text": tombstone_text}
        else:
            quoted = _parse_tweet_result(qt_res, user_parser)
    elif leg.get("quoted_status_id_str") and result.get("quoted_status_result") == {}:
        # Empty result object = tweet was deleted
        permalink = leg.get("quoted_status_permalink", {})
        expanded = permalink.get("expanded", "")
        m_sn = re.search(r"(?:twitter|x)\.com/([^/]+)/status", expanded)
        sn = m_sn.group(1) if m_sn else ""
        quoted = {"__tombstone": True, "screen_name": sn, "text": "This tweet is unavailable."}

    rt_id = leg.get("retweeted_status_id_str")

    rt_result = (result.get("retweeted_status_result") or
                 leg.get("retweeted_status_result") or
                 result.get("tweet", {}).get("retweeted_status_result") or {}).get("result", {})

    if not rt_id and rt_result:
        rt_id = rt_result.get("rest_id") or rt_result.get("legacy", {}).get("id_str") or "rt"
    rt_leg = rt_result.get("legacy", {}) if rt_result else {}

    if rt_id and rt_result:
        original = _parse_tweet_result(rt_result, user_parser)
        if original:
            original["rt_by_user"] = user
            return original

    rt_orig_sn = None
    if rt_id and not rt_result:
        import re as _re
        full_text = leg.get("full_text", "")
        _rt_m = _re.match(r"^RT @(\w+): ?", full_text)
        if _rt_m:
            rt_orig_sn = _rt_m.group(1)
        stripped = _re.sub(r"^RT @\w+: ?", "", full_text)
        leg = dict(leg, full_text=stripped)

    bw = result.get("birdwatch_pivot") or {}
    bw_note = bw.get("note", {}).get("text") or bw.get("subtitle", {}).get("text") or ""
    bw_ents = bw.get("note", {}).get("entities") or bw.get("subtitle", {}).get("entities") or []

    card = None
    raw_card = (result.get("card") or rt_result.get("card") or {}).get("legacy", {})
    if raw_card:
        bv = {b["key"]: b["value"] for b in raw_card.get("binding_values", [])}
        def sv(k): return bv.get(k, {}).get("string_value", "")
        def iv(k): return bv.get(k, {}).get("image_value", {})
        title   = sv("title")
        desc    = sv("description")
        domain  = sv("vanity_url") or sv("domain")
        url     = sv("card_url")
        img_url = (iv("summary_photo_image") or iv("thumbnail_image") or
                   iv("photo_image_full_size") or {}).get("url", "")
        is_player = bool(sv("player_url")) or raw_card.get("name", "") == "player"
        if is_player:
            img_url = (iv("player_image_large") or iv("player_image_original") or
                       iv("player_image") or {}).get("url", "") or img_url
            player_url = sv("app_url_resolved") or sv("player_url") or sv("card_url")
            card = {"title": title, "desc": desc, "domain": domain,
                    "url": player_url, "img_url": img_url, "is_player": True}
        elif title or desc:
            card = {"title": title, "desc": desc, "domain": domain,
                    "url": url, "img_url": img_url, "is_player": False}

    nt = (result.get("note_tweet") or {}).get("note_tweet_results", {}).get("result", {})
    if nt.get("text"):
        full_text = nt["text"]
        entities  = nt.get("entity_set") or nt.get("entities") or leg.get("entities", {})
    else:
        full_text = leg.get("full_text", "")
        entities  = leg.get("entities", {})

    broadcast_card = None
    raw_broadcast = result.get("card") or rt_result.get("card") or {}
    if raw_broadcast:
        bc_bv_raw = raw_broadcast.get("legacy", {}).get("binding_values", [])
        if isinstance(bc_bv_raw, list):
            bc_bv = {b["key"]: b["value"] for b in bc_bv_raw if "key" in b and "value" in b}
        else:
            bc_bv = bc_bv_raw or {}
        def bc_sv(k): return bc_bv.get(k, {}).get("string_value", "")
        def bc_iv(k): return bc_bv.get(k, {}).get("image_value", {}).get("url", "")
        bc_title = bc_sv("broadcast_title")
        bc_thumb = bc_iv("broadcast_thumbnail_large") or bc_iv("broadcast_thumbnail")
        bc_url   = bc_sv("broadcast_url")
        bc_name  = raw_broadcast.get("legacy", {}).get("name", "")
        if bc_thumb or bc_title or "broadcast" in bc_name:
            broadcast_card = {
                "title": bc_title,
                "image": bc_thumb,
                "url":   bc_url,
            }
    ext_entities = (
        leg.get("extended_entities")
        or rt_leg.get("extended_entities")
        or nt.get("extended_entities")
        or leg.get("entities", {})
        or rt_leg.get("entities", {})
    )

    media_attr = None
    is_ai_media = False
    for media_item in ext_entities.get("media", []):
        if media_item.get("grok_post_id"):
            is_ai_media = True
        src_user = (
            media_item.get("additional_media_info", {})
            .get("source_user", {})
            .get("user_results", {})
            .get("result")
        )

        if src_user:
            media_attr = _parse_user({"result": src_user})
            break

    if not media_attr:
        media_attr = _extract_media_attribution(
            ext_entities,
            result=result,
            rt_result=rt_result if rt_result else None,
        )

    return {
        "id":              result.get("rest_id"),
        "user":            user,
        "full_text":       full_text,
        "entities":        entities,
        "ext_entities":    ext_entities,
        "media_attribution": media_attr,
        "is_ai_media":     is_ai_media,
        "created_at":      leg.get("created_at", ""),
        "reply_count":     leg.get("reply_count", 0),
        "retweet_count":   leg.get("retweet_count", 0),
        "quote_count":     leg.get("quote_count", 0),
        "like_count":      leg.get("favorite_count", 0),
        "view_count":      result.get("views", {}).get("count", 0),
        "source":          re.sub(r"(?i)^twitter\s+for\s+|^twitter\s*", "", re.sub(r"<[^>]+>", "", result.get("source", ""))),
        "in_reply_to_id":  leg.get("in_reply_to_status_id_str", ""),
        "in_reply_to_sn":  leg.get("in_reply_to_screen_name", ""),
        "lang":            leg.get("lang", ""),
        "is_rt":           bool(rt_id),
        "rt_orig_sn":      rt_orig_sn,
        "quoted":          quoted,
        "card":            card,
        "birdwatch":       bw_note,
        "birdwatch_ents":  bw_ents,
        "broadcast_card":  broadcast_card,
        "rt_by_user":      None,
    }

def parse_tweet_detail(data, focal_id):
    instr   = data["data"]["threaded_conversation_with_injections_v2"]["instructions"]
    entries = next((i["entries"] for i in instr if i.get("type") == "TimelineAddEntries"), [])
    by_id = {}
    tombstones = {}  # id -> screen_name extracted from entry id
    for e in entries:
        item   = e.get("content", {}).get("itemContent", {})
        result = item.get("tweet_results", {}).get("result", {})
        if not result: continue
        if result.get("__typename") == "TweetTombstone":
            # entry id like "tweet-1234567" gives us the tweet id
            entry_id = e.get("entryId", "")
            m = re.search(r"tweet-(\d+)", entry_id)
            tid = m.group(1) if m else None
            if tid:
                tombstone_text = result.get("tombstone", {}).get("text", {}).get("text", "This tweet is unavailable.")
                tombstones[tid] = {"__tombstone": True, "id": tid, "text": tombstone_text, "screen_name": ""}
            continue
        entry_id = (result.get("legacy") or result.get("tweet", {}).get("legacy") or {}).get("id_str") or result.get("rest_id")
        t = _parse_tweet_result(result, _parse_user)
        if t:
            by_id[t["id"]] = t
            if entry_id and entry_id != t["id"]:
                by_id[entry_id] = t

    chain = []
    cur   = by_id.get(focal_id)
    while cur:
        chain.insert(0, cur)
        parent_id = cur["in_reply_to_id"]
        if not parent_id:
            break
        next_cur = by_id.get(parent_id)
        if not next_cur:
            if parent_id in tombstones:
                ts = tombstones[parent_id]
                sn = cur.get("in_reply_to_sn", "")
                ts = dict(ts, screen_name=sn)
                chain.insert(0, ts)
            elif cur.get("in_reply_to_sn"):
                chain.insert(0, {"__tombstone": True, "id": parent_id,
                                  "screen_name": cur["in_reply_to_sn"],
                                  "text": "This tweet is unavailable."})
            break
        cur = next_cur
    return chain if chain else list(by_id.values())

def parse_tweet_result_single(data):
    result = data["data"]["tweetResult"]["result"]
    if result.get("__typename") == "TweetTombstone":
        msg = result.get("tombstone", {}).get("text", {}).get("text", "This tweet is unavailable.")
        sys.exit(f"Error: {msg}")
    return [_parse_tweet_result(result, _parse_user)]

_FULL_STATS = False   # set to True by --full-stats / config full_stats=true

def fmt(n):
    n = int(n or 0)
    if _FULL_STATS:
        return f"{n:,}"
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

def format_tweet_line(tweet, nsfw=False, birdwatch=False):
    """Return a single summary line for a parsed tweet dict.

    Badge colour rules (ANSI bg/fg):
      Business ✔    gold   bg=\033[43m  fg=\033[30m  (black on yellow)
      Government ✔  teal  bg=\033[46m  fg=\033[30m  (black on cyan)
      Blue ✔        blue  bg=\033[44m  fg=\033[97m  (white on blue)
      No badge      plain, no color
    """
    RESET  = "\033[0m"
    RED    = "\033[31m"

    user = tweet["user"]
    sn   = user["screen_name"]
    name = user["name"]
    vtype = user.get("verified_type")
    blue  = user.get("is_blue_verified", False)

    # some terminals, like kitty default to font with a small cell width that chops
    # the checkmark in half. choose a better font like Nerd Font, or force a wider
    # width for the checkmark with: kitty -o "narrow_symbols U+2714 1" (or set
    # in kitty config)
    if vtype == "Business":
        badge = " \033[43m\033[30m✔\033[0m "   # black on yellow
    elif vtype == "Government":
        badge = " \033[46m\033[30m✔\033[0m "   # black on cyan
    elif blue:
        badge = " \033[44m\033[97m✔\033[0m "   # white on blue
    else:
        badge = " "

    entities     = tweet.get("entities", {})
    ext_entities = tweet.get("ext_entities", {})
    text = tweet["full_text"]

    media_urls = {m["url"] for m in ext_entities.get("media", [])}
    for u in entities.get("urls", []):
        if u["url"] in media_urls:
            text = text.replace(u["url"], "")
        else:
            text = text.replace(u["url"], u.get("expanded_url", u["url"]))
    for url in media_urls:
        text = text.replace(url, "")

    text = re.sub(r"  +", " ", text).strip()
    text = text.replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
    text = re.sub(r"\\n\\n|\\n|\n\n|\n", " ", text)

    quoted_part = ""
    qt = tweet.get("quoted")
    if qt and not qt.get("__tombstone"):
        qt_sn   = qt.get("user", {}).get("screen_name", "")
        qt_text = qt.get("full_text", "")
        qt_ents = qt.get("entities", {})
        qt_ext  = qt.get("ext_entities", {})
        qt_media_urls = {m["url"] for m in qt_ext.get("media", [])}
        for u in qt_ents.get("urls", []):
            if u["url"] in qt_media_urls:
                qt_text = qt_text.replace(u["url"], "")
            else:
                qt_text = qt_text.replace(u["url"], u.get("expanded_url", u["url"]))
        for url in qt_media_urls:
            qt_text = qt_text.replace(url, "")
        qt_text = re.sub(r"  +", " ", qt_text).strip()
        qt_text = qt_text.replace("&amp;", "&").replace("&gt;", ">").replace("&lt;", "<")
        qt_text = re.sub(r"\\n\\n|\\n|\n\n|\n", " ", qt_text)
        quoted_part = f" [@{qt_sn}] {qt_text}"

    media_items = ext_entities.get("media", [])
    media_part = ""
    if media_items:
        mtype  = media_items[0].get("type", "photo")
        plural = "s" if len(media_items) >= 2 else ""
        media_part = f" [{mtype}{plural}]"

    replies = fmt(tweet.get("reply_count",   0))
    rts     = fmt(tweet.get("retweet_count", 0))
    quotes  = fmt(tweet.get("quote_count",   0))
    likes   = fmt(tweet.get("like_count",    0))
    views   = tweet.get("view_count")
    views_s = f" 🡕 {fmt(views)}" if views else ""

    source = tweet.get("source", "")

    loc = user.get("location", "").strip()
    loc_part = f"| {loc} " if loc else ""

    nsfw_part = f"| {RED}NSFW{RESET} " if nsfw else ""

    bw_part = "| birdwatch " if (birdwatch or tweet.get("birdwatch")) else ""

    tid = tweet.get("id", "")
    url = f"https://x.com/i/status/{tid}" if tid else ""

    sep = ": " if not badge else ""
    header = f"@{sn} ({name}){badge}{sep}"
    body   = f"{text}{quoted_part}{media_part}"
    stats  = f"↳ {replies} ⇅ {rts} ‟ {quotes} ♥ {likes}{views_s}"
    footer = f"| {stats} | {source} {loc_part}{nsfw_part}{bw_part}| {url}"

    return f"{header}{body} {footer}"


def upload_imgur(path):
    import uuid
    client_id = os.environ.get("IMGUR_CLIENT_ID", "17385cf5260cef9")
    with open(path, "rb") as f:
        img_data = f.read()
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{os.path.basename(path)}"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + img_data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        "https://api.imgur.com/3/image",
        data=body,
        headers={
            "Authorization": f"Client-ID {client_id}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req) as r:
        resp = json.loads(r.read())
    url = resp["data"]["link"].replace("http://", "https://")
    delete_hash = resp["data"]["deletehash"]
    return url, delete_hash

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

def _fmt_duration(ms):
    if not ms:
        return ""
    s = int(ms) // 1000
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

PLAY_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 65 65" ' 
    'width="52" height="52" style="filter:drop-shadow(0 1px 3px rgba(0,0,0,.5))">' 
    '<circle cx="32.5" cy="32.5" r="32.5" fill="rgba(0,0,0,0.45)"/>' 
    '<path d="M 24.2275 18.1971 V 44.6465 L 45.0304 31.4218 L 24.2275 18.1971 Z" ' 
    'fill="rgb(255,255,255)"/>' 
    '</svg>'
)

def _attribution_html(attr):
    """Render attribution row between tweet text and media: mini round avatar + bold full name + checkmark, matching nitter's layout."""
    if not attr: return ""
    vicon = verified_svg(attr.get("verified_type"), attr.get("is_blue_verified", False))
    avatar = attr["avatar_url"]
    return (
        f'<div class="media-attribution" style="display: flex; align-items: center; margin-top: 2px; margin-bottom: 8px; font-size: 13px; color: var(--grey); gap: 4px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">'
        f'<img class="attr-avatar" src="{avatar}" style="width: 16px; height: 16px; border-radius: 50%; object-fit: cover; display: inline-block; vertical-align: middle;">'
        f'<span>From </span><strong class="attr-name" style="color: var(--fg); font-weight: 700; margin-left: 1px; margin-right: 1px;">{attr["name"]}</strong>{vicon}'
        f'</div>'
    )

def media_html(ext_entities, is_ai=False):
    media_list = ext_entities.get("media", [])
    if not media_list:
        return ""

    parts = []
    for m in media_list:
        if m["type"] == "photo":
            parts.append(f'<div class="attachment"><img src="{m["media_url_https"]}"></div>')
        elif m["type"] in ("video", "animated_gif"):
            vi = m.get("video_info", {})
            dur_ms = vi.get("duration_millis", 0)
            dur_label = _fmt_duration(dur_ms) if m["type"] == "video" else ""
            dur_html = (
                f'<div class="vid-duration">{dur_label}</div>' if dur_label else ""
            )
            parts.append(
                f'<div class="attachment video-wrap">'
                f'<img src="{m["media_url_https"]}">'
                f'<div class="play-overlay">{PLAY_SVG}</div>'
                f'{dur_html}'
                f'</div>'
            )

    ai_label = (
        '<div class="ai-label" style="font-size:10px;color:var(--grey);margin-top:3px;display:flex;align-items:center;gap:0;">'
        + icon_svg("robot", 18, "var(--grey)")
        + 'Made with AI'
        '</div>'
    ) if is_ai else ""

    if len(parts) == 5:
        grid = f'''<div class="media-grid-5">
            <div class="row-top">
                <div class="grid-item">{parts[0]}</div>
                <div class="grid-item">{parts[1]}</div>
            </div>
            <div class="row-bottom">
                <div class="grid-item">{parts[2]}</div>
                <div class="grid-item">{parts[3]}</div>
                <div class="grid-item">{parts[4]}</div>
            </div>
        </div>'''
    elif len(parts) == 4:
        grid = f'''<div class="media-grid-2x2">
            <div class="grid-item">{parts[0]}</div>
            <div class="grid-item">{parts[1]}</div>
            <div class="grid-item">{parts[2]}</div>
            <div class="grid-item">{parts[3]}</div>
        </div>'''
    elif len(parts) == 3:
        grid = f'''<div class="media-grid-3">
            <div class="grid-item">{parts[0]}</div>
            <div class="grid-item">{parts[1]}</div>
            <div class="grid-item span-2">{parts[2]}</div>
        </div>'''
    else:
        grid = f'<div class="media-row">{"".join(parts)}</div>'

    return grid + ai_label

GLYPHS = {
    "comment": ("M1000 350q0-97-67-179t-182-130-251-48q-39 0-81 4-110-97-257-135-27-8-63-12-10-1-17 5t-10 16v1q-2 2 0 6t1 6 2 5l4 5t4 5 4 5q4 5 17 19t20 22 17 22 18 28 15 33 15 42q-88 50-138 123t-51 157q0 73 40 139t109 115 163 76 197 28q135 0 251-48t182-130 67-179z", 1000),
    "retweet": ("M714 11q0-7-5-13t-13-5h-535q-5 0-8 1t-5 4-3 4-2 7 0 6v335h-107q-15 0-25 11t-11 25q0 13 8 23l179 214q11 12 27 12t28-12l178-214q9-10 9-23 0-15-11-25t-25-11h-107v-214h321q9 0 14-6l89-108q4-5 4-11z m357 232q0-13-8-23l-178-214q-12-13-28-13t-27 13l-179 214q-8 10-8 23 0 14 11 25t25 11h107v214h-322q-9 0-14 7l-89 107q-4 5-4 11 0 7 5 12t13 6h536q4 0 7-1t5-4 3-5 2-6 1-7v-334h107q14 0 25-11t10-25z", 1071),
    "quote":   ("M18 685l335 0 0-334q0-140-98-238t-237-97l0 111q92 0 158 65t65 159l-223 0 0 334z m558 0l335 0 0-334q0-140-98-238t-237-97l0 111q92 0 158 65t65 159l-223 0 0 334z", 928),
    "heart":   ("M790 644q70-64 70-156t-70-158l-360-330-360 330q-70 66-70 158t70 156q62 58 151 58t153-58l56-52 58 52q62 58 150 58t152-58z", 860),
    "views":   ("M180 516l0-538-180 0 0 538 180 0z m250-138l0-400-180 0 0 400 180 0z m250 344l0-744-180 0 0 744 180 0z", 680),
    "group":   ("M0 106l0 134q0 26 18 32l171 80q-66 39-68 131 0 56 35 103 37 41 90 43 31 0 63-19-49-125 23-237-12-11-25-19l-114-55q-48-23-52-84l0-143-114 0q-25 0-27 34z m193-59l0 168q0 27 22 37l152 70 57 28q-37 23-60 66t-22 94q0 76 46 130t110 54 109-54 45-130q0-105-78-158l61-30 146-70q24-10 24-37l0-168q-2-37-37-41l-541 0q-14 2-24 14t-10 27z m473 330q68 106 22 231 31 19 66 21 49 0 90-43 35-41 35-103 0-82-65-131l168-80q18-10 18-32l0-134q0-32-27-34l-118 0 0 143q0 57-50 84l-110 53q-15 8-29 25z", 1000),
    "robot":   ("M409.6 758.0 c-26.2 -9.8 -39.2 -37.0 -29.6 -62.4 1.7 -4.5 5.1 -10.6 7.6 -13.6 6.1 -7.3 17.3 -14.6 20.6 -13.4 2.2 0.8 2.3 0.5 0.7 -1.3 -1.7 -1.5 -2.2 -11.8 -2.2 -39.2 l0.0 -36.9 -96.6 -0.5 -96.8 -0.5 -8.3 -3.8 c-11.3 -5.1 -22.9 -17.1 -28.1 -28.7 l-4.3 -9.5 0.3 -186.6 0.5 -186.4 4.8 -8.8 c6.1 -11.0 15.3 -19.3 27.4 -25.1 l9.3 -4.3 210.0 0.0 210.0 0.0 9.3 4.3 c12.1 5.8 21.2 14.1 27.4 25.1 l4.8 8.8 0.5 186.4 0.3 186.6 -4.3 9.5 c-5.1 11.6 -16.8 23.6 -28.1 28.7 l-8.3 3.8 -96.6 0.5 -96.8 0.5 0.0 36.9 c0.0 27.4 -0.5 37.7 -2.0 39.2 -1.8 1.8 -1.7 2.2 0.5 1.3 6.6 -2.3 23.1 13.3 28.2 26.9 6.6 17.8 1.2 41.3 -12.3 52.6 -12.9 10.8 -34.0 15.3 -48.1 10.0z m-69.7 -305.6 c17.1 -5.0 28.6 -17.9 32.4 -35.9 3.8 -18.3 -6.5 -38.2 -24.4 -47.1 -25.9 -12.9 -55.0 0.3 -63.3 28.7 -2.3 7.8 -2.5 11.0 -1.0 18.4 3.7 17.6 14.1 29.6 30.7 35.2 10.6 3.8 14.9 3.8 25.6 0.7z m194.2 0.0 c17.1 -5.0 28.6 -17.9 32.4 -35.9 3.8 -18.3 -6.5 -38.2 -24.4 -47.1 -22.6 -11.3 -47.5 -3.3 -59.8 19.4 -7.0 12.6 -7.0 26.9 0.0 40.3 5.8 11.5 13.9 18.4 26.1 22.6 10.8 3.8 14.9 3.8 25.7 0.7z M124.5 456.7 c-14.4 -4.5 -28.9 -17.3 -35.4 -31.4 -3.5 -7.5 -3.7 -10.0 -3.7 -60.9 l0.0 -53.1 4.3 -9.3 c6.0 -12.8 14.9 -22.1 26.1 -27.6 8.6 -4.0 11.6 -4.5 32.4 -5.1 l22.7 -0.7 0.0 94.8 0.0 94.8 -21.1 -0.2 c-11.8 0.0 -23.1 -0.7 -25.4 -1.3z M679.0 363.4 l0.0 -94.8 22.9 0.7 c18.6 0.5 24.2 1.3 30.5 4.2 11.5 5.1 21.7 15.6 27.2 27.6 l4.8 10.3 0.0 53.1 0.0 53.1 -4.6 9.5 c-6.1 12.5 -19.6 24.7 -31.0 28.4 -6.3 2.0 -14.4 2.8 -29.2 2.8 l-20.6 0.0 0.0 -94.8z", 1000),
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
    return (f'<svg width="12" height="12" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" '
            f'style="display:inline-block;vertical-align:middle;margin:0 0 4px 4px">'
            f'<circle cx="9" cy="9" r="9" fill="{fill}"/>'
            f'<polyline points="4.5,10 7.5,13 13.5,7" stroke="{stroke}" stroke-width="2.2" '
            f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

NITTER_CSS = """
body {
    --bg_color: #0f0f0f;
    --fg_color: #f8f8f2;
    --fg_faded: #f8f8f2cf;
    --fg_dark: #ff6c60;
    --bg_panel: #161616;
    --bg_elements: #121212;
    --bg_hover: #1a1a1a;
    --grey: #888889;
    --border_grey: #3e3e35;
    --accent: #ff6c60;
    --accent_dark: #8a3731;
    --play_button: #d8574d;
}
"""

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
    --accent:  #8899A6;
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
    --fg: #0f1419;
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
.thread { padding-top: 10px; }
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
.username { color: var(--accent); font-size: 14px; white-space: nowrap; padding-left: 4px; }
.tweet-time { color: var(--accent); font-size: 14px; white-space: nowrap; flex-shrink: 0; margin-left: 8px; }
.replying-to { color: var(--grey); font-size: 13px; margin-bottom: 3px; line-height: 1.4; }
.tweet-content { font-size: 15px; line-height: 1.5; margin: 4px 0 0; white-space: pre-wrap; word-wrap: break-word; }
.focal .tweet-content { font-size: 17px; }
.focal .tweet-date { color: var(--grey); font-size: 13px; margin-bottom: 0; padding-top: 6px; }
.stats { display: flex; align-items: center; color: var(--grey); font-size: 13px; padding-top: 8px; }
.stat { white-space: nowrap; margin-right: 10px; }
.stat svg { margin: 3px 1px 5px 0; }
.source { margin-left: auto; font-size: 12px; }
.media-row { display: flex; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-row .attachment { flex: 1; }
.media-row .attachment + .attachment { margin-left: 3px; }
.media-grid-2x2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-2x2 .grid-item { position: relative; overflow: hidden; }
.media-grid-2x2 .grid-item img { width: 100%; height: 100%; object-fit: cover; }

.media-grid-3 { display: grid; grid-template-columns: 1fr 1fr; gap: 0; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-3 .grid-item { position: relative; overflow: hidden; }
.media-grid-3 .grid-item img { width: 100%; height: 100%; object-fit: cover; }
.media-grid-3 .span-2 { grid-column: 1 / span 2; }
.media-grid-5 { display: grid; grid-template-columns: 1fr 1fr; gap: 3px; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-5 .grid-item { position: relative; overflow: hidden; }
.media-grid-5 .grid-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.media-grid-5 .row-top { grid-column: 1 / span 2; display: grid; grid-template-columns: 1fr 1fr; gap: 3px; }
.media-grid-5 .row-bottom { grid-column: 1 / span 2; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 3px; }
.attachment img { width: 100%; display: block; }
.video-wrap { position: relative; }
.play-overlay { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.vid-duration { position: absolute; bottom: 6px; left: 8px; background: rgba(0,0,0,0.6); color: #fff; font-size: 12px; font-weight: 600; line-height: 1; padding: 3px 5px; border-radius: 4px; pointer-events: none; }
.media-attribution { display: flex; align-items: center; gap: 6px; margin: 6px 0 4px; }
.attr-avatar { width: 24px; height: 24px; border-radius: 50%; display: block; flex-shrink: 0; }
.attr-name { font-size: 14px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.quote-block { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px; margin: 6px 0; background: var(--qt-bg); overflow: hidden; }
.quote-header { display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 4px; }
.quote-header > * { margin-right: 4px; }
.quote-avatar { width: 20px; height: 20px; border-radius: 10px; display: inline-block; }
.quote-name { font-weight: 700; font-size: 14px; margin-right: 0; }
.quote-sn { color: var(--accent); font-size: 13px; padding-left: 4px; }
.quote-time { color: var(--grey); font-size: 13px; margin-left: auto; }
.quote-text { font-size: 14px; line-height: 1.45; white-space: pre-wrap; word-wrap: break-word; }
.quote-media { margin-top: 6px; border-radius: 8px; overflow: hidden; }
.quote-media img { width: 100%; display: block; }
.birdwatch { border: 1px solid var(--border); border-radius: 10px; margin: 6px 0; background: var(--bw-bg); overflow: hidden; }
.community-note-header { background-color: var(--bg-hover); font-weight: 700; font-size: 13px; padding: 6px 10px 8px; display: flex; align-items: center; gap: 12px; color: var(--fg); }
.community-note-header .icon-container { flex-shrink: 0; color: var(--accent); }
.community-note-text { font-size: 13px; line-height: 1.45; color: var(--fg); white-space: pre-line; padding: 6px 10px 10px; }
.card { border: 1px solid var(--border); border-radius: 10px; margin: 6px 0; overflow: hidden; display: flex; flex-direction: column; }
.card-img { width: 100%; display: block; max-height: 220px; object-fit: cover; }
.card-body { padding: 8px 12px 10px; }
.card-domain { font-size: 12px; color: var(--grey); text-transform: uppercase; margin-bottom: 2px; }
.card-title { font-size: 14px; font-weight: 700; line-height: 1.3; margin-bottom: 2px; }
.card-desc { font-size: 13px; color: var(--grey); line-height: 1.4; }
.tweet-row.focal { flex-direction: column; padding: 0; }
.focal-header { display: flex; align-items: center; padding: 0 14px 8px; gap: 12px; }
.focal-header .avatar { width: 46px; height: 46px; border-radius: 23px; flex-shrink: 0; }
.focal-header-names { display: flex; flex-direction: column; justify-content: center; line-height: 1.25; }
.focal-header-top { display: flex; align-items: center; gap: 3px; }
.focal-header-top .fullname { font-size: 15px; font-weight: 700; }
.focal-header-bottom .username { color: var(--accent); font-size: 14px; padding-left: 0; }
.focal-body { padding: 0 14px 14px; }
.rt-header { display: flex; align-items: center; color: var(--grey); font-size: 13px; font-weight: 700; padding: 8px 14px 0 53px; gap: 5px; }
.rt-header svg { flex-shrink: 0; }
"""

def quote_block_html(qt):
    if not qt: return ""
    if qt.get("__tombstone"):
        sn = qt.get("screen_name", "")
        label = f"This tweet from @{sn} is unavailable." if sn else qt.get("text", "This tweet is unavailable.")
        return f'''<div class="quote-block" style="display:flex;align-items:center;justify-content:center;padding:14px 12px;">
  <span style="color:var(--grey);font-size:14px;">{label}</span>
</div>'''
    u    = qt["user"]
    text = linkify(qt["full_text"], qt["entities"])
    vicon = verified_svg(u["verified_type"], u["is_blue_verified"])
    time  = rel_time(qt["created_at"])
    media = ""
    mlist = qt["ext_entities"].get("media", [])
    if mlist:
        m = mlist[0]
        if m["type"] in ("video", "animated_gif"):
            vi = m.get("video_info", {})
            dur_ms = vi.get("duration_millis", 0)
            dur_label = _fmt_duration(dur_ms) if m["type"] == "video" else ""
            dur_html = f'<div class="vid-duration">{dur_label}</div>' if dur_label else ""
            media = (
                f'<div class="quote-media">'
                f'<div class="video-wrap" style="position:relative;">'
                f'<img src="{m["media_url_https"]}">'
                f'<div class="play-overlay">{PLAY_SVG}</div>'
                f'{dur_html}'
                f'</div></div>'
            )
        else:
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

def card_html(card):
    if not card: return ""
    if card.get("is_player") and card.get("img_url"):
        return f'''<a href="{card["url"]}" class="card" style="position:relative;display:block;">
  <div class="attachment video-wrap" style="margin:0;border-radius:0;">
    <img src="{card["img_url"]}" style="width:100%;display:block;max-height:220px;object-fit:cover;">
    <div class="play-overlay">{PLAY_SVG}</div>
  </div>
  <div class="card-body">
    <div class="card-domain">{card["domain"]}</div>
    <div class="card-title">{card["title"]}</div>
  </div>
</a>'''
    img = f'<img class="card-img" src="{card["img_url"]}">' if card.get("img_url") else ""
    return f'''<a href="{card["url"]}" class="card">
  {img}
  <div class="card-body">
    <div class="card-domain">{card["domain"]}</div>
    <div class="card-title">{card["title"]}</div>
    <div class="card-desc">{card["desc"]}</div>
  </div>
</a>'''

def tweet_row_html(t, is_parent=False, no_source=False):
    if t.get("__tombstone"):
        sn = t.get("screen_name", "")
        label = f"This tweet from @{sn} is unavailable." if sn else "This tweet is unavailable."
        line = "<div class='thread-line'></div>" if is_parent else ""
        return f"""<div class="tweet-row">
  <div class="left-col">
    <svg width="46" height="46" viewBox="0 0 46 46" xmlns="http://www.w3.org/2000/svg"><circle cx="23" cy="23" r="23" fill="var(--border)"/><text x="23" y="28" text-anchor="middle" font-size="20" fill="var(--grey)">?</text></svg>
    {line}
  </div>
  <div class="right-col" style="display:flex;align-items:center;padding-bottom:12px;">
    <span style="color:var(--grey);font-size:14px;">{label}</span>
  </div>
</div>"""
    u      = t["user"]
    vicon  = verified_svg(u["verified_type"], u["is_blue_verified"])
    grey   = "var(--grey)"

    clean_text, reply_to_sns = strip_all_lead_mentions(t["full_text"], t["entities"])

    if not reply_to_sns and t["in_reply_to_sn"]:
        reply_to_sns = [t["in_reply_to_sn"]]

    tweet_text  = linkify(clean_text, t["entities"])
    attr_block  = _attribution_html(t.get("media_attribution"))
    is_ai       = t.get("is_ai_media", False)
    media_block = (attr_block + media_html(t["ext_entities"], is_ai=is_ai)) if attr_block else media_html(t["ext_entities"], is_ai=is_ai)
    time_str    = rel_time(t["created_at"])
    row_class   = "tweet-row" + ("" if is_parent else " focal")

    rt_by = t.get("rt_by_user")
    rt_header = ""
    if rt_by:
        rt_header = (
            f'<div class="rt-header">'
            f'{icon_svg("retweet", 13, "var(--grey)")}'
            f'{rt_by["name"]} retweeted'
            f'</div>'
        )

    replying = ""
    if reply_to_sns and not is_parent:
        links = " ".join([f'<a href="#">@{sn}</a>' for sn in reply_to_sns])
        replying = f'<div class="replying-to">Replying to {links}</div>'

    card_block = card_html(t.get("card")) if not t.get("ext_entities", {}).get("media") else ""
    qt_html = quote_block_html(t["quoted"]) if t.get("quoted") else ""
    bw_html = ""
    if t.get("birdwatch"):
        bw_text = t["birdwatch"]
        ents = [e for e in t.get("birdwatch_ents", [])
                if e.get("fromIndex") is not None and e.get("toIndex") is not None]
        ents.sort(key=lambda e: e["fromIndex"], reverse=True)
        for e in ents:
            start, end = e["fromIndex"], e["toIndex"]
            ref = e.get("ref", {})
            href = ref.get("url", "")
            display = bw_text[start:end]
            if "help.x.com" in href or "help.x.com" in display:
                bw_text = bw_text[:start] + bw_text[end:]
                continue
            if href:
                # short 'display' urls are snippets
                bw_text = bw_text[:start] + f'<a href="{href}">{display}</a>' + bw_text[end:]
                # full urls are wrapped in t.co links, to enable we need to
                # HEAD each url, might be too much overhead for birdwatch
                # cards.
                # todo: resolve_url() on these 
                #bw_text = bw_text[:start] + f'<a href="{href}">{href}</a>' + bw_text[end:]

        bw_html = f'''<div class="birdwatch">
          <div class="community-note-header"><span class="icon-container">{icon_svg("group", 13, "var(--accent)")}</span> Community Note</div>
          <div class="community-note-text">{bw_text}</div>
        </div>'''
    broadcast_html = ""
    if t.get("broadcast_card"):
        bc = t["broadcast_card"]
        bc_img = bc.get("image")
        bc_title = bc.get("title", "")
        
        broadcast_html = f'''
        <div class="tweet-card broadcast-card" style="
            margin-top: 12px;
            border: 1px solid var(--border);
            border-radius: 16px;
            overflow: hidden;
            background-color: rgba(0, 0, 0, 0.02);
        ">
            {f'<img src="{bc_img}" style="width: 100%; display: block; aspect-ratio: 16/9; object-fit: cover;" />' if bc_img else ''}
            <div style="padding: 12px; border-top: 1px solid var(--border);">
                <div style="font-weight: bold; font-size: 15px; color: var(--text); line-height: 1.4;">{bc_title}</div>
                <div style="font-size: 13px; color: var(--dim); margin-top: 4px; text-transform: lowercase;">x.com/i/broadcasts</div>
            </div>
        </div>
        '''
    src = "" if no_source else f'<span class="source">{t["source"]}</span>'
    stats = f"""<div class="stats">
      <span class="stat">{icon_svg("comment", 13, grey)} {fmt(t["reply_count"])}</span>
      <span class="stat">{icon_svg("retweet", 13, grey)} {fmt(t["retweet_count"])}</span>
      <span class="stat">{icon_svg("quote",   13, grey)} {fmt(t["quote_count"])}</span>
      <span class="stat">{icon_svg("heart",   13, grey)} {fmt(t["like_count"])}</span>
      <span class="stat">{icon_svg("views",   13, grey)} {fmt(t["view_count"])}</span>
      {src}
    </div>"""
    if is_parent:
        return f"""{rt_header}<div class="{row_class}">
  <div class="left-col">
    <img class="avatar" src="{u["avatar_url"]}">
    <div class='thread-line'></div>
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
    {card_block}
    {qt_html}
    {bw_html}
    {broadcast_html}
    {stats}
  </div>
</div>"""
    else:
        return f"""{rt_header}<div class="{row_class}">
  <div class="focal-header">
    <img class="avatar" src="{u["avatar_url"]}">
    <div class="focal-header-names">
      <div class="focal-header-top"><span class="fullname">{u["name"]}</span>{vicon}</div>
      <div class="focal-header-bottom"><span class="username">@{u["screen_name"]}</span></div>
    </div>
    <span class="tweet-time" style="margin-left:auto">{time_str}</span>
  </div>
  <div class="focal-body">
    {replying}
    <div class="tweet-content">{tweet_text}</div>
    {media_block}
    {card_block}
    {qt_html}
    {bw_html}
    {broadcast_html}
    <div class="tweet-date">{abs_time(t["created_at"])}</div>
    {stats}
  </div>
</div>"""

def _parse_css_vars(css_text):
    m = re.search(r'body\s*\{([^}]*)\}', css_text, re.DOTALL)
    if not m:
        return {}
    result = {}
    for line in m.group(1).splitlines():
        line = line.strip().rstrip(";")
        if line.startswith("--") and ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result

def _resolve_var(val, lookup):
    m = re.fullmatch(r'var\(([^)]+)\)', val.strip())
    if m:
        ref = m.group(1).strip()
        return lookup.get(ref, val)
    return val

def _apply_nitter_theme(css_text):
    nv = _parse_css_vars(css_text)
    if not nv:
        return ""
    resolved = {k: _resolve_var(v, nv) for k, v in nv.items()}

    def get(key, fallback=""):
        return resolved.get(key, fallback)

    mapping = {
        "--bg":       get("--bg_color"),
        "--fg":       get("--fg_color"),
        "--grey":     get("--grey"),
        "--border":   get("--border_grey"),
        "--link":     get("--accent"),
        "--accent":   get("--accent"),
        "--acc":      get("--accent_dark") or get("--accent"),
        "--play":     get("--play_button")  or get("--accent"),
        "--qt-bg":    get("--bg_elements")  or get("--bg_panel") or get("--bg_color"),
        "--bw-bg":    get("--bg_panel")     or get("--bg_color"),
        "--bw-fg":    get("--fg_faded")     or get("--grey"),
        "--bg-hover": get("--bg_hover"),
    }
    lines = [f"    {k}: {v};" for k, v in mapping.items() if v]
    if not lines:
        return ""
    return "body {\n" + "\n".join(lines) + "\n    background: var(--bg);\n    color: var(--fg);\n}\na { color: var(--link); text-decoration: none; }\n"

def build_html(tweets, light=False, no_source=False, css_path=None, width=598, nitter=False, for_browser=False):
    theme_css = LIGHT_CSS if light else DARK_CSS
    if nitter and not css_path:
        override = _apply_nitter_theme(NITTER_CSS)
        base_css = theme_css + SHARED_CSS + "\n" + override
    elif css_path and Path(css_path).exists():
        raw = Path(css_path).read_text()
        override = _apply_nitter_theme(raw)
        if override:
            base_css = theme_css + SHARED_CSS + "\n" + override
        else:
            base_css = raw
    else:
        base_css = theme_css + SHARED_CSS
    rows = []
    for i, t in enumerate(tweets):
        rows.append(tweet_row_html(t, is_parent=(i < len(tweets)-1), no_source=no_source))
    if for_browser:
        # When viewed in a real browser, center the tweet card in the viewport.
        centering_css = f"""
html, body {{
    width: 100%;
    min-height: 100vh;
    margin: 0;
    padding: 0;
    display: flex;
    justify-content: center;
    align-items: flex-start;
    box-sizing: border-box;
    padding-top: 2rem;
    padding-bottom: 2rem;
}}
.thread {{
    width: {width}px;
    max-width: 100%;
    flex-shrink: 0;
}}
"""
        extra_style = centering_css
    else:
        extra_style = f"body {{ width: {width}px; }}"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
{base_css}
{extra_style}
</style></head><body>
<div class="thread">{"".join(rows)}</div>
</body></html>"""

async def render_png(html, output_path, width=598, retina=True):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        sys.exit("Error: playwright is required for PNG rendering.\n"
                 "Install it with: pip install playwright && playwright install chromium")
    scale = 2 if retina else 1
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox"])
        context = await browser.new_context(
            viewport={"width": width, "height": 800},
            device_scale_factor=scale,
        )
        page = await context.new_page()
        await page.set_content(html, wait_until="networkidle")
        await asyncio.sleep(0.5)
        thread = page.locator(".thread")
        await thread.screenshot(path=output_path)
        await browser.close()

async def _main():
    # Pre-parse -c/--config before building the full parser so it can seed defaults.
    # We do a lightweight scan of sys.argv rather than a separate ArgumentParser
    # to avoid interfering with positional arguments.
    _pre = argparse.ArgumentParser(add_help=False)
    _pre.add_argument("-c", "--config", default=None)
    _pre_args, _ = _pre.parse_known_args()
    conf = load_config(extra_path=_pre_args.config)

    def _b(key):
        """Return bool default from conf, defaulting to False."""
        return conf.get(key, "false").strip().lower() == "true"

    p = argparse.ArgumentParser(
        description="Render tweet as PNG via Playwright",
        epilog=(
            "Config file (INI format, [tw2img] section) is loaded in priority order:\n"
            "  1. ~/.config/tw2img/tw2img.conf  (user default)\n"
            "  2. <script_dir>/tw2img.conf       (next to script, if present)\n"
            "  3. -c /path/to/custom.conf        (explicit override)\n"
            "  4. CLI flags                      (always highest priority)\n"
            "\nExample: tw2img.py 12345 -c ~/work/tw2img-work.conf --light"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("input",        nargs="?", default=None, help="Tweet ID, URL, JSON file, or - for stdin")
    p.add_argument("output",       nargs="?", help="Output PNG (default: <screen_name>-<id>.png)")
    p.add_argument("-c", "--config", default=None, metavar="FILE",
                   help="Load config from FILE instead of (or in addition to) defaults. "
                        "Merged on top of ~/.config/tw2img/tw2img.conf and <script_dir>/tw2img.conf; "
                        "CLI flags still override everything.")
    p.add_argument("--user",       default=conf.get("user"), help="Fetch latest tweet from this screen_name")
    p.add_argument("--light",      action="store_true", default=_b("light"), help="Render image in light mode")
    p.add_argument("--no-source",  action="store_true", default=_b("no_source"), help="Hide the device name used (iPhone, etc)")
    p.add_argument("--no-context", action="store_true", default=_b("no_context"), help="Only show focal tweet, no thread")
    p.add_argument("--no-retina",  action="store_true", default=_b("no_retina"), help="Generate a 50%% smaller image")
    p.add_argument("--guest",      action="store_true", default=_b("guest"), help="Guest mode (no account needed)")
    p.add_argument("--width",      type=int, default=int(conf.get("width", 598)))
    p.add_argument("--css",        default=conf.get("css") or None, help="File to override the theme (ex: nitter/public/css/themes/pleroma.css)")
    p.add_argument("--nitter",     action="store_true", default=_b("nitter"), help="Use Nitter default theme")
    p.add_argument("--html-only",  action="store_true", default=_b("html_only"), help="Print HTML to stdout instead of rendering PNG")
    p.add_argument("--save-html",  default=conf.get("save_html") or None, help="Save HTML to this file instead of rendering PNG")
    p.add_argument("--output-dir", default=conf.get("output_dir") or None, metavar="DIR",
                   help="Directory to save output PNG (default: current working directory)")
    p.add_argument("--imgur",      action="store_true", default=_b("imgur"), help="Upload PNG to imgur after rendering")
    p.add_argument("--dump-json",  action="store_true", default=_b("dump_json"), help="Print raw API JSON to stdout and exit")
    p.add_argument("--print-line", action="store_true", default=_b("print_line"),
                   help="Print a one-line text summary of the focal tweet to stdout (implies no PNG unless other flags set)")
    p.add_argument("--imgur-log",  default=conf.get("imgur_log") or None, metavar="FILE",
                   help="Append imgur URL + delete link to FILE after each upload (e.g. ~/tw2imgur_urls)")
    p.add_argument("--full-stats", action="store_true", default=_b("full_stats"),
                   help="Show full unabbreviated stat numbers (e.g. 12,345 instead of 12.3K)")
    p.add_argument("--trans",      default=conf.get("trans") or None, metavar="[SOURCE:]TARGET",
                   help="Translate tweet text before rendering. "
                        "Format: TARGET (e.g. --trans en) to auto-detect source, or "
                        "SOURCE:TARGET (e.g. --trans ja:en) to specify both. "
                        "Uses deep-translator (pip install deep-translator). "
                        "Examples: --trans en  |  --trans ja:en  |  --trans auto:fr")
    p.add_argument("--auth-token", default=conf.get("auth_token") or os.environ.get("TWITTER_AUTH_TOKEN"), help="or use envar TWITTER_AUTH_TOKEN")
    p.add_argument("--csrf-token", default=conf.get("csrf_token") or os.environ.get("TWITTER_CSRF_TOKEN"), help="or use envar TWITTER_CSRF_TOKEN")
    p.add_argument("--view",   action="store_true", default=_b("view"),
                   help="Open the saved file with the configured viewer after saving")
    p.add_argument("--viewer", default=conf.get("viewer") or None, metavar="CMD",
                   help="Viewer command used by --view (overrides config). "
                        "Use {} as a placeholder for the filename, e.g. 'kitty +icat {}'. "
                        "If omitted the filename is appended automatically. "
                        "Examples: viewnior  |  eog  |  'kitty +icat {}'  |  firefox")
    args = p.parse_args()

    # Apply full-stats flag globally so fmt() picks it up
    global _FULL_STATS
    _FULL_STATS = args.full_stats

    tweet_index = 1
    if args.input and re.fullmatch(r'@[A-Za-z0-9_]{1,15}', args.input):
        if not args.user:
            args.user = args.input.lstrip('@')
        args.input = None
        if args.output and re.fullmatch(r'[1-9]|1[0-9]|20', args.output):
            tweet_index = int(args.output)
            args.output = None

    if not args.user and not args.input:
        sys.exit("Error: provide a tweet ID/URL/file, @username, or use --user <screen_name>")
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
        tweet_id = fetch_nth_tweet_id(user_id, headers, n=tweet_index)
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
        if inp.isdigit():
            m = inp
            tweet_id = m
        else:
            m = re.search(r"/status/(\d+)", inp)
            if not m:
                sys.exit("Invalid tweet URL")
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

    if not tweets:
        sys.exit("Failed to parse tweet from API response")

    tweet_id = tweets[-1]["id"]
    focal = tweets[-1]

    if args.trans:
        raw = args.trans.strip()
        if ":" in raw:
            src_lang, tgt_lang = raw.split(":", 1)
        else:
            src_lang, tgt_lang = "auto", raw
        for t in tweets:
            if t.get("__tombstone"):
                continue
            # Skip if the tweet is already in the target language.
            # legacy.lang is a BCP-47 tag (e.g. "en", "ja") from the API.
            # Compare only the primary subtag so "zh-Hant" won't skip --trans zh.
            tweet_lang = (t.get("lang") or "").split("-")[0].lower()
            tgt_primary = tgt_lang.split("-")[0].lower()
            if tweet_lang and tweet_lang == tgt_primary:
                continue
            effective_src = src_lang if src_lang != "auto" else (tweet_lang or "auto")
            print(f"Translating tweet text ({effective_src} -> {tgt_lang}) ...", file=sys.stderr)
            if t.get("full_text"):
                t["full_text"] = translate_text(t["full_text"], effective_src, tgt_lang)
            qt = t.get("quoted")
            if qt and not qt.get("__tombstone") and qt.get("full_text"):
                qt_lang = (qt.get("lang") or "").split("-")[0].lower()
                if not qt_lang or qt_lang != tgt_primary:
                    qt_src = src_lang if src_lang != "auto" else (qt_lang or "auto")
                    qt["full_text"] = translate_text(qt["full_text"], qt_src, tgt_lang)

    if args.print_line:
        print(format_tweet_line(focal))
        return

    user_name = focal["user"]["screen_name"]
    if not args.output and (focal.get("rt_by_user") or focal.get("is_rt")):
        if focal.get("rt_by_user"):
            rt_by = focal["rt_by_user"]["screen_name"]
            orig  = focal["user"]["screen_name"]
            output = f"{rt_by}-rt-{orig}-{tweet_id}.png"
        elif focal.get("rt_orig_sn"):
            orig  = focal["rt_orig_sn"]
            output = f"{user_name}-rt-{orig}-{tweet_id}.png"
        else:
            output = f"{user_name}-rt-{tweet_id}.png"
    else:
        output = args.output or f"{user_name}-{tweet_id}.png"

    if args.output_dir and not os.path.isabs(output) and not os.path.dirname(output):
        output = os.path.join(os.path.expanduser(args.output_dir), output)

    # Apply duplicate_files handling (only when output was auto-generated, not explicit)
    if not args.output:
        dup_mode = conf.get("duplicate_files", "overwrite").strip().lower()
        output = resolve_output_path(output, dup_mode)

    html = build_html(tweets, light=args.light, no_source=args.no_source, css_path=args.css, width=args.width, nitter=args.nitter,
                      for_browser=bool(args.save_html))

    if args.save_html:
        with open(args.save_html, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"HTML saved to {args.save_html}")
        if args.view:
            viewer = args.viewer or "firefox"
            open_with_viewer(args.save_html, viewer)
        return

    if args.html_only:
        print(html)
        return

    await render_png(html, output, width=args.width, retina=not args.no_retina)
    print(f"{output} saved")
    if args.view:
        viewer = args.viewer or "viewnior"
        open_with_viewer(output, viewer)
    if args.imgur:
        url, delete_hash = upload_imgur(output)
        print(f"{url} delete: https://imgur.com/delete/{delete_hash}")
        if args.imgur_log:
            log_path = os.path.expanduser(args.imgur_log)
            with open(log_path, "a") as f:
                f.write(f"{url} delete: https://imgur.com/delete/{delete_hash} {output}\n")

def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
