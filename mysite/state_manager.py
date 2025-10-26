# mysite/state_manager.py

"""
KioskStateManager (v3 - 안정화)

- `simplified_message_router` (v3)와 호환.
- LLM, TTS, DB 핸들러를 모두 상태 관리자 내부로 통합.
- 모든 I/O(TTS, DB)를 비동기(async)로 처리.
- 대화 흐름(FSM)을 더 명확하게 분리.
- 타임아웃/정리 로직 추가.
"""

import json
import asyncio
import logging
from datetime import datetime, timedelta
import re
from django.conf import settings
import openai
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv
# import soundfile as sf               # [수정됨] '띵' 소리 제거
# import sounddevice as sd             # [수정됨] '띵' 소리 제거
# import numpy as np                   # [수정됨] '띵' 소리 제거
from concurrent.futures import ThreadPoolExecutor

# 로거 설정
logger = logging.getLogger('kiosk')
load_dotenv()

# OpenAI 설정
OPENAI_API_KEY = settings.OPENAI_API_KEY
if OPENAI_API_KEY:
    # openai.api_key = OPENAI_API_KEY # OpenAI V1 이상에서는 이 방식 사용 안 함
    pass
else:
    logger.warning("OPENAI_API_KEY가 설정되지 않았습니다. LLM 기능이 제한될 수 있습니다.")

# Azure TTS 설정
AZURE_SPEECH_KEY = settings.AZURE_SPEECH_KEY
AZURE_SPEECH_REGION = settings.AZURE_SPEECH_REGION

# ★★★ 여기가 KioskStateManager 클래스 정의입니다 ★★★
class KioskStateManager:
    """
    KioskStateManager (v3 - 안정화)
    - KioskStateManager는 단일 WebSocket 연결(단일 사용자)의 상태를 관리합니다.
    - Router(Consumer)는 연결마다 KioskStateManager 인스턴스를 생성합니다.
    """

    STATE_IDLE = 'idle'
    STATE_AWAITING_BIRTH = 'awaiting_birth'
    STATE_AWAITING_NAME = 'awaiting_name'
    STATE_CONFIRMING_USER = 'confirming_user'
    STATE_AWAITING_SERVICE = 'awaiting_service'
    STATE_AWAITING_DOCUMENT = 'awaiting_document'
    STATE_AWAITING_ISSUE = 'awaiting_issue'
    STATE_BUSY = 'busy' # TTS/LLM/DB 처리 중

    def __init__(self, send_callback, db_manager):
        self.send_tts_callback = send_callback # Router의 비동기 send 함수
        self.db_manager = db_manager

        self.state = self.STATE_IDLE
        self.user_context = {} # 인증된 사용자 정보 (patient_id, patient_name 등)
        self.conversation_history = []
        self.last_interaction_time = datetime.now()

        # 비동기 I/O 작업을 위한 스레드 풀
        self.executor = ThreadPoolExecutor(max_workers=5)

        self.current_tts_future = None # 현재 재생 중인 TTS Future
        self.pending_llm_task = None   # 현재 처리 중인 LLM Task

        self.ding_sound = None # self._load_ding_sound() # [수정됨] '띵' 소리 제거

        # OpenAI V1 이상 클라이언트 초기화
        if OPENAI_API_KEY:
             self.openai_client = openai.OpenAI(api_key=OPENAI_API_KEY)
        else:
             self.openai_client = None


        logger.info(f"[StateM] KioskStateManager v3 (async) 초기화 완료.")
        logger.debug(f"[StateM] LLM='{settings.LLM_PROVIDER}', TTS='{settings.TTS_PROVIDER}', STT='{settings.STT_MODE}'")

    # --- 1. 주요 상태 진입/전환 로직 ---

    async def initialize_session(self):
        """(v3 신규) 세션 시작 및 초기 프롬프트"""
        logger.info("[StateM] 세션 초기화 및 환영 메시지 시작.")
        self.state = self.STATE_BUSY
        await self._play_ding_and_speak(
            "안녕하세요. 병원 서류 발급 키오스크입니다. 먼저 본인 확인을 위해, 주민등록번호 앞 6자리를 말씀해주세요.",
            next_state=self.STATE_AWAITING_BIRTH
        )

    async def start_user_confirmation(self, patient_data):
        """(v3 신규) 이름 검색 후, 본인 확인 단계 진입"""
        logger.info(f"[StateM] 본인 확인 단계 진입: {patient_data.get('patient_name')}")
        self.state = self.STATE_BUSY
        self.user_context = patient_data # 임시 저장

        prompt = (
            f"{patient_data.get('patient_name')} 님이 맞으시면 '본인 확인' 또는 '맞아요'라고 말씀해주세요. "
            f"본인이 아니시라면, 다시 '이름 검색'이라고 말씀해주세요."
        )
        await self._play_ding_and_speak(prompt, next_state=self.STATE_CONFIRMING_USER)

    # --- 2. 사용자 입력(STT) 처리 ---

    async def handle_user_input(self, text):
        """
        라우터(Router)로부터 사용자 음성 입력(text)을 수신합니다.
        현재 상태(self.state)에 따라 입력을 처리합니다.
        """
        logger.info(f"[StateM] 입력 수신 (상태: {self.state}): {text}")
        self.last_interaction_time = datetime.now()

        # LLM/TTS가 진행 중일 때 들어온 입력은 일단 무시 (에코 방지)
        # 📌 BUSY 상태 확인 로직 강화
        if self.state == self.STATE_BUSY:
            logger.warning(f"[StateM] BUSY 상태 (TTS/LLM 처리 중?) 입력 무시: {text}")
            return # BUSY 상태면 처리하지 않고 종료

        # 상태별 핸들러 호출 전에 BUSY 상태로 설정
        current_handler_state = self.state # 핸들러 호출 시점의 상태 저장
        logger.debug(f"[StateM] 핸들러 호출 시도 (상태: {current_handler_state})")
        self.state = self.STATE_BUSY # 핸들러 실행 전 BUSY로 설정

        try:
            # 상태별 핸들러 호출
            if current_handler_state == self.STATE_AWAITING_BIRTH:
                await self._handle_birth_input(text)
            elif current_handler_state == self.STATE_AWAITING_NAME:
                await self._handle_name_input(text)
            elif current_handler_state == self.STATE_CONFIRMING_USER:
                await self._handle_user_confirmation(text)
            elif current_handler_state == self.STATE_AWAITING_SERVICE:
                await self._handle_service_request(text)
            # (TODO: 다른 상태 핸들러 추가 - 예: STATE_AWAITING_ISSUE)
            elif current_handler_state == self.STATE_AWAITING_ISSUE:
                 await self._handle_issue_confirmation(text) # 이슈 확인 핸들러 추가
            else:
                # 'idle' 또는 정의되지 않은 상태에서 입력이 들어온 경우
                logger.warning(f"[StateM] 처리기 없는 상태({current_handler_state}) 또는 IDLE 상태 입력. 무시 또는 재안내.")
                # 필요시 여기서 재안내 메시지 전송
                # await self.send_tts_callback("죄송합니다. 현재는 응답할 수 없습니다.")
                if current_handler_state != self.STATE_IDLE: # IDLE 상태가 아니었다면 복구 시도
                    self.state = current_handler_state # BUSY 상태 해제하고 이전 상태 복구 시도
                else:
                    self.state = self.STATE_IDLE # IDLE이었다면 IDLE 유지


        except Exception as e:
            logger.error(f"[StateM] 입력 처리 중 심각한 오류: {e}", exc_info=True)
            await self.send_tts_callback(f"처리 중 오류가 발생했습니다.") # 사용자에게는 오류 상세 내용 숨김
            self.state = self.STATE_IDLE # 오류 발생 시 IDLE로 안전하게 복귀


    # --- 3. 상태별 입력 처리 핸들러 (비공개) ---

    async def _handle_birth_input(self, text):
        """(상태: STATE_AWAITING_BIRTH) 주민번호 입력 처리"""
        logger.debug(f"[StateM] 주민번호 처리 시도: {text}")

        # (v3) LLM을 사용하여 주민번호/날짜 추출
        llm_response = await self._run_llm_extraction(
            text,
            "주민등록번호 앞 6자리(예: 900101) 또는 생년월일 6자리(예: 90년 1월 1일)"
        )

        birth_number = llm_response.get("extracted_value")

        if birth_number and re.match(r'^\d{6}$', birth_number):
            logger.info(f"[StateM] 주민번호 6자리 추출 성공: {birth_number}")
            self.user_context['birth_number'] = birth_number
            await self._play_ding_and_speak(
                "주민번호 확인되었습니다. 이제 이름을 말씀해주세요.",
                next_state=self.STATE_AWAITING_NAME
            )
        else:
            logger.warning(f"[StateM] 주민번호 6자리 추출 실패: {birth_number}")
            await self._play_ding_and_speak(
                "주민등록번호 앞 6자리를 찾지 못했습니다. '90년 1월 1일'처럼 다시 말씀해주세요.",
                next_state=self.STATE_AWAITING_BIRTH # 상태 유지
            )

    async def _handle_name_input(self, text):
        """(상태: STATE_AWAITING_NAME) 이름 입력 처리"""
        logger.debug(f"[StateM] 이름 처리 시도: {text}")

        # (v3) LLM을 사용하여 이름 추출
        llm_response = await self._run_llm_extraction(
            text,
            "한글 이름 2~6자리 (예: 홍길동)"
        )

        name = llm_response.get("extracted_value")

        if name and 2 <= len(name) <= 6:
            logger.info(f"[StateM] 이름 추출 성공: {name}")
            # (v3 신규) 이름 검색 결과를 클라이언트 UI(HTML)로 전달
            # 📌 stt.forward_to_input 스텝 수정: 실제 이름 값을 포함하여 전달
            await self.send_tts_callback(
                f"'{name}' 님을 검색합니다. 검색 결과는 화면을 확인해주세요.",
                step={'type': 'stt.forward_to_input', 'text': name} # step을 dict 형태로 수정
            )
            # 📌 상태 전환 로직 수정: BUSY -> IDLE 명시적 전환
            self.state = self.STATE_IDLE # 이름 검색 후 상태 관리자는 대기 상태(IDLE)로 복귀
                                        # (이후 user.confirmation_start 메시지로 상태 전환됨)
            logger.info(f"[StateM] 이름 검색 요청 후 IDLE 상태로 전환.")
        else:
            logger.warning(f"[StateM] 이름 추출 실패: {name}")
            await self._play_ding_and_speak(
                "이름을 찾지 못했습니다. '홍길동'처럼 이름을 다시 말씀해주세요.",
                next_state=self.STATE_AWAITING_NAME # 상태 유지
            )

    async def _handle_user_confirmation(self, text):
        """(상태: STATE_CONFIRMING_USER) 본인 확인 응답 처리"""
        logger.debug(f"[StateM] 본인 확인 처리 시도: {text}")

        # (v3) LLM을 사용하여 의도 분석
        llm_response = await self._run_llm_intent_analysis(
            text,
            ["confirm_identity", "deny_identity", "restart_name_search"]
        )

        intent = llm_response.get("intent")

        if intent == "confirm_identity":
            logger.info(f"[StateM] 본인 확인 성공: {self.user_context.get('patient_name')}")
            # (v3 신규) Router를 통해 클라이언트(JS)에 최종 인증 성공 전송
            # Router(Consumer)는 이 콜백을 받고 'user.confirmed' 메시지를 전송
            # 📌 라우터 콜백 호출 방식 변경: 안전하게 __self__ 확인
            router = getattr(self.send_tts_callback, '__self__', None)
            if router and hasattr(router, 'send_user_confirmed'):
                 await router.send_user_confirmed(self.user_context)
            else:
                 logger.warning("[StateM] Router의 send_user_confirmed 콜백 호출 불가.")


            # (인증 성공 후 다음 단계 안내)
            await asyncio.sleep(0.5) # JS가 페이지 넘길 시간
            await self._play_ding_and_speak(
                f"{self.user_context.get('patient_name')} 님, 안녕하세요. 원하시는 서비스를 말씀해주세요. (예: 진료 확인서 발급)",
                next_state=self.STATE_AWAITING_SERVICE
            )

        elif intent == "deny_identity" or intent == "restart_name_search":
            logger.warning(f"[StateM] 본인 확인 실패/거부. 이름 검색 재시작.")

            # 📌 라우터 콜백 호출 방식 변경: 안전하게 __self__ 확인
            router = getattr(self.send_tts_callback, '__self__', None)
            if router and hasattr(router, 'send_confirmation_failed'):
                await router.send_confirmation_failed()
            else:
                logger.warning("[StateM] Router의 send_confirmation_failed 콜백 호출 불가.")


            self.user_context = {} # 임시 저장했던 사용자 정보 삭제
            await self._play_ding_and_speak(
                "본인 확인이 취소되었습니다. 이름을 다시 말씀해주세요.",
                next_state=self.STATE_AWAITING_NAME
            )

        else: # (confirm/deny/restart 모두 아님)
            logger.warning(f"[StateM] 본인 확인 의도 불명확: {intent}")
            await self._play_ding_and_speak(
                "죄송합니다. '본인 확인' 또는 '다시 검색'이라고 말씀해주세요.",
                next_state=self.STATE_CONFIRMING_USER # 상태 유지
            )

    async def _handle_service_request(self, text):
        """(상태: STATE_AWAITING_SERVICE) 서비스 요청 처리"""
        logger.debug(f"[StateM] 서비스 요청 처리 시도: {text}")

        # (v3) LLM을 사용하여 의도 분석
        llm_response = await self._run_llm_intent_analysis(
            text,
            ["request_document", "request_receipt", "other_service"]
        )

        intent = llm_response.get("intent")
        document_entity = llm_response.get("entity", {}).get("document_type") # LLM이 추출한 서류 종류

        requested_doc = None
        if intent == "request_document":
             requested_doc = document_entity or "진료 확인서" # LLM 추출값 우선 사용
             logger.info(f"[StateM] 서류 발급 요청 확인: {requested_doc}")

        elif intent == "request_receipt":
             requested_doc = "영수증" # 영수증은 고정
             logger.info(f"[StateM] 영수증 발급 요청 확인")

        if requested_doc:
            self.user_context['requested_doc'] = requested_doc
            await self._play_ding_and_speak(
                f"{requested_doc} 발급을 요청하셨습니다. 발급을 원하시면 '발급'이라고 말씀해주세요.",
                next_state=self.STATE_AWAITING_ISSUE
            )
        else: # other_service 또는 unknown
             logger.warning(f"[StateM] 알 수 없는 서비스 요청: {intent}, Text: {text}")
             await self._play_ding_and_speak(
                 "죄송합니다. '진료 확인서 발급' 또는 '영수증 발급'이라고 말씀해주세요.",
                 next_state=self.STATE_AWAITING_SERVICE # 상태 유지
             )


    # 📌 이슈 확인 핸들러 추가
    async def _handle_issue_confirmation(self, text):
        """(상태: STATE_AWAITING_ISSUE) 발급 확인 처리"""
        logger.debug(f"[StateM] 발급 확인 처리 시도: {text}")

        llm_response = await self._run_llm_intent_analysis(
             text,
             ["confirm_issue", "cancel_issue", "request_different_document"] # 가능한 의도
        )
        intent = llm_response.get("intent")
        requested_doc = self.user_context.get('requested_doc')

        if intent == "confirm_issue":
             logger.info(f"[StateM] 발급 확정: {requested_doc}")
             # TODO: 실제 발급(프린팅) 로직 호출
             # await self._initiate_printing(requested_doc)
             await self._play_ding_and_speak(
                 f"{requested_doc} 발급을 시작합니다. 잠시만 기다려주세요.", # 발급 시작 안내
                 next_state=self.STATE_BUSY # 발급 중 상태 (임시)
             )
             # 임시: 발급 완료 가정 후 다음 상태로
             await asyncio.sleep(3) # 발급 시간 가정
             await self._play_ding_and_speak(
                 f"{requested_doc} 발급이 완료되었습니다. 추가로 필요하신 서비스가 있으신가요?",
                 next_state=self.STATE_AWAITING_SERVICE # 발급 후 서비스 선택으로 복귀
             )

        elif intent == "cancel_issue":
             logger.info(f"[StateM] 발급 취소: {requested_doc}")
             await self._play_ding_and_speak(
                 "발급을 취소했습니다. 다른 원하시는 서비스가 있으신가요?",
                 next_state=self.STATE_AWAITING_SERVICE
             )

        elif intent == "request_different_document":
             logger.info(f"[StateM] 다른 서류 요청으로 판단.")
             # 다른 서류 요청 처리 로직 (STATE_AWAITING_SERVICE 핸들러 재호출 또는 유사 로직)
             await self._play_ding_and_speak(
                 "네, 다른 어떤 서류가 필요하신가요?",
                 next_state=self.STATE_AWAITING_SERVICE
             )

        else: # unknown
             logger.warning(f"[StateM] 발급 확인 의도 불명확: {intent}")
             await self._play_ding_and_speak(
                 f"죄송합니다. {requested_doc} 발급을 원하시면 '발급', 아니면 '취소'라고 말씀해주세요.",
                 next_state=self.STATE_AWAITING_ISSUE # 상태 유지
             )


    # --- 4. 비동기 I/O (LLM / TTS / Audio) ---

    async def _run_llm_extraction(self, text, target_description):
        """(v3) LLM을 사용하여 텍스트에서 특정 정보 추출 (비동기 스레드)"""
        if not self.openai_client:
             logger.error("[LLM] OpenAI 클라이언트가 초기화되지 않았습니다.")
             return {"extracted_value": None, "error": "OpenAI client not initialized"}

        logger.debug(f"[LLM] 추출 작업 시작: {target_description}")
        prompt = f"""
        사용자 발화: "{text}"
        위 발화에서 다음 항목을 추출하세요:
        - 항목: {target_description}

        추출한 값을 JSON 형식으로 {{"extracted_value": "값"}} 으로만 응답하세요.
        추출할 수 없으면 {{"extracted_value": null}} 로 응답하세요.
        (예: 90년 1월 1일 -> 900101, 저 홍길동입니다 -> 홍길동)
        """

        def llm_call():
            try:
                response = self.openai_client.chat.completions.create( # self.openai_client 사용
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": "당신은 사용자 발화에서 특정 정보를 추출하는 AI입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.0,
                    response_format={"type": "json_object"}
                )
                content = response.choices[0].message.content
                logger.debug(f"[LLM] 추출 응답: {content}")
                return json.loads(content)
            except Exception as e:
                logger.error(f"[LLM] 추출 작업 오류: {e}", exc_info=True)
                return {"extracted_value": None, "error": str(e)}

        loop = asyncio.get_event_loop()
        llm_response = await loop.run_in_executor(self.executor, llm_call)
        return llm_response


    async def _run_llm_intent_analysis(self, text, possible_intents):
        """(v3) LLM을 사용하여 텍스트의 의도 분석 (비동기 스레드)"""
        if not self.openai_client:
            logger.error("[LLM] OpenAI 클라이언트가 초기화되지 않았습니다.")
            return {"intent": "unknown", "error": "OpenAI client not initialized"}


        logger.debug(f"[LLM] 의도 분석 작업 시작. (가능한 의도: {possible_intents})")
        intents_str = ", ".join([f'"{i}"' for i in possible_intents])
        prompt = f"""
        사용자 발화: "{text}"
        위 발화의 핵심 의도(intent)를 다음 목록에서 하나만 고르세요:
        [ {intents_str}, "unknown" ]

        추가 정보(entity)가 있다면 함께 추출하세요 (예: document_type).

        JSON 형식으로 {{"intent": "의도", "entity": {{"키": "값"}}}} 으로만 응답하세요.
        (예: 네 맞아요 -> {{"intent": "confirm_identity", "entity": {{}}}})
        (예: 진료 확인서 줘 -> {{"intent": "request_document", "entity": {{"document_type": "진료 확인서"}}}})
        (예: 아니요 -> {{"intent": "deny_identity", "entity": {{}}}})
        """

        def llm_call():
            try:
                response = self.openai_client.chat.completions.create( # self.openai_client 사용
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": "당신은 사용자 발화의 의도를 분석하는 AI입니다."},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.1,
                    response_format={"type": "json_object"}
                )
                content = response.choices[0].message.content
                logger.debug(f"[LLM] 의도 분석 응답: {content}")
                # 📌 LLM 응답 파싱 강화: entity가 없는 경우 빈 dict로 처리
                result = json.loads(content)
                if "entity" not in result:
                     result["entity"] = {}
                return result
            except Exception as e:
                logger.error(f"[LLM] 의도 분석 작업 오류: {e}", exc_info=True)
                return {"intent": "unknown", "entity": {}, "error": str(e)}


        loop = asyncio.get_event_loop()
        llm_response = await loop.run_in_executor(self.executor, llm_call)
        return llm_response


    async def _play_ding_and_speak(self, text, next_state):
        """(v3) '땡' 효과음 재생 후 TTS 비동기 실행 및 상태 전환"""
        # await self._play_ding_sound_async() # [수정됨] '띵' 소리 제거

        # TTS 콜백 (Router로 전송)
        # 📌 next_state를 step 정보로 함께 전송
        await self.send_tts_callback(text, step=next_state)

        # 상태 전환 (TTS 재생 *시작*과 동시에 상태 전환)
        # 📌 상태 전환 로직 수정: BUSY -> next_state (핸들러 실행 전 BUSY로 설정했으므로)
        # 이전: if self.state == self.STATE_BUSY:
        self.state = next_state # BUSY에서 다음 상태로 직접 전환
        logger.info(f"[StateM] 상태 전환 -> {self.state}")


    def _load_ding_sound(self):
        """(v3) '땡' 효과음 파일 로드 (시작 시 1회)"""
        # [수정됨] '띵' 소리 제거
        return None
        # try:
        #     ding_path = settings.KIOSK_SETTINGS.get('AUDIO_DING_PATH', 'static/audio/ding.wav')
        #     # Path 객체가 아닌 문자열 경로로 BASE_DIR와 결합
        #     full_path = str(settings.BASE_DIR / ding_path)

        #     data, samplerate = sf.read(full_path, dtype='float32')
        #     logger.info(f"[Audio] 'ding.wav' 로드 성공 (경로: {full_path})")
        #     return data, samplerate
        # except Exception as e:
        #     logger.error(f"[Audio] 'ding.wav' 로드 실패: {e}", exc_info=True)
        #     return None

    async def _play_ding_sound_async(self):
        """(v3) '땡' 효과음 비동기 재생 (스레드)"""
        # [수정됨] '띵' 소리 제거
        pass
        # if self.ding_sound:
        #     logger.debug("[Audio] 'ding' 효과음 재생 시도")
        #     data, samplerate = self.ding_sound

        #     def play_sound():
        #         try:
        #             sd.play(data, samplerate)
        #             sd.wait()
        #         except Exception as e:
        #             logger.warning(f"[Audio] 'ding' 효과음 재생 오류: {e}")

        #     loop = asyncio.get_event_loop()
        #     await loop.run_in_executor(self.executor, play_sound)

    # --- 5. 유틸리티 및 정리 ---

    async def notify_tts_playback_complete(self):
        """(v3 신규) 클라이언트(JS)로부터 TTS 재생 완료 신호를 받음"""
        logger.debug("[StateM] 클라이언트 TTS 재생 완료 확인.")
        # TTS 완료 후 BUSY 상태였다면, 원래 기다리던 상태로 돌아가야 함.
        # 하지만 현재 로직(_play_ding_and_speak)에서 미리 상태를 전환하므로
        # 이 콜백에서는 BUSY 상태 해제만 확인하는 정도로 충분할 수 있음.
        # 필요하다면 여기서 self.state가 BUSY인지 확인하고 로깅 추가.
        pass


    async def cleanup(self):
        """(v3) WebSocket 연결 종료 시 리소스 정리"""
        logger.info(f"[StateM] 리소스 정리 시작 (사용자: {self.user_context.get('patient_name', 'N/A')})") # 사용자 이름 없을 때 대비
        # 스레드 풀 종료 시 wait=True로 변경하여 진행 중인 작업 완료 대기 (선택 사항)
        self.executor.shutdown(wait=True)
        if self.pending_llm_task and not self.pending_llm_task.done():
             self.pending_llm_task.cancel()
             try:
                 await self.pending_llm_task # 취소 완료 대기
             except asyncio.CancelledError:
                 logger.info("[StateM] 진행 중인 LLM 작업 취소됨.")
        logger.info(f"[StateM] 리소스 정리 완료.")