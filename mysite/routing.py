"""
WebSocket URL 라우팅 설정
"""

from django.urls import re_path
from . import consumers
from . import service_consumers

websocket_urlpatterns = [
    # 키오스크 WebSocket 엔드포인트
    re_path(r'ws/kiosk/$', consumers.KioskWebSocketConsumer.as_asgi()),
    re_path(r'ws/kiosk/services/$', service_consumers.ServiceWebSocketConsumer.as_asgi()),

]


