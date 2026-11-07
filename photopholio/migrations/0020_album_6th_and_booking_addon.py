from django.db import migrations, models
import django.db.models.deletion


def add_sixth(apps, schema_editor):
    AlbumStyle = apps.get_model("photopholio", "AlbumStyle")
    AlbumStyle.objects.get_or_create(slug="travel-journal-book", defaults=dict(
        name="Travel Journal Book", tagline="Your trip, told page by page",
        best_for="travel, wildlife, street",
        description="A square book made for journeys. Photos sit with space around them, so a trip reads like a story.",
        size_label="8 x 8 in", pages=24, max_photos=60, cover_material="Linen soft cover with photo window",
        paper_quality="220 gsm matte paper", design_style="Story pages, one theme per spread",
        price=1800, extra_page_price=40, print_cost=900, status="available",
        delivery_days_min=7, delivery_days_max=10, sort_order=6))


def remove_sixth(apps, schema_editor):
    apps.get_model("photopholio", "AlbumStyle").objects.filter(slug="travel-journal-book").delete()


class Migration(migrations.Migration):
    dependencies = [("photopholio", "0019_album_prices_1000_to_4000")]

    operations = [
        migrations.AddField(model_name="booking", name="deliverable", field=models.CharField(
            choices=[("photos", "Only the photographer"), ("album", "Photographer + printed album"), ("digital", "Photographer + digital photos")],
            default="photos", max_length=10)),
        migrations.CreateModel(
            name="AlbumOrder",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("style_name", models.CharField(max_length=120)),
                ("pages", models.PositiveSmallIntegerField()),
                ("price", models.PositiveIntegerField(help_text="What the customer pays for the album.")),
                ("print_cost", models.PositiveIntegerField(default=0, help_text="Estimated print lab cost at order time.")),
                ("design_percent", models.PositiveSmallIntegerField(default=60, help_text="Photographer's share of (price - print cost).")),
                ("design_fee", models.PositiveIntegerField(default=0, help_text="Paid to the photographer for designing the album.")),
                ("platform_commission", models.PositiveIntegerField(default=0, help_text="PhotoPholio's album commission.")),
                ("status", models.CharField(choices=[("placed", "Waiting for payment"), ("paid", "Paid"), ("cancelled", "Cancelled")], default="placed", max_length=10)),
                ("paid_at", models.DateTimeField(blank=True, null=True)),
                ("refund_amount", models.PositiveIntegerField(default=0)),
                ("payout_status", models.CharField(blank=True, choices=[("held", "Held"), ("ready", "Ready to pay"), ("paid", "Paid"), ("void", "Void")], default="", max_length=8)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("booking", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="album_order", to="photopholio.booking")),
                ("style", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="orders", to="photopholio.albumstyle")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.RunPython(add_sixth, remove_sixth),
    ]
