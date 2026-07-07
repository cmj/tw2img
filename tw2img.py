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
    @username            implies --user; grabs the latest original tweet (skips RTs/replies)
    @username 3          grabs the 3rd most-recent tweet (skips RTs/replies)
    @username 3 out.png  same, saves to out.png
    --with-replies       also include own replies in @user timeline (auth only, opt-in)
    --last-reply         for reply threads: show only immediate parent + focal tweet
    --top-reply           append the top reply (by likes) below the focal tweet
    --top-replies N       append top N replies (by likes) below focal tweet (1-20)
    --no-nested-quotes    don't fetch a quoted tweet's own quoted tweet (shown as a link instead)
    --with-note            add the top-voted proposed MISLEADING Community Note (labelled 'Proposed' if not yet shown on Twitter)
    --with-notes           add every proposed Community Note (misleading and not-misleading) for the tweet
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
      view      = true              # enable auto-open of PNG after saving
      viewer    = viewnior          # PNG viewer  (default for --view)
      viewer    = kitty +icat {}    # terminal inline display (kitty)
      viewer    = eog               # GNOME image viewer
      viewer    = firefox           # used by --view-html (falls back to xdg-open)
      viewer    = xdg-open          # let the OS pick the right app
      view_html = true              # always auto-save + open HTML alongside the PNG
    Use {} as a placeholder for the filename; otherwise the path is appended.

    Header display:
      bird_icon = true              # show the classic Twitter bird glyph top-right
    
    --view-html saves <name>.html next to the PNG and opens it in the browser
      (uses --viewer if set, otherwise xdg-open). If no PNG is otherwise needed
      (no --view, no --imgur, no explicit output path), Playwright is not required.
"""

import sys, json, re, os, argparse, asyncio, tempfile, urllib.request, urllib.parse, configparser, struct
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

BEARER = "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
BEARER2 = "AAAAAAAAAAAAAAAAAAAAACHguwAAAAAAaSlT0G31NDEyg%2BSnBN5JuyKjMCU%3Dlhg0gv0nE7KKyiJNEAojQbn8Y3wJm1xidDK7VnKGBP4ByJwHPb"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

TWEET_DETAIL_URL               = "https://x.com/i/api/graphql/xIYgDwjboktoFeXe_fgacw/TweetDetail"
TWEET_RESULT_URL               = "https://api.x.com/graphql/SgZWKwvBiOKrSC0QeOGvXw/TweetResultByRestId"
USER_BY_SCREEN_NAME_URL        = "https://x.com/i/api/graphql/laYnJPCAcVo0o6pzcnlVxQ/UserByScreenName"
USER_TWEETS_URL                = "https://x.com/i/api/graphql/fgsimYxdCfQmTI_dtJsTXw/UserTweets"
USER_TWEETS_AND_REPLIES_URL    = "https://x.com/i/api/graphql/xdqXQQg4vOBF9Np6VtUsdw/UserTweetsAndReplies"
BIRDWATCH_FETCH_NOTES_URL      = "https://x.com/i/api/graphql/3G9Ms1POEEiF86dFhV-tTg/BirdwatchFetchNotes"
GUEST_TOKEN_URL                = "https://api.twitter.com/1.1/guest/activate.json"

# Base URL used when building @mention / #hashtag / tweet hyperlinks.
# Override via config: nitter_url = https://nitter.example.com
# Set to empty string to disable hyperlinking of mentions/hashtags entirely.
# This is ONLY FOR HTML rendered output.
_TWEET_BASE_URL = "https://nitter.net"

def _nitter_link(url):
    """Rewrite a twitter.com/x.com tweet permalink to use the configured
    nitter_url base (see _TWEET_BASE_URL / tw2img.conf), preserving the
    rest of the URL (path, query string, etc). Falls back to the original
    URL if it doesn't look like a twitter.com/x.com link, or if nitter_url
    has been explicitly disabled (set to an empty string in the config)."""
    if not url or not _TWEET_BASE_URL:
        return url
    return re.sub(r"^https?://(?:www\.)?(?:twitter|x)\.com", _TWEET_BASE_URL, url)

def _tweet_permalink(screen_name, tweet_id):
    """Build a permalink to a tweet under the configured nitter_url base
    (defaults to nitter.net; see _TWEET_BASE_URL). Returns "" if either
    piece is missing or hyperlinking has been disabled (nitter_url set to
    an empty string), so callers can skip wrapping in an <a> tag."""
    if not _TWEET_BASE_URL or not screen_name or not tweet_id:
        return ""
    return f"{_TWEET_BASE_URL}/{screen_name}/status/{tweet_id}"

# rankingMode: Likes, Recency, Relevance
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
TWEET_RESULT_FTOG   = {"withArticleRichContentState": True, "withArticlePlainText": False,
    "withArticleSummaryText": True, "withArticleVoiceOver": True, "withGrokAnalyze": False,
    "withDisallowedReplyControls": False}

TWEET_RESULT_FEAT   = {"creator_subscriptions_tweet_preview_api_enabled": True,
    "premium_content_api_read_enabled": False, "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": False,
    "rweb_cashtags_composer_attachment_enabled": True, "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True, "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True, "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True, "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True, "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True, "rweb_cashtags_enabled": True,
    "freedom_of_speech_not_reach_fetch_enabled": True, "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True, "longform_notetweets_inline_media_enabled": False,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False, "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True, "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True}

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

USER_TWEETS_AND_REPLIES_FEAT = {"rweb_video_screen_enabled": False, "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": False, "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False, "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "premium_content_api_read_enabled": False, "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True, "responsive_web_jetfuel_frame": True,
    "responsive_web_grok_share_attachment_enabled": True, "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True, "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True, "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True, "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True, "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": True, "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True, "longform_notetweets_inline_media_enabled": False,
    "responsive_web_grok_image_annotation_enabled": True, "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False}

BIRDWATCH_FETCH_NOTES_FEAT = {
    "responsive_web_birdwatch_media_notes_enabled": True,
    "responsive_web_birdwatch_url_notes_enabled": True,
    "responsive_web_birdwatch_translation_enabled": True,
    "responsive_web_birdwatch_fast_notes_badge_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": True,
}

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
        "fieldToggles": json.dumps(TWEET_RESULT_FTOG),
    })

def fetch_birdwatch_notes(tweet_id, headers):
    """Hit BirdwatchFetchNotes for *tweet_id*. Returns every note ever
    proposed for the tweet (helpful, not-helpful, and still-awaiting-ratings),
    split into 'misleading_birdwatch_notes' and 'not_misleading_birdwatch_notes'
    groups -- this is a superset of whatever single note (if any) X is
    currently showing via birdwatch_pivot on the tweet itself."""
    return _req(BIRDWATCH_FETCH_NOTES_URL, headers, {
        "variables": json.dumps({"tweet_id": tweet_id}),
        "features":  json.dumps(BIRDWATCH_FETCH_NOTES_FEAT),
    })

_BW_STATUS_RANK = {"CurrentlyRatedHelpful": 0, "NeedsMoreRatings": 1}  # everything else (e.g. CurrentlyRatedNotHelpful) ranks last

def parse_birdwatch_fetch_notes(data):
    """Flatten a BirdwatchFetchNotes response into a list of note dicts:
    {text, entities, rating_status, shown, is_misleading, created_at}.

    'shown' mirrors whether X is currently surfacing the note on the tweet
    itself (rating_status == CurrentlyRatedHelpful); everything else is a
    proposed/awaiting-ratings note that only this tool is surfacing.

    Order: the whole misleading_birdwatch_notes group first, then the whole
    not_misleading_birdwatch_notes group -- the two are never interleaved.
    Within each group, notes are ranked helpful-first, then awaiting-ratings,
    then not-helpful, newest first within each tier (the API doesn't expose
    a per-note vote tally, only the *author's* lifetime stats, so rating
    tier + recency is the closest available proxy for 'top-voted')."""
    result = ((data.get("data") or {}).get("tweet_result_by_rest_id") or {}).get("result") or {}
    if isinstance(result.get("tweet"), dict):  # some responses nest one level deeper
        result = result["tweet"]

    out = []
    for group, is_misleading in (("misleading_birdwatch_notes", True),
                                  ("not_misleading_birdwatch_notes", False)):
        for n in (result.get(group) or {}).get("notes", []):
            summary = (n.get("data_v1") or {}).get("summary", {})
            out.append({
                "text":          summary.get("text", ""),
                "entities":      summary.get("entities", []),
                "rating_status": n.get("rating_status", ""),
                "shown":         n.get("rating_status") == "CurrentlyRatedHelpful",
                "is_misleading": is_misleading,
                "created_at":    n.get("created_at", 0),
            })
    out.sort(key=lambda n: (0 if n["is_misleading"] else 1,
                             _BW_STATUS_RANK.get(n["rating_status"], 2),
                             -n["created_at"]))
    return out

def _quote_chain_has_stub(qt):
    """True if *qt* (a parsed quoted-tweet dict, possibly nested) contains an
    unresolved stub anywhere down its 'quoted' chain."""
    while qt:
        if qt.get("__stub"):
            return True
        qt = qt.get("quoted")
    return False

def resolve_quote_chain(qt, headers, depth=0, max_depth=3, quiet=True):
    """Resolve a 'quote of a quote' stub (see _parse_tweet_result) by fetching
    the missing tweet with TweetResultByRestId and parsing it like any other
    tweet. Recurses up to max_depth levels in case the resolved tweet itself
    quotes yet another quote. Network/parse failures are swallowed and the
    stub is left in place so quote_block_html() can fall back to a plain
    link -- nested-quote context is a nice-to-have, not worth aborting over."""
    if not qt or depth >= max_depth:
        return qt
    if qt.get("__stub"):
        try:
            data = fetch_tweet_result(qt["id"], headers)
            result = data["data"]["tweetResult"]["result"]
            if result.get("__typename") == "TweetTombstone":
                tombstone_text = (result.get("tombstone", {}).get("text", {}).get("text")
                                  or "This tweet is unavailable.")
                return {"__tombstone": True, "screen_name": qt.get("screen_name", ""), "text": tombstone_text,
                        "permalink": qt.get("permalink", "")}
            resolved = _parse_tweet_result(result, _parse_user)
            if resolved:
                resolved["quoted"] = resolve_quote_chain(resolved.get("quoted"), headers, depth + 1, max_depth, quiet)
                return resolved
        except Exception as e:
            if not quiet:
                print(f"Warning: couldn't resolve nested quoted tweet {qt.get('id')}: {e}", file=sys.stderr)
        return qt  # leave the stub in place; renders as a fallback link
    if qt.get("__tombstone"):
        return qt
    qt["quoted"] = resolve_quote_chain(qt.get("quoted"), headers, depth + 1, max_depth, quiet)
    return qt

def resolution_headers(args, headers):
    """Best-effort headers for resolving nested quoted tweets: reuse whatever
    headers were already built for the main fetch, otherwise build auth
    headers from --auth-token/--csrf-token, otherwise fall back to a fresh
    guest token. In --guest mode auth headers are never used. Returns None
    if nothing works."""
    if headers:
        return headers
    if not getattr(args, "guest", False) and args.auth_token and args.csrf_token:
        return auth_headers(args.auth_token, args.csrf_token)
    try:
        return guest_headers(get_guest_token())
    except Exception:
        return None

def fetch_user_id(screen_name, headers):
    ubsn_headers = dict(headers)
    if "x-twitter-auth-type" in headers:
        ubsn_headers["Authorization"] = f"Bearer {BEARER}"
    data = _req(USER_BY_SCREEN_NAME_URL, ubsn_headers, {
        "variables": json.dumps({"screen_name": screen_name, "includePromotedContent": False, "withBirdwatchNotes": True, "withVoice": True}),
        "features":  json.dumps(USER_BY_SCREEN_NAME_FEAT),
    })
    return data["data"]["user"]["result"]["rest_id"]

def fetch_nth_tweet_id(user_id, headers, n=1, with_replies=True):
    """Return the Nth tweet (1-based) from a user's timeline.

    When with_replies=True (default, requires auth): uses UserTweetsAndReplies so
    the user's own replies are included alongside original tweets and RTs.
    When with_replies=False: uses UserTweets and skips replies.

    Both modes always skip RTs.  guest mode forces with_replies=False since
    UserTweetsAndReplies requires authentication.
    """
    if with_replies:
        url  = USER_TWEETS_AND_REPLIES_URL
        feat = USER_TWEETS_AND_REPLIES_FEAT
        variables = {"userId": user_id, "count": 20, "includePromotedContent": True,
                     "withCommunity": True, "withVoice": True}
        # This endpoint requires its own bearer token
        headers = dict(headers, Authorization=f"Bearer {BEARER2}")
    else:
        url  = USER_TWEETS_URL
        feat = USER_TWEETS_FEAT
        variables = {"userId": user_id, "count": 20, "includePromotedContent": False,
                     "withQuickPromoteEligibilityTweetFields": False, "withVoice": True}

    data = _req(url, headers, {
        "variables":    json.dumps(variables),
        "features":     json.dumps(feat),
        "fieldToggles": json.dumps({"withArticlePlainText": False}),
    })
    instructions = data["data"]["user"]["result"]["timeline"]["timeline"]["instructions"]
    hits = []

    for instr in instructions:
        if instr.get("type") != "TimelineAddEntries":
            continue
        for entry in instr.get("entries", []):
            eid = entry.get("entryId", "")
            if "pin" in eid:
                continue

            if eid.startswith("tweet-"):
                result = (entry.get("content", {}).get("itemContent", {})
                               .get("tweet_results", {}).get("result", {}))
                leg = result.get("legacy", {})
                if leg.get("retweeted_status_id_str"):
                    continue
                if not with_replies and leg.get("in_reply_to_status_id_str"):
                    continue
                hits.append(result.get("rest_id"))

            elif with_replies and eid.startswith("profile-conversation"):
                items = entry.get("content", {}).get("items", [])
                if not items:
                    continue
                last = items[-1]
                result = (last.get("item", {}).get("itemContent", {})
                               .get("tweet_results", {}).get("result", {}))
                leg = result.get("legacy", {})
                # Must be an actual reply by the user (sanity check)
                if not leg.get("in_reply_to_status_id_str"):
                    continue
                if leg.get("retweeted_status_id_str"):
                    continue
                hits.append(result.get("rest_id"))

            if len(hits) >= n:
                return hits[-1]

    return hits[-1] if hits else None

def _parse_user(ur):
    res = ur.get("result", {})
    if not res: return {"name": "Unknown", "screen_name": "unknown", "avatar_url": "",
                         "is_blue_verified": False, "verified_type": None, "parody_label": None}
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

    parody_raw = res.get("parody_commentary_fan_label")
    parody_label = None
    if parody_raw:
        if isinstance(parody_raw, str):
            parody_label = parody_raw
        elif isinstance(parody_raw, dict):
            parody_label = (
                parody_raw.get("label")
                or parody_raw.get("text")
                or parody_raw.get("name")
            )

    return {
        "name":           name,
        "screen_name":    screen_name,
        "avatar_url":     avatar_url.replace("_normal", "_bigger"),
        "is_blue_verified": res.get("is_blue_verified", False),
        "verified_type":  verified_type,
        "parody_label":   parody_label,
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

def _permalink_screen_name(leg):
    """Extract the screen_name embedded in a quoted_status_permalink's
    expanded URL -- often the only place it appears when the quoted tweet
    couldn't be hydrated normally (blocked/suspended/deleted author)."""
    expanded = leg.get("quoted_status_permalink", {}).get("expanded", "")
    m = re.search(r"(?:twitter|x)\.com/([^/]+)/status", expanded)
    return (m.group(1) if m else ""), expanded

def _classify_unavailable(res):
    """Given a TweetTombstone or TweetUnavailable result object, return
    (reason, text). reason is a short lowercase word ("suspended",
    "deleted", "removed", "unavailable") suitable for use in
    "This Tweet was ... {reason}.", text is a fallback human-readable
    message for when we don't have a screen_name to build that sentence."""
    typename = res.get("__typename")
    if typename == "TweetUnavailable":
        reason = (res.get("reason") or "unavailable").lower()
        return reason, f"This Tweet is {reason}."
    if typename == "TweetTombstone":
        text = res.get("tombstone", {}).get("text", {}).get("text", "") or "This tweet is unavailable."
        low = text.lower()
        if "suspend" in low:
            reason = "suspended"
        elif "no longer exist" in low or "delet" in low:
            reason = "deleted"
        elif "violat" in low:
            reason = "removed"
        else:
            reason = "unavailable"
        return reason, text
    return "unavailable", "This tweet is unavailable."

def _parse_tweet_result(result, user_parser):
    if not result or result.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
        return None
    if "tweet" in result and not result.get("legacy"):
        result = result["tweet"]
    leg  = result.get("legacy", {})
    user = user_parser(result.get("core", {}).get("user_results", {}))

    quoted = None
    qt_res = result.get("quoted_status_result", {}).get("result") or \
             result.get("quoted_status_results", {}).get("result")
    if qt_res:
        if qt_res.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
            reason, tombstone_text = _classify_unavailable(qt_res)
            sn, expanded = _permalink_screen_name(leg)
            quoted = {"__tombstone": True, "screen_name": sn, "text": tombstone_text, "permalink": expanded,
                      "reason": reason}
        else:
            quoted = _parse_tweet_result(qt_res, user_parser)
            if quoted and quoted.get("user", {}).get("screen_name") == "unknown":
                # The tweet itself hydrated, but the author's user data didn't.
                # X gives us the tweet id/permalink but no user_results, and
                # doesn't tell us why (blocked, protected, suspended, etc all
                # look the same here) -- so _parse_user's empty-result
                # fallback kicked in. Show a plain unavailable link instead
                # of guessing at a reason, or showing "Unknown".
                sn, expanded = _permalink_screen_name(leg)
                quoted = {"__tombstone": True, "screen_name": sn, "text": "This tweet is unavailable.",
                          "permalink": expanded, "reason": "unavailable"}
    elif leg.get("quoted_status_id_str") and result.get("quoted_status_result") == {}:
        # Empty result object with no error info at all -- could be blocked,
        # deleted, suspended, or protected; X gives us nothing to tell them
        # apart, so we don't guess.
        sn, expanded = _permalink_screen_name(leg)
        quoted = {"__tombstone": True, "screen_name": sn, "text": "This tweet is unavailable.", "permalink": expanded,
                  "reason": "unavailable"}
    elif leg.get("quoted_status_id_str"):
        # A "quote of a quote": X's API only hydrates one level of
        # quoted_status_result, so a tweet that quotes an already-quoting
        # tweet shows up here with just a stub ("quotedRefResult" containing
        # only a rest_id, or no result at all) instead of full tweet data.
        # Keep a lightweight stub; resolve_quote_chain() can fetch the full
        # tweet separately (this is the "quoted quote" nitter/X also miss).
        permalink = leg.get("quoted_status_permalink", {})
        expanded = permalink.get("expanded", "")
        m_sn = re.search(r"(?:twitter|x)\.com/([^/]+)/status", expanded)
        sn = m_sn.group(1) if m_sn else ""
        quoted = {"__stub": True, "id": leg["quoted_status_id_str"], "screen_name": sn, "permalink": expanded}

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
    # has_birdwatch_notes: true in TweetResultByRestId (guest) and TweetDetail when
    # the tweet has any Community Note activity (proposed or shown).
    has_birdwatch_notes = bool(result.get("has_birdwatch_notes")) or bool(bw)

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

    # Extract Grok share attachment
    grok_attachment = result.get("grok_share_attachment") or {}
    grok_items = grok_attachment.get("items", [])
    grok_question = grok_items[0].get("message", "") if len(grok_items) > 0 else ""
    grok_answer   = grok_items[1].get("message", "") if len(grok_items) > 1 else ""

    # Fallback: unified_card grok_share / image_website / video_website
    if raw_card:
        uc_str = bv.get("unified_card", {}).get("string_value", "")
        if uc_str:
            try:
                uc = json.loads(uc_str)
                uc_type = uc.get("type", "")
                comps = uc.get("component_objects", {})
                media_ents = uc.get("media_entities", {})

                if not grok_answer:
                    for comp in comps.values():
                        if comp.get("type") == "grok_share":
                            preview = comp.get("data", {}).get("conversation_preview", [])
                            for item in preview:
                                sender = item.get("sender", "")
                                msg    = item.get("message", "")
                                if sender == "USER" and not grok_question:
                                    grok_question = msg
                                elif sender == "AGENT" and not grok_answer:
                                    grok_answer = msg
                            break

                if not card and uc_type in ("image_website", "video_website") and not card:
                    details = next((c["data"] for c in comps.values() if c.get("type") == "details"), {})
                    uc_title  = details.get("title", {}).get("content", "")
                    uc_domain = details.get("subtitle", {}).get("content", "")
                    dest_key = details.get("destination", "")
                    dests = uc.get("destination_objects", {})
                    uc_url = (dests.get(dest_key) or next(iter(dests.values()), {})).get("data", {}).get("url_data", {}).get("url", "")
                    media_id_key = next((c["data"].get("id") for c in comps.values() if c.get("type") == "media"), None)
                    uc_media = media_ents.get(media_id_key, {}) if media_id_key else {}
                    uc_img = uc_media.get("media_url_https", "")
                    uc_mtype = uc_media.get("type", "photo")
                    uc_vi = uc_media.get("video_info", {})
                    card = {
                        "title":    uc_title,
                        "desc":     "",
                        "domain":   uc_domain,
                        "url":      uc_url,
                        "img_url":  uc_img,
                        "is_player": uc_mtype in ("video", "animated_gif"),
                        "uc_media": uc_media,
                        "uc_type":  uc_type,
                    }
            except (json.JSONDecodeError, AttributeError, StopIteration):
                pass

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
        "has_birdwatch_notes": has_birdwatch_notes,
        "broadcast_card":  broadcast_card,
        "grok_question":   grok_question,
        "grok_answer":     grok_answer,
        "rt_by_user":      None,
    }

def parse_tweet_detail(data, focal_id):
    instr   = data["data"]["threaded_conversation_with_injections_v2"]["instructions"]
    entries = next((i["entries"] for i in instr if i.get("type") == "TimelineAddEntries"), [])
    by_id = {}
    tombstones = {}  # id -> {screen_name, text, reason} extracted from entry id
    for e in entries:
        item   = e.get("content", {}).get("itemContent", {})
        result = item.get("tweet_results", {}).get("result", {})
        entry_id_raw = e.get("entryId", "")
        m = re.search(r"tweet-(\d+)", entry_id_raw)
        tid_guess = m.group(1) if m else None
        if not result:
            # Entirely empty tweet_results -- no tombstone object at all,
            # which in practice means the tweet was deleted.
            if tid_guess:
                tombstones[tid_guess] = {"__tombstone": True, "id": tid_guess,
                                          "text": "This Tweet was deleted.", "screen_name": "",
                                          "reason": "deleted"}
            continue
        if result.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
            if tid_guess:
                reason, tombstone_text = _classify_unavailable(result)
                tombstones[tid_guess] = {"__tombstone": True, "id": tid_guess, "text": tombstone_text,
                                          "screen_name": "", "reason": reason}
            continue
        entry_id = (result.get("legacy") or result.get("tweet", {}).get("legacy") or {}).get("id_str") or result.get("rest_id")
        t = _parse_tweet_result(result, _parse_user)
        if t:
            by_id[t["id"]] = t
            if entry_id and entry_id != t["id"]:
                by_id[entry_id] = t

    chain = []
    cur   = by_id.get(focal_id)
    if cur is None and focal_id:
        # The focal tweet itself couldn't be hydrated (deleted, suspended,
        # or an invalid/typo'd id) -- show a tombstone placeholder for it
        # instead of erroring out, same as we do for unavailable parents.
        if focal_id in tombstones:
            return [tombstones[focal_id]]
        return [{"__tombstone": True, "id": focal_id, "text": "This Tweet could not be found.",
                 "screen_name": "", "reason": "unavailable"}]
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
                                  "text": "This Tweet was deleted.", "reason": "deleted"})
            break
        cur = next_cur
    return chain if chain else list(by_id.values())

def parse_top_reply(data, focal_id, count=1):
    """Return up to `count` top-reply tweets (one per conversationthread entry) after the focal
    tweet.  TweetDetail is fetched with rankingMode=Likes so threads are already sorted."""
    instr   = data["data"]["threaded_conversation_with_injections_v2"]["instructions"]
    entries = next((i["entries"] for i in instr if i.get("type") == "TimelineAddEntries"), [])
    results = []
    focal_seen = False
    for e in entries:
        if len(results) >= count:
            break
        eid = e.get("entryId", "")
        if eid == f"tweet-{focal_id}":
            focal_seen = True
            continue
        if not focal_seen:
            continue
        if not eid.startswith("conversationthread-"):
            continue
        # Each conversationthread entry's first item is the lead reply tweet
        items = e.get("content", {}).get("items", [])
        for item in items:
            result = (item.get("item", {}).get("itemContent", {})
                         .get("tweet_results", {}).get("result", {}))
            if not result or result.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
                continue
            t = _parse_tweet_result(result, _parse_user)
            if t:
                results.append(t)
                break
    return results

def parse_tweet_result_single(data):
    result = data["data"]["tweetResult"]["result"]
    if result.get("__typename") in ("TweetTombstone", "TweetUnavailable"):
        reason, msg = _classify_unavailable(result)
        return [{"__tombstone": True, "id": result.get("rest_id"), "text": msg, "screen_name": "", "reason": reason}]
    return [_parse_tweet_result(result, _parse_user)]

_FULL_STATS = False   # set to True by --full-stats / config full_stats=true
_BIRD_ICON  = False   # set to True by --bird-icon / config bird_icon=true

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

def _linked_abs_time(t):
    """Render the focal tweet's absolute timestamp as a permalink, same as
    a real Nitter/X page would, without altering its existing text color."""
    label = abs_time(t.get("created_at"))
    link = _tweet_permalink(t.get("user", {}).get("screen_name"), t.get("id"))
    if link:
        return f'<a href="{link}" style="color:inherit;text-decoration:none;">{label}</a>'
    return label

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

    if tweet.get("__tombstone"):
        sn = tweet.get("screen_name", "")
        reason = tweet.get("reason", "unavailable")
        label = f"This Tweet was from @{sn} was {reason}." if sn else (tweet.get("text") or f"This Tweet was {reason}.")
        return label

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
    if _TWEET_BASE_URL:
        text = re.sub(r"#(\w+)", lambda m: f'<a href="{_TWEET_BASE_URL}/hashtag/{m.group(1)}">#{m.group(1)}</a>', text)
        text = re.sub(r"@(\w+)", lambda m: f'<a href="{_TWEET_BASE_URL}/{m.group(1)}">@{m.group(1)}</a>', text)
    else:
        text = re.sub(r"#(\w+)", r"#\1", text)
        text = re.sub(r"@(\w+)", r"@\1", text)
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
        f'<span>From </span>'
        f'<img class="attr-avatar" src="{avatar}" style="width: 16px; height: 16px; border-radius: 50%; object-fit: cover; display: inline-block; vertical-align: middle;">'
        f'<span style="display:inline-flex;align-items:center;margin-top:2px;">'
        f'<strong class="attr-name" style="color: var(--fg); font-weight: 700;">{attr["name"]}</strong>'
        f'{vicon.replace("margin:0 0 2px 4px", "margin:2px 0 0 4px")}'
        f'</span>'
        f'</div>'
    )

def _aspect(m):
    """Return width/height aspect ratio for a media item, falling back to 1.0."""
    oi = m.get("original_info", {})
    w, h = oi.get("width", 0), oi.get("height", 0)
    if w and h:
        return w / h
    lg = m.get("sizes", {}).get("large", {})
    w2, h2 = lg.get("w", 0), lg.get("h", 0)
    return (w2 / h2) if w2 and h2 else 1.0

def _ar_cols(aspects):
    """Convert aspect ratios to CSS grid-template-columns (fr units)."""
    total = sum(aspects)
    return " ".join(f"{a/total:.4f}fr" for a in aspects)

def media_html(ext_entities, is_ai=False):
    media_list = ext_entities.get("media", [])
    if not media_list:
        return ""

    parts = []
    aspects = []
    for m in media_list:
        aspects.append(_aspect(m))
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
        '<div class="ai-label" style="font-size:12px;color:var(--grey);margin-top:3px;display:flex;align-items:center;gap:0;">'
        + icon_svg("robot", 18, "var(--grey)")
        + 'Made with AI'
        '</div>'
    ) if is_ai else ""

    n = len(parts)
    if n == 5:
        top_cols = _ar_cols(aspects[0:2])
        bot_cols = _ar_cols(aspects[2:5])
        grid = (
            f'<div class="media-grid-5">'
            f'<div class="row-top" style="grid-template-columns:{top_cols}">'
            f'<div class="grid-item">{parts[0]}</div>'
            f'<div class="grid-item">{parts[1]}</div>'
            f'</div>'
            f'<div class="row-bottom" style="grid-template-columns:{bot_cols}">'
            f'<div class="grid-item">{parts[2]}</div>'
            f'<div class="grid-item">{parts[3]}</div>'
            f'<div class="grid-item">{parts[4]}</div>'
            f'</div></div>'
        )
    elif n == 4:
        top_cols = _ar_cols(aspects[0:2])
        bot_cols = _ar_cols(aspects[2:4])
        grid = (
            f'<div class="media-grid-2x2">'
            f'<div class="media-grid-row" style="display:grid;grid-template-columns:{top_cols};gap:3px;">'
            f'<div class="grid-item">{parts[0]}</div>'
            f'<div class="grid-item">{parts[1]}</div>'
            f'</div>'
            f'<div class="media-grid-row" style="display:grid;grid-template-columns:{bot_cols};gap:3px;">'
            f'<div class="grid-item">{parts[2]}</div>'
            f'<div class="grid-item">{parts[3]}</div>'
            f'</div></div>'
        )
    elif n == 3:
        top_cols = _ar_cols(aspects[0:2])
        grid = (
            f'<div class="media-grid-3">'
            f'<div class="media-grid-row" style="display:grid;grid-template-columns:{top_cols};gap:3px;">'
            f'<div class="grid-item">{parts[0]}</div>'
            f'<div class="grid-item">{parts[1]}</div>'
            f'</div>'
            f'<div class="grid-item">{parts[2]}</div>'
            f'</div>'
        )
    elif n == 2:
        cols = _ar_cols(aspects)
        grid = (
            f'<div class="media-row" style="grid-template-columns:{cols}">'
            f'{"".join(parts)}'
            f'</div>'
        )
    else:
        # Single image: let it define its own height naturally
        grid = f'<div class="media-row single-image">{"".join(parts)}</div>'

    return grid + ai_label


GLYPHS = {
    "comment": ("M1000 350q0-97-67-179t-182-130-251-48q-39 0-81 4-110-97-257-135-27-8-63-12-10-1-17 5t-10 16v1q-2 2 0 6t1 6 2 5l4 5t4 5 4 5q4 5 17 19t20 22 17 22 18 28 15 33 15 42q-88 50-138 123t-51 157q0 73 40 139t109 115 163 76 197 28q135 0 251-48t182-130 67-179z", 1000),
    "retweet": ("M714 11q0-7-5-13t-13-5h-535q-5 0-8 1t-5 4-3 4-2 7 0 6v335h-107q-15 0-25 11t-11 25q0 13 8 23l179 214q11 12 27 12t28-12l178-214q9-10 9-23 0-15-11-25t-25-11h-107v-214h321q9 0 14-6l89-108q4-5 4-11z m357 232q0-13-8-23l-178-214q-12-13-28-13t-27 13l-179 214q-8 10-8 23 0 14 11 25t25 11h107v214h-322q-9 0-14 7l-89 107q-4 5-4 11 0 7 5 12t13 6h536q4 0 7-1t5-4 3-5 2-6 1-7v-334h107q14 0 25-11t10-25z", 1071),
    "quote":   ("M18 685l335 0 0-334q0-140-98-238t-237-97l0 111q92 0 158 65t65 159l-223 0 0 334z m558 0l335 0 0-334q0-140-98-238t-237-97l0 111q92 0 158 65t65 159l-223 0 0 334z", 928),
    "heart":   ("M790 644q70-64 70-156t-70-158l-360-330-360 330q-70 66-70 158t70 156q62 58 151 58t153-58l56-52 58 52q62 58 150 58t152-58z", 860),
    "views":   ("M180 516l0-538-180 0 0 538 180 0z m250-138l0-400-180 0 0 400 180 0z m250 344l0-744-180 0 0 744 180 0z", 680),
    "group":   ("M0 106l0 134q0 26 18 32l171 80q-66 39-68 131 0 56 35 103 37 41 90 43 31 0 63-19-49-125 23-237-12-11-25-19l-114-55q-48-23-52-84l0-143-114 0q-25 0-27 34z m193-59l0 168q0 27 22 37l152 70 57 28q-37 23-60 66t-22 94q0 76 46 130t110 54 109-54 45-130q0-105-78-158l61-30 146-70q24-10 24-37l0-168q-2-37-37-41l-541 0q-14 2-24 14t-10 27z m473 330q68 106 22 231 31 19 66 21 49 0 90-43 35-41 35-103 0-82-65-131l168-80q18-10 18-32l0-134q0-32-27-34l-118 0 0 143q0 57-50 84l-110 53q-15 8-29 25z", 1000),
    "robot":   ("M409.6 758.0 c-26.2 -9.8 -39.2 -37.0 -29.6 -62.4 1.7 -4.5 5.1 -10.6 7.6 -13.6 6.1 -7.3 17.3 -14.6 20.6 -13.4 2.2 0.8 2.3 0.5 0.7 -1.3 -1.7 -1.5 -2.2 -11.8 -2.2 -39.2 l0.0 -36.9 -96.6 -0.5 -96.8 -0.5 -8.3 -3.8 c-11.3 -5.1 -22.9 -17.1 -28.1 -28.7 l-4.3 -9.5 0.3 -186.6 0.5 -186.4 4.8 -8.8 c6.1 -11.0 15.3 -19.3 27.4 -25.1 l9.3 -4.3 210.0 0.0 210.0 0.0 9.3 4.3 c12.1 5.8 21.2 14.1 27.4 25.1 l4.8 8.8 0.5 186.4 0.3 186.6 -4.3 9.5 c-5.1 11.6 -16.8 23.6 -28.1 28.7 l-8.3 3.8 -96.6 0.5 -96.8 0.5 0.0 36.9 c0.0 27.4 -0.5 37.7 -2.0 39.2 -1.8 1.8 -1.7 2.2 0.5 1.3 6.6 -2.3 23.1 13.3 28.2 26.9 6.6 17.8 1.2 41.3 -12.3 52.6 -12.9 10.8 -34.0 15.3 -48.1 10.0z m-69.7 -305.6 c17.1 -5.0 28.6 -17.9 32.4 -35.9 3.8 -18.3 -6.5 -38.2 -24.4 -47.1 -25.9 -12.9 -55.0 0.3 -63.3 28.7 -2.3 7.8 -2.5 11.0 -1.0 18.4 3.7 17.6 14.1 29.6 30.7 35.2 10.6 3.8 14.9 3.8 25.6 0.7z m194.2 0.0 c17.1 -5.0 28.6 -17.9 32.4 -35.9 3.8 -18.3 -6.5 -38.2 -24.4 -47.1 -22.6 -11.3 -47.5 -3.3 -59.8 19.4 -7.0 12.6 -7.0 26.9 0.0 40.3 5.8 11.5 13.9 18.4 26.1 22.6 10.8 3.8 14.9 3.8 25.7 0.7z M124.5 456.7 c-14.4 -4.5 -28.9 -17.3 -35.4 -31.4 -3.5 -7.5 -3.7 -10.0 -3.7 -60.9 l0.0 -53.1 4.3 -9.3 c6.0 -12.8 14.9 -22.1 26.1 -27.6 8.6 -4.0 11.6 -4.5 32.4 -5.1 l22.7 -0.7 0.0 94.8 0.0 94.8 -21.1 -0.2 c-11.8 0.0 -23.1 -0.7 -25.4 -1.3z M679.0 363.4 l0.0 -94.8 22.9 0.7 c18.6 0.5 24.2 1.3 30.5 4.2 11.5 5.1 21.7 15.6 27.2 27.6 l4.8 10.3 0.0 53.1 0.0 53.1 -4.6 9.5 c-6.1 12.5 -19.6 24.7 -31.0 28.4 -6.3 2.0 -14.4 2.8 -29.2 2.8 l-20.6 0.0 0.0 -94.8z", 1000),
    "mask":    ("M8.575 3.085 C9.977 3.051 12.629 2.773 14.122 1.953 C14.417 1.791 14.909 2.005 14.911 2.341 C14.946 6.644 14.994 12.710 10.105 14.781 C9.618 14.987 9.097 15.094 8.575 15.102 L8.575 12.106 C10.752 12.088 12.104 11.111 12.560 10.499 C12.753 10.241 12.699 9.875 12.441 9.682 C12.182 9.490 11.816 9.543 11.624 9.802 C11.405 10.095 10.412 10.920 8.575 10.938 L8.575 3.085 Z M8.575 3.085 C8.545 3.086 8.515 3.086 8.486 3.086 C7.101 3.059 4.393 2.785 2.878 1.953 C2.583 1.791 2.091 2.005 2.089 2.341 C2.054 6.644 2.006 12.710 6.895 14.781 C7.429 15.007 8.003 15.114 8.575 15.101 L8.575 12.105 C8.545 12.106 8.516 12.106 8.486 12.106 C6.273 12.106 4.900 11.117 4.440 10.499 C4.247 10.241 4.301 9.875 4.559 9.682 C4.818 9.490 5.184 9.543 5.376 9.802 C5.597 10.099 6.610 10.939 8.486 10.939 C8.516 10.939 8.545 10.938 8.575 10.938 L8.575 3.085 Z M12.910 6.608 C12.947 6.241 12.568 6.024 12.212 6.119 L11.083 6.422 L9.954 6.724 C9.598 6.819 9.379 7.197 9.594 7.496 C9.605 7.511 9.616 7.525 9.627 7.539 C9.773 7.731 9.956 7.891 10.165 8.012 C10.374 8.132 10.605 8.210 10.844 8.242 C11.083 8.273 11.326 8.257 11.558 8.195 C11.791 8.133 12.010 8.025 12.201 7.878 C12.392 7.731 12.553 7.548 12.673 7.340 C12.794 7.131 12.872 6.900 12.903 6.661 C12.906 6.643 12.908 6.626 12.910 6.608 Z M7.576 7.553 C7.632 7.917 7.265 8.154 6.905 8.077 L5.761 7.834 L4.618 7.591 C4.258 7.515 4.019 7.149 4.218 6.839 C4.228 6.824 4.238 6.809 4.248 6.794 C4.385 6.596 4.559 6.426 4.761 6.295 C4.964 6.163 5.190 6.073 5.427 6.029 C5.664 5.985 5.907 5.988 6.143 6.039 C6.379 6.089 6.602 6.185 6.801 6.321 C7.000 6.458 7.170 6.632 7.301 6.834 C7.432 7.037 7.523 7.263 7.566 7.500 C7.570 7.518 7.573 7.535 7.576 7.553 Z", 17),
}

def icon_svg(name, size=13, color="currentColor"):
    d, adv = GLYPHS[name]
    if name == "mask":
        return (f'<svg width="{size}" height="{size}" viewBox="0 0 17 17" '
                f'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle;flex-shrink:0">'
                f'<path d="{d}" fill="{color}" fill-rule="evenodd" clip-rule="evenodd"/></svg>')
    w = adv * size / 1000
    return (f'<svg width="{w:.1f}" height="{size}" viewBox="0 150 {adv} 850" '
            f'xmlns="http://www.w3.org/2000/svg" style="display:inline-block;vertical-align:middle;flex-shrink:0">'
            f'<g transform="scale(1,-1) translate(0,-850)"><path d="{d}" fill="{color}"/></g></svg>')

def bird_svg(size=16, color="var(--grey)"):
    """Classic Twitter 'bird' glyph, used in the header top-right slot in place of the
    relative timestamp when --bird-icon / config bird_icon=true is set."""
    d = ("M23.643 4.937c-.835.37-1.732.62-2.675.733.962-.576 1.7-1.49 2.048-2.578-.9.534-1.897.922-2.958 "
         "1.13-.85-.904-2.06-1.47-3.4-1.47-2.572 0-4.658 2.086-4.658 4.66 0 .364.042.718.12 "
         "1.06-3.873-.195-7.304-2.05-9.602-4.868-.4.69-.63 1.49-.63 2.342 0 1.616.823 3.043 2.072 "
         "3.878-.764-.025-1.482-.234-2.11-.583v.06c0 2.257 1.605 4.14 3.737 4.568-.392.106-.803.162-1.227 "
         ".162-.3 0-.593-.028-.877-.082.593 1.85 2.313 3.198 4.352 3.234-1.595 1.25-3.604 1.995-5.786 "
         "1.995-.376 0-.747-.022-1.112-.065 2.062 1.323 4.51 2.093 7.14 2.093 8.57 0 13.255-7.098 "
         "13.255-13.254 0-.2-.005-.402-.014-.602.91-.658 1.7-1.477 2.323-2.41z")
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg" '
            f'style="display:inline-block;vertical-align:middle;flex-shrink:0">'
            f'<path fill="{color}" d="{d}"/></svg>')

def verified_svg(verified_type, is_blue):
    if is_blue and not verified_type:   fill, stroke = "#1d9bf0", "white"
    elif verified_type == "Business":   fill, stroke = "#e7b332", "black"
    elif verified_type == "Government": fill, stroke = "#829aab", "black"    
    else: return ""
    return (f'<svg width="12" height="12" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg" '
            f'style="display:inline-block;vertical-align:middle;margin:0 0 2px 4px">'
            f'<circle cx="9" cy="9" r="9" fill="{fill}"/>'
            f'<polyline points="4.5,10 7.5,13 13.5,7" stroke="{stroke}" stroke-width="2.2" '
            f'fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>')

def parody_label_html(label):
    """Return a small grey theatre-mask + label row for Parody/Commentary/Fan accounts."""
    if not label or str(label).strip().lower() in {"none", "null"}:
        return ""
    return (
        f'<div class="parody-label" style="display:inline-flex;align-items:center;gap:3px;'
        f'color:var(--grey);font-size:12px;line-height:1;margin-top:1px;">'
        f'{icon_svg("mask", 12, "var(--grey)")}'
        f'<span>{label} account</span>'
        f'</div>'
    )

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
    --qt-bg:   #222222;
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

.tweet-row { display: flex; padding: 14px 14px 0; }
.tweet-row:last-child { padding-bottom: 14px; }
.left-col { display: flex; flex-direction: column; align-items: center; flex-shrink: 0; width: 46px; margin-right: 10px; }
.avatar { width: 46px; height: 46px; border-radius: 23px; display: block; }
.thread-line { width: 2px; flex: 1; min-height: 6px; background: var(--acc); margin: 3px 0; }
.right-col { flex: 1; overflow: hidden; padding-bottom: 12px; }
.tweet-row:last-child .right-col { padding-bottom: 0; }
.tweet-header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 1px; }
.tweet-header-left { display: flex; flex-direction: column; align-items: flex-start; flex: 1; overflow: hidden; }
.tweet-header-left > * { margin-right: 0; }
.fullname { font-weight: 700; font-size: 15px; white-space: nowrap; }
.username { color: var(--accent); font-size: 14px; white-space: nowrap; padding-left: 4px; }
.tweet-time { color: var(--accent); font-size: 14px; white-space: nowrap; flex-shrink: 0; margin-left: 8px; }
.replying-to { color: var(--grey); font-size: 13px; margin-bottom: 3px; line-height: 1.4; }
.tweet-content { font-size: 15px; margin: 4px 0 0; line-height: 1.3em; white-space: pre-wrap; word-wrap: break-word; }
.focal .tweet-content { font-size: 17px; }
.focal .tweet-date { color: var(--grey); font-size: 13px; margin-bottom: 0; padding-top: 6px; }
.stats { display: flex; align-items: center; color: var(--grey); font-size: 13px; padding-top: 8px; }
.stat { white-space: nowrap; margin-right: 10px; }
.stat svg { margin: 0 1px 0 0; }
.source { margin-left: auto; font-size: 12px; }
.media-row { display: grid; margin: 6px 0; border-radius: 10px; overflow: hidden; gap: 3px; }
.media-row .attachment { min-height: 0; overflow: hidden; }
.media-row .attachment img { width: 100%; height: 100%; object-fit: cover; display: block; }
.media-row.single-image .attachment img { height: auto; max-height: 510px; object-fit: contain; }
.media-grid-2x2 { display: flex; flex-direction: column; gap: 3px; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-2x2 .grid-item { position: relative; overflow: hidden; }
.media-grid-2x2 .grid-item img { width: 100%; height: 100%; object-fit: cover; display: block; }

.media-grid-3 { display: flex; flex-direction: column; gap: 3px; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-3 .grid-item { position: relative; overflow: hidden; }
.media-grid-3 .grid-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.media-grid-5 { display: flex; flex-direction: column; gap: 3px; margin: 6px 0; border-radius: 10px; overflow: hidden; }
.media-grid-5 .grid-item { position: relative; overflow: hidden; }
.media-grid-5 .grid-item img { width: 100%; height: 100%; object-fit: cover; display: block; }
.media-grid-5 .row-top { display: grid; gap: 3px; }
.media-grid-5 .row-bottom { display: grid; gap: 3px; }
.attachment img { width: 100%; display: block; }
.video-wrap { position: relative; max-height: 510px; overflow: hidden; display: flex; justify-content: center; background: #000; }
.video-wrap img { width: auto; height: auto; max-width: 100%; max-height: 510px; object-fit: contain; display: block; }
.play-overlay { position: absolute; top: 0; left: 0; right: 0; bottom: 0; display: flex; align-items: center; justify-content: center; pointer-events: none; }
.vid-duration { position: absolute; bottom: 6px; left: 8px; background: rgba(0,0,0,0.6); color: #fff; font-size: 12px; font-weight: 600; line-height: 1; padding: 3px 5px; border-radius: 4px; pointer-events: none; }
.media-attribution { display: flex; align-items: center; gap: 6px; margin: 6px 0 4px; }
.attr-avatar { width: 24px; height: 24px; border-radius: 50%; display: block; flex-shrink: 0; }
.attr-name { font-size: 14px; font-weight: 700; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.quote-block { border: 1px solid var(--border); border-radius: 10px; padding: 10px 12px 10px; margin: 6px 0; background: var(--qt-bg); overflow: hidden; }
.quote-block.has-media { padding-bottom: 0; }
.quote-header { display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 4px; line-height: 1; }
.quote-header > * { margin-right: 4px; }
.quote-avatar { width: 20px; height: 20px; border-radius: 10px; display: inline-block; }
.quote-name { font-weight: 700; font-size: 14px; margin-right: 0; }
.quote-sn { color: var(--accent); font-size: 13px; padding-left: 4px; }
.quote-time { color: var(--grey); font-size: 13px; margin-left: auto; }
.quote-text { font-size: 14px; line-height: 1.3em; white-space: pre-wrap; word-wrap: break-word; }
.quote-media { margin: 6px -12px 0; overflow: hidden; border-radius: 0 0 10px 10px; }
.quote-media > img { width: 100%; display: block; }
.quote-media .video-wrap { max-height: 400px; background: #000; }
.quote-media .video-wrap img { width: auto; height: auto; max-width: 100%; max-height: 400px; object-fit: contain; }
.quote-media .media-row { margin: 0; border-radius: 0; }
.quote-media .media-grid-2x2 { margin: 0; border-radius: 0; }
.quote-media .media-grid-3 { margin: 0; border-radius: 0; }
.quote-card { margin: 6px -12px 0; overflow: hidden; border-radius: 0 0 10px 10px; }
.quote-card .card { border: none; border-radius: 0; margin: 0; }
/* "Quote of a quote": a smaller quote-block nested inside another one */
.quote-block .quote-block { margin: 8px 0 0; border-radius: 8px; }
.quote-block .quote-block .quote-avatar { width: 16px; height: 16px; border-radius: 8px; }
.quote-block .quote-block .quote-name { font-size: 13px; }
.quote-block .quote-block .quote-sn { font-size: 12px; }
.quote-block .quote-block .quote-time { font-size: 12px; }
.quote-block .quote-block .quote-text { font-size: 13px; }
.quote-block .quote-block .quote-media { margin: 6px -10px 0; }
.quote-block .quote-block .quote-card { margin: 6px -10px 0; }
.quote-stub { padding: 9px 12px; }
.quote-stub-link { display: flex; align-items: center; gap: 5px; font-size: 13px; color: var(--accent); text-decoration: none; }
.quote-stub-link:hover { text-decoration: underline; }
.birdwatch { border: 1px solid var(--border); border-radius: 10px; margin: 6px 0; background: var(--bw-bg); overflow: hidden; }
.birdwatch.proposed { border-style: dashed; opacity: 0.92; }
.community-note-header { background-color: var(--bg-hover); font-weight: 700; font-size: 13px; padding: 6px 10px 8px; display: flex; align-items: center; gap: 12px; color: var(--fg); }
.community-note-header .icon-container { flex-shrink: 0; color: var(--accent); }
.community-note-text { font-size: 13px; color: var(--fg); white-space: pre-line; padding: 6px 10px 10px; }
.card { border: 1px solid var(--border); border-radius: 10px; margin: 6px 0; overflow: hidden; display: flex; flex-direction: column; }
.card-img { width: 100%; display: block; max-height: 220px; object-fit: cover; }
.card-body { padding: 8px 12px 10px; }
.card-domain { font-size: 12px; color: var(--grey); text-transform: uppercase; margin-bottom: 2px; }
.card-title { font-size: 14px; font-weight: 700; line-height: 1.3; margin-bottom: 2px; }
.card-desc { font-size: 13px; color: var(--grey); line-height: 1.4; }
.tweet-row.focal { flex-direction: column; padding: 0; }
.focal-header { display: flex; align-items: center; padding: 14px 14px 8px; gap: 12px; }
.focal-header .avatar { width: 46px; height: 46px; border-radius: 23px; flex-shrink: 0; }
.focal-header-names { display: flex; flex-direction: column; justify-content: center; line-height: 1.25; }
.focal-header-top { display: flex; align-items: center; gap: 0; }
.focal-header-top .fullname { font-size: 15px; font-weight: 700; }
.focal-header-bottom { display: flex; flex-direction: column; align-items: flex-start; }
.focal-header-bottom .username { color: var(--accent); font-size: 14px; padding-left: 0; }
.focal-body { padding: 0 14px 14px; }
.rt-header { display: flex; align-items: center; color: var(--grey); font-size: 13px; font-weight: 700; padding: 14px 14px 0 53px; gap: 5px; }
.rt-header svg { flex-shrink: 0; }
.focal-header.has-rt { padding-top: 0; }
.top-reply-divider { height: 1px; background: var(--border); margin: 0 14px; }
.tweet-row.top-reply { padding-top: 10px; }
"""

_LANG_NAMES = {
    "af": "Afrikaans", "sq": "Albanian", "am": "Amharic", "ar": "Arabic",
    "hy": "Armenian", "az": "Azerbaijani", "eu": "Basque", "be": "Belarusian",
    "bn": "Bengali", "bs": "Bosnian", "bg": "Bulgarian", "ca": "Catalan",
    "ceb": "Cebuano", "zh": "Chinese", "co": "Corsican", "hr": "Croatian",
    "cs": "Czech", "da": "Danish", "nl": "Dutch", "en": "English",
    "eo": "Esperanto", "et": "Estonian", "fi": "Finnish", "fr": "French",
    "fy": "Frisian", "gl": "Galician", "ka": "Georgian", "de": "German",
    "el": "Greek", "gu": "Gujarati", "ht": "Haitian Creole", "ha": "Hausa",
    "haw": "Hawaiian", "he": "Hebrew", "iw": "Hebrew", "hi": "Hindi", "hmn": "Hmong",
    "hu": "Hungarian", "is": "Icelandic", "ig": "Igbo", "id": "Indonesian",
    "ga": "Irish", "it": "Italian", "ja": "Japanese", "jv": "Javanese",
    "kn": "Kannada", "kk": "Kazakh", "km": "Khmer", "rw": "Kinyarwanda",
    "ko": "Korean", "ku": "Kurdish", "ky": "Kyrgyz", "lo": "Lao",
    "la": "Latin", "lv": "Latvian", "lt": "Lithuanian", "lb": "Luxembourgish",
    "mk": "Macedonian", "mg": "Malagasy", "ms": "Malay", "ml": "Malayalam",
    "mt": "Maltese", "mi": "Maori", "mr": "Marathi", "mn": "Mongolian",
    "my": "Myanmar", "ne": "Nepali", "no": "Norwegian", "ny": "Nyanja",
    "or": "Odia", "ps": "Pashto", "fa": "Persian", "pl": "Polish",
    "pt": "Portuguese", "pa": "Punjabi", "ro": "Romanian", "ru": "Russian",
    "sm": "Samoan", "gd": "Scots Gaelic", "sr": "Serbian", "st": "Sesotho",
    "sn": "Shona", "sd": "Sindhi", "si": "Sinhala", "sk": "Slovak",
    "sl": "Slovenian", "so": "Somali", "es": "Spanish", "su": "Sundanese",
    "sw": "Swahili", "sv": "Swedish", "tl": "Filipino", "tg": "Tajik",
    "ta": "Tamil", "tt": "Tatar", "te": "Telugu", "th": "Thai",
    "tr": "Turkish", "tk": "Turkmen", "uk": "Ukrainian", "ur": "Urdu",
    "ug": "Uyghur", "uz": "Uzbek", "vi": "Vietnamese", "cy": "Welsh",
    "xh": "Xhosa", "yi": "Yiddish", "yo": "Yoruba", "zu": "Zulu",
}

# Twitter-specific language codes that cannot be translated.
# See: https://github.com/igorbrigadir/twitter-advanced-search#supported-languages
_UNTRANSLATABLE_LANGS = {"und", "qam", "qct", "qht", "qme", "qst", "zxx"}

def _lang_display_name(code):
    """Return a human-readable name for a BCP-47 language code, e.g. 'ja' -> 'Japanese'."""
    if not code or code == "auto":
        return code or "Unknown"
    primary = code.split("-")[0].lower()
    return _LANG_NAMES.get(primary, code)

# deep-translator's GoogleTranslator backend is case-sensitive (rejects "EN",
# only accepts "en") and a handful of codes don't match ISO 639-1/BCP-47 at
# all: bare "zh" is rejected (only the region-qualified "zh-CN"/"zh-TW"
# work), and Hebrew/Javanese still use old codes ("iw"/"jw") rather than the
# modern ones ("he"/"jv") that X's API and most of the world use.
_GTRANS_LANG_FIXUPS = {
    "zh": "zh-CN", "zh-cn": "zh-CN", "zh-hans": "zh-CN", "zh-sg": "zh-CN",
    "zh-tw": "zh-TW", "zh-hant": "zh-TW", "zh-hk": "zh-TW", "zh-mo": "zh-TW",
    "he": "iw", "jv": "jw",
}

def _gtrans_lang(code):
    """Map a language code to the exact form GoogleTranslator (deep-translator)
    expects. See _GTRANS_LANG_FIXUPS for why this is needed."""
    if not code:
        return code
    low = code.strip().lower()
    if low == "auto":
        return "auto"
    return _GTRANS_LANG_FIXUPS.get(low, low)

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
        src = _gtrans_lang(source_lang)
        tgt = _gtrans_lang(target_lang)
        translated = GoogleTranslator(source=src, target=tgt).translate(text)
        return translated or text
    except Exception as e:
        print(f"Warning: translation failed ({e}), using original text.", file=sys.stderr)
        return text

def _trans_label_html(lang_name):
    """Return a tiny 'Translated from X' label in accent colour, or empty string."""
    if not lang_name:
        return ""
    return (
        f'<div class="translated-from" style="font-size:10px;color:var(--accent);'
        f'margin-bottom:3px;line-height:1.3;opacity:0.75;">'
        f'Translated from {lang_name}</div>'
    )

def _birdwatch_note_html(text, entities, shown=True, is_misleading=True):
    """Render a single Community Note box from raw note text + entities.
    Shared by the tweet's official birdwatch_pivot note and by notes fetched
    via --with-note/--with-notes (BirdwatchFetchNotes). Notes that aren't
    currently shown on Twitter (rating_status != CurrentlyRatedHelpful) are
    labelled 'Proposed' and rendered with a dashed border so they're not
    mistaken for X's own verdict; among proposed notes, ones from the
    not_misleading_birdwatch_notes group get an extra '- Not Misleading'
    suffix so they're not confused with notes proposing the tweet IS
    misleading."""
    ents = [e for e in entities if e.get("fromIndex") is not None and e.get("toIndex") is not None]
    ents.sort(key=lambda e: e["fromIndex"], reverse=True)
    for e in ents:
        start, end = e["fromIndex"], e["toIndex"]
        ref = e.get("ref", {})
        href = ref.get("url", "")
        display = text[start:end]
        if "help.x.com" in href or "help.x.com" in display:
            text = text[:start] + text[end:]
            continue
        if href:
            # ref.url is always a t.co shortlink; the text slice at
            # [fromIndex:toIndex] is already the expanded destination URL,
            # so use it as both the visible text and the href.
            text = text[:start] + f'<a href="{display}">{display}</a>' + text[end:]
    # Birdwatch entity lists only carry TimelineUrl entries; bare @mentions
    # and #hashtags in note text are untracked, so linkify them here using
    # the same base URL as the main tweet body.
    if _TWEET_BASE_URL:
        text = re.sub(r"@(\w+)", lambda m: f'<a href="{_TWEET_BASE_URL}/{m.group(1)}">@{m.group(1)}</a>', text)
        text = re.sub(r"#(\w+)", lambda m: f'<a href="{_TWEET_BASE_URL}/hashtag/{m.group(1)}">#{m.group(1)}</a>', text)
    if shown:
        label = "Community Note"
    elif is_misleading:
        label = "Proposed Community Note"
    else:
        label = "Proposed Community Note - Not Misleading"
    cls = "birdwatch" if shown else "birdwatch proposed"
    return f'''<div class="{cls}">
          <div class="community-note-header"><span class="icon-container">{icon_svg("group", 13, "var(--accent)")}</span> {label}</div>
          <div class="community-note-text">{text}</div>
        </div>'''

def _birdwatch_html(t):
    """Return Community Note box(es) for a parsed tweet dict, or empty string.
    Shared by the focal/parent tweet path and quote_block_html, since a
    quoted tweet can carry its own birdwatch note independent of the
    tweet quoting it. Renders the tweet's official (currently-shown) note
    first, followed by any extra notes attached via --with-note/--with-notes:
    misleading-group notes first (already ranked top-first by
    parse_birdwatch_fetch_notes), then not-misleading-group notes, each
    flagged 'Proposed' unless independently CurrentlyRatedHelpful."""
    parts = []
    if t.get("birdwatch"):
        parts.append(_birdwatch_note_html(t["birdwatch"], t.get("birdwatch_ents", []), shown=True))
    for n in t.get("proposed_notes") or []:
        if n["shown"] and n["text"] == t.get("birdwatch"):
            continue  # same note X already surfaces via birdwatch_pivot, don't duplicate
        parts.append(_birdwatch_note_html(n["text"], n["entities"], shown=n["shown"], is_misleading=n["is_misleading"]))
    return "".join(parts)

def quote_block_html(qt, depth=0):
    if not qt: return ""
    if qt.get("__tombstone"):
        sn = qt.get("screen_name", "")
        link = _nitter_link(qt.get("permalink", ""))
        reason = qt.get("reason", "unavailable")
        label = f"This tweet from @{sn} is {reason}." if sn else qt.get("text", "This tweet is unavailable.")
        inner = f'<a href="{link}" style="color:inherit;text-decoration:none;">{label}</a>' if link else label
        return f'''<div class="quote-block" style="display:flex;align-items:center;justify-content:center;padding:16px 14px;">
  <span style="color:#7b93a8;font-size:15px;line-height:1.4;text-align:center;">{inner}</span>
</div>'''
    if qt.get("__stub"):
        # Nested quote X never hydrated for us (a "quote of a quote") and we
        # weren't able to (or didn't try to) fetch it separately -- show a
        # plain link instead of silently dropping the context.
        sn   = qt.get("screen_name", "")
        link = _nitter_link(qt.get("permalink", ""))
        label = f"Quoted @{sn}'s post" if sn else "Quoted another post"
        open_tag  = f'<a class="quote-stub-link" href="{link}">' if link else '<span class="quote-stub-link">'
        close_tag = "</a>" if link else "</span>"
        return f'''<div class="quote-block quote-stub">
  {open_tag}{icon_svg("quote", 13, "var(--accent)")}<span>{label}</span>{close_tag}
</div>'''
    u    = qt["user"]
    text = linkify(qt["full_text"], qt["entities"])
    vicon = verified_svg(u.get("verified_type"), u.get("is_blue_verified", False))
    time  = rel_time(qt["created_at"])
    time_link = _tweet_permalink(u.get("screen_name"), qt.get("id"))
    if time_link:
        time = f'<a href="{time_link}" style="color:inherit;text-decoration:none;">{time}</a>'
    media = ""
    mlist = qt["ext_entities"].get("media", [])
    if mlist:
        if len(mlist) == 1:
            m = mlist[0]
            if m["type"] in ("video", "animated_gif"):
                vi = m.get("video_info", {})
                dur_ms = vi.get("duration_millis", 0)
                dur_label = _fmt_duration(dur_ms) if m["type"] == "video" else ""
                dur_html = f'<div class="vid-duration">{dur_label}</div>' if dur_label else ""
                media = (
                    f'<div class="quote-media">'
                    f'<div class="video-wrap">'
                    f'<img src="{m["media_url_https"]}">'
                    f'<div class="play-overlay">{PLAY_SVG}</div>'
                    f'{dur_html}'
                    f'</div></div>'
                )
            else:
                media = f'<div class="quote-media"><img src="{m["media_url_https"]}"></div>'
        else:
            media = f'<div class="quote-media">{media_html(qt["ext_entities"])}</div>'
    has_media_cls = " has-media" if media else ""
    qcard = ""
    if not media and qt.get("card"):
        qcard = f'<div class="quote-card">{card_html(qt["card"])}</div>'
        has_media_cls = " has-media"
    # A quote tweet can itself quote another tweet ("quote of a quote"); when
    # that inner quote was resolved (see resolve_quote_chain), render it as a
    # smaller quote-block nested inside this one, same as X does.
    nested = ""
    if depth < 2 and qt.get("quoted"):
        nested = quote_block_html(qt["quoted"], depth + 1)
    bw_html = _birdwatch_html(qt)
    return f"""<div class="quote-block{has_media_cls}">
  <div class="quote-header">
    <img class="quote-avatar" src="{u["avatar_url"]}">
    <span class="quote-name">{u["name"]}</span>{vicon}
    <span class="quote-sn">@{u["screen_name"]}</span>
    <span class="quote-time">{time}</span>
  </div>
  {_trans_label_html(qt.get("translated_from"))}
  <div class="quote-text">{text}</div>
  {media}
  {qcard}
  {nested}
  {bw_html}
</div>"""

GROK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 33 32" width="16" height="16" '
    'style="display:inline-block;vertical-align:middle;margin-right:5px;flex-shrink:0">'
    '<path fill="var(--grey)" d="M 12.745 20.54 l 10.97 -8.19 c 0.539 -0.4 1.307 -0.244 1.564 0.38'
    ' c 1.349 3.288 0.746 7.241 -1.938 9.955 c -2.683 2.714 -6.417 3.31 -9.83 1.954 l -3.728 1.745'
    ' c 5.347 3.697 11.84 2.782 15.898 -1.324 c 3.219 -3.255 4.216 -7.692 3.284 -11.693 l 0.008 0.009'
    ' c -1.351 -5.878 0.332 -8.227 3.782 -13.031 L 33 0 l -4.54 4.59 v -0.014 L 12.743 20.544'
    ' m -2.263 1.987 c -3.837 -3.707 -3.175 -9.446 0.1 -12.755 c 2.42 -2.449 6.388 -3.448 9.852 -1.979'
    ' l 3.72 -1.737 c -0.67 -0.49 -1.53 -1.017 -2.515 -1.387 c -4.455 -1.854 -9.789 -0.931 -13.41 2.728'
    ' c -3.483 3.523 -4.579 8.94 -2.697 13.561 c 1.405 3.454 -0.899 5.898 -3.22 8.364'
    ' C 1.49 30.2 0.666 31.074 0 32 l 10.478 -9.466"/>'
    '</svg>'
)

def _linkify_md(text):
    """Replace markdown [label](url) / [](url) with <a> tags in already-escaped text.
    The input *text* must NOT yet be html-escaped; escaping is done here per segment."""
    import html as _html
    parts = re.split(r'(\[[^\]]*\]\([^)]+\))', text)
    out = []
    for p in parts:
        m = re.match(r'\[([^\]]*)\]\(([^)]+)\)', p)
        if m:
            label, url = m.group(1).strip(), m.group(2).strip()
            display = _html.escape(label) if label else _html.escape(url)
            out.append(f'<a href="{_html.escape(url)}">{display}</a>')
        else:
            out.append(_html.escape(p))
    return "".join(out)

def _apply_inline_md(raw):
    """Apply bold and link markdown to a raw (unescaped) string, return HTML."""
    # Split on **bold** first, then linkify each segment
    segments = re.split(r'(\*\*.*?\*\*)', raw)
    out = []
    for seg in segments:
        bm = re.match(r'\*\*(.+?)\*\*', seg)
        if bm:
            inner = _linkify_md(bm.group(1))
            out.append(f'<strong>{inner}</strong>')
        else:
            out.append(_linkify_md(seg))
    return "".join(out)

def _is_bare_link_line(line):
    """Return the URL if *line* is solely a markdown link [label](url) or [](url), else None."""
    m = re.fullmatch(r'\[([^\]]*)\]\(([^)]+)\)', line.strip())
    return m.group(2).strip() if m else None

def _md_to_html(text):
    """Convert Markdown subset (bold, headers, bullets, [label](url) links) to HTML.
    Trailing lines that consist solely of a markdown link are collected into a
    Sources section rendered as plain <a> blocks, one per line."""
    # Pre-process: any [label](url) token gets its own line, handles Grok appending
    # sources inline without newlines: "...actions.[](https://axios.com/...)[](https://...)"
    text = re.sub(r'(\[[^\]]*\]\([^)]+\))', r'\n\1\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    lines = text.split("\n")

    # Peel off trailing blank + bare-link lines to render as sources
    sources = []
    i = len(lines) - 1
    while i >= 0:
        stripped = lines[i].strip()
        url = _is_bare_link_line(stripped)
        if url:
            sources.insert(0, (stripped, url))
            i -= 1
        elif stripped == "":
            i -= 1
        else:
            break
    body_lines = lines[:i + 1]

    out = []
    in_list = False
    for line in body_lines:
        hm = re.match(r'^#{1,3}\s+(.*)', line)
        if hm:
            if in_list:
                out.append("</ul>")
                in_list = False
            content = _apply_inline_md(hm.group(1))
            out.append(f'<div style="font-weight:700;margin-top:8px;margin-bottom:2px;">{content}</div>')
            continue
        bm = re.match(r'^[-*]\s+(.*)', line)
        if bm:
            if not in_list:
                out.append('<ul style="padding-left:16px;margin:4px 0;">')
                in_list = True
            content = _apply_inline_md(bm.group(1))
            out.append(f'<li style="margin-bottom:2px;">{content}</li>')
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.strip() == "":
            out.append('<div style="height:6px;"></div>')
            continue
        out.append(f'<div>{_apply_inline_md(line)}</div>')
    if in_list:
        out.append("</ul>")

    if sources:
        import html as _html
        out.append('<div style="margin-top:8px;padding-top:6px;border-top:1px solid var(--border);display:block;">')
        for raw_link, url in sources:
            m = re.fullmatch(r'\[([^\]]*)\]\(([^)]+)\)', raw_link.strip())
            label = m.group(1).strip() if m and m.group(1).strip() else url
            out.append(f'<a href="{_html.escape(url)}" style="display:block;margin-top:4px;font-size:12px;color:var(--link);word-break:break-all;text-decoration:none;">{_html.escape(label)}</a>')
        out.append("</div>")

    return "\n".join(out)

def grok_card_html(question, answer):
    """Render a Grok share attachment card styled like X's native Grok response card."""
    if not answer:
        return ""
    q_html = f'<div style="font-weight:700;font-size:14px;margin-bottom:8px;">{question}</div>' if question else ""
    a_html = _md_to_html(answer)
    return f'''<div class="grok-card" style="border:1px solid var(--border);border-radius:12px;margin:6px 0;overflow:hidden;">
  <div style="background:var(--qt-bg);padding:10px 12px 12px;">
    <div style="display:flex;align-items:center;font-size:12px;color:var(--grey);margin-bottom:8px;">
      {GROK_SVG}<span>Answer by Grok</span>
    </div>
    {q_html}
    <div style="font-size:13px;line-height:1.5;color:var(--fg);">
      {a_html}
    </div>
  </div>
</div>'''

def card_html(card):
    if not card: return ""

    # unified_card image_website / video_website - full-width media + title bar
    if card.get("uc_type") in ("image_website", "video_website"):
        uc_media = card.get("uc_media", {})
        oi = uc_media.get("original_info", {})
        w, h = oi.get("width", 0), oi.get("height", 0)
        ar = (w / h) if w and h else 1.0
        # Portrait images: center with side bars; landscape: full width
        if ar < 0.9:
            # portrait - cap render width so it doesn't stretch full card width
            max_w = round(ar * 400)  # at 400px tall
            media_style = f'display:flex;justify-content:center;align-items:stretch;background:#000;'
            inner_style = f'width:{max_w}px;flex-shrink:0;'
        else:
            media_style = ''
            inner_style = 'width:100%;'
        if card.get("is_player"):
            vi = uc_media.get("video_info", {})
            dur_ms = vi.get("duration_millis", 0)
            dur_label = _fmt_duration(dur_ms) if dur_ms else ""
            dur_html = f'<div class="vid-duration">{dur_label}</div>' if dur_label else ""
            media_html_inner = (
                f'<div class="video-wrap" style="margin:0;border-radius:0;{inner_style}">'
                f'<img src="{card["img_url"]}" style="width:100%;height:100%;object-fit:cover;display:block;">'
                f'<div class="play-overlay">{PLAY_SVG}</div>'
                f'{dur_html}'
                f'</div>'
            )
        else:
            media_html_inner = (
                f'<div style="{inner_style}overflow:hidden;max-height:420px;">'
                f'<img src="{card["img_url"]}" style="width:100%;height:100%;max-height:420px;object-fit:cover;display:block;">'
                f'</div>'
            )
        media_wrap = f'<div style="{media_style}max-height:420px;overflow:hidden;">{media_html_inner}</div>' if media_style else media_html_inner
        return (
            f'<a href="{card["url"]}" class="card" style="display:block;overflow:hidden;">'
            f'{media_wrap}'
            f'<div class="card-body">'
            f'<div class="card-domain">{card["domain"]}</div>'
            f'<div class="card-title">{card["title"]}</div>'
            f'</div>'
            f'</a>'
        )

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


def tweet_row_html(t, is_parent=False, no_source=False, is_reply=False):
    if t.get("__tombstone"):
        sn     = t.get("screen_name", "")
        reason = t.get("reason", "unavailable")
        label  = f"This Tweet was from @{sn} was {reason}." if sn else (t.get("text") or f"This Tweet was {reason}.")
        link   = _tweet_permalink(sn, t.get("id"))
        inner  = f'<a href="{link}" style="color:inherit;text-decoration:none;">{label}</a>' if link else label
        line = "<div class='left-col' style='height:14px;margin-top:4px;'><div class=\"thread-line\"></div></div>" if is_parent else ""
        return f"""<div class="tweet-row" style="flex-direction:column;">
  <div class="quote-block" style="display:flex;align-items:center;justify-content:center;padding:16px 14px;margin:0;">
    <span style="color:#7b93a8;font-size:15px;line-height:1.4;text-align:center;">{inner}</span>
  </div>
  {line}
</div>"""
    u      = t["user"]
    vicon  = verified_svg(u.get("verified_type"), u.get("is_blue_verified", False))
    plabel = parody_label_html(u.get("parody_label", ""))
    grey   = "var(--grey)"

    clean_text, reply_to_sns = strip_all_lead_mentions(t["full_text"], t["entities"])

    if not reply_to_sns and t["in_reply_to_sn"]:
        reply_to_sns = [t["in_reply_to_sn"]]

    tweet_text  = linkify(clean_text, t["entities"])
    attr_block  = _attribution_html(t.get("media_attribution"))
    is_ai       = t.get("is_ai_media", False)
    media_block = (attr_block + media_html(t["ext_entities"], is_ai=is_ai)) if attr_block else media_html(t["ext_entities"], is_ai=is_ai)
    is_focal    = not is_parent and not is_reply
    rel_str     = rel_time(t["created_at"])
    row_class   = "tweet-row" + (" top-reply" if is_reply else ("" if is_parent else " focal"))
    time_link   = _tweet_permalink(u.get("screen_name"), t.get("id"))
    rel_str_linked = (f'<a href="{time_link}" style="color:inherit;text-decoration:none;">{rel_str}</a>'
                       if time_link else rel_str)

    if _BIRD_ICON:
        bird_color   = "#1DA1F2" if is_focal else "var(--grey)"
        corner_html  = (f'<span class="tweet-bird" style="margin-left:auto;flex-shrink:0;'
                         f'display:inline-flex;align-items:center;">{bird_svg(15, bird_color)}</span>')
        inline_time  = "" if is_focal else (
            f'<span class="tweet-time-inline" style="color:var(--grey);font-size:14px;">'
            f'&nbsp;\u00b7&nbsp;{rel_str_linked}</span>')
    else:
        corner_html  = f'<span class="tweet-time">{rel_str_linked}</span>'
        inline_time  = ""

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
        links = " ".join([f'<a href="{_TWEET_BASE_URL}/{sn}">@{sn}</a>' for sn in reply_to_sns])
        replying = f'<div class="replying-to">Replying to {links}</div>'

    card_block = card_html(t.get("card")) if not t.get("ext_entities", {}).get("media") else ""
    grok_html = grok_card_html(t.get("grok_question", ""), t.get("grok_answer", ""))
    qt_html = quote_block_html(t["quoted"]) if t.get("quoted") else ""
    bw_html = _birdwatch_html(t)
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
    bw_icon = f'<span class="stat">{icon_svg("group", 13, grey)}</span>' if t.get("has_birdwatch_notes") else ""
    stats = f"""<div class="stats">
      <span class="stat">{icon_svg("comment", 13, grey)} {fmt(t["reply_count"])}</span>
      <span class="stat">{icon_svg("retweet", 13, grey)} {fmt(t["retweet_count"])}</span>
      <span class="stat">{icon_svg("quote",   13, grey)} {fmt(t["quote_count"])}</span>
      <span class="stat">{icon_svg("heart",   13, grey)} {fmt(t["like_count"])}</span>
      <span class="stat">{icon_svg("views",   13, grey)} {fmt(t["view_count"])}</span>
      {bw_icon}
      {src}
    </div>"""
    if is_parent or is_reply:
        no_thread_line = is_reply
        return f"""{rt_header}<div class="{row_class}">
  <div class="left-col">
    <img class="avatar" src="{u["avatar_url"]}">
    {"" if no_thread_line else "<div class='thread-line'></div>"}
  </div>
  <div class="right-col">
    <div class="tweet-header">
      <div class="tweet-header-left">
        <div style="display:flex;align-items:center;"><span class="fullname">{u["name"]}</span>{vicon}<span class="username">@{u["screen_name"]}</span>{inline_time}</div>{plabel}
      </div>
      {corner_html}
    </div>
    {replying}
    {_trans_label_html(t.get("translated_from"))}
    <div class="tweet-content">{tweet_text}</div>
    {media_block}
    {grok_html}
    {card_block}
    {qt_html}
    {bw_html}
    {broadcast_html}
    {stats}
  </div>
</div>"""
    else:
        return f"""<div class="{row_class}">
   {rt_header}<div class="focal-header{" has-rt" if rt_by else ""}">
    <img class="avatar" src="{u["avatar_url"]}">
    <div class="focal-header-names">
      <div class="focal-header-top"><span class="fullname">{u["name"]}</span>{vicon}</div>
      <div class="focal-header-bottom"><span class="username">@{u["screen_name"]}</span>{plabel}</div>
    </div>
    <span style="margin-left:auto;align-self:flex-start;margin-top:2px;display:inline-flex;align-items:center;">{corner_html}</span>
  </div>
  <div class="focal-body">
    {replying}
    {_trans_label_html(t.get("translated_from"))}
    <div class="tweet-content">{tweet_text}</div>
    {media_block}
    {grok_html}
    {card_block}
    {qt_html}
    {bw_html}
    {broadcast_html}
    <div class="tweet-date">{_linked_abs_time(t)}</div>
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

def build_html(tweets, light=False, no_source=False, css_path=None, width=598, nitter=False, for_browser=False, top_reply=None):  # top_reply: list
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
    if top_reply:
        rows.append('<div class="top-reply-divider"></div>')
        for tr in top_reply:
            rows.append(tweet_row_html(tr, is_parent=False, no_source=no_source, is_reply=True))
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
    border-radius: 12px;
    box-shadow: 0 4px 32px rgba(0,0,0,0.45);
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

def embed_exif_url(output_path, url):
    """Inject tweet URL into EXIF ImageDescription. Supports JPEG and PNG.
    - JPEG: piexif.insert() handles it natively.
    - PNG:  piexif.insert() only supports JPEG/WebP, so we splice an eXIf
            chunk directly into the PNG byte stream.
    Falls back silently if piexif is not installed or anything goes wrong."""
    try:
        import piexif, zlib
    except ImportError:
        return
    try:
        exif_bytes = piexif.dump({"0th": {piexif.ImageIFD.ImageDescription: url.encode("ascii", errors="replace")}})
        ext = Path(output_path).suffix.lower()
        if ext in (".jpg", ".jpeg"):
            piexif.insert(exif_bytes, output_path)
        elif ext == ".png":
            PNG_SIG = b"\x89PNG\r\n\x1a\n"
            with open(output_path, "rb") as f:
                data = f.read()
            if data[:8] != PNG_SIG:
                return
            tiff_bytes = exif_bytes[6:]  # strip JPEG-style "Exif\x00\x00" preamble; PNG eXIf wants raw TIFF
            chunk_type = b"eXIf"
            chunk_crc  = struct.pack(">I", zlib.crc32(chunk_type + tiff_bytes) & 0xFFFFFFFF)
            exif_chunk = struct.pack(">I", len(tiff_bytes)) + chunk_type + tiff_bytes + chunk_crc
            pos, chunks = 8, []
            while pos < len(data):
                length = struct.unpack(">I", data[pos:pos+4])[0]
                chunks.append((data[pos+4:pos+8], data[pos:pos+12+length]))
                pos += 12 + length
            out, inserted = [PNG_SIG], False
            for ctype, raw in chunks:
                if ctype == b"eXIf":
                    continue  # drop old chunk
                out.append(raw)
                if ctype == b"IHDR" and not inserted:
                    out.append(exif_chunk)
                    inserted = True
            with open(output_path, "wb") as f:
                f.write(b"".join(out))
    except Exception:
        pass  # Never fatal - EXIF is best-effort

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

    # Allow the nitter_url config key to redirect @mention / #hashtag / tweet
    # hyperlinks -- including the "this tweet is unavailable/blocked" and
    # "quote of a quote" stub links -- to a self-hosted/public Nitter instance.
    # Defaults to https://nitter.net. Set nitter_url = x.com (or your own
    # instance) to override, or nitter_url = (empty) to disable mention/hashtag
    # hyperlinking (unavailable/stub links still point at x.com/twitter.com
    # in that case, since there's nowhere else to send them).
    global _TWEET_BASE_URL
    _TWEET_BASE_URL = conf.get("nitter_url", "https://nitter.net").rstrip("/")

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
    p.add_argument("--last-reply", action="store_true", default=_b("last_reply"),
                   help="For reply threads: show only the immediate parent tweet + focal tweet (trims long threads)")
    p.add_argument("--top-reply",   action="store_true", default=_b("top_reply"),
                   help="Append the top reply (sorted by likes) below the focal tweet")
    p.add_argument("--top-replies", type=lambda x: max(1, min(20, int(x))), default=None, metavar="N",
                   help="Append top N replies (by likes) below focal tweet (1-20)")
    p.add_argument("--no-nested-quotes", action="store_true", default=_b("no_nested_quotes"),
                   help="Don't fetch/render a 'quote of a quote' (the quoted tweet's own quoted tweet); shows a plain link instead")
    p.add_argument("--with-note",  action="store_true", default=_b("with_note"),
                   help="Fetch the top-voted MISLEADING Community Note via BirdwatchFetchNotes, even if not yet shown on Twitter (labelled 'Proposed' if so)")
    p.add_argument("--with-notes", action="store_true", default=_b("with_notes"),
                   help="Like --with-note, but show every proposed Community Note (misleading and not-misleading) for the tweet")
    p.add_argument("--no-retina",  action="store_true", default=_b("no_retina"), help="Generate a 50%% smaller image")
    p.add_argument("--guest",      action="store_true", default=_b("guest"), help="Guest mode (no account needed)")
    p.add_argument("--with-replies", action=argparse.BooleanOptionalAction,
                   default=_b("with_replies"),
                   help="Include the user's own replies when fetching @user timeline "
                        "(default: on; requires auth - silently ignored in guest mode)")
    p.add_argument("--width",      type=int, default=int(conf.get("width", 598)))
    p.add_argument("--css",        default=conf.get("css") or None, help="File to override the theme (ex: nitter/public/css/themes/pleroma.css)")
    p.add_argument("--nitter",     action="store_true", default=_b("nitter"), help="Use Nitter default theme")
    p.add_argument("--html-only",  action="store_true", default=_b("html_only"), help="Print HTML to stdout instead of rendering PNG")
    p.add_argument("--save-html",  nargs="?", const="", default=conf.get("save_html") or None,
                   metavar="FILE",
                   help="Save HTML instead of rendering PNG. "
                        "Omit FILE to auto-name as <user>-<id>.html in the same directory as the PNG output.")
    p.add_argument("--view-html",  action="store_true", default=_b("view_html"),
                   help="Auto-save HTML alongside the PNG (same directory, same base name) and open it "
                        "in the browser defined by --viewer (or xdg-open if not set). "
                        "If no PNG is otherwise needed (no --view, no --imgur, no explicit output path), "
                        "Playwright is skipped entirely")
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
    p.add_argument("--bird-icon",  action="store_true", default=_b("bird_icon"),
                   help="Show the classic Twitter 'bird' glyph in the header's top-right slot")
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
    p.add_argument("-q", "--quiet", action="store_true", default=_b("quiet"),
                   help="Suppress all progress messages (stderr and status prints).")
    args = p.parse_args()

    # --view-html is shorthand for: auto-save HTML next to the PNG, then open in browser.
    # We defer the actual save/open until after the output path is resolved below.
    _view_html_requested = args.view_html

    # Apply full-stats flag globally so fmt() picks it up
    global _FULL_STATS
    _FULL_STATS = args.full_stats

    # Apply bird-icon flag globally so tweet_row_html() picks it up
    global _BIRD_ICON
    _BIRD_ICON = args.bird_icon

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
    headers = None

    if args.user:
        if args.guest:
            gt = get_guest_token()
            headers = guest_headers(gt)
        else:
            if not args.auth_token or not args.csrf_token:
                sys.exit("Error: --auth-token/--csrf-token required (or use --guest)")
            headers = auth_headers(args.auth_token, args.csrf_token)
        user_id = fetch_user_id(args.user, headers)
        # UserTweetsAndReplies requires auth; fall back to UserTweets in guest mode
        use_with_replies = args.with_replies and not args.guest
        tweet_id = fetch_nth_tweet_id(user_id, headers, n=tweet_index, with_replies=use_with_replies)
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

    if not tweets:
        # Shouldn't happen any more (parsers now return a tombstone placeholder
        # instead of an empty list), but keep this as a last-resort guard.
        tweets = [{"__tombstone": True, "id": fid, "text": "This Tweet could not be found.",
                   "screen_name": "", "reason": "unavailable"}]

    if args.no_context:
        tweets = [tweets[-1]]

    if tweets[-1].get("rt_by_user"):
        tweets = [tweets[-1]]

    if args.last_reply and len(tweets) > 2:
        tweets = tweets[-2:]

    if not tweets:
        sys.exit("Failed to parse tweet from API response")

    top_reply_tweets = []
    _tr_count = args.top_replies if args.top_replies else (1 if args.top_reply else 0)
    if _tr_count and "threaded_conversation_with_injections_v2" in data.get("data", {}):
        top_reply_tweets = parse_top_reply(data, fid, count=_tr_count)

    # A quote tweet can quote a tweet that is itself quoting something else.
    # X's API only ever hydrates one level of quoted_status_result, so that
    # inner "quote of a quote" comes back as a stub (id + permalink only) --
    # this is the context nitter (and a naive renderer) misses entirely.
    # Resolving it needs a separate TweetResultByRestId request per stub.
    if not args.no_nested_quotes:
        _stub_tweets = [t for t in (tweets + top_reply_tweets) if _quote_chain_has_stub(t.get("quoted"))]
        if _stub_tweets:
            _qheaders = resolution_headers(args, headers)
            if _qheaders:
                for t in _stub_tweets:
                    t["quoted"] = resolve_quote_chain(t["quoted"], _qheaders, quiet=args.quiet)
            elif not args.quiet:
                print("Note: found a 'quote of a quote' but couldn't fetch it (no auth/network); showing a link instead.",
                      file=sys.stderr)

    tweet_id = tweets[-1].get("id") or "unknown"
    focal = tweets[-1]

    if args.with_note or args.with_notes:
        if not focal.get("has_birdwatch_notes"):
            pass  # tweet has no note activity; skip the extra request entirely
        elif args.guest:
            if not args.quiet:
                print("Note: --with-note/--with-notes requires auth; skipping in --guest mode.", file=sys.stderr)
        else:
            _bw_headers = resolution_headers(args, headers)
            if _bw_headers:
                try:
                    bw_data = fetch_birdwatch_notes(focal["id"], _bw_headers)
                    all_notes = parse_birdwatch_fetch_notes(bw_data)
                    if args.with_notes:
                        focal["proposed_notes"] = all_notes
                    else:
                        misleading = [n for n in all_notes if n["is_misleading"]]
                        if misleading:
                            focal["proposed_notes"] = [misleading[0]]
                except Exception as e:
                    if not args.quiet:
                        print(f"Warning: couldn't fetch birdwatch notes for {focal['id']}: {e}", file=sys.stderr)
            elif not args.quiet:
                print("Note: --with-note/--with-notes needs auth; skipping.", file=sys.stderr)

    if args.trans:
        raw = args.trans.strip()
        if ":" in raw:
            src_lang, tgt_lang = raw.split(":", 1)
        else:
            src_lang, tgt_lang = "auto", raw
        if tgt_lang.strip().lower() == "auto":
            sys.exit(
                "Error: --trans target language can't be 'auto' -- only the "
                "source can be auto-detected, not the target. Use a real "
                "target code, e.g. --trans en, or be explicit about both "
                "with --trans auto:en."
            )
        tgt_primary = tgt_lang.split("-")[0].lower()
        for t in tweets:
            if t.get("__tombstone"):
                continue
            # Skip if the tweet is already in the target language.
            # legacy.lang is a BCP-47 tag (e.g. "en", "ja") from the API.
            # Compare only the primary subtag so "zh-Hant" won't skip --trans zh.
            tweet_lang = (t.get("lang") or "").split("-")[0].lower()
            if tweet_lang in _UNTRANSLATABLE_LANGS:
                pass  # untranslatable Twitter-specific lang code; skip, but still check quoted below
            elif tweet_lang and tweet_lang == tgt_primary:
                pass  # don't translate, but still check quoted below
            else:
                effective_src = src_lang if src_lang != "auto" else (tweet_lang or "auto")
                if t.get("full_text"):
                    if not args.quiet:
                        print(f"Translating tweet text ({effective_src} -> {tgt_lang}) ...", file=sys.stderr)
                    t["full_text"] = translate_text(t["full_text"], effective_src, tgt_lang)
                    t["translated_from"] = _lang_display_name(effective_src)
            qt = t.get("quoted")
            while qt and not qt.get("__tombstone") and not qt.get("__stub") and qt.get("full_text"):
                qt_lang = (qt.get("lang") or "").split("-")[0].lower()
                if qt_lang in _UNTRANSLATABLE_LANGS:
                    pass  # untranslatable Twitter-specific lang code; skip
                elif not qt_lang or qt_lang != tgt_primary:
                    qt_src = src_lang if src_lang != "auto" else (qt_lang or "auto")
                    if not args.quiet:
                        print(f"Translating quoted tweet text ({qt_src} -> {tgt_lang}) ...", file=sys.stderr)
                    qt["full_text"] = translate_text(qt["full_text"], qt_src, tgt_lang)
                    qt["translated_from"] = _lang_display_name(qt_src)
                qt = qt.get("quoted")  # walk into a "quote of a quote", if any

    if args.print_line:
        print(format_tweet_line(focal))
        return

    user_name = focal.get("screen_name") if focal.get("__tombstone") else focal["user"]["screen_name"]
    user_name = user_name or "unknown"
    if not focal.get("__tombstone") and not args.output and (focal.get("rt_by_user") or focal.get("is_rt")):
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
                      for_browser=(args.save_html is not None or _view_html_requested),
                      top_reply=top_reply_tweets)

    if args.save_html is not None:
        if args.save_html == "":
            # Auto-name: same base as the PNG output but with .html extension.
            # duplicate_files handling (increment/epoch) was already applied to
            # `output` above, so we inherit that suffix here.
            html_path = str(Path(output).with_suffix(".html"))
        else:
            html_path = args.save_html
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        if not args.quiet:
            print(f"HTML saved to {html_path}")
        if args.view:
            viewer = args.viewer or "firefox"
            open_with_viewer(html_path, viewer)
        return

    # --view-html: save HTML alongside the PNG, open in browser.
    # If no PNG is otherwise needed (no --view, no --imgur, no explicit output path),
    # skip Playwright entirely, the browser is the viewer.
    if _view_html_requested:
        html_path = str(Path(output).with_suffix(".html"))
        with open(html_path, 'w', encoding='utf-8') as f:
            f.write(html)
        if not args.quiet:
            print(f"HTML saved to {html_path}")
        browser_viewer = args.viewer or "xdg-open"
        open_with_viewer(html_path, browser_viewer)
        png_needed = args.output or args.view or args.imgur
        if not png_needed:
            return

    if args.html_only:
        print(html)
        return

    await render_png(html, output, width=args.width, retina=not args.no_retina)
    tweet_url = f"https://x.com/{user_name}/status/{tweet_id}"
    embed_exif_url(output, tweet_url)
    if not args.quiet:
        print(f"{output} saved")
    if args.view:
        viewer = args.viewer or "viewnior"
        open_with_viewer(output, viewer)
    if args.imgur:
        url, delete_hash = upload_imgur(output)
        if not args.quiet:
            print(f"{url} delete: https://imgur.com/delete/{delete_hash}")
        if args.imgur_log:
            log_path = os.path.expanduser(args.imgur_log)
            with open(log_path, "a") as f:
                f.write(f"{url} delete: https://imgur.com/delete/{delete_hash} {output}\n")

def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
