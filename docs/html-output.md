# HTML Output

tw2img can save or print the intermediate HTML it passes to Playwright, which is useful for debugging, browser viewing, or skipping PNG rendering entirely.

## `--html-only`

Print the generated HTML to stdout and exit without launching Playwright:

```bash
tw2img 2054583770045386950 --guest --html-only
tw2img 2054583770045386950 --guest --html-only > tweet.html
```

## `--save-html [FILE]`

Save the HTML to a file, then exit (no PNG is rendered):

```bash
# Auto-named alongside where the PNG would have been saved
tw2img 2054583770045386950 --guest --save-html

# Explicit filename
tw2img 2054583770045386950 --guest --save-html tweet.html
```

The HTML saved this way is rendered in **browser mode**: it adds centring CSS and a drop-shadow around the card so it looks good when opened directly in a browser.

Combine with `--view` to open it immediately:

```bash
tw2img 2054583770045386950 --guest --save-html tweet.html --view --viewer firefox
```

## `--view-html`

Shorthand that auto-saves the HTML alongside the PNG (same base name, `.html` extension) and opens it in a browser:

```bash
tw2img 2054583770045386950 --guest --view-html
```

**Playwright skip:** if no PNG is otherwise needed — i.e. you haven't passed `--view`, `--imgur`, or an explicit output path — Playwright is not launched at all. The browser becomes the viewer, making this the fastest way to preview a tweet.

The viewer used is `--viewer` if set, otherwise `xdg-open`. Set a permanent default in `tw2img.conf`:

```ini
[tw2img]
view_html = true
viewer = firefox
```

## When to use each

| Goal | Command |
|---|---|
| Inspect the HTML source | `--html-only` |
| Save HTML, open in browser | `--save-html --view --viewer firefox` |
| Quick browser preview (no PNG) | `--view-html` |
| Save HTML + PNG both | `--view-html --view` |
