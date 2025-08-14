"""
ASGI config for mysite project.

WebSocket과 HTTP를 함께 처리하도록 설정
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

# Django 설정 로드
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mysite.settings')

# Django ASGI 애플리케이션 초기화 (HTTP 요청 처리)
django_asgi_app = get_asgi_application()

# WebSocket 라우팅 import
from mysite.routing import websocket_urlpatterns

# ASGI 애플리케이션 구성
application = ProtocolTypeRouter({
    # HTTP 프로토콜 처리
    "http": django_asgi_app,
    
    # WebSocket 프로토콜 처리
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})