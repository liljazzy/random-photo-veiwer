"""Draws icon.ico (two overlapping photos). Run: python make_icon.py"""
from PIL import Image, ImageDraw, ImageFilter

S = 1024  # draw big, then shrink for crisp small sizes


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def photo(w, h, sky_top, sky_bottom, sun, far, near, hill):
    """One framed landscape photo."""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    border, radius = 34, 54
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle((0, 0, w - 1, h - 1), radius, fill=(250, 250, 252, 255))
    iw, ih = w - 2 * border, h - 2 * border
    pic = Image.new("RGBA", (iw, ih))
    d = ImageDraw.Draw(pic)
    for y in range(ih):
        d.line((0, y, iw, y), fill=lerp(sky_top, sky_bottom, y / ih) + (255,))
    d.ellipse((iw * 0.62, ih * 0.14, iw * 0.62 + ih * 0.26, ih * 0.14 + ih * 0.26), fill=sun + (255,))
    d.polygon([(0, ih * 0.72), (iw * 0.28, ih * 0.30), (iw * 0.55, ih * 0.72)], fill=far + (255,))
    d.polygon([(iw * 0.30, ih * 0.78), (iw * 0.62, ih * 0.38), (iw * 0.95, ih * 0.78)], fill=near + (255,))
    d.ellipse((-iw * 0.2, ih * 0.66, iw * 0.75, ih * 1.5), fill=hill + (255,))
    d.ellipse((iw * 0.3, ih * 0.74, iw * 1.3, ih * 1.6), fill=lerp(hill, (0, 0, 0), 0.18) + (255,))
    mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, iw - 1, ih - 1), radius - border // 2, fill=255)
    card.paste(pic, (border, border), mask)
    img.alpha_composite(card)
    return img


def shadowed(layer, blur=26, offset=(0, 18), opacity=110):
    pad = blur * 3
    out = Image.new("RGBA", (layer.width + pad * 2, layer.height + pad * 2), (0, 0, 0, 0))
    sh = Image.new("RGBA", out.size, (0, 0, 0, 0))
    alpha = layer.getchannel("A").point(lambda v: v * opacity // 255)
    sh.paste((20, 25, 50, 255), (pad + offset[0], pad + offset[1]), alpha)
    out.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)))
    out.alpha_composite(layer, (pad, pad))
    return out


def build():
    back = photo(640, 520, (255, 170, 110), (255, 226, 170), (255, 245, 200),
                 (176, 92, 120), (124, 62, 112), (74, 56, 108))
    front = photo(660, 540, (86, 160, 235), (196, 230, 252), (255, 214, 80),
                  (86, 138, 190), (48, 104, 150), (62, 160, 110))
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    b = shadowed(back).rotate(13, resample=Image.BICUBIC, expand=True)
    f = shadowed(front).rotate(-7, resample=Image.BICUBIC, expand=True)
    canvas.alpha_composite(b, (S // 2 - b.width // 2 - 105, S // 2 - b.height // 2 - 95))
    canvas.alpha_composite(f, (S // 2 - f.width // 2 + 70, S // 2 - f.height // 2 + 85))
    return canvas


if __name__ == "__main__":
    big = build()
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.save("icon.ico", sizes=[(s, s) for s in sizes])
    big.resize((256, 256), Image.LANCZOS).save("icon_preview.png")
    print("wrote icon.ico with sizes", sizes)
