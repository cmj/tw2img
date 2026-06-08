# Configuration

tw2img uses an INI-format config file with a `[tw2img]` section.

## Load order

Settings are merged in this order — later sources override earlier ones:

1. `~/.config/tw2img/tw2img.conf` — user default
2. `./tw2img.conf` — current directory (if present)
3. `-c /path/to/custom.conf` — explicit override
4. CLI flags — always highest priority

```bash
# Use an alternate config for a specific run
tw2img 2054583770045386950 -c ~/work/tw2img-work.conf --light
```

## Installing the default config

```bash
tw2img-install-config
```

Copies the bundled default `tw2img.conf` to `~/.config/tw2img/` as a documented starting point.

Alternatively:

```bash
mkdir -p ~/.config/tw2img
cp tw2img.conf ~/.config/tw2img/tw2img.conf
```

## All config keys

```ini
[tw2img]

# --- Auth ---
auth_token    = your_auth_token_here
csrf_token    = your_ct0_or_random_hex

# --- Input ---
user          = NASA          # default @username to fetch
guest         = false         # always use guest mode

# --- Output ---
output_dir    = ~/Pictures/tweets
duplicate_files = overwrite   # overwrite | increment | epoch
no_retina     = false
width         = 598

# --- Theme ---
light         = false
nitter        = false
css           = /path/to/theme.css
no_source     = false

# --- Stats ---
full_stats    = false

# --- Translation ---
trans         = en            # translate everything to English

# --- Viewer ---
view          = false         # auto-open after saving
viewer        = viewnior      # or: eog | firefox | "kitty +icat {}"
view_html     = false         # always save+open HTML
# article_viewer used by article2img --view / --view-html
article_viewer = xdg-open

# --- Misc ---
imgur         = false
imgur_log     = ~/tw2imgur_urls
quiet         = false
dump_json     = false
with_replies  = true
```

## Quiet mode

Suppress all progress output to stderr, leaving only filenames and `--print-line` on stdout:

```bash
tw2img 2054583770045386950 --guest -q
```

Useful when scripting or piping output.
