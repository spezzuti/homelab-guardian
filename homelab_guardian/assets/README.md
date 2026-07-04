# Brand assets

Drop the files below into this directory and the dashboard picks them up on
the next page load — no config, no restart. Absent files fall back to the
text-only header band.

| File | Used as | Specs |
|---|---|---|
| `hero.png` / `hero.webp` | Character art blended into the right side of the header band | Portrait or square, ≥ 800px tall, **transparent background** (or solid `#10141a` to match the band). Subject weighted to the RIGHT of the frame — the left edge fades into the band. Keep it under ~400 KB (webp preferred). |
| `logotype.png` / `logotype.webp` | Replaces the lettered "Homelab Guardian" title | Wide banner, roughly 5:1, ≥ 1200×240, transparent background. Rendered at 60px tall. |

`.webp` is preferred over `.png` when both exist. Files are served at
`/brand/<name>` (fixed allowlist, public, cached for a day).

## Generation prompts that match the dashboard

The band behind the art is dark slate stone (`#10141a` → `#1a212b` gradient)
with cold light. Art with warm/orange lighting will look pasted on; keep the
palette cold steel, slate, and iron with a single cold highlight.

**hero** (character):

> Graphic novel style, semi-realistic digital painting. A hulking, muscular,
> bearded guardian warrior, chest-up, gripping a massive battle axe across
> his body. Aggressive, protective stare directly at the viewer. Weathered
> steel and leather armor. Cold rim lighting in pale blue-grey, deep shadow.
> Palette: dark slate, iron grey, cold steel highlights — no warm tones.
> Isolated on a transparent background, subject weighted to the right of
> frame, high resolution.

**logotype** (stone lettering):

> The words "HOMELAB GUARDIAN" carved into weathered grey stone, bold roman
> capitals, deep chiseled bevels, fine cracks running through the letters,
> front-lit with cold light from above. Isolated on a transparent
> background, wide banner composition, single line, high resolution.
