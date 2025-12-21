from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class RoomMember(models.Model):
    """Tracks users currently in a video chat room."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=200)
    uid = models.CharField(max_length=1000)
    room_name = models.CharField(max_length=200)
    insession = models.BooleanField(default=True)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = ['uid', 'room_name']

    def __str__(self):
        return f"{self.name} in {self.room_name}"
