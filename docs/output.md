# Output

## Default filename

Auto-named as `<screen_name>-<tweet_id>.png` in the current directory. Retweets encode both accounts: `<retweeter>-rt-<original>-<id>.png`.

## Custom filename

Pass a filename after the input:

```bash
tw2img 2054583770045386950 --guest my_tweet.png
tw2img @NASA --guest nasa.png
tw2img @NASA 3 --guest nasa-3rd.png
```

## Output directory

Set a default directory so you don't have to specify it each run:

```bash
tw2img 2054583770045386950 --output-dir ~/Pictures/tweets
```

Or set it permanently in `tw2img.conf`:

```ini
[tw2img]
output_dir = ~/Pictures/tweets
```

Explicit paths with a directory component or absolute paths bypass `output_dir`.

## Duplicate file handling

Configurable via `duplicate_files` in `tw2img.conf`:

| Value | Behaviour |
|---|---|
| `overwrite` | Replace existing file (default) |
| `increment` | Append `-1`, `-2`, … before extension |
| `epoch` | Append Unix timestamp before extension |

```ini
[tw2img]
duplicate_files = increment
```

## PNG rendering

- Rendered by Playwright (headless Chromium) at `device_scale_factor=2` (retina) by default
- Page waits for `networkidle` + 0.5 s before screenshotting the `.thread` element directly
- `--no-retina` halves resolution for smaller files
- `--width N` sets the viewport width in pixels (default 598)

## EXIF metadata

The tweet's canonical URL is injected into every saved file as EXIF `ImageDescription`:

- **PNG** — spliced as a raw `eXIf` chunk after `IHDR`
- **JPEG** — written via `piexif.insert()`

Silently skipped if `piexif` is not installed.

## HTML output

See [html-output.md](html-output.md) for `--html-only`, `--save-html`, and `--view-html`.

## Imgur upload

```bash
tw2img @NASA --guest --imgur
```

- Uploads anonymously to Imgur and prints the URL + delete hash
- Set `IMGUR_CLIENT_ID` env var to use your own client ID
- `--imgur-log FILE` appends `<url> delete: <delete_url> <local_path>` to a file after each upload

## Auto-open after saving

```bash
tw2img @NASA --guest --view
tw2img @NASA --guest --view --viewer viewnior
tw2img @NASA --guest --view --viewer "kitty +icat {}"
```

- `{}` is replaced with the output path; otherwise the path is appended to the command
- Terminal viewers (`kitty +icat`, `chafa`, `viu`, `timg`, etc.) run in-process so output appears in the terminal
- GUI apps (`viewnior`, `eog`, etc.) are launched detached

## One-line text summary

```bash
tw2img 21 --print-line --guest
# @biz (Biz Stone) ✔ just setting up my twttr | ↳ 153 ⇅ 4.8K ‟ 302 ♥ 4.3K | Web Client | https://x.com/i/status/21
```

Prints to stdout and exits without rendering a PNG. Verification badges are ANSI-colourised (gold for Business, teal for Government, blue for X Blue).

## Raw JSON

```bash
tw2img 2054583770045386950 --guest --dump-json
```

Prints the raw API response JSON to stdout and exits.
