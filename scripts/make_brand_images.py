"""Generate the brand icon.

Home Assistant's brands repository wants a 256px icon and a 512px @2x version.
The mark is the one idea the integration is actually about: a single light
whose colour sweeps from warm to cool, the way the adaptive curve moves it
across a day. Warm sits at the bottom left where the sun rises and sets, cool
at the top where it is overhead.

Drawn at 4x and downsampled, because anti-aliasing a circle by hand is not
worth it. Run with:

    .venv/bin/python scripts/make_brand_images.py
"""

from __future__ import annotations

import itertools
import math
import pathlib

from PIL import Image

OUT = pathlib.Path(__file__).parents[1] / "brands"
SUPERSAMPLE = 4

# The ends of the adaptive colour-temperature sweep. Saturated rather than
# literal: the true colours of 2000K and 5500K are both near-white, which
# disappears on the white card HACS draws these on.
# Three stops, not two. Colour temperature genuinely sweeps *through* white,
# and interpolating straight from orange to blue crosses grey instead --
# complementary colours mix to mud, which looked exactly as good as it sounds.
STOPS = (
    (0.00, (255, 132, 24)),
    (0.52, (255, 233, 203)),
    (1.00, (72, 162, 255)),
)


def _sweep(t: float) -> tuple[int, ...]:
    """The colour at position ``t`` along the warm-to-cool sweep."""
    t = min(max(t, 0.0), 1.0)
    for (t0, c0), (t1, c1) in itertools.pairwise(STOPS):
        if t <= t1:
            local = (t - t0) / (t1 - t0)
            return tuple(
                round(a + (b - a) * local) for a, b in zip(c0, c1, strict=True)
            )
    return STOPS[-1][1]


def _gradient_disc(size: int) -> Image.Image:
    """A disc filled with a warm-to-cool diagonal sweep."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    centre = size / 2
    radius = size * 0.46

    for y in range(size):
        for x in range(size):
            dx, dy = x - centre, y - centre
            if math.hypot(dx, dy) > radius:
                continue
            # Sweep along the diagonal: warm at the bottom left, cool at the
            # top right, which is the direction a day runs.
            t = ((dx / radius) - (dy / radius) + 2) / 4
            pixels[x, y] = (*_sweep(t), 255)
    return image


def build(target: int) -> Image.Image:
    size = target * SUPERSAMPLE
    # No specular highlight: at the size this is actually displayed it turned
    # into a distracting ring, and the sweep alone reads more clearly.
    return _gradient_disc(size).resize((target, target), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for target, name in ((256, "icon.png"), (512, "icon@2x.png")):
        image = build(target)
        image.save(OUT / name)
        print(f"wrote {OUT / name} ({image.size[0]}x{image.size[1]})")


if __name__ == "__main__":
    main()
