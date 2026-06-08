# Thread & Tweet Rendering

## Thread chains

In authenticated mode, `TweetDetail` returns the full conversation context. tw2img walks the `in_reply_to_status_id_str` chain backwards from the focal tweet to reconstruct all parent tweets, rendering each as a stacked row with a vertical connecting line between avatars.

The focal (bottom) tweet uses a two-row header layout (name + badge on top, `@handle` + label below) at a larger font size. Parent tweets use a compact inline single-row header to save vertical space.

## `--no-context`

Render only the focal tweet, stripping all parents:

```bash
tw2img 2054583770045386950 --guest --light --no-context
```

## Tombstones

Deleted or restricted tweets that appear in a thread are rendered as a grey "This tweet is unavailable" placeholder with a question-mark avatar. The thread visual continuity is preserved rather than silently dropping the gap.

## Retweet rendering

When the fetched tweet is a retweet, the original author's content is rendered with a "**Name** retweeted" bar above it (grey, with the retweet icon). The retweeter's name links to their profile.

When the full RT result object is unavailable, tw2img extracts the original screen name from the `RT @handle:` prefix in the tweet text and strips the prefix before rendering.

## Reply-to stripping

Twitter prepends leading `@mention` handles to reply text. tw2img detects and strips these, replacing them with a "Replying to @handle" line rendered in grey above the tweet body — matching the native X UI.

## Verified badges

Inline SVG checkmark badges are rendered with correct colours:

| Account type | Badge colour |
|---|---|
| X Blue (`is_blue_verified`) | Blue circle, white check |
| Business (`verified_type = "Business"`) | Gold circle, black check |
| Government (`verified_type = "Government"`) | Grey-blue circle, black check |

## Parody / Commentary / Fan labels

Accounts flagged with `parody_commentary_fan_label` get a small grey theatre-mask glyph followed by the label text rendered beneath the username. The API field is accepted as either a plain string or a dict with `label`/`text`/`name` keys.

## Avatar rendering

Profile image URLs are automatically upgraded from `_normal` (48 px) to `_bigger` (73 px) for crisper output at retina scale.

## Stat display

Engagement stats (replies, retweets, quotes, likes, views) are rendered with inline SVG icons below the focal tweet. Numbers are abbreviated by default (e.g. `12.3K`). Use `--full-stats` for unabbreviated figures (e.g. `12,345`).

## Source label

The client name (e.g. "iPhone", "Android") is shown at the right of the stat row. Use `--no-source` to hide it.
