# article2img

`article2img` renders X/Twitter Articles — long-form content attached to a tweet. Because articles don't fit well as a single PNG, the recommended workflow is to save as HTML and open in a browser.

## Quick start

```bash
# Preferred: save HTML and open immediately
article2img --guest --view-html https://x.com/ARCRaidersGame/status/2054607629738037736

# Alias for convenience
alias tw-article='article2img --guest --view-html'
tw-article https://x.com/XDevelopers/status/2041295840325636551
```

## Input formats

```bash
# Tweet URL containing the article
article2img https://x.com/user/status/2054607629738037736

# Direct article URL (guest-accessible)
article2img --guest https://x.com/user/article/2054607629738037736

# /i/article/<id> URL (requires auth — resolved via ArticleRedirectScreenQuery)
article2img https://x.com/i/article/2054607629738037736

# Tweet ID
article2img 2054607629738037736

# Cached API JSON
article2img article.json
```

## Authentication note

Articles are a premium X feature. Guest mode (`--guest`) may return an empty article body. Authenticated mode is strongly recommended:

```bash
export TWITTER_AUTH_TOKEN="..."
export TWITTER_CSRF_TOKEN="..."
article2img https://x.com/user/status/2054607629738037736
```

## Output options

| Flag | Effect |
|---|---|
| *(default)* | Render PNG as `<user>-article-<id>.png` |
| `--save-html [FILE]` | Save HTML and exit (auto-named if no file given) |
| `--view-html` | Shorthand for `--save-html` + `--view` |
| `--html-only` | Print HTML to stdout |
| `--markdown` / `--md [FILE]` | Output as Markdown (stdout or file) |
| `--dump-json` | Print raw API JSON and exit |

## Markdown output

Render the article as Markdown for terminal reading via `mdcat`:

```bash
# Print to stdout, pipe through mdcat
article2img --guest --md https://x.com/user/status/2054607629738037736 | mdcat

# Quiet mode for clean pipe output
article2img -q --guest --md - https://x.com/user/status/... | mdcat

# Save to file
article2img --guest --markdown out.md https://x.com/user/status/...
```

Add `--images` to include the cover image and inline article images (requires a terminal with inline image support, e.g. kitty):

```bash
article2img --guest --md --images https://x.com/user/status/... | mdcat
```

**Markdown mapping:**

| Article block | Markdown |
|---|---|
| `header-one/two/three` | `#` / `##` / `###` |
| `blockquote` | `>` |
| `code-block` | Fenced ` ``` ` |
| `unordered-list-item` | `- item` |
| `ordered-list-item` | `1. item` |
| Bold / Italic / CODE | `**bold**` / `_italic_` / `` `code` `` |
| Embedded tweet | Blockquote with author + link |

## Embedded tweets

Articles can embed tweets inline. `article2img` fetches them and renders them as styled cards:

- **Authenticated mode** — all tweet IDs batched into one `TweetResultsByRestIds` request
- **Guest mode** — fetched one-by-one via `TweetResultByRestId`

## Options reference

| Flag | Default | Description |
|---|---|---|
| `--light` | off | Light theme |
| `--width N` | 680 | Output width in pixels |
| `--no-retina` | off | 1× scale instead of 2× |
| `--output-dir DIR` | cwd | Directory for saved files |
| `--view` | off | Open after saving |
| `--viewer CMD` | `xdg-open` | Viewer command |
| `--guest` | off | No auth required |
| `--auth-token` | env | Twitter auth token |
| `--csrf-token` | env | Twitter CSRF token |
| `-q` / `--quiet` | off | Suppress stderr output |
| `--images` | off | Include images in Markdown output |

## Viewer config

Set the default browser for article HTML in `tw2img.conf` (separate from the `viewer` key used by `tw2img`):

```ini
[tw2img]
article_viewer = firefox
```
