"""
Look of each album style: cover and back colours, page paper, and the fonts and number style.
One list, used by the customer preview (CSS variables) and by the PDF download, so both always match.
A style can pick its look in the admin; if it does not, it gets one automatically so albums never all look the same.
"""
THEMES = {
    # key: (label, cover top, cover bottom, cover text, cover line, page top, page bottom, page number colour, number style, title font)
    "midnight": {"label": "Midnight and gold", "cv1": "#3b3f5e", "cv2": "#171a2b", "ink": "#f1dfb5", "line": "#e2c482",
                 "pg1": "#fdfbf6", "pg2": "#f6f1e6", "pn": "#a79d86", "num": "plain", "font": "serif"},
    "rosewood": {"label": "Rosewood", "cv1": "#8a2a42", "cv2": "#3d0f1b", "ink": "#f8dccb", "line": "#e8b9a3",
                 "pg1": "#fff8f5", "pg2": "#f9e8e3", "pn": "#b5636e", "num": "dash", "font": "script"},
    "forest": {"label": "Forest", "cv1": "#336651", "cv2": "#13281e", "ink": "#ece5c9", "line": "#cdbb7a",
               "pg1": "#f7faf4", "pg2": "#e8f0e3", "pn": "#6d8a73", "num": "dot", "font": "elegant"},
    "ivory": {"label": "Ivory linen", "cv1": "#f4ecda", "cv2": "#ddcfae", "ink": "#4a3b24", "line": "#9b8352",
              "pg1": "#ffffff", "pg2": "#f6f2e8", "pn": "#8c7c5a", "num": "of", "font": "classic"},
    "noir": {"label": "Noir", "cv1": "#232326", "cv2": "#050506", "ink": "#ececee", "line": "#9094a0",
             "pg1": "#f5f5f6", "pg2": "#e3e3e6", "pn": "#6b6e75", "num": "mono", "font": "modern"},
    "ocean": {"label": "Ocean", "cv1": "#22658f", "cv2": "#0b2840", "ink": "#e4f2fb", "line": "#8ec8ea",
              "pg1": "#f5fafd", "pg2": "#e1eef7", "pn": "#5b8cad", "num": "ring", "font": "round"},
}
ORDER = list(THEMES)
CHOICES = [("", "Automatic")] + [(k, v["label"]) for k, v in THEMES.items()]

# The fonts are only names here; the page loads them and falls back to a close system font.
FONT_STACKS = {
    "serif": "'Playfair Display',Georgia,serif",
    "script": "'Cormorant Garamond','Palatino Linotype',Georgia,serif",
    "elegant": "'Cormorant Garamond',Georgia,serif",
    "classic": "'Libre Baskerville',Georgia,serif",
    "modern": "'Montserrat','Segoe UI',Arial,sans-serif",
    "round": "'Quicksand','Trebuchet MS',Arial,sans-serif",
}


def key_for(style):
    """The theme key of an AlbumStyle (or None): its own choice, else one picked from its id."""
    if style is not None and style.theme in THEMES:
        return style.theme
    if style is not None and style.pk:
        return ORDER[(style.pk - 1) % len(ORDER)]
    return ORDER[0]


def theme_for(style):
    key = key_for(style)
    t = dict(THEMES[key]); t["key"] = key; t["font_stack"] = FONT_STACKS[t["font"]]
    return t


def number_text(n, key_or_num, total=None):
    """Page number text in the style of the album."""
    num = THEMES.get(key_or_num, {}).get("num", key_or_num)
    n = int(n)
    return {"plain": str(n), "dash": f"— {n} —", "dot": f"· {n} ·", "of": f"{n} / {total}" if total else str(n),
            "mono": f"{n:02d}", "ring": str(n)}.get(num, str(n))
