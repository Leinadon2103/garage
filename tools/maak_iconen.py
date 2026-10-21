"""Maakt app-iconen (ICO voor de snelkoppeling, PNG's voor het manifest) uit app/static/favicon.svg."""
import io
from pathlib import Path

import pymupdf
from PIL import Image

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def main() -> None:
    with pymupdf.open(STATIC / "favicon.svg") as doc:
        pagina = doc[0]
        zoom = 512 / pagina.rect.width
        png = pagina.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True).tobytes("png")
    groot = Image.open(io.BytesIO(png)).convert("RGBA")
    groot.save(STATIC / "icoon-512.png")
    groot.resize((192, 192), Image.LANCZOS).save(STATIC / "icoon-192.png")
    groot.save(STATIC / "garage.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("Iconen gemaakt in", STATIC)


if __name__ == "__main__":
    main()
