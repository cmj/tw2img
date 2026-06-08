# Translation

tw2img can translate tweet text before rendering using the `deep-translator` library.

## Setup

```bash
pip install deep-translator
```

## Usage

```bash
# Auto-detect source, translate to English
tw2img 2059593901607153975 --guest --trans en

# Specify source explicitly (Japanese → English)
tw2img 2059593901607153975 --guest --trans ja:en

# Any BCP-47 / ISO 639-1 code works
tw2img 2059593901607153975 --guest --trans zh-CN
```

**Format:** `--trans TARGET` or `--trans SOURCE:TARGET`. Use `auto` as the source to force auto-detection even when you're also specifying a target.

## What gets translated

- The focal tweet's text
- Quoted tweet text (if present)

Text entities (hashtags, @mentions, URLs) are preserved — only the natural language content is sent to the translator.

## Language pre-checks

Before calling the translation API, tw2img checks the tweet's `lang` field (a BCP-47 tag from the Twitter API). If the primary subtag already matches the target (e.g. tweet is `zh-Hant` and target is `zh`), the tweet is skipped to avoid unnecessary API calls.

## Untranslatable codes

Twitter uses several platform-specific language codes for content that can't be identified or is intentionally language-neutral. These are silently skipped:

| Code | Meaning |
|---|---|
| `und` | Undetermined |
| `qam` | @-mention only |
| `qct` | Cashtag only |
| `qht` | Hashtag only |
| `qme` | Media only |
| `qst` | Short tweet |
| `zxx` | No linguistic content |

## "Translated from" label

After translation, a small italicised "Translated from Language" label renders in accent colour below the tweet text. Language codes are mapped to human-readable names (e.g. `ja` → "Japanese") from a full BCP-47 name table.

## Persistent default

Set a default translation target in `tw2img.conf` to translate every non-matching tweet automatically:

```ini
[tw2img]
trans = en
```
