#!/usr/bin/env python3
"""Generate the woodchip-wallpaper ("Raufaser") image every feed item carries.

EasySignage shows the item image full screen behind the slide text and lays a
dark layer over it (white becomes ``#7d7d7d``). The image is therefore a white
woodchip wall: under that layer it reads as the same grey as the former plain
white image, now with the relief of a Raufaser wallpaper.

The texture is computed from scratch (no photo, no third-party image): a
height map of small, randomly turned wood chips is blurred, lit from the top
left and mapped onto near-white. All steps work on a periodic grid
(wrap-around stamping, FFT blur, ``np.roll`` gradients), so the picture also
tiles seamlessly. The output is deterministic for the fixed seed.

Run it whenever the texture parameters change, then update
``_ITEM_IMAGE_BYTES`` in ``src/build_feed.py`` (tests/test_item_image.py
checks it)::

    python scripts/make_feed_item_image.py
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image

WIDTH, HEIGHT = 1920, 1080
SEED = 20261023
# Mean grey level of the wall and the strength of its relief (8-bit levels).
# Highlights clip at white, so the relief shows mostly as shadows.
BASE = 251.0
RELIEF = 14.0
QUALITY = 80
TARGET = Path(__file__).resolve().parents[1] / "docs" / "assets" / "feed-item-raufaser.jpg"


def _chip_stamps(rng: np.random.Generator, count: int = 96) -> list[np.ndarray]:
    """Small elongated, slightly irregular bumps: the wood chips."""
    stamps = []
    for _ in range(count):
        length = rng.uniform(2.5, 9.0)
        width = rng.uniform(1.0, 2.6)
        angle = rng.uniform(0.0, np.pi)
        size = int(np.ceil(length)) * 2 + 3
        yy, xx = np.mgrid[:size, :size] - size // 2
        u = xx * np.cos(angle) + yy * np.sin(angle)
        v = -xx * np.sin(angle) + yy * np.cos(angle)
        bump = np.clip(1.0 - (u / length) ** 2 - (v / width) ** 2, 0.0, None) ** 0.7
        bump *= rng.uniform(0.7, 1.0, bump.shape)  # rough chip surface
        stamps.append(bump.astype(np.float32))
    return stamps


def _periodic_blur(field: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur via FFT; the FFT wraps around, so the edges stay seamless."""
    fy = np.fft.fftfreq(field.shape[0])[:, None]
    fx = np.fft.rfftfreq(field.shape[1])[None, :]
    kernel = np.exp(-2.0 * (np.pi * sigma) ** 2 * (fx**2 + fy**2))
    return np.fft.irfft2(np.fft.rfft2(field) * kernel, s=field.shape)


def height_map(rng: np.random.Generator) -> np.ndarray:
    field = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    stamps = _chip_stamps(rng)
    chips = WIDTH * HEIGHT // 80
    picks = rng.integers(0, len(stamps), chips)
    ys = rng.integers(0, HEIGHT, chips)
    xs = rng.integers(0, WIDTH, chips)
    heights = rng.uniform(0.4, 1.0, chips).astype(np.float32)
    for k, y, x, h in zip(picks, ys, xs, heights):
        stamp = stamps[k]
        rows = (np.arange(stamp.shape[0]) + y) % HEIGHT
        cols = (np.arange(stamp.shape[1]) + x) % WIDTH
        field[np.ix_(rows, cols)] += h * stamp
    # paper grain between the chips, then a soft unevenness of the wall
    field += 0.12 * rng.standard_normal(field.shape).astype(np.float32)
    field = _periodic_blur(field, 0.9)
    field += 24.0 * _periodic_blur(rng.standard_normal(field.shape), 40.0)
    return field


def shading(field: np.ndarray) -> np.ndarray:
    """Lambert shading, light from the top left, centred on zero, unit spread."""
    dx = (np.roll(field, -1, axis=1) - np.roll(field, 1, axis=1)) / 2.0
    dy = (np.roll(field, -1, axis=0) - np.roll(field, 1, axis=0)) / 2.0
    nx, ny, nz = -1.6 * dx, -1.6 * dy, np.ones_like(field)
    light = np.array([-1.0, -1.0, 1.4]) / np.linalg.norm([-1.0, -1.0, 1.4])
    lit = (nx * light[0] + ny * light[1] + nz * light[2]) / np.sqrt(nx**2 + ny**2 + nz**2)
    lit -= lit.mean()
    return lit / (lit.std() + 1e-9)


def render(seed: int = SEED) -> Image.Image:
    shade = np.clip(shading(height_map(np.random.default_rng(seed))), -3.0, 3.0)
    grey = BASE + RELIEF * shade
    # pull the mean back onto BASE (clipping at white lifts it a little)
    for _ in range(3):
        grey += BASE - np.clip(grey, 0, 255).mean()
    return Image.fromarray(np.clip(np.rint(grey), 0, 255).astype(np.uint8), "L").convert("RGB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=TARGET)
    args = parser.parse_args(argv)
    image = render()
    image.save(args.out, "JPEG", quality=QUALITY, optimize=True, progressive=False)
    mean = float(np.asarray(image.convert("L")).mean())
    print(f"{args.out.name}: {args.out.stat().st_size} bytes, mean grey {mean:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
