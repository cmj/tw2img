# Authentication

tw2img supports two modes: guest (no account needed) and authenticated (full thread context).

## Guest Mode

Pass `--guest` to fetch without an account. tw2img requests a short-lived guest token from the Twitter API automatically.

```bash
tw2img 2054583770045386950 --guest
tw2img @NASA --guest
```

**Limitations:** Guest mode uses the `TweetResultByRestId` endpoint, which returns a single tweet with no thread context. For full reply chains, authenticated mode is required.

## Authenticated Mode

Export your tokens as environment variables:

```bash
export TWITTER_AUTH_TOKEN="your_auth_token_here"
export TWITTER_CSRF_TOKEN="your_ct0_token_here"
# CSRF token can be any random hex string if you only have auth_token:
export TWITTER_CSRF_TOKEN=$(openssl rand -hex 16)
```

Then run without `--guest`:

```bash
tw2img 2054583770045386950
```

**Where to find tokens:** Open browser devtools → Network tab → any `x.com` request → Cookies tab → `auth_token` and `ct0`.

## Supplying Tokens

Tokens can be provided in three ways, listed in priority order (lower overrides higher):

1. **Config file** — `auth_token` and `csrf_token` keys in `tw2img.conf`
2. **Environment variables** — `TWITTER_AUTH_TOKEN` and `TWITTER_CSRF_TOKEN`
3. **CLI flags** — `--auth-token` and `--csrf-token`

## `--with-replies`

When fetching by `@username`, controls whether the user's own replies are included in the timeline scan.

```bash
# Include the user's replies (default in auth mode)
tw2img @user --with-replies

# Original tweets only
tw2img @user --no-with-replies
```

- Uses `UserTweetsAndReplies` when enabled (requires a separate bearer token, handled internally)
- Falls back to `UserTweets` when disabled
- Silently ignored in guest mode — `UserTweetsAndReplies` requires authentication
