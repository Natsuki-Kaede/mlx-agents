"""Draw the app icon (gradient squircle with a four-point sparkle) as icon.png and AppIcon.icns."""

import math
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent
SIZE = 1024


def gradient(size: int) -> Image.Image:
    a, b = (91, 108, 255), (178, 91, 255)
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * (size - 1))
            px[x, y] = tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))
    return img


def sparkle(cx: float, cy: float, r: float, waist: float = 0.1) -> list[tuple[float, float]]:
    """Four-point star: tips on the axes joined by concave quadratic curves bending toward the centre."""
    tips = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in (0, math.pi / 2, math.pi, 3 * math.pi / 2)]
    points = []
    for i in range(4):
        (x0, y0), (x2, y2) = tips[i], tips[(i + 1) % 4]
        mid = math.pi / 4 + i * math.pi / 2
        x1, y1 = cx + r * waist * math.cos(mid), cy + r * waist * math.sin(mid)
        for step in range(48):
            t = step / 48
            points.append(((1 - t) ** 2 * x0 + 2 * (1 - t) * t * x1 + t**2 * x2, (1 - t) ** 2 * y0 + 2 * (1 - t) * t * y1 + t**2 * y2))
    return points


def main() -> None:
    inset = int(SIZE * 0.09)  # macOS icon grid: artwork sits inside the canvas with margin for the shadow
    body = SIZE - 2 * inset
    mask = Image.new("L", (body, body), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, body - 1, body - 1], radius=int(body * 0.225), fill=255)

    tile = gradient(body).convert("RGBA")
    gloss = Image.new("RGBA", (body, body), (0, 0, 0, 0))
    ImageDraw.Draw(gloss).ellipse([-body * 0.2, -body * 0.75, body * 1.2, body * 0.45], fill=(255, 255, 255, 40))
    tile = Image.alpha_composite(tile, gloss.filter(ImageFilter.GaussianBlur(body * 0.04)))
    star = Image.new("RGBA", (body, body), (0, 0, 0, 0))
    ImageDraw.Draw(star).polygon(sparkle(body * 0.47, body * 0.53, body * 0.33), fill=(255, 255, 255, 248))
    ImageDraw.Draw(star).polygon(sparkle(body * 0.75, body * 0.25, body * 0.11), fill=(255, 255, 255, 215))
    star = Image.alpha_composite(star.filter(ImageFilter.GaussianBlur(body * 0.012)), star)
    tile = Image.alpha_composite(tile, star)
    tile.putalpha(mask)

    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.paste((20, 20, 60, 110), (inset, inset + 14), mask)
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(18)))
    canvas.paste(tile, (inset, inset), tile)
    canvas.save(HERE / "icon.png")

    iconset = HERE / "build" / "AppIcon.iconset"
    shutil.rmtree(iconset, ignore_errors=True)
    iconset.mkdir(parents=True)
    for px in (16, 32, 128, 256, 512):
        canvas.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{px}x{px}.png")
        canvas.resize((px * 2, px * 2), Image.LANCZOS).save(iconset / f"icon_{px}x{px}@2x.png")
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "build" / "AppIcon.icns")], check=True)
    shutil.rmtree(iconset)
    print("icon.png and build/AppIcon.icns written", file=sys.stderr)


if __name__ == "__main__":
    main()
