"""Create the simple vector-style CaCaCa owner app icons."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'static' / 'brand'

def brand_font(size):
    for candidate in ('C:/Windows/Fonts/georgia.ttf', 'DejaVuSerif.ttf'):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    raise RuntimeError('Install Georgia or DejaVu Serif to regenerate the owner app icons.')


def icon(size):
    image = Image.new('RGB', (size, size), '#082d3f')
    draw = ImageDraw.Draw(image)
    margin = round(size * .1)
    draw.rounded_rectangle((margin, margin, size - margin, size - margin),
                           radius=round(size * .14), fill='#0e4554', outline='#81b2a8', width=max(2, size // 120))
    font = brand_font(round(size * .37))
    box = draw.textbbox((0, 0), 'Cá', font=font)
    draw.text(((size - (box[2] - box[0])) / 2 - box[0],
               (size - (box[3] - box[1])) / 2 - box[1] - size * .015),
              'Cá', font=font, fill='#ffffff')
    return image


if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512, 180):
        suffix = 'apple-touch' if size == 180 else str(size)
        icon(size).save(OUT / f'owner-app-{suffix}.png', optimize=True)
