# tw2img

A tool that renders tweets as PNG images using Playwright (headless Chromium). Works with usernames, tweet IDs, URLs, local JSON files, or stdin.

This style is based on [nitter](https://github.com/zedeus/nitter/) using [Midnight](https://github.com/cmj/nitter/blob/master/public/css/themes/midnight.css) theme as default.

| ![image grid](img/2041557036274475228-nasa.png) | ![birdwatch note and thread](img/1819250493442695340-note.png) |
|---|---|

---

## Installation

### From PyPI (recommended)

```bash
pip install tw2img
playwright install chromium
```

### From source

```bash
git clone https://github.com/cmj/tw2img.git
cd tw2img
pip install playwright
playwright install chromium
```

---

## Quick Start

### 1. Guest Mode (no auth token required, missing context for replies)

```bash
# By @username (fetch latest tweet)
tw2img @AP --guest
# By @username, fetch the 3rd most recent tweet
tw2img @AP 3 --guest
# By tweet ID
tw2img 2041557036274475228 --guest
# By tweet URL
tw2img https://x.com/NASA/status/2041557036274475228 --guest
```

> When running from source, replace `tw2img` with `python tw2img.py`.

Here is a list of popular Twitter accounts sorted by most recent, and useful for guest access:
https://github.com/cmj/twitter-tools/wiki/RSS%E2%80%90Friendly

### 2. Authenticated Mode (full thread + reply data)

```bash
export TWITTER_AUTH_TOKEN="your_auth_token_here"
export TWITTER_CSRF_TOKEN="your_ct0_token_here"
# alternative: only requires setting auth_token
export TWITTER_CSRF_TOKEN=$(openssl rand -hex 16)

tw2img 2054583770045386950
```

**Where to find tokens:** Open browser devtools → Network tab → any x.com request → Cookies tab → `auth_token` and `ct0`.

---

## Examples

```bash
# Light theme, focal tweet only
tw2img 2054583770045386950 --guest --light --no-context

# 5th most recent tweet from a user
tw2img @NASA 5 --guest

# Open snapshot after rendering (GUI viewer)
tw2img @NASA --guest --view --viewer viewnior

# Render inline in kitty terminal
tw2img @NASA --guest --view --viewer "kitty +icat {}"

# Upload to Imgur
tw2img @NASA --guest --imgur

# Save HTML and open in browser (skips PNG rendering)
tw2img 2054583770045386950 --guest --view-html

# Translate to English before rendering
tw2img 2059593901607153975 --guest --trans en

# Print one-line summary to stdout
tw2img 21 --print-line --guest
# @biz (Biz Stone) ✔ just setting up my twttr | ↳ 153 ⇅ 4.8K ‟ 302 ♥ 4.3K | Web Client | https://x.com/i/status/21
```

---

## Documentation

| Doc | Contents |
|---|---|
| [Authentication](docs/authentication.md) | Guest mode, auth tokens, `--with-replies` |
| [Input](docs/input.md) | Tweet ID, URL, `@username`, JSON file, stdin |
| [Output](docs/output.md) | Filenames, `--output-dir`, Imgur, `--view`, `--print-line`, EXIF |
| [HTML Output](docs/html-output.md) | `--html-only`, `--save-html`, `--view-html` |
| [Themes & Styling](docs/themes.md) | Dark/light/Nitter themes, custom CSS, width, retina |
| [Thread Rendering](docs/thread-rendering.md) | Thread chains, tombstones, retweets, verified badges |
| [Media](docs/media.md) | Image grids, videos, AI label, attribution |
| [Cards & Attachments](docs/cards.md) | Link cards, Grok, Community Notes, quoted tweets |
| [Translation](docs/translation.md) | `--trans`, language pre-checks, untranslatable codes |
| [Configuration](docs/configuration.md) | Config file, all keys, load order |
| [article2img](docs/article2img.md) | Article rendering, Markdown output, embedded tweets |

---

## Articles

Articles are long-form content that don't render well as a PNG. See [docs/article2img.md](docs/article2img.md) for full details.

```bash
# Preferred: auto-save HTML and open immediately
article2img --guest --view-html https://x.com/ARCRaidersGame/status/2054607629738037736

alias tw-article='article2img --guest --view-html'
tw-article https://x.com/XDevelopers/status/2041295840325636551
```
