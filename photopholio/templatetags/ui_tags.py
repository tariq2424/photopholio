from django import template

register = template.Library()

STYLE_ICONS = {
    "wedding": ("bi-balloon-heart", "oc-1", "Haldi to reception"),
    "portrait": ("bi-person-bounding-box", "oc-2", "Profiles and pre-wedding"),
    "fashion": ("bi-handbag", "oc-3", "Looks, brands, lookbooks"),
    "street": ("bi-signpost-split", "oc-4", "Candid city stories"),
    "event": ("bi-calendar-event", "oc-5", "Parties, corporate, birthdays"),
    "travel": ("bi-compass", "oc-6", "Trips and destinations"),
    "product": ("bi-box-seam", "oc-7", "Shop and catalogue shots"),
    "wildlife": ("bi-tree", "oc-8", "Nature and animals"),
}


@register.filter
def style_icon(value):
    return STYLE_ICONS.get(value, ("bi-camera", "oc-1", ""))[0]


@register.filter
def style_tone(value):
    return STYLE_ICONS.get(value, ("bi-camera", "oc-1", ""))[1]


@register.filter
def style_hint(value):
    return STYLE_ICONS.get(value, ("bi-camera", "oc-1", ""))[2]


_ASSET_VERSIONS = {}


@register.simple_tag
def asset(path):
    """Static URL with a version taken from the file itself, so a changed CSS/JS file is never served from an old browser cache.
    The version is the file's size and modified time; it changes only when the file changes."""
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static
    if path not in _ASSET_VERSIONS:
        try:
            import os
            f = finders.find(path)
            st = os.stat(f) if f else None
            _ASSET_VERSIONS[path] = f"{int(st.st_mtime)}{st.st_size}" if st else "0"
        except Exception:
            _ASSET_VERSIONS[path] = "0"
    return f"{static(path)}?v={_ASSET_VERSIONS[path]}"
