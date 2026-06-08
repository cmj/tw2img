# Cards & Attachments

## Link preview cards

Standard Twitter cards render with a rounded bordered box containing:

- **`summary`** — card image (thumbnail), domain, title, description
- **`summary_large_image`** — same, with a larger image above the text
- **Player cards** — thumbnail with a play-button overlay

## `unified_card` (image_website / video_website)

Some tweets use a `unified_card` type in their card's `binding_values`. tw2img parses the `unified_card` JSON blob to reconstruct:

- Title and domain from the `details` component's `title`/`subtitle` fields
- URL from `destination_objects`
- Media from `media_entities` keyed by the `media` component's `id`

Portrait media inside these cards is centred with black side bars rather than stretched to full card width.

## Grok share attachments

Tweets that share a Grok AI conversation render a styled card showing:

- The user's question in bold
- Grok's answer rendered from Markdown (bold, headers, bullets, inline links)
- Trailing source URLs collected into a "Sources" section with one link per line

tw2img reads Grok data from two paths:
1. `grok_share_attachment.items[]` on the tweet result (primary)
2. `unified_card` component objects of type `grok_share` (fallback)

## Community Notes (Birdwatch)

Tweets with a `birdwatch_pivot` render a "Community Note" block below the tweet content:

- Header bar with a group icon and "Community Note" label
- Note text with linkified URLs
- `help.x.com` links are stripped automatically to reduce noise

## Space / broadcast cards

`audiospace` card type renders as a rounded card with the broadcast thumbnail image and title.

## Quoted tweets

Quoted tweets render inside a rounded bordered box with a lighter background, including:

- Author avatar, name, verification badge, handle, relative timestamp
- Body text with linkified URLs and hashtags
- Inline media (same aspect-ratio grid logic as top-level tweets, capped at 400 px)
- Their own link cards

**Tombstones:** if a quoted tweet was deleted or the result object is empty, a "This tweet is unavailable" placeholder renders inside the quote block instead of disappearing silently.
