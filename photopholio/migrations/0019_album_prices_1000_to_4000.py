from django.db import migrations

# slug: (old price, old extra, old pages) -> (pages, price, extra page price, print cost, max photos)
# Every price a customer can see, for the included pages and for +10 and +20 pages, stays between Rs 1000 and Rs 4000.
CHANGES = {
    "mini-parents-album":   ((1299, 30, 20), (20, 1000, 20, 550, 40)),
    "slim-photobook":       ((1499, 35, 20), (20, 1200, 30, 650, 40)),
    "classic-hardbound":    ((3499, 60, 30), (30, 2200, 50, 1100, 80)),
    "flush-mount-layflat":  ((5499, 80, 30), (30, 3000, 50, 1700, 100)),
    "coffee-table-premium": ((6499, 90, 40), (30, 3200, 40, 1900, 100)),
}


def apply(apps, schema_editor):
    AlbumStyle = apps.get_model("photopholio", "AlbumStyle")
    for slug, (old, new) in CHANGES.items():
        row = AlbumStyle.objects.filter(slug=slug).first()
        if row and (row.price, row.extra_page_price, row.pages) == old:     # only if the admin has not changed it
            row.pages, row.price, row.extra_page_price, row.print_cost, row.max_photos = new
            row.save()


def undo(apps, schema_editor):
    AlbumStyle = apps.get_model("photopholio", "AlbumStyle")
    for slug, (old, new) in CHANGES.items():
        row = AlbumStyle.objects.filter(slug=slug).first()
        if row and (row.pages, row.price, row.extra_page_price) == new[:3]:
            row.price, row.extra_page_price, row.pages = old
            row.save()


class Migration(migrations.Migration):
    dependencies = [("photopholio", "0018_seed_album_styles")]
    operations = [migrations.RunPython(apply, undo)]
