import json
from channels.generic.websocket import AsyncWebsocketConsumer
import azure.cognitiveservices.speech as speechsdk
from django.conf import settings
from .simplified_message_router import HospitalMessageRouter

class HospitalKioskConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.message_router = HospitalMessageRouter()

    async def connect(self):
        await self.accept()
        self.session_state = await self.message_router.handle_connection(self)
        print("🔗 병원 키오스크 클라이언트 연결")

    async def disconnect(self, close_code):
        await self.message_router.cleanup_connection(self)

    async def receive(self, text_data=None, bytes_data=None):
        if text_data:
            await self.message_router.process_message(self, text_data)
        elif bytes_data:
            # 음성 데이터 처리
            result = await self.recognize_speech(bytes_data)
            if result:
                await self.message_router.process_message(self, result)

    async def recognize_speech(self, audio_bytes):
        """Azure Speech Service 음성 인식"""
        # 음성 → 텍스트 변환 로직
        pass