from django.db import migrations

STYLES = [
    dict(name="Classic Hardbound", slug="classic-hardbound", tagline="The family album that lasts for generations", best_for="wedding, event, portrait",
         description="A sturdy hard cover with pages that open in the traditional way. A safe, timeless choice for weddings and family events.",
         size_label="12 x 15 in", pages=30, max_photos=80, cover_material="Linen-finish hard cover", paper_quality="250 gsm matte art paper",
         design_style="Classic, two photos a page", price=3499, extra_page_price=60, print_cost=1700, sort_order=1),
    dict(name="Coffee-table Premium", slug="coffee-table-premium", tagline="Big, bold pages made to be shown", best_for="wedding, travel, wildlife, product",
         description="A large format album with a leather-look cover and thick pages. Single large photos get the room they deserve.",
         size_label="14 x 18 in", pages=40, max_photos=150, cover_material="Leather-look hard cover with box", paper_quality="300 gsm lustre paper",
         design_style="Full-bleed, large photos", price=6499, extra_page_price=90, print_cost=3200, sort_order=2),
    dict(name="Flush-mount Layflat", slug="flush-mount-layflat", tagline="Opens perfectly flat, no photo lost in the fold", best_for="wedding, portrait, fashion",
         description="Pages are mounted flat so a photo can run across both sides without a gap in the middle. Our most premium wedding album.",
         size_label="12 x 18 in", pages=30, max_photos=100, cover_material="Fabric cover, edge wrapped", paper_quality="300 gsm photo paper, layflat",
         design_style="Panoramic spreads", price=5499, extra_page_price=80, print_cost=2800, sort_order=3),
    dict(name="Slim Photobook", slug="slim-photobook", tagline="A light, modern book for a single shoot", best_for="portrait, fashion, street, event, travel",
         description="A slim soft-cover book. Right for a birthday, a portrait shoot, a trip or a small event.",
         size_label="8 x 10 in", pages=20, max_photos=40, cover_material="Matte soft cover", paper_quality="200 gsm matte paper",
         design_style="Clean and simple", price=1499, extra_page_price=35, print_cost=650, sort_order=4),
    dict(name="Mini Parents Album", slug="mini-parents-album", tagline="A small copy to hand to the family", best_for="wedding, event",
         description="A compact copy of the main album, made for parents and grandparents.",
         size_label="6 x 8 in", pages=20, max_photos=40, cover_material="Hard cover", paper_quality="200 gsm matte paper",
         design_style="Classic", price=1299, extra_page_price=30, print_cost=550, sort_order=5),
]


def seed(apps, schema_editor):
    AlbumStyle = apps.get_model("photopholio", "AlbumStyle")
    if AlbumStyle.objects.exists():
        return
    for row in STYLES:
        AlbumStyle.objects.create(status="available", delivery_days_min=7, delivery_days_max=10, **row)


def unseed(apps, schema_editor):
    apps.get_model("photopholio", "AlbumStyle").objects.filter(slug__in=[r["slug"] for r in STYLES]).delete()


class Migration(migrations.Migration):
    dependencies = [("photopholio", "0017_album_styles")]
    operations = [migrations.RunPython(seed, unseed)]
