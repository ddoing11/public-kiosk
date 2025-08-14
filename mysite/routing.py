"""
WebSocket URL 라우팅 설정
"""

from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # 키오스크 WebSocket 엔드포인트
    re_path(r'ws/kiosk/$', consumers.KioskWebSocketConsumer.as_asgi()),
    
    # 추가 WebSocket 엔드포인트들 (필요시)
    # re_path(r'ws/admin/$', consumers.AdminWebSocketConsumer.as_asgi()),
    # re_path(r'ws/monitor/$', consumers.MonitorWebSocketConsumer.as_asgi()),
]