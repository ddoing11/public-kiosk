# mysite/simplified_message_router.py

"""
simplified_message_router.py (v3 - 안정화)

- `main.py` 대신 `asgi.py` (Daphne/Uvicorn)와 통합되도록 수정.
- `state_manager` v3 (`KioskStateManager`)와 호환되도록 수정.
- LLM/TTS/DB 핸들러를 `state_manager` 내부로 이동시킴 (router는 단순 메시지 전달만)
- 비동기(async) 처리 강화.
- Django 세션 및 인증 로직 제거 (키오스크는 비인증 세션 기반)
"""

import json
import logging
from .state_manager import KioskStateManager # ★ 여기가 KioskStateManager 여야 합니다 ★
from .database_handler import DatabaseManager
import asyncio



# 로거 설정
logger = logging.getLogger('kiosk')

class HospitalMessageRouter:
    """
    WebSocket 메시지를 수신하여 KioskStateManager로 전달하고,
    StateManger의 콜백(TTS)을 클라이언트로 전송하는 비동기 라우터.
    """
    def __init__(self, send_callback):
        self.send_callback = send_callback  # WebSocket 'send' 함수 (async)
        self.db_manager = DatabaseManager()

        # ★ 여기가 KioskStateManager 여야 합니다 ★
        self.state_manager = KioskStateManager(
            send_callback=self.send_tts_message,
            db_manager=self.db_manager
        )
        logger.info(f"[Router] HospitalMessageRouter v3 (KioskStateManager) 초기화 완료.")

    async def handle_message(self, text_data):
        """
        클라이언트로부터 WebSocket 메시지를 비동기로 수신합니다.
        """
        try:
            data = json.loads(text_data)
            logger.warning(f"[Router DEBUG] 원문 수신 데이터: {text_data}")
            message_type = data.get('type', '').strip().lower()

            logger.debug(f"[Router] 메시지 수신: {message_type}, 데이터: {data.get('text') or data.get('patient', {}).get('patient_name')}")

            if message_type == 'stt.result':
                text = data.get('text', '').strip()
                if text:
                    # StateManager의 비동기 핸들러 호출
                    await self.state_manager.handle_user_input(text)

            elif message_type == 'user.confirmation_start':
                # '본인 확인 시작' (이름 검색 후 확인 단계 진입 시)
                patient_data = data.get('patient')
                if patient_data:
                    await self.state_manager.start_user_confirmation(patient_data)

            elif message_type in ["tts.complete", "audio.tts_playback_complete", "ttscomplete"]:
                logger.info("[KioskConsumer] TTS 완료 신호 수신 — 마이크 활성화 처리")
                await asyncio.sleep(0.2)
                await self.send_message("mic.on")
                return


            else:
                logger.warning(f"[Router] 알 수 없는 메시지 타입: {message_type}")

        except json.JSONDecodeError:
            logger.error(f"[Router] JSON 디코딩 오류: {text_data}")
        except Exception as e:
            logger.critical(f"[Router] 메시지 처리 중 심각한 오류: {e}", exc_info=True)
            await self.send_error_message(f"서버 내부 오류 발생: {e}")

    async def send_tts_message(self, text, step=None):
        """
        StateManager가 호출하는 콜백 함수.
        TTS 텍스트를 클라이언트로 전송합니다. (비동기)
        """
        logger.info(f"[Router] TTS 전송 -> {text}")
        message = {'type': 'tts.text', 'text': text}

        # 현재 상태(step)가 제공되면 함께 전송
        if step:
            # 📌 step 값이 dict 형태일 경우 처리 추가 (state_manager v3 호환)
            if isinstance(step, dict):
                 message.update(step) # type, text 등 추가 정보 포함 가능
            else:
                 message['step'] = step # 기존 문자열 step
            logger.debug(f"[Router] 상태/스텝 정보 포함: {step}")


        await self.send_callback(message)

    async def send_mic_control(self, mode):
        """
        클라이언트의 마이크 상태를 제어합니다. (비동기)
        """
        if mode not in ['on', 'off']:
            return

        logger.info(f"[Router] 마이크 제어 -> {mode.upper()}")
        await self.send_callback({
            'type': f'mic.{mode}'
        })

    async def send_user_confirmed(self, patient_data):
        """
        본인 확인이 최종 완료되었음을 클라이언트에 알립니다. (비동기)
        """
        logger.info(f"[Router] 사용자 확인 완료 전송: {patient_data.get('patient_name')}")
        await self.send_callback({
            'type': 'user.confirmed',
            'patient': patient_data
        })

    async def send_confirmation_failed(self):
        """
        본인 확인 실패를 클라이언트에 알립니다. (비동기)
        """
        logger.warning(f"[Router] 사용자 확인 실패 전송")
        await self.send_callback({
            'type': 'user.confirmation_failed'
        })

    # 📌 send_stt_to_input 콜백 제거: state_manager v3에서 직접 step 정보로 전달
    # async def send_stt_to_input(self, text): ...

    async def send_error_message(self, error_text):
        """
        클라이언트에 오류 메시지를 전송합니다. (비동기)
        """
        logger.error(f"[Router] 클라이언트 오류 전송: {error_text}")
        await self.send_callback({
            'type': 'error',
            'message': error_text
        })

    async def cleanup(self):
        """
        WebSocket 연결 종료 시 StateManager 정리 (비동기)
        """
        # 📌 state_manager가 None이 아닐 경우에만 cleanup 호출
        if self.state_manager:
            await self.state_manager.cleanup()
            logger.info(f"[Router] 라우터 및 상태 관리자 정리 완료.")
        else:
            logger.warning("[Router] StateManager가 초기화되지 않아 cleanup 생략.")