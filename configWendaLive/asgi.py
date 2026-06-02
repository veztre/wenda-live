"""ASGI entrypoint for Wenda-Live.

HTTP traffic is served by Django's normal request/response cycle; WebSocket
traffic is routed through Channels to the consumers defined in
wenda_live.routing. AuthMiddlewareStack populates scope['user'] from the
session, so consumers can authorise hosts the same way views do.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'configWendaLive.settings')

# get_asgi_application() must run BEFORE importing anything that touches Django
# models or settings (e.g. wenda_live.routing), so app registry is ready.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from wenda_live.routing import websocket_urlpatterns  # noqa: E402


application = ProtocolTypeRouter({
    'http': django_asgi_app,
    'websocket': AllowedHostsOriginValidator(
        AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})
