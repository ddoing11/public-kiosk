import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from .state_manager import StateManager
from .utils import get_gpt_streaming_response, azure_text_to_speech, is_consultation_request

logger = logging.getLogger('kiosk')

class KioskWebSocketConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state_manager = StateManager()
        self.client_state = None

    async def connect(self):
        await self.accept()
        self.client_state = self.state_manager.create_initial_state()
        logger.info("WebSocket client connected")
        
        # 연결 즉시 자동으로 안내 멘트 시작
        await self.start_automatic_guidance()

    async def disconnect(self, close_code):
        logger.info(f"WebSocket client disconnected: {close_code}")

    async def start_automatic_guidance(self):
        """페이지 접속 시 자동으로 안내 멘트 시작"""
        try:
            self.client_state['step'] = 'prompting'
            
            # 마이크 먼저 끄기
            await self.send_message('mic.off')
            
            # Azure TTS로 안내 멘트 출력
            guidance_text = "주민번호 앞 6자리를 입력하여 서류 출력 서비스로 이동하시거나 띵 소리 이후 '상담'이라고 말씀해주세요."
            
            # Azure TTS 실행 (내부에서 띵 소리 + 마이크 ON 처리)
            await azure_text_to_speech(guidance_text, self)
            
            # 상태를 listening으로 변경
            self.client_state['step'] = 'listening'
            await self.send_message('status', {'state': 'listening'})
            
            logger.info("Automatic guidance completed")
            
        except Exception as e:
            logger.error(f"Automatic guidance error: {str(e)}")
            await self.send_error("시스템 초기화 중 오류가 발생했습니다.")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'ui.touch_start':
                await self.handle_touch_start()
            elif message_type == 'stt.result':
                await self.handle_stt_result(data.get('text', ''))
            elif message_type == 'stt.partial':
                await self.handle_stt_partial(data.get('text', ''))
            else:
                logger.warning(f"Unknown message type: {message_type}")
                
        except json.JSONDecodeError:
            logger.error("Invalid JSON received")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await self.send_error("처리 중 오류가 발생했습니다.")

    async def handle_touch_start(self):
        """메인화면에서 상담 시작 처리 (사실상 안 쓰임 - 자동 시작됨)"""
        # 이미 자동으로 시작되므로 추가 처리 없음
        logger.info("Touch start received (automatic guidance already running)")

    async def handle_stt_result(self, text):
        """STT 결과 처리"""
        text = text.strip().lower()
        
        if self.client_state['step'] != 'listening':
            return
            
        if is_consultation_request(text):
            await self.start_consultation(text)
        else:
            # 상담이 아닌 다른 발화 시 재안내
            await self.send_guidance_retry()

    async def handle_stt_partial(self, text):
        """STT 부분 결과 처리 (옵션)"""
        logger.debug(f"STT partial: {text}")

    async def start_consultation(self, user_input):
        """상담 모드 진입"""
        self.client_state['step'] = 'advising'
        await self.send_message('status', {'state': 'advising'})
        
        # 마이크 끄기
        await self.send_message('mic.off')
        
        # GPT 스트리밍 응답 시작
        try:
            response_chunks = []
            async for delta, is_done in get_gpt_streaming_response(user_input):
                if is_done:
                    await self.send_message('gpt.stream', {'event': 'done'})
                    # 전체 응답을 Azure TTS로 읽기
                    full_response = ''.join(response_chunks)
                    if full_response.strip():
                        await azure_text_to_speech(full_response, self)
                    else:
                        await self.finish_consultation()
                else:
                    await self.send_message('gpt.stream', {'delta': delta})
                    response_chunks.append(delta)
                    
        except Exception as e:
            logger.error(f"GPT streaming error: {str(e)}")
            await self.send_error("상담 서비스에 일시적 문제가 있습니다.")
            await self.finish_consultation()

    async def finish_consultation(self):
        """상담 완료 후 복귀"""
        await asyncio.sleep(0.5)
        self.client_state['step'] = 'listening'
        await self.send_message('status', {'state': 'listening'})

    async def send_guidance_retry(self):
        """재안내 메시지"""
        await self.send_message('mic.off')
        retry_text = "주민번호 앞 6자리를 입력하시거나 '상담'이라고 말씀해주세요."
        await azure_text_to_speech(retry_text, self)

    async def send_message(self, msg_type, data=None):
        """클라이언트로 메시지 전송"""
        message = {'type': msg_type}
        if data:
            message.update(data)
        await self.send(text_data=json.dumps(message))

    async def send_error(self, error_message):
        """에러 메시지 전송 후 listening 복귀"""
        await self.send_message('mic.off')
        await azure_text_to_speech(error_message, self)
        self.client_state['step'] = 'listening'