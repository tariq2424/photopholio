from django import template

from ..models import Notification

register = template.Library()


@register.simple_tag
def unread_notifications(user):
    if not user.is_authenticated:
        return 0
    return Notification.objects.filter(user=user, is_read=False).count()


@register.simple_tag
def recent_notifications(user, limit=6):
    if not user.is_authenticated:
        return []
    return list(Notification.objects.filter(user=user, cleared=False)[:limit])


@register.simple_tag
def live_version(user):
    from ..livever import live_version as lv
    return lv(user)
