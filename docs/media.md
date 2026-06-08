# Media Rendering

## Image grids

tw2img uses aspect-ratio-aware layouts for 1–5 images:

| Count | Layout |
|---|---|
| 1 | Full width, natural height (capped at 510 px) |
| 2 | Side by side, column widths proportional to aspect ratio |
| 3 | Two on top, one below |
| 4 | Two rows of two |
| 5 | Two on top, three on bottom |

Column widths in multi-image grids are computed from the actual `original_info.width/height` values in the API response, so landscape images get proportionally more space than portrait ones.

**Single images** use `object-fit: contain` — portrait photos are never cropped or stretched. Multi-image grids use `object-fit: cover`.

## Videos & GIFs

Video and animated GIF media items render as:

- A thumbnail image from `media_url_https`
- A centred SVG play-button overlay (semi-transparent circle + triangle)
- A duration badge in the bottom-left corner (e.g. `0:42`) for videos with `duration_millis`

## AI-generated media

If any media item in a tweet carries a `grok_post_id` field (Twitter's marker for Grok-generated images), a "Made with AI" label with a robot icon is appended below the media grid.

## Media attribution

When media originates from a different account (e.g. an embedded broadcast clip), the source user's avatar, name, and verification badge are rendered in a small attribution row directly above the media block.

Attribution is sourced from:
1. `additional_media_info.source_user` on the media item (available in `TweetResultByRestId`)
2. The card's `amplify_card_user_results` binding value (available in `TweetDetail`)

## Quoted tweet media

The same aspect-ratio grid logic applies inside quoted tweet blocks. Video wraps inside quotes are capped at 400 px height instead of 510 px.

## Media in cards

See [cards.md](cards.md) for how link preview cards, `unified_card`, and Grok attachment cards handle their own media.
