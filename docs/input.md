# Input

tw2img accepts tweets in several formats.

## Tweet ID

```bash
tw2img 2054583770045386950 --guest
```

## Full URL

```bash
tw2img "https://x.com/NASA/status/2054583770045386950" --guest
```

## @username

Fetches the user's latest original tweet (skips retweets and replies):

```bash
tw2img @NASA --guest
```

## @username with index

Fetches the Nth most recent original tweet (1–20, skips retweets and replies):

```bash
tw2img @NASA 3 --guest   # 3rd most recent tweet
```

Equivalent to `--user`:

```bash
tw2img --user NASA 3 --guest
```

## Local JSON file

Pass a path to a cached API JSON response:

```bash
tw2img tweet.json
```

## Stdin

Pipe raw API JSON directly:

```bash
cat tweet.json | tw2img -
```

## API Endpoints

tw2img selects the endpoint based on auth mode:

| Mode | Endpoint | Returns |
|---|---|---|
| Authenticated | `TweetDetail` | Full thread chain + replies |
| Guest | `TweetResultByRestId` | Single tweet only |

Feature flags and GraphQL hashes are captured from real browser network requests and match what the X web app sends.
