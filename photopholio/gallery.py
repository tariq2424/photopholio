"""Private shoot gallery and the customer's favourite selection (Patch 30).

The photographer uploads the shoot photos for a booking. Only the customer who booked, that photographer and staff can
open them: the pages need a login and are never listed publicly. If the customer ordered a printed album they pick their
favourites (up to the album's limit) and send the selection back."""
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.core.files.base import ContentFile
from django.http import Http404, JsonResponse
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import album_ops, album_pdf, album_themes, money, notifications
from .forms import MAX_IMAGE_MB
from .booking_flow import DELIVERY_SCHEMES
from .models import AlbumDesign, AlbumDesignPage, Booking, GalleryPhoto
from .roles import get_role, photographer_required

MAX_PER_UPLOAD = 40
MAX_PER_BOOKING = 400
MIN_PICKS = 5
MAX_DESIGN_PAGES = 60
MAX_CHANGE_ROUNDS = 3
LONG_EDGE = 2000


def _prepare(upload):
    """Returns a ContentFile (JPEG, longest side <= 2000 px) or raises ValueError with a message for the photographer."""
    from PIL import Image, ImageOps
    if upload.size > MAX_IMAGE_MB * 1024 * 1024:
        raise ValueError(f"{upload.name}: larger than {MAX_IMAGE_MB} MB.")
    try:
        img = Image.open(upload)
        img.load()
    except Exception:
        raise ValueError(f"{upload.name}: not a valid image.")
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((LONG_EDGE, LONG_EDGE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return ContentFile(out.getvalue(), name=f"{timezone.now():%H%M%S%f}.jpg")


def _album(booking):
    return booking.album


def _can_see(request, booking):
    u = request.user
    return u.is_staff or booking.user_id == u.id or booking.photographer.user_id == u.id


@photographer_required
def pro_gallery(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("photographer", "user"), pk=pk, photographer__user=request.user)
    can_upload = booking.status == "completed"  # photos are sent after the shoot is done
    album = _album(booking)
    locked = bool(album and album.selection_status == "submitted")
    if request.method == "POST":
        if not can_upload:
            messages.error(request, "Photos can be added after you mark the shoot as completed.")
            return redirect("pro_gallery", pk=pk)
        files = request.FILES.getlist("images") + request.FILES.getlist("folder")
        if request.FILES.getlist("folder"):          # a whole folder: keep the pictures, skip anything else in it
            files = [f for f in files if (f.content_type or "").startswith("image/") or f.name.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))]
        room = MAX_PER_BOOKING - booking.gallery.count()
        if not files:
            messages.error(request, "Choose at least one photo.")
            return redirect("pro_gallery", pk=pk)
        if len(files) > MAX_PER_UPLOAD:
            messages.error(request, f"Add up to {MAX_PER_UPLOAD} photos at a time.")
            return redirect("pro_gallery", pk=pk)
        if len(files) > room:
            messages.error(request, f"A booking can hold {MAX_PER_BOOKING} photos. You can add {max(room, 0)} more.")
            return redirect("pro_gallery", pk=pk)
        added, problems = 0, []
        for f in files:
            try:
                GalleryPhoto.objects.create(booking=booking, image=_prepare(f))
                added += 1
            except ValueError as exc:
                problems.append(str(exc))
        if added:
            messages.success(request, f"{added} photo{'s' if added != 1 else ''} added to the private gallery.")
            notifications.notify(
                booking.user, "New photos from your shoot",
                f"{booking.photographer.name} added {added} photo{'s' if added != 1 else ''} to your private gallery.",
                f"/bookings/{booking.pk}/gallery/", subject="photos")
        for line in problems[:5]:
            messages.error(request, line)
        return redirect("pro_gallery", pk=pk)
    photos = list(booking.gallery.all())
    return render(request, "photopholio/gallery_pro.html", {
        "b": booking, "photos": photos, "can_upload": can_upload, "album": album, "locked": locked,
        "delivered": bool(album and album.production_status == "delivered"),
        "favs": sum(1 for p in photos if p.is_favourite), "max_upload": MAX_PER_UPLOAD,
        "designs": list(album.designs.prefetch_related("pages")) if album else [],
        "can_design": bool(album and locked and album.design_status in ("", "changes")),
        "max_pages": MAX_DESIGN_PAGES})


@photographer_required
@require_POST
def pro_gallery_delete(request, pk):
    photo = get_object_or_404(GalleryPhoto.objects.select_related("booking"), pk=pk, booking__photographer__user=request.user)
    album = _album(photo.booking)
    if album and album.selection_status == "submitted":
        messages.error(request, "The customer has already sent their selection, so photos can no longer be removed.")
    else:
        photo.image.delete(save=False)
        photo.delete()
        messages.info(request, "Photo removed.")
    return redirect("pro_gallery", pk=photo.booking_id)


@login_required
def my_gallery(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("photographer", "user"), pk=pk)
    if not _can_see(request, booking):
        raise Http404("Gallery not found")
    album_ops.sweep_late()
    photos = list(booking.gallery.all())
    album = _album(booking)
    can_pick = bool(album and album.status == "paid" and booking.user_id == request.user.id)
    favs = sum(1 for p in photos if p.is_favourite)
    return render(request, "photopholio/gallery_customer.html", {
        "b": booking, "photos": photos, "album": album, "can_pick": can_pick,
        "locked": bool(album and album.selection_status == "submitted"),
        "favs": favs, "limit": album.max_photos if album else 0, "min_picks": MIN_PICKS,
        "is_owner": booking.user_id == request.user.id,
        "designs": list(album.designs.prefetch_related("pages")) if album else [],
        "design": album.latest_design if album else None,
        "rounds_left": max(0, MAX_CHANGE_ROUNDS - (album.designs.filter(status="changes").count() if album else 0)),
        "aspect": album.page_aspect if album else 0.75,
        "delivered": bool(album and album.production_status == "delivered"),
        "theme": album_themes.theme_for(album.style) if album else None,
        "can_pdf": bool(album and album.status == "paid" and any(f for f in photos if f.is_favourite))})


def _pick_context(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("photographer"), pk=pk, user=request.user)
    album = _album(booking)
    if not album or album.status != "paid":
        raise Http404("No album to pick photos for")
    return booking, album


@login_required
@require_POST
def gallery_toggle(request, pk, photo_pk):
    booking, album = _pick_context(request, pk)
    photo = get_object_or_404(GalleryPhoto, pk=photo_pk, booking=booking)
    if album.selection_status == "submitted":
        return JsonResponse({"ok": False, "error": "Your selection is already sent. Choose Change selection first."}, status=409)
    want = not photo.is_favourite
    if want and booking.gallery.filter(is_favourite=True).count() >= album.max_photos:
        return JsonResponse({"ok": False, "error": f"This album holds up to {album.max_photos} photos."}, status=409)
    photo.is_favourite = want
    photo.save(update_fields=["is_favourite"])
    return JsonResponse({"ok": True, "favourite": want, "count": booking.gallery.filter(is_favourite=True).count(),
                         "limit": album.max_photos})


@login_required
@require_POST
def gallery_submit(request, pk):
    booking, album = _pick_context(request, pk)
    n = booking.gallery.filter(is_favourite=True).count()
    if album.selection_status == "submitted":
        messages.info(request, "Your selection was already sent.")
    elif n < MIN_PICKS:
        messages.error(request, f"Pick at least {MIN_PICKS} photos (you picked {n}).")
    else:
        try:
            album.layout = int(request.POST.get("layout", album.layout))
        except (TypeError, ValueError):
            pass
        if album.layout not in (1, 2, 4):
            album.layout = 2
        album.selection_status, album.selection_submitted_at = "submitted", timezone.now()
        album.save(update_fields=["selection_status", "selection_submitted_at", "layout"])
        notifications.notify(booking.photographer.user, "Album photos selected",
                             f"{booking.user.first_name or booking.user.username} picked {n} photos, {album.layout} per page. Open the gallery to see them.",
                             f"/photographer/bookings/{booking.pk}/gallery/", subject="photos")
        messages.success(request, f"Selection sent to {booking.photographer.name} ({n} photos).")
    return redirect("my_gallery", pk=pk)


@login_required
@require_POST
def gallery_reopen(request, pk):
    booking, album = _pick_context(request, pk)
    if album.design_status:
        messages.error(request, "The design has already started, so the selection can no longer be changed.")
    elif album.selection_status == "submitted":
        album.selection_status, album.selection_submitted_at = "open", None
        album.save(update_fields=["selection_status", "selection_submitted_at"])
        messages.info(request, "You can change your selection. Send it again when you are done.")
    return redirect("my_gallery", pk=pk)


@photographer_required
@require_POST
def pro_design_submit(request, pk):
    booking = get_object_or_404(Booking.objects.select_related("photographer", "user"), pk=pk, photographer__user=request.user)
    album = _album(booking)
    if not album or album.selection_status != "submitted" or album.design_status not in ("", "changes"):
        messages.error(request, "A design can be sent after the customer sends their selection, and not while one is waiting for approval.")
        return redirect("pro_gallery", pk=pk)
    link = (request.POST.get("link") or "").strip()
    try:
        URLValidator(schemes=DELIVERY_SCHEMES)(link)
    except ValidationError:
        messages.error(request, "Paste a valid link to the design (a Drive or Dropbox file or folder).")
        return redirect("pro_gallery", pk=pk)
    version = (album.designs.last().version + 1) if album.designs.exists() else 1
    design = AlbumDesign.objects.create(order=album, version=version, link=link, note=(request.POST.get("note") or "").strip()[:500])
    album.design_status = "submitted"
    album.save(update_fields=["design_status"])
    notifications.notify(booking.user, "Your album design is ready",
                         f"{booking.photographer.name} sent design {version} of your {album.style_name}. Please approve it or ask for changes.",
                         f"/bookings/{booking.pk}/gallery/", subject="photos")
    messages.success(request, f"Design {version} sent to the customer.")
    return redirect("pro_gallery", pk=pk)


@login_required
@require_POST
def design_decide(request, pk, decision):
    booking, album = _pick_context(request, pk)
    design = album.latest_design
    if decision not in ("approve", "changes") or not design or design.status != "submitted" or album.design_status != "submitted":
        messages.error(request, "There is no design waiting for your answer.")
        return redirect("my_gallery", pk=pk)
    now = timezone.now()
    if decision == "approve":
        design.status, design.decided_at = "approved", now
        album.design_status, album.approved_at, album.production_status = "approved", now, "queued"
        design.save(update_fields=["status", "decided_at"])
        album.save(update_fields=["design_status", "approved_at", "production_status"])
        album_ops.log(album, "queued", "Design approved")
        money.album_release_if_due(album)
        notifications.notify(booking.photographer.user, "Album design approved",
                             f"{booking.user.first_name or booking.user.username} approved the design of the {album.style_name}.",
                             f"/photographer/bookings/{booking.pk}/gallery/", subject="photos")
        messages.success(request, f"Design approved. Your album will be delivered by {album.delivery_to.day} {album.delivery_to:%b}.")
        return redirect("my_gallery", pk=pk)
    note = (request.POST.get("note") or "").strip()[:500]
    if album.designs.filter(status="changes").count() >= MAX_CHANGE_ROUNDS:
        messages.error(request, f"You have used all {MAX_CHANGE_ROUNDS} rounds of changes. Please approve the design or contact us.")
    elif not note:
        messages.error(request, "Write what you want changed so the photographer knows.")
    else:
        design.status, design.customer_note, design.decided_at = "changes", note, now
        album.design_status = "changes"
        design.save(update_fields=["status", "customer_note", "decided_at"])
        album.save(update_fields=["design_status"])
        notifications.notify(booking.photographer.user, "Changes asked on the album design",
                             f"{booking.user.first_name or booking.user.username}: {note}"[:300],
                             f"/photographer/bookings/{booking.pk}/gallery/", subject="photos")
        messages.success(request, "Your changes were sent. You will be told when a new design is ready.")
    return redirect("my_gallery", pk=pk)


# --- Help center ---------------------------------------------------------------------

@login_required
def help_center(request):
    """The customer writes to the admin (for example, why an album is late) and reads the answers here."""
    from .models import AlbumOrder, HelpRequest
    albums = list(AlbumOrder.objects.filter(booking__user=request.user, status="paid").select_related("booking"))
    if request.method == "POST":
        text = " ".join((request.POST.get("message") or "").split())[:1000]
        topic = request.POST.get("topic") if request.POST.get("topic") in dict(HelpRequest.TOPIC_CHOICES) else "other"
        album = next((a for a in albums if str(a.pk) == request.POST.get("album")), None) if topic in ("late", "album") else None
        if len(text) < 5:
            messages.error(request, "Write a few words so we can help.")
        else:
            HelpRequest.objects.create(user=request.user, album=album, topic=topic, message=text)
            messages.success(request, "Message sent. We reply here, usually within a day.")
            return redirect("help_center")
    return render(request, "photopholio/help_center.html", {
        "requests": HelpRequest.objects.filter(user=request.user).select_related("album"),
        "albums": albums, "topics": HelpRequest.TOPIC_CHOICES,
        "pick_album": request.GET.get("album", ""), "pick_topic": request.GET.get("topic", "album"),
        "phone": getattr(settings, "SUPPORT_PHONE", ""),
    })


@login_required
def album_pdf_download(request, pk):
    """The album preview as a PDF: cover, the picked photos laid out as the customer chose, back cover.
    For the customer who booked and the photographer of that booking (and staff)."""
    from django.http import HttpResponse
    booking = get_object_or_404(Booking.objects.select_related("photographer", "user"), pk=pk)
    if not _can_see(request, booking):
        raise Http404("Gallery not found")
    album = _album(booking)
    picks = list(booking.gallery.filter(is_favourite=True))
    if not album or album.status != "paid" or not picks:
        messages.error(request, "There is no album with picked photos to download yet.")
        return redirect("my_gallery", pk=pk)
    data = album_pdf.build(album, picks)
    resp = HttpResponse(data, content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="photopholio-album-{booking.pk}.pdf"'
    resp["Cache-Control"] = "private, no-store"
    return resp
