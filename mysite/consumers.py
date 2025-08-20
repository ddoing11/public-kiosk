import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from .state_manager import StateManager
from .utils import (
    get_gpt_streaming_response, azure_text_to_speech,
    is_consultation_request, is_consultation_end_request, is_simple_agreement,
    is_identity_confirmation
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
        """페이지 접속 시 자동으로 안내 멘트 시작 (상태 관리 강화)"""
        try:
            self.client_state['step'] = 'prompting'
            await self.send_message('mic.off')
            
            guidance_text = "주민번호 앞 여섯자리를 입력하여 서류 출력 서비스로 이동하시거나 상담이 필요하시면 상담이라고 말씀해주세요."
            await self.send_message('tts.text', {'text': guidance_text})
            
            # [중요] 음성 안내 후 상태를 'listening'으로 명확히 변경
            self.client_state['step'] = 'listening'
            await self.send_message('status', {'state': 'listening'})
            logger.info("Automatic guidance sent. State -> listening")
            
        except Exception as e:
            logger.error(f"Automatic guidance error: {str(e)}")
            await self.send_error("시스템 초기화 중 오류가 발생했습니다.")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            # user.confirmation_start 로직을 patient 데이터와 함께 받도록 수정
            if message_type == 'user.confirmation_start':
                await self.start_user_confirmation(data.get('patient'))
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

    async def start_user_confirmation(self, patient):
        """TTS로 사용자 확인 질문을 시작하는 함수 (상태 전송 추가)"""
        if patient:
            self.client_state['patient_to_confirm'] = patient
            self.client_state['step'] = 'confirming_user'
            
            confirmation_text = f"{patient.get('patient_name')} 님이 맞으시면, '본인 확인'이라고 말씀해주세요."
            
            # [수정] TTS 메시지와 함께 클라이언트의 상태를 'confirming_user'로 변경하라는 메시지를 보냅니다.
            await self.send_message('tts.text', {'text': confirmation_text})
            await self.send_message('state.update', {'step': 'confirming_user'})
            
            logger.info(f"사용자 확인 시작: {confirmation_text}")
        else:
            await self.send_error("확인할 환자 정보가 없습니다. 다시 시도해주세요.")

    async def handle_stt_result(self, text):
        """STT 결과 처리 (상태 전송 추가)"""
        text = text.strip().lower()
        current_step = self.client_state.get('step', 'idle')
        logger.info(f"STT Result: '{text}', State: '{current_step}'")

        if current_step == 'confirming_user':
            if is_identity_confirmation(text):
                patient = self.client_state.get('patient_to_confirm')
                await self.send_message('user.confirmed', {'patient': patient})
            else:
                await self.send_message('user.confirmation_failed')
            
            # [수정] 확인 절차가 끝났으니 클라이언트 상태를 다시 'listening'으로 되돌리라고 알려줍니다.
            self.client_state['step'] = 'listening'
            await self.send_message('state.update', {'step': 'listening'})
            return

        # 2. 듣기 단계 (상담 요청 또는 이름 입력 처리)
        if current_step == 'listening':
            # 상담 요청 단어가 포함된 경우
            if is_consultation_request(text):
                await self.start_consultation(text)
            # 그 외의 모든 음성은 이름 입력으로 간주하고 클라이언트로 전달
            elif len(text) > 0:
                await self.send_message('stt.forward_to_input', {'text': text})
            return
        
        # 3. 상담 진행 중인 경우
        if current_step == 'advising':
            if is_consultation_end_request(text):
                await self.end_consultation_with_guidance()
            else:
                await self.continue_consultation(text)
            return

        logger.warning(f"STT result received in unhandled state: {current_step}")

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


    