from .models import Notification


def unread_notifications(request):
    if not request.user.is_authenticated:
        return {"unread_notifications": 0}
    return {"unread_notifications": request.user.notifications.filter(read=False).count()}
