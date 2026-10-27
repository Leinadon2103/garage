"""Makes app icons (ICO for the shortcut, PNGs for the manifest) from app/static/favicon.svg."""
import io
from pathlib import Path

import pymupdf
from PIL import Image

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def main() -> None:
    with pymupdf.open(STATIC / "favicon.svg") as doc:
        page = doc[0]
        zoom = 512 / page.rect.width
        png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).tobytes("png")
    large = Image.open(io.BytesIO(png)).convert("RGBA")
    large.save(STATIC / "icon-512.png")
    large.resize((192, 192), Image.LANCZOS).save(STATIC / "icon-192.png")
    large.save(STATIC / "garage.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Icons made in", STATIC)


if __name__ == "__main__":
    main()
