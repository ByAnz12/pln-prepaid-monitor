"""Buat ulang aset brand integrasi ini.

Jalankan dari akar repositori:

    python scripts/make_brand_icon.py custom_components/pln_prepaid_monitor/brand

Butuh Pillow. Hasilnya deterministik, jadi ikon hanya berubah kalau angka di
berkas ini yang diubah.

Sengaja BUKAN logo PT PLN (Persero). Logo itu merek dagang terdaftar milik
mereka, sementara integrasi ini bukan buatan dan tidak berafiliasi dengan PLN.
Yang digambar di sini adalah maknanya: listrik prabayar dan sisa token.
"""

from PIL import Image, ImageDraw

S = 1024  # digambar 4x lalu dikecilkan, supaya tepiannya halus

GROUND_TOP = (29, 84, 128)      # #1D5480
GROUND_BOTTOM = (12, 32, 53)    # #0C2035
BOLT_TOP = (255, 209, 102)      # #FFD166
BOLT_BOTTOM = (245, 166, 35)    # #F5A623
FILL = (255, 193, 69)           # #FFC145

BOLT = [
    (0.615, 0.150),
    (0.280, 0.470),
    (0.470, 0.470),
    (0.385, 0.700),
    (0.720, 0.360),
    (0.530, 0.360),
]
BAR_LEFT, BAR_RIGHT, BAR_TOP, BAR_BOTTOM = 0.225, 0.775, 0.760, 0.815
BAR_FILLED = 0.45


def _linear_gradient(size, top, bottom, diagonal=True):
    """Gradien halus tanpa numpy: hitung kecil, lalu perbesar."""
    small = Image.new("RGB", (64, 64))
    px = small.load()
    for y in range(64):
        for x in range(64):
            t = ((x + y) / 126) if diagonal else (y / 63)
            px[x, y] = tuple(
                round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)
            )
    return small.resize((size, size), Image.BICUBIC)


def _scaled(points):
    return [(x * S, y * S) for x, y in points]


def build_icon() -> Image.Image:
    """Ubin membulat biru malam, petir kuning, dan bilah sisa token."""
    ground = _linear_gradient(S, GROUND_TOP, GROUND_BOTTOM).convert("RGBA")

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, S - 1, S - 1), radius=round(0.22 * S), fill=255
    )
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(ground, (0, 0), mask)

    # Petir: gradien sendiri supaya tidak terlihat datar.
    bolt_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bolt_mask).polygon(_scaled(BOLT), fill=255)
    icon.paste(_linear_gradient(S, BOLT_TOP, BOLT_BOTTOM, diagonal=False), (0, 0), bolt_mask)

    # Bilah sisa token: alur redup, terisi sebagian dengan warna petir.
    # Digambar di lapisan sendiri lalu dikomposit - ImageDraw menimpa nilai
    # piksel apa adanya, jadi menggambar warna semi-transparan langsung ke
    # ikon akan melubangi ubinnya, bukan meredupkannya.
    overlay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bar = ImageDraw.Draw(overlay)
    x0, x1 = BAR_LEFT * S, BAR_RIGHT * S
    y0, y1 = BAR_TOP * S, BAR_BOTTOM * S
    radius = (y1 - y0) / 2
    bar.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(255, 255, 255, 56))
    bar.rounded_rectangle(
        (x0, y0, x0 + (x1 - x0) * BAR_FILLED, y1), radius=radius, fill=(*FILL, 255)
    )
    return Image.alpha_composite(icon, overlay)


base = build_icon()
import sys
out = sys.argv[1]
for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
    base.resize((size, size), Image.LANCZOS).save(f"{out}/{name}", optimize=True)
    print(name, size)
