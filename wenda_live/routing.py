"""WebSocket URL routing for the Wenda-Live live-game consumers (Phase 3).

Referenced from configWendaLive.asgi via AuthMiddlewareStack, so scope['user']
(host auth) and scope['session'] (player identity) are populated by the time a
consumer's connect() runs.
"""

from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path('ws/host/<str:room_code>/', consumers.HostConsumer.as_asgi()),
    path('ws/play/<str:room_code>/', consumers.PlayConsumer.as_asgi()),
]
