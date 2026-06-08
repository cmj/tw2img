# Themes & Styling

## Built-in themes

### Dark (default)

Nitter-inspired Midnight palette:

| Variable | Value |
|---|---|
| Background | `#191919` |
| Text | `#FFFFFF` |
| Grey | `#8899A6` |
| Border | `#38444D` |
| Link | `#80CEFF` |
| Accent | `#8899A6` |
| Quote background | `#1e2732` |

### Light (`--light`)

Matches X's native light mode:

| Variable | Value |
|---|---|
| Background | `#ffffff` |
| Text | `#0f1419` |
| Grey | `#536471` |
| Border | `#cfd9de` |
| Link | `#1d9bf0` |
| Accent | `#1d9bf0` |
| Quote background | `#f7f9f9` |

```bash
tw2img 2054583770045386950 --guest --light
```

### Nitter (`--nitter`)

Applies original Nitter Midnight CSS variable names (`--bg_color`, `--fg_color`, `--accent`, etc.) for visual compatibility with Nitter-based screenshots.

```bash
tw2img 2054583770045386950 --guest --nitter
```

## Custom CSS

Load any CSS file to override or extend the built-in theme. Any Nitter theme file works directly:

```bash
tw2img 2054583770045386950 --guest --css nitter/public/css/themes/pleroma.css
```

The custom CSS is appended after the base theme, so you can target any class.

Set permanently in `tw2img.conf`:

```ini
[tw2img]
css = /path/to/my-theme.css
```

## Width

```bash
tw2img 2054583770045386950 --guest --width 800
```

Default is 598 px. The screenshot clips to the content height of the `.thread` element, so taller threads produce taller images regardless of width.

## Retina rendering

By default, `device_scale_factor=2` produces a 2× resolution PNG (crisp on HiDPI displays). Use `--no-retina` for a 1× image at half the file size.

## SVG icon glyphs

All stat icons (reply, retweet, quote, heart, views, community note, robot, theatre mask) are inline SVG paths from a normalised 1000-unit coordinate system — no external icon font required. They scale cleanly at any width or DPI.
