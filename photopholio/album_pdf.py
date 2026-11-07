"""
A PDF of the album preview: the cover, the customer's picked photos laid out 1, 2 or 4 per page, and the back cover,
in the colours, fonts and page-number style of the chosen album style.

It is a preview of the layout (the real design comes from the photographer). No extra library: every page is drawn with Pillow
as a JPEG and written into a small PDF by hand, one page at a time, so a 60 page album does not fill the server's memory.
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import album_themes

W = 900                       # page width in pixels (the height follows the album's shape)
_FONTS = ["DejaVuSerif.ttf", "DejaVuSans.ttf", "LiberationSerif-Regular.ttf", "Arial.ttf"]


def _font(size, serif=True):
    names = _FONTS if serif else ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"] + _FONTS
    for n in names:
        for base in ("/usr/share/fonts/truetype/dejavu/", "/usr/share/fonts/truetype/liberation/", "C:/Windows/Fonts/", "/Library/Fonts/", ""):
            try:
                return ImageFont.truetype(base + n, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _gradient(size, top, bottom):
    w, h = size
    col = Image.new("RGB", (1, h))
    a, b = _rgb(top), _rgb(bottom)
    col.putdata([tuple(int(a[i] + (b[i] - a[i]) * y / max(1, h - 1)) for i in range(3)) for y in range(h)])
    return col.resize((w, h))


def _centered(d, text, y, font, fill, width, spacing=0):
    if spacing:
        text = (" " * 0).join(text)
    w = d.textlength(text, font=font)
    d.text(((width - w) / 2, y), text, font=font, fill=fill)


def _open(photo):
    with photo.image.open("rb") as f:
        im = Image.open(f)
        im.draft("RGB", (W * 2, W * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
    im.thumbnail((W, W))
    return im


def _fit(im, box):
    """Whole photo inside the box (nothing is cut), returns the pasted size."""
    bw, bh = box
    r = min(bw / im.width, bh / im.height)
    return im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)


def cover_page(h, t, title, size_label, photo_im):
    img = _gradient((W, h), t["cv1"], t["cv2"])
    d = ImageDraw.Draw(img)
    line, ink = _rgb(t["line"]), _rgb(t["ink"])
    d.rectangle([int(W * .07), int(h * .035), W - int(W * .05), h - int(h * .035)], outline=line, width=2)
    top = int(h * .08)
    if photo_im is not None:
        box = (W - int(W * .24), int(h * .6))
        ph = ImageOps.fit(photo_im, box, Image.LANCZOS)
        x = int(W * .15)
        d.rectangle([x - 4, top - 4, x + box[0] + 3, top + box[1] + 3], fill=line)
        img.paste(ph, (x, top))
        ty = top + box[1] + int(h * .05)
    else:
        ty = int(h * .4)
    f1, f2 = _font(int(W * .058)), _font(int(W * .026), serif=False)
    _centered(d, title, ty, f1, ink, W)
    d.line([W / 2 - 30, ty + int(W * .085), W / 2 + 30, ty + int(W * .085)], fill=line, width=2)
    _centered(d, (size_label or "").upper(), ty + int(W * .105), f2, ink, W)
    return img


def back_page(h, t, pages):
    img = _gradient((W, h), t["cv1"], t["cv2"])
    d = ImageDraw.Draw(img)
    line, ink = _rgb(t["line"]), _rgb(t["ink"])
    d.rectangle([int(W * .05), int(h * .035), W - int(W * .07), h - int(h * .035)], outline=line, width=2)
    cx, cy, r = W // 2, int(h * .42), int(W * .06)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=line, width=2)
    fp = _font(int(r * 1.1))
    d.text((cx - d.textlength("P", font=fp) / 2, cy - r * .62), "P", font=fp, fill=ink)
    _centered(d, "PHOTOPHOLIO", cy + r + 30, _font(int(W * .034)), ink, W)
    _centered(d, f"{pages} PAGES", cy + r + 30 + int(W * .06), _font(int(W * .022), serif=False), ink, W)
    return img


def photo_page(h, t, photos, per, number, total):
    img = _gradient((W, h), t["pg1"], t["pg2"])
    d = ImageDraw.Draw(img)
    mx, my = int(W * .09), int(h * .07)
    area_w, area_h = W - 2 * mx, h - 2 * my - int(h * .04)
    if per == 1:
        cells = [(mx, my, area_w, area_h)]
    elif per == 2:
        gap = int(h * .03); ch = (area_h - gap) // 2
        cells = [(mx, my, area_w, ch), (mx, my + ch + gap, area_w, ch)]
    else:
        gap = int(W * .03); cw, ch = (area_w - gap) // 2, (area_h - gap) // 2
        cells = [(mx + (i % 2) * (cw + gap), my + (i // 2) * (ch + gap), cw, ch) for i in range(4)]
    for im, (x, y, cw, ch) in zip(photos, cells):
        f = _fit(im, (cw - 12, ch - 12))
        px, py = x + (cw - f.width) // 2, y + (ch - f.height) // 2
        d.rectangle([px - 6, py - 6, px + f.width + 5, py + f.height + 5], fill=(255, 255, 255), outline=(222, 214, 196))
        img.paste(f, (px, py))
    txt = album_themes.number_text(number, t["num"], total)
    fn = _font(int(W * .026), serif=t["num"] not in ("mono", "ring"))
    pn = _rgb(t["pn"])
    tw = d.textlength(txt, font=fn)
    ny = h - int(h * .055)
    if t["num"] == "ring":
        r = int(W * .028)
        d.ellipse([W / 2 - r, ny - 2, W / 2 + r, ny + 2 * r - 2], outline=pn, width=2)
        d.text((W / 2 - tw / 2, ny + r - int(W * .016)), txt, font=fn, fill=pn)
    else:
        d.text(((W - tw) / 2, ny), txt, font=fn, fill=pn)
    return img


def _jpeg(img):
    b = io.BytesIO()
    img.save(b, "JPEG", quality=82)
    return b.getvalue()


def write_pdf(pages):
    """pages: iterable of PIL images. Returns the PDF bytes."""
    out = io.BytesIO()
    offsets = []

    def obj(body):
        offsets.append(out.tell())
        out.write(f"{len(offsets)} 0 obj\n".encode() + body + b"\nendobj\n")

    out.write(b"%PDF-1.4\n")
    jpgs = []
    sizes = []
    for im in pages:
        jpgs.append(_jpeg(im)); sizes.append(im.size)
    n = len(jpgs)
    # object numbers: 1 catalog, 2 pages, then for page i: 3+3i page, 4+3i content, 5+3i image
    obj(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + 3 * i} 0 R" for i in range(n))
    obj(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    for i, (jpg, (w, h)) in enumerate(zip(jpgs, sizes)):
        pw, ph = w * 0.5, h * 0.5
        obj(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.1f} {ph:.1f}] /Resources << /XObject << /Im0 {5 + 3 * i} 0 R >> >> /Contents {4 + 3 * i} 0 R >>".encode())
        stream = f"q {pw:.1f} 0 0 {ph:.1f} 0 0 cm /Im0 Do Q".encode()
        obj(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
        obj(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length {len(jpg)} >>\nstream\n".encode() + jpg + b"\nendstream")
    xref = out.tell()
    out.write(f"xref\n0 {len(offsets) + 1}\n0000000000 65535 f \n".encode())
    for o in offsets:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(offsets) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def build(order, photos):
    """PDF bytes for an AlbumOrder and its picked GalleryPhoto rows."""
    t = album_themes.theme_for(order.style)
    h = int(W / (order.page_aspect or 0.75))
    per = order.layout if order.layout in (1, 2, 4) else 2
    first = None
    cover_im = None
    try:
        if order.style and order.style.cover_image:
            with order.style.cover_image.open("rb") as f:
                cover_im = ImageOps.exif_transpose(Image.open(f)).convert("RGB")
                cover_im.thumbnail((W, W))
    except Exception:
        cover_im = None
    photos = list(photos)
    total = max(1, -(-len(photos) // per)) if photos else 1

    def gen():
        nonlocal first
        ci = cover_im
        if ci is None and photos:
            try:
                ci = _open(photos[0])
            except Exception:
                ci = None
        yield cover_page(h, t, order.style_name, order.style.size_label if order.style else "", ci)
        for i in range(0, len(photos), per):
            ims = []
            for p in photos[i:i + per]:
                try:
                    ims.append(_open(p))
                except Exception:
                    continue
            yield photo_page(h, t, ims, per, i // per + 1, total)
        yield back_page(h, t, order.pages)

    return write_pdf(gen())
