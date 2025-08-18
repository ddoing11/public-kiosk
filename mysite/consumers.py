import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from .state_manager import StateManager
from .utils import (
    get_gpt_streaming_response, azure_text_to_speech, 
    is_consultation_request, is_consultation_end_request, is_simple_agreement
)

logger = logging.getLogger('kiosk')

class KioskWebSocketConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.state_manager = StateManager()
        self.client_state = None

    async def connect(self):
        await self.accept()
        self.client_state = self.state_manager.create_initial_state()
      
        self.client_state['retry_count'] = 0
        self.client_state['max_retries'] = 2  # 최대 2번까지만 재안내
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
            guidance_text = "주민번호 앞 여섯자리를 입력하여 서류 출력 서비스로 이동하시거나 띵 소리 이후 '상담'이라고 말씀해주세요."
            
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
        """🔥 수정된 STT 결과 처리"""
        text = text.strip().lower()
        current_step = self.client_state['step']
        
        logger.info(f"STT 결과 처리: '{text}', 현재 단계: {current_step}")
        
        # 빈 텍스트나 너무 짧은 텍스트는 무시
        if len(text.strip()) < 2:
            logger.info(f"너무 짧은 텍스트 무시: '{text}'")
            return
        
        # TTS 관련 텍스트 필터링 (자기 음성 인식 방지)
        # 더 정확한 TTS 텍스트 필터링
        tts_phrases = ['무엇을 도와드릴', '목적에 따라 필요한', '원하시는 목적을', '안내해 드리겠습니다']
        if any(phrase in text for phrase in tts_phrases):
            logger.info(f"TTS 음성 인식 감지 - 무시: '{text}'")
            return
        
        # 상담 중인 경우
        if current_step == 'advising':
            # 상담 종료 요청 확인
            if is_consultation_end_request(text):
                await self.end_consultation_with_guidance()
                return
            else:
                # 계속 상담 진행
                await self.continue_consultation(text)
                return
        
        # listening 상태가 아니면 무시
        if current_step != 'listening':
            logger.info(f"listening 상태가 아님 - 무시: {current_step}")
            return
        
        # 1. 단순 동의 표현인지 먼저 확인 ("네", "예" 등)
        if is_simple_agreement(text):
            await self.handle_simple_agreement()
            return
            
        # 2. 상담 요청인지 확인
        if is_consultation_request(text):
            await self.start_consultation(text)
            return
        
        # 3. 의미있는 발화가 있는 경우 → 상담으로 처리 (길이 3자 이상)
        if len(text.strip()) >= 3:
            logger.info(f"의미있는 발화를 상담으로 처리: '{text}'")
            await self.start_consultation(text)
            return
        
        # 4. 그 외 무시
        logger.info(f"의미없는 발화 무시: '{text}'")

    async def handle_simple_agreement(self):
        """단순 동의 표현 처리 ("네", "예" 등) - 무시"""
        logger.info("단순 동의 표현 감지 - 무시하고 대기 상태 유지")
        # 아무 처리도 하지 않음 - 사용자가 주민번호 입력하거나 상담 요청할 때까지 대기

    async def handle_stt_partial(self, text):
        """STT 부분 결과 처리 (옵션)"""
        logger.debug(f"STT partial: {text}")

    async def start_consultation(self, user_input):
        """상담 모드 진입"""
        self.client_state['step'] = 'advising'
        self.client_state['retry_count'] = 0  # 재안내 카운터 리셋
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
                        # 상담 종료 멘트가 포함되어 있는지 확인
                        if "주민번호 앞 여섯자리를 입력하시면" in full_response:
                            await azure_text_to_speech(full_response, self)
                            # 바로 listening 상태로 복귀
                            await asyncio.sleep(0.5)
                            self.client_state['step'] = 'listening'
                            await self.send_message('status', {'state': 'listening'})
                            logger.info("상담 종료 - 다시 상담 가능 상태")
                        else:
                            await azure_text_to_speech(full_response, self)
                            await self.finish_consultation()
                    else:
                        await self.finish_consultation()
                else:
                    await self.send_message('gpt.stream', {'delta': delta})
                    response_chunks.append(delta)
                    
        except Exception as e:
            logger.error(f"GPT streaming error: {str(e)}")
            await self.send_error("상담 서비스에 일시적 문제가 있습니다.")
            await self.finish_consultation()

    async def continue_consultation(self, user_input):
        """상담 계속 진행"""
        # 마이크 끄기
        await self.send_message('mic.off')
        
        try:
            response_chunks = []
            async for delta, is_done in get_gpt_streaming_response(user_input):
                if is_done:
                    await self.send_message('gpt.stream', {'event': 'done'})
                    full_response = ''.join(response_chunks)
                    if full_response.strip():
                        # 상담 종료 멘트 확인
                        if "주민번호 앞 여섯자리를 입력하시면" in full_response:
                            await azure_text_to_speech(full_response, self)
                            # 바로 listening 상태로 복귀
                            await asyncio.sleep(0.5)
                            self.client_state['step'] = 'listening'
                            await self.send_message('status', {'state': 'listening'})
                            logger.info("상담 종료 - 다시 상담 가능 상태")
                        else:
                            await azure_text_to_speech(full_response, self)
                            await self.finish_consultation()
                    else:
                        await self.finish_consultation()
                else:
                    await self.send_message('gpt.stream', {'delta': delta})
                    response_chunks.append(delta)
                    
        except Exception as e:
            logger.error(f"상담 계속 오류: {str(e)}")
            await self.send_error("상담 중 오류가 발생했습니다.")
            await self.finish_consultation()

    async def end_consultation_with_guidance(self):
        """상담 종료 및 서류발급 안내 - 바로 listening 상태로 복귀"""
        await self.send_message('mic.off')
        
        end_message = "도움이 되셨기를 바랍니다. 원하시는 서류를 발급받으시려면 주민번호 앞 여섯자리를 입력하시면 서류 발급이 가능합니다. 감사합니다!"
        
        await azure_text_to_speech(end_message, self)
        
        # 바로 listening으로 복귀
        await asyncio.sleep(0.5)
        self.client_state['step'] = 'listening'  # 다시 상담 가능
        await self.send_message('status', {'state': 'listening'})
        logger.info("상담 종료 - 다시 상담 가능 상태")

    async def finish_consultation(self):
        """상담 완료 후 복귀 (계속 상담 가능)"""
        await asyncio.sleep(0.5)
        self.client_state['step'] = 'advising'  # 상담 상태 유지
        await self.send_message('status', {'state': 'advising'})

    async def return_to_initial_state(self):
        """초기 상태로 완전 복귀"""
        await asyncio.sleep(1.0)
        self.client_state['step'] = 'listening'
        self.client_state['retry_count'] = 0
        await self.send_message('status', {'state': 'listening'})

    async def send_guidance_retry(self):
        """재안내 메시지 (거의 사용되지 않음)"""
        retry_count = self.client_state.get('retry_count', 0)
        
        if retry_count < self.client_state.get('max_retries', 1):  # 최대 1번만
            await self.send_message('mic.off')
            retry_text = "주민번호 앞 여섯자리를 입력하시거나 '상담'이라고 말씀해주세요."
            self.client_state['retry_count'] = retry_count + 1
            await azure_text_to_speech(retry_text, self)
            logger.info(f"재안내 실행 ({retry_count + 1}/{self.client_state['max_retries']})")
        else:
            # 재안내 제한 초과 시 대기 상태 유지
            logger.info("재안내 제한 초과 - 대기 상태 유지")
            await self.send_message('mic.off')
            await asyncio.sleep(0.5)
            await self.send_message('mic.on')

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
        self.client_state['retry_count'] = 0