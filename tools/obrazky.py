"""
Příprava obrázků pro web: ořez průhledných okrajů, čistě šedý uhel, WebP.

    uv run --no-project --with pillow --with numpy \
        python tools/obrazky.py assets/puvodni/03-boby.png assets/03-boby.webp

  - průhledné okraje se oříznou (zůstane malý okraj, aby kresba nebyla „nalepená“ na hranu)
  - kresby uhlem (téměř bez barev) se převedou na čistě šedou – zmizí barevné
    třásně, které nechává odstraňování pozadí; barevné obrázky zůstanou barevné
  - průhlednost se „vypeče“ na bílou: web obrázky s kartonem násobí (multiply)
    a karton × bílá = karton, takže výsledek je stejný jako s průhledností,
    jen je soubor několikrát menší (celkem zhruba 10× menší než PNG)

    --kvalita 88     kvalita WebP (výchozí 88; pro pozadí stačí 82)
    --pruhledne      zachovat průhlednost (pro obrázky, které se nenásobí)
"""
import argparse

import numpy as np
from PIL import Image


def is_charcoal(rgba):
    """Kresba je „uhel“, pokud skoro žádný viditelný pixel není výrazně barevný."""
    px = rgba.astype(np.float32) / 255
    rgb, a = px[..., :3], px[..., 3]
    mx, mn = rgb.max(-1), rgb.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    visible = a > 0.02
    return ((sat > 0.35) & visible).sum() < 0.2 * visible.sum()


def prepare(src, dst, quality=88, keep_alpha=False):
    im = Image.open(src)
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)

    if not has_alpha:                                   # např. karton na pozadí
        im.convert("RGB").save(dst, "WEBP", quality=quality, method=6)
        return im.size, "bez průhlednosti"

    im = im.convert("RGBA")
    alpha = im.getchannel("A").point(lambda v: 255 if v > 12 else 0)
    l, t, r, b = alpha.getbbox()
    pad = int(max(r - l, b - t) * 0.025)
    im = im.crop((max(0, l - pad), max(0, t - pad), min(im.width, r + pad), min(im.height, b + pad)))

    charcoal = is_charcoal(np.asarray(im))
    kind = "uhel" if charcoal else "barevný"

    if keep_alpha:
        if charcoal:
            gray = im.convert("L")
            im = Image.merge("RGBA", (gray, gray, gray, im.getchannel("A")))
        im.save(dst, "WEBP", quality=quality, alpha_quality=100, method=6)
        return im.size, kind + ", průhledný"

    px = np.asarray(im).astype(np.float32) / 255
    flat = px[..., :3] * px[..., 3:4] + (1 - px[..., 3:4])          # na bílou
    out = Image.fromarray((flat * 255 + 0.5).astype(np.uint8))
    if charcoal:
        out = out.convert("L")
    out.save(dst, "WEBP", quality=quality, method=6)
    return out.size, kind


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--kvalita", type=int, default=88)
    ap.add_argument("--pruhledne", action="store_true")
    args = ap.parse_args()
    size, kind = prepare(args.src, args.dst, args.kvalita, args.pruhledne)
    print(f"{args.src} → {args.dst}  {size[0]}×{size[1]}  {kind}")


if __name__ == "__main__":
    main()
