import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from openai import OpenAI
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from datetime import date, datetime
from .models import Medical_Certificate, Prescription, MedicalReceipt
from .utils import get_gpt_streaming_response, SYSTEM_PROMPT
import os
import re
import subprocess  
import time

logger = logging.getLogger('kiosk')
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

def normalize_date_input(text: str) -> str:
    """
    사용자가 말한 날짜("7월 16일", "16일") → "YYYY-MM-DD" 형태로 변환
    """
    print(f"🔍 날짜 입력: '{text}'") # 디버깅용

    try:
        # "7월 16일" 또는 "7월16일" 형태 추출
        match_month_day = re.search(r"(\d{1,2})월\s*(\d{1,2})일", text)
        if match_month_day:
            month, day = match_month_day.groups()
            year = datetime.now().year
            result = f"{year}-{int(month):02d}-{int(day):02d}"
            print(f"✅ 날짜 변환 성공 (월/일): '{text}' → '{result}'")
            return result

        # "16일" 형태 추출 (월/일 매치보다 뒤에 와야 함)
        match_day_only = re.search(r"(\d{1,2})일", text)
        if match_day_only:
            day = match_day_only.group(1)
            today = datetime.now()
            year = today.year
            month = today.month
            result = f"{year}-{int(month):02d}-{int(day):02d}"
            print(f"✅ 날짜 변환 성공 (일자만): '{text}' → '{result}'")
            return result

        # 이미 YYYY-MM-DD 형태면 그대로 사용
        if re.match(r"\d{4}-\d{2}-\d{2}", text):
            print(f"✅ 날짜 형식 유지: '{text}'")
            return text

    except Exception as e:
        print(f"❌ 날짜 변환 오류: {e}")
        logger.error(f"날짜 변환 오류: {e}")

    print(f"❌ 날짜 변환 실패, 원본 반환 안 함: '{text}'")
    return None # [수정] 실패 시 text가 아닌 None 반환

def find_document_path(patient_id, doc_type, issue_date):
    """
    주어진 문서 종류(doc_type)와 날짜(issue_date)에 맞는 파일 경로를 찾아 반환.
    """
    base_dir = os.path.join(settings.BASE_DIR, "documents")

    if "확인서" in doc_type or "certificate" in doc_type:
        target_dir = os.path.join(base_dir, "Medical_Certificate_DOC")
        keyword = "certificate"
    elif "영수증" in doc_type or "receipt" in doc_type:
        target_dir = os.path.join(base_dir, "Medical_receipt_DOC")
        keyword = "receipt"
    else:
        logger.warning(f"[find_document_path] 알 수 없는 문서 유형: {doc_type}")
        return None

    if not os.path.exists(target_dir):
        logger.error(f"[find_document_path] 폴더 없음: {target_dir}")
        return None

    logger.info(f"[find_document_path] 검색 시작 → 폴더: {target_dir}")
    logger.info(f" - 환자번호: {patient_id}")
    logger.info(f" - 문서유형: {doc_type} (키워드: {keyword})")
    logger.info(f" - 날짜: {issue_date} → 비교용: {issue_date.replace('-', '')}")

    found_path = None
    for file in os.listdir(target_dir):
        if not file.endswith(".pdf"):
            continue

        full_path = os.path.join(target_dir, file)
        match_patient = patient_id and patient_id in file
        match_keyword = keyword in file
        match_date = issue_date.replace("-", "") in file

        logger.info(
            f"  [검사] {file} → "
            f"환자번호: {match_patient}, 키워드: {match_keyword}, 날짜: {match_date}"
        )

        if match_patient and match_keyword and match_date:
            found_path = full_path
            logger.info(f"✅ 일치 파일 발견: {found_path}")
            break

    if not found_path:
        logger.warning(
            f"⚠️ 일치하는 파일 없음 (환자번호={patient_id}, doc_type={doc_type}, date={issue_date})"
        )

    return found_path

def print_document(_scope, patient_id, doc_type, issue_date, print_queue_name=None):
    if not print_queue_name:
        logger.info("[프린터] print_queue_name이 None → 기본 프린터로 설정됨")

    # 이제 'patient_id', 'doc_type', 'issue_date' 변수에 올바른 값이 들어갑니다.
    logger.info(f"[발급요청] {patient_id} / {doc_type} ({issue_date}) (Queue: {print_queue_name or 'Default printer'})")
    found_file = find_document_path(patient_id, doc_type, issue_date)

    if not found_file:
        logger.warning(f"[경고] 파일을 찾을 수 없음: {patient_id}, {doc_type}, {issue_date}")
        return ("not_found", None) # 👈 이 return 문이 필수
    
    # ✅ SumatraPDF 실행 파일 경로 수정 (프로젝트 내부 경로 사용)
    sumatra_path = os.path.join(settings.BASE_DIR, "sumatra", "SumatraPDF-3.5.2-64.exe")

    try:
        if os.path.exists(sumatra_path):
            if print_queue_name:
                os.system(f'"{sumatra_path}" -print-to "{print_queue_name}" "{found_file}"')
            else:
                subprocess.run([sumatra_path, "-print-to-default", found_file], check=True)
            logger.info(f"프린트 명령 전송 완료: {found_file} → {print_queue_name or '기본 프린터'}")
            return ("success", found_file)
        else:
            logger.error(f"SumatraPDF 실행 파일 없음: {sumatra_path}")
            return ("print_failed", None)
    except Exception as e: # SumatraPDF 실행 관련 예외 처리
        logger.error(f"SumatraPDF 프린트 실패: {e}")
        return ("print_failed", str(e))


class KioskWebSocketConsumer(AsyncWebsocketConsumer):
    # --- (KioskWebSocketConsumer 코드는 이전과 동일하게 유지) ---
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_state = {}

    async def send_tts(self, text):
        """
        프론트엔드로 TTS 텍스트를 전송하는 헬퍼 메서드
        """
        await self.send_json({
            "type": "tts.text",
            "text": text
        })


    async def connect(self):
        await self.accept()
        self.client_state = {'step': 'idle'}
        logger.info("WebSocket client connected")
        await self.start_automatic_guidance()

    async def disconnect(self, close_code):
        logger.info(f"WebSocket client disconnected: {close_code}")

    async def start_automatic_guidance(self):
        try:
            self.client_state['step'] = 'prompting'
            await self.send_message('mic.off')
            guidance_text = "주민번호 앞 여섯자리를 입력하여 서류 출력 서비스로 이동해주세요."
            await self.send_message('tts.text', {'text': guidance_text})

            self.client_state['step'] = 'listening'
            await self.send_message('status', {'state': 'listening'})
            logger.info("Automatic guidance sent. State -> listening")
        except Exception as e:
            logger.error(f"Automatic guidance error: {str(e)}")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type')

            if message_type == 'user.confirmation_start':
    
                patient_data = data.get('patient')
                if patient_data:
                    await self.state_manager.start_user_confirmation(patient_data)

                    # ✅ 본인확인 TTS 재생 후에만 complete 전송
                    await self.send_message({"type": "tts.complete"})


            elif message_type == 'stt.result':
                await self.handle_stt_result(data.get('text', ''))
            elif message_type == 'document.print': # Kiosk Consumer도 print 요청 처리 가능하도록 추가
                await self.handle_print_request(data)

            elif message_type in ["tts.complete", "audio.tts_playback_complete", "ttscomplete"]:
                logger.info("[Consumer] 클라이언트 TTS 재생 완료 신호 수신 → Router로 전달")
                await self.router.handle_message(json.dumps({"type": "tts.complete"}))


            else:
                logger.warning(f"Unknown message type: {message_type}")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

    async def start_user_confirmation(self, patient):
        if patient:
            self.client_state['patient_to_confirm'] = patient
            self.client_state['step'] = 'confirming_user'
            confirmation_text = f"{patient.get('patient_name')} 님이 맞으시면, '본인 확인'이라고 말씀해주세요."
            await self.send_message('tts.text', {'text': confirmation_text})
            await self.send_message('state.update', {'step': 'confirming_user'})
            logger.info(f"User confirmation started: {confirmation_text}")
        else:
            await self.send_message('error', {'message': "확인할 환자 정보가 없습니다."})

    async def handle_stt_result(self, text):
        text = text.strip().lower()
        current_step = self.client_state.get('step', 'idle')
        logger.info(f"STT Result: '{text}', State: '{current_step}'")

        if current_step == 'confirming_user':
            if any(word in text for word in ['본인', '확인', '맞', '네', '예']):
                patient = self.client_state.get('patient_to_confirm')
                await self.send_message('user.confirmed', {'patient': patient})
            elif any(word in text for word in ['아니', '틀렸', '다른']):
                await self.send_message('user.confirmation_failed')
                await self.send_message('tts.text', {'text': '다른 이름을 말씀해주세요.'})
            else:
                await self.send_message('tts.text', {'text': "'본인 확인' 또는 '아니요'로 말씀해주세요."})
                return

            self.client_state['step'] = 'listening'
            await self.send_message('state.update', {'step': 'listening'})
            return

        if current_step == 'listening' and len(text) > 0:
            await self.send_message('stt.forward_to_input', {'text': text})
            return

        logger.warning(f"STT result received in unhandled state: {current_step}")

    async def send_message(self, msg_type, data=None):
        message = {'type': msg_type}
        if data:
            message.update(data)
        await self.send(text_data=json.dumps(message))

    async def handle_print_request(self, data):
        """프린트 요청 처리 (Kiosk Consumer 버전 - 독립 실행되도록 수정)"""
        try:
            # [수정] Kiosk Consumer는 self.selected_patient가 없으므로 data에서 직접 가져옵니다.
            patient_id = data.get('patient_id') 
            doc_type = data.get('doc_type')
            issue_date_text = data.get('issue_date') # "7월 16일" (원본)
            consumer_name = self.__class__.__name__
            
            # [수정] 날짜 변환
            normalized_date = normalize_date_input(issue_date_text)
            
            logger.info(f"🖨️ 출력 요청 ({consumer_name}): {patient_id}, {doc_type}, {normalized_date} (원본: {issue_date_text})")

            if not all([patient_id, doc_type, normalized_date]):
                # ‼️ [수정] 직접 send_message 대신 헬퍼 함수 사용
                # await self.send_message('tts.text', {'text': '출력 정보를 확인하거나 날짜를 인식할 수 없습니다.'})
                await self.send_tts_and_reactivate_mic('출력 정보를 확인하거나 날짜를 인식할 수 없습니다.', 'listening')
                return

            # [수정] 헬퍼 함수 대신 직접 send_message 사용
            await self.send_message('tts.text', {'text': f'{issue_date_text}의 {doc_type} 인쇄를 시작합니다. 잠시만 기다려주세요.'})

            # [수정] 변환된 normalized_date를 인쇄 함수로 전달
            status, detail = await database_sync_to_async(print_document)(
                self.scope,                 # ✅ 첫 번째 인자는 항상 self.scope
                patient_id,                 # 두 번째: patient_id
                doc_type,                   # 세 번째: 문서 종류
                normalized_date,            # 네 번째: 날짜
                self.client_state.get('printer_name')  # (선택) 프린터 이름
            )
            final_tts_message = ""

            if status == "success":
                # ----------------------------------------------------
                # ‼️ [추가] Kiosk Consumer에도 10초 대기 추가
                # ----------------------------------------------------
                await asyncio.sleep(10.0)
                # ----------------------------------------------------
                
                # [수정] issue_date_text 사용 (기존 코드 유지)
                await self.send_message('tts.text', {'text': f'{issue_date_text}의 {doc_type} 출력이 완료되었습니다.'})
                await asyncio.sleep(0.5) # Pause between messages
                await self.send_tts_without_mic_reactivation('더 필요하신 서류 있으세요?')
                await asyncio.sleep(0.5)
                # Final prompt, then reactivate mic and set state
                await self.send_tts_and_reactivate_mic(
                    "필요하신 서류를 말씀해주세요. 없으시면 종료 라고 말씀해주세요.",
                    'await_additional_request' # New state
                )

            elif status == "not_found":
                final_tts_message = '서류 파일을 찾을 수 없습니다. 날짜를 다시 확인해주세요.'
                logger.error(f"파일 찾기 실패 ({consumer_name}): {detail}")
            elif status == "print_failed":
                final_tts_message = '프린터 오류가 발생했습니다. 관리자에게 문의해주세요.'
                logger.error(f"프린터 실패 세부 정보 ({consumer_name}): {detail}")

            if final_tts_message:
                await self.send_message('tts.text', {'text': final_tts_message})

        except Exception as e:
            consumer_name = self.__class__.__name__
            logger.error(f"출력 중 오류 ({consumer_name}): {e}")
            await self.send_message('tts.text', {'text': '출력 처리 중 오류가 발생했습니다.'})

class ServiceWebSocketConsumer(AsyncWebsocketConsumer):
    """GPT 기반 스마트 서류 인식 Consumer (인쇄 흐름 개선됨)"""

    # ... (__init__, _fmt, connect, disconnect - 이전과 동일) ...
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_patient = None
        self.client_state = {}
        # ✅ 최근 TTS 내용과 완료 시간 추적 (에코 방지용)
        self.recent_tts_content = ""
        self.tts_completed_time = 0.0
        self.recognition_failure_count = 0 # ✅ 인식 실패 횟수 추가

        self.doc_type_model_map = {
            "진료확인서": Medical_Certificate,
            "처방전": Prescription,
            "진료영수증": MedicalReceipt,
        }
        self.FIELD_MAP = {
            "진료확인서": [("환자명", "patient_name"), ("환자번호", "patient_id"), ("성별", "gender"), ("생년월일", "birth_date"), ("연락처", "contact"), ("주소", "address")],
            "처방전": [("환자명", "patient_name"), ("환자번호", "patient_id"), ("성별", "gender"), ("생년월일", "birth_date"), ("연락처", "contact"), ("처방일", "prescription_date"), ("담당의", "doctor_name"), ("진료과", "department"), ("병원명", "hospital_name")],
            "진료영수증": [("환자명", "patient_name"), ("환자번호", "patient_id"), ("성별", "gender"), ("생년월일", "birth_date"), ("영수일", "receipt_date")],
        }

    def _fmt(self, v):
        if isinstance(v, datetime): return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, date): return v.strftime("%Y-%m-%d")
        return v

    async def connect(self):
        await self.accept()
        self.client_state = {'step': 'listening'}
        logger.info("🚀 Service WebSocket connected")
        self.selected_patient = self.scope['session'].get('selected_patient', None)
        if self.selected_patient:
            logger.info(f"👤 인증된 사용자: {self.selected_patient.get('patient_name')}")
            await self.send_message('patient.info', {'patient': self.selected_patient})
        await self.send_message("voice.mode", {"allow_short_input": False}) # ✅ 초기 짧은 입력 비허용
        await asyncio.sleep(1)
        await self.start_voice_guidance()

    async def disconnect(self, close_code):
        logger.info(f"Service WebSocket disconnected: {close_code}")


    async def receive(self, text_data):
        """메시지 수신 및 라우팅"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')

            # ✅ TTS 완료 신호 처리
            if message_type == 'tts.complete':
                self.logger.info("🔊 TTS 완료 신호 수신 — 마이크 재활성화 처리")
                self.tts_completed_time = asyncio.get_event_loop().time()

                now = asyncio.get_event_loop().time()
                if (now - getattr(self, "last_mic_on_ts", 0)) >= getattr(self, "mic_on_min_interval", 0.5):
                    await asyncio.sleep(0.2)
                    await self.send_message("mic.on")
                    self.last_mic_on_ts = asyncio.get_event_loop().time()
                else:
                    self.logger.info("mic.on 디바운스에 의해 생략")
                return

            if message_type == 'voice.input' or message_type == 'stt.result': # stt.result도 처리
                await self.process_voice_input(data.get('text', ''))
            elif message_type == 'service.select':
                # start_consultation 함수가 정의되어 있지 않으므로 주석 처리 또는 구현 필요
                # if data.get('service') == '상담':
                #     await self.start_consultation()
                # else:
                await self.process_service_selection(data.get('service', ''))
            elif message_type == 'document.print':
                await self.handle_print_request(data)
            elif message_type == 'recognition.failed': # ✅ 인식 실패 처리 추가
                await self.handle_recognition_failure(data.get("error", "unknown"))
            else:
                 logger.warning(f"알 수 없는 메시지 타입: {message_type}")


        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

    # ✅ [수정됨] handle_print_request: 인쇄 흐름 및 TTS 개선
    async def handle_print_request(self, data):
        """프린트 요청 처리 (흐름 및 TTS 수정)"""
        # Ensure mic is off during printing
        await self.send_message('mic.off')
        self.client_state['step'] = 'printing' # Indicate printing is in progress

        try:
            patient_id = data.get('patient_id')
            doc_type = data.get('doc_type')
            issue_date_text = data.get('issue_date') # "7월 16일" (원본)
            consumer_name = self.__class__.__name__

            # [수정] 날짜 변환
            normalized_date = normalize_date_input(issue_date_text)
            
            logger.info(f"🖨️ 출력 요청 ({consumer_name}): {patient_id}, {doc_type}, {normalized_date} (원본: {issue_date_text})")

            if not all([patient_id, doc_type, normalized_date]):
                await self.send_message('tts.text', {'text': '출력 정보를 확인하거나 날짜를 인식할 수 없습니다.'})
                return

            # [수정] 변환된 normalized_date로 인쇄
            status, detail = await database_sync_to_async(print_document)(
                self.scope,
                patient_id,
                doc_type,
                normalized_date,
                self.client_state.get('printer_name')
            )

            final_tts_message = ""

            if status == "success":
                await self.send_message('tts.text', {'text': f'{issue_date_text}의 {doc_type}을 출력합니다.'}) # TTS는 원본 텍스트
                # Send completion TTS sequence and ask for next action
                await self.send_tts_without_mic_reactivation('인쇄가 완료되었습니다.')
                await asyncio.sleep(0.5) # Pause between messages
                await self.send_tts_without_mic_reactivation('더 필요하신 서류 있으세요?')
                await asyncio.sleep(0.5)
                # Final prompt, then reactivate mic and set state
                await self.send_tts_and_reactivate_mic(
                    "필요하신 서류를 말씀해주세요. 없으시면 종료 라고 말씀해주세요.",
                    'await_additional_request' # New state
                )

            elif status == "not_found":
                logger.error(f"파일 찾기 실패 ({consumer_name}): {detail}")
                await self.send_tts_and_reactivate_mic(
                    '서류 파일을 찾을 수 없습니다. 날짜를 다시 확인해주세요.',
                    'listening' # Go back to listening after error
                )

            elif status == "print_failed":
                logger.error(f"프린터 실패 세부 정보 ({consumer_name}): {detail}")
                await self.send_tts_and_reactivate_mic(
                    '프린터 오류가 발생했습니다. 관리자에게 문의해주세요.',
                    'listening' # Go back to listening after error
                )

        except Exception as e:
            consumer_name = self.__class__.__name__
            logger.error(f"출력 처리 중 예외 발생 ({consumer_name}): {e}")
            await self.send_tts_and_reactivate_mic(
                '출력 처리 중 오류가 발생했습니다. 다시 말씀해주세요.',
                'listening' # Go back to listening after exception
            )

    async def start_voice_guidance(self):
        guidance_text = "진료확인서, 처방전, 진료영수증 중 원하는 서류를 말씀해주세요."
        # [수정] 새 헬퍼 함수 사용
        await self.send_tts_and_reactivate_mic(guidance_text, 'listening')


    # --- (analyze_input_with_context, fallback_analysis, handle_analysis_result, handle_issue_confirmation - 이전과 동일) ---
    CONTENT_QUERY_KWS = ["포함", "들어가", "기재", "내용", "적혀", "들어있", "표시되", "성명", "주민번호", "주소", "비용", "기간", "유효", "차이", "언제", "왜", "방법"]
    ISSUE_KWS = ["발급", "출력", "진행", "해줘", "해주세요", "바로해", "진행해", "출력해", "뽑아", "인쇄", "프린트", "8급", "팔급"] # 발급 유사어 추가

    async def analyze_input_with_context(self, text: str) -> dict:
        self.logger.info(f"컨텍스트 기반 GPT 분석 시작: '{text}'")
        content_kws = " | ".join(self.CONTENT_QUERY_KWS)
        issue_kws = " | ".join(self.ISSUE_KWS)
        try:
            prompt = f"""
당신은 병원 키오스크의 분류기입니다. 사용자의 한 문장을 보고 다음 중 하나로 판정하세요:
- "issue": 지금 당장 특정 서류를 발급/출력하려는 의도 ({issue_kws} 포함 시 유력)
- "consult": 서류의 '내용·차이·조건·방법·용도' 등을 묻는 상담 의도 ({content_kws} 포함 시 유력, 물음표 없어도 가능)
- "other": 위 두 가지가 아닌 기타
반드시 JSON만 출력하세요.
입력: "{text}"
[도움 규칙]
- 단일 명사(예: "처방전", "진료확인서") → issue
- '발급해줘?', '출력해?'처럼 물음표가 있어도 명시적 발급 요구면 issue.
- 제출처/용도 추론: 회사/병가/휴가/학교/결석 → 진료확인서, 보험/환급/공제 → 진료영수증, 약국/처방/복용 → 처방전
반환 형식(JSON):
{{
  "mode": "issue" | "consult" | "other",
  "document_type": "진료확인서" | "처방전" | "진료영수증" | null,
  "submit_to": "회사" | "학교" | "보험사" | "약국" | "기타" | null,
  "reason": "간략한 판정 근거",
  "confidence": 0.0
}}
"""
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model=getattr(settings, "LLM_MODEL", "gpt-4o-mini"),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.1,
            )
            result_text = response.choices[0].message.content.strip()
            if result_text.startswith("```"):
                result_text = result_text.replace("```json", "").replace("```", "").strip()
            result = json.loads(result_text)
            explicit_map = {"회사": ["회사", "직장", "병가", "휴가"], "학교": ["학교", "대학", "결석", "수업"], "보험사": ["보험", "환급", "공제", "청구", "보상"], "약국": ["약국", "약", "처방", "복용"]}
            explicit_submit = None
            low = (text or "").lower()
            for k, kws in explicit_map.items():
                if any(kw in low for kw in kws): explicit_submit = k; break
            result["submit_to"] = explicit_submit if explicit_submit else (None if result.get("submit_to") in ("null", "기타") else result.get("submit_to"))
            if result.get("document_type") not in ("진료확인서", "처방전", "진료영수증"): result["document_type"] = None
            if result.get("mode") not in ("issue", "consult", "other"): result["mode"] = "other"
            return result
        except Exception as e:
            self.logger.error(f"GPT 분석 오류: {e}")
            return self.fallback_analysis(text)

    def fallback_analysis(self, text: str) -> dict:
        t = (text or "").lower().strip()
        self.logger.info(f"백업 분석 사용: '{t}'")
        if t in ("진료확인서", "확인서", "증명서"): return {"mode": "issue", "document_type": "진료확인서", "submit_to": None, "confidence": 0.7, "reason": "단일 명사"}
        if t in ("처방전", "처방서"): return {"mode": "issue", "document_type": "처방전", "submit_to": "약국", "confidence": 0.7, "reason": "단일 명사"}
        if t in ("진료영수증", "영수증"): return {"mode": "issue", "document_type": "진료영수증", "submit_to": "보험사", "confidence": 0.7, "reason": "단일 명사"}
        if any(k in t for k in self.CONTENT_QUERY_KWS): return {"mode": "consult", "document_type": None, "submit_to": None, "confidence": 0.6, "reason": "내용 문의 키워드"}
        if any(k in t for k in self.ISSUE_KWS):
            doc = "진료확인서"
            if "처방" in t or "약" in t: doc = "처방전"
            elif "보험" in t or "공제" in t or "환급" in t: doc = "진료영수증"
            return {"mode": "issue", "document_type": doc, "submit_to": None, "confidence": 0.6, "reason": "발급 키워드"}
        return {"mode": "other", "document_type": None, "submit_to": None, "confidence": 0.3, "reason": "기타"}

    async def handle_analysis_result(self, analysis: dict, original_text: str):
        mode = analysis.get("mode", "other")

        doc_type = analysis.get("document_type")
        submit_to_analyzed = analysis.get("submit_to")
        explicit_tokens = ["회사", "학교", "보험", "약국", "직장", "병가", "휴가", "결석", "공제", "청구", "보상"]
        explicit_present = any(tok in (original_text or "") for tok in explicit_tokens)
        submit_to = submit_to_analyzed if (explicit_present and submit_to_analyzed not in (None, "null", "기타")) else None
        
        if mode == "issue":
            if doc_type not in ("진료확인서", "처방전", "진료영수증"):
                await self.send_tts_and_reactivate_mic("어떤 서류를 발급하시겠습니까? 진료확인서, 처방전, 진료영수증 중에서 말씀해주세요.", "listening")
                return
            self.logger.info(f"서류 직접 요청으로 처리: {doc_type} (제출처: {submit_to})")
            await self.send_message("document.recognized", {"document_type": doc_type})
            await self.query_database(doc_type, submit_to=submit_to)
            return
        if mode == "consult":
            self.logger.info(f"상담 모드로 처리 (제출처: {submit_to}, 문서: {doc_type})")
            await self.start_smart_consultation(original_text, doc_hint=doc_type)
            return
        self.logger.info(f"기타 질문 → 일반 GPT 상담으로 처리: {original_text}")
        await self.start_general_gpt_consultation(original_text)

    async def handle_issue_confirmation(self, text: str):
        self.logger.info(f"발급 의사 확인 응답(규칙 매칭): '{text}'")
        t = (text or "").replace(" ", "")
        issue_synonyms = ["발급", "출력", "진행", "해줘", "해주세요", "바로해", "진행해", "출력해", "8급", "팔급", "예", "네"]
        cancel_synonyms = ["취소", "그만", "안해", "안해요", "안할래", "아니", "아니요", "중단"]
        if any(s in t for s in issue_synonyms):
            self.logger.info("발급 의사 확정 → 날짜 선택")
            self.client_state["step"] = "date_selection"
            await self.send_message("voice.mode", {"allow_short_input": True})
            # [수정] 새 헬퍼 함수 사용
            await self.send_tts_and_reactivate_mic("원하는 날짜를 말씀하시거나 '취소'라고 말씀해주세요.", "date_selection")
            return
        if any(s in t for s in cancel_synonyms):
            self.logger.info("발급 취소")
            self.client_state["step"] = "listening"
            await self.send_message("voice.mode", {"allow_short_input": False})
            # [수정] 새 헬퍼 함수 사용
            await self.send_tts_and_reactivate_mic("다른 서류가 필요하시면 말씀해주세요.", "listening")
            return
        # [수정] 새 헬퍼 함수 사용
        await self.send_tts_and_reactivate_mic("발급 또는 취소라고 정확히 말씀해주세요.", "waiting_for_issue_confirmation")

    # ✅ [수정됨] process_voice_input: printing 상태, await_additional_request 상태 추가
    async def process_voice_input(self, text):
        """음성 입력 처리 (await_additional_request 상태 추가)"""
        current_step = self.client_state.get("step")
        now = asyncio.get_event_loop().time()

        # TTS 에코 방지
        if self.recent_tts_content and (now - self.tts_completed_time < 2.0):
            from difflib import SequenceMatcher
            similarity_threshold = 0.85
            # 유사도 비교 시 공백 제거 추가
            if SequenceMatcher(None, self.recent_tts_content.replace(" ", ""), text.replace(" ", "")).ratio() > similarity_threshold:
                 self.logger.info(f"🎤 자기 TTS 에코로 판단하여 무시 (유사도 > {similarity_threshold}): '{text}'")
                 return

        self.logger.info(f"🎤 음성 입력: '{text}' (상태: {current_step})")
        self.recognition_failure_count = 0 # 성공 시 초기화

        try:
            # 0. Printing in progress - ignore input
            if current_step == 'printing':
                self.logger.info(f"🖨️ 인쇄 중 입력 무시: '{text}'")
                return

            # 1. Waiting for additional request ("종료" or new document)
            if current_step == 'await_additional_request':
                cleaned_text = text.replace(" ", "").lower()
                if "종료" in cleaned_text:
                    await self.send_tts_without_mic_reactivation("키오스크 이용을 종료합니다. 감사합니다.") # 종료 메시지 후 마이크 켜지 않음
                    self.client_state['step'] = 'completed' # 완료 상태
                    await self.close(code=1000) # 연결 종료
                else:
                    # Assume it's a new document request, go back to analysis
                    self.logger.info(f"추가 서류 요청으로 처리: '{text}'")
                    self.client_state['step'] = 'listening' # Reset state before analysis
                    await self.send_message("voice.mode", {"allow_short_input": False}) # 일반 입력 모드
                    analysis = await self.analyze_input_with_context(text)
                    self.logger.info(f"🤖 추가 요청 분석 결과: {analysis}")
                    await self.handle_analysis_result(analysis, text)
                return # Handled this state

            # 2. 발급 의사 확인 단계 ("발급", "취소" 등)
            if current_step == "waiting_for_issue_confirmation":
                await self.handle_issue_confirmation(text)
                return

            # 3. 날짜 선택 단계 ("7월 16일", "16일" 등)
            if current_step == 'date_selection':
                logger.info(f"📅 날짜 선택 입력 감지: '{text}' → 직접 프린터 처리 요청")
                patient_id = self.selected_patient.get("patient_id")
                doc_type = self.client_state.get("selected_doc_type")

                # [수정] 날짜 변환 실패 시
                if not normalize_date_input(text): # 날짜 변환 실패 시 None 반환
                    # [수정] 새 헬퍼 함수 사용
                    await self.send_tts_and_reactivate_mic("날짜를 인식하지 못했습니다. 다시 말씀해주세요.", "date_selection")
                    return

                await self.send_message('document.print', {
                    'patient_id': patient_id, 'doc_type': doc_type, 'issue_date': text
                })
                self.client_state['step'] = 'printing'
                return

            # 4. 그 외 (초기 listening, 상담 advising 등) -> GPT 분석 수행
            if current_step in ['listening', 'advising']:
                analysis = await self.analyze_input_with_context(text)
                self.logger.info(f"🤖 분석 결과: {analysis}")
                await self.handle_analysis_result(analysis, text)
            else:
                logger.warning(f"처리되지 않은 상태({current_step})에서 음성 입력 수신: '{text}'")
                # [수정] 새 헬퍼 함수 사용
                await self.send_tts_and_reactivate_mic("현재 요청을 처리할 수 없습니다. 잠시 후 다시 시도해주세요.", "listening")

        except Exception as e:
            self.logger.error(f"음성 입력 처리 오류: {str(e)}")
            # [수정] 새 헬퍼 함수 사용
            await self.send_tts_and_reactivate_mic("오류가 발생했습니다. 다시 말씀해주세요.", "listening")

    async def start_smart_consultation(self, user_input, doc_hint=None):
        """스마트 상담 시작"""
        self.logger.info(f"🧠 스마트 상담 시작: '{user_input}' (힌트: {doc_hint})")
        self.client_state['step'] = 'advising'
        await self.send_message('status', {'state': 'advising'})
        # 임시로 바로 일반 상담 호출
        await self.handle_general_consultation(user_input, doc_hint)


    async def handle_general_consultation(self, user_input, doc_hint=None):
        """일반 스트리밍 GPT 상담"""
        self.logger.info(f"💬 일반 GPT 상담 시작: '{user_input}'")
        await self.send_message('mic.off')
        try:
            cta = f"발급을 원하시면 '{doc_hint}'라고 말씀해주세요." if doc_hint else "발급을 원하시면 서류명을 말씀해주세요."
            injected_user = (f"{user_input}\n\n[응답지침]\n- 한국어 2~4문장 간결하게.\n"
                             f"- 본문에서 발급/조회 진행 문구 금지.\n- 마지막 줄에는 반드시 다음 문장 그대로 붙이세요: \"{cta}\"\n")

            chunks = []
            full_response_text = ""
            async for delta, is_done in get_gpt_streaming_response(injected_user, system_prompt=SYSTEM_PROMPT):
                if not is_done:
                    chunks.append(delta)
                    full_response_text += delta
                    await self.send_message("gpt.stream", {"delta": delta})
                else:
                    self.recent_tts_content = full_response_text.strip()
                    await self.send_message("gpt.stream", {"event": "done", "full_text": self.recent_tts_content})
                    # 상담 완료 후 마이크 켜기 (헬퍼 함수 사용)
                    await self.send_tts_and_reactivate_mic("", "advising", delay=0.1) # 빈 텍스트 보내서 마이크만 켜기

        except Exception as e:
            self.logger.error(f"상담 오류: {e}")
            cta = f"발급을 원하시면 '{doc_hint}'라고 말씀해주세요." if doc_hint else "발급을 원하시면 서류명을 말씀해주세요."
            await self.send_tts_and_reactivate_mic("죄송합니다, 답변 중 오류가 발생했습니다. 다시 질문해주세요. " + cta, "advising")


    async def process_service_selection(self, service_name):
        """서비스 이름(카드 클릭 등)으로 직접 조회"""
        logger.info(f"🖱️ 서비스 선택: {service_name}")
        if service_name in ['진료확인서', '처방전', '진료영수증']:
            await self.send_message('document.recognized', {'document_type': service_name})
            await self.query_database(service_name)
        else:
            await self.send_tts_and_reactivate_mic(f"{service_name}는 현재 지원되지 않습니다.", 'listening')


    async def query_database(self, doc_type, submit_to=None): # submit_to 인자 추가
        """DB 조회 후 TTS 안내 (발급 확인 단계로 이동)"""
        try:
            logger.info(f"🗃️ DB 조회: {doc_type} (제출처: {submit_to})")
            model_class = self.doc_type_model_map.get(doc_type)
            if not model_class:
                await self.send_tts_and_reactivate_mic(f'{doc_type}는 준비 중입니다.', 'listening')
                return

            patient_filter = {}
            if self.selected_patient:
                patient_filter['patient_id'] = self.selected_patient.get('patient_id')
            else:
                 await self.send_tts_and_reactivate_mic("환자 정보가 확인되지 않아 조회할 수 없습니다.", 'listening')
                 return

            field_map = self.FIELD_MAP.get(doc_type, [])
            results = await self.fetch_all_generic(model_class, field_map, filters=patient_filter)
            logger.info(f"📊 조회 결과: {len(results)}건")

            await self.send_message('db.results', {'results': results})

            if results:
                prefix = f"{submit_to} 제출용 " if submit_to else ""
                confirmation_text = f"{prefix}{doc_type} {len(results)}건을 찾았습니다. 발급을 원하시면 '발급' 취소하시려면 '취소'라고 말씀해주세요."
                await self.send_tts_and_reactivate_mic(confirmation_text, "waiting_for_issue_confirmation")
                await self.send_message("voice.mode", {"allow_short_input": True}) # 상태 변경 후 모드 설정
            else:
                await self.send_tts_and_reactivate_mic(f"조회된 {doc_type}이 없습니다. 다른 서류를 원하시면 말씀해주세요.", "listening")
                await self.send_message("voice.mode", {"allow_short_input": False})

        except Exception as e:
            logger.exception("DB query error")
            await self.send_error("DB 조회 중 오류가 발생했습니다.")
            await self.send_tts_and_reactivate_mic("데이터베이스 오류가 발생했습니다. 다시 시도해주세요.", "listening")


    @database_sync_to_async
    def fetch_all_generic(self, model_class, field_map, filters=None):
        qs = model_class.objects.all()
        if filters: qs = qs.filter(**filters)
        order_fields = []
        candidates = ["prescription_date", "receipt_date", "id"]
        model_fields = {f.name for f in model_class._meta.get_fields()}
        for c in candidates:
            if c in model_fields: order_fields.append(f"-{c}"); break
        if order_fields: qs = qs.order_by(*order_fields)
        rows = [{out_key: self._fmt(getattr(obj, attr, None)) for out_key, attr in field_map} for obj in qs[:100]]
        return rows

    async def send_message(self, msg_type, data=None):
        message = {'type': msg_type, **(data or {})}
        await self.send(text_data=json.dumps(message))

    async def send_error(self, error_message):
        await self.send_message('error', {'message': error_message})

    # ✅ [헬퍼] TTS만 전송 (마이크 자동 활성화 X)
    async def send_tts_without_mic_reactivation(self, text):
        if not text: return
        # await self.send_message("mic.off") # 호출 전에 이미 off 상태여야 함
        self.recent_tts_content = text
        self.tts_completed_time = 0.0
        await self.send_message("tts.text", {"text": text})

    # ✅ [헬퍼] 최종 TTS 전송 + 상태 변경 + 마이크 활성화
    async def send_tts_and_reactivate_mic(self, text, next_state, delay=2.0): # 기본 딜레이 증가
        if text: # 빈 텍스트가 아닐 경우에만 TTS 전송
             await self.send_message("mic.off") # 확실하게 끄기
             self.recent_tts_content = text
             self.tts_completed_time = 0.0
             await self.send_message("tts.text", {"text": text})
        else: # 빈 텍스트면 마이크만 켜기 위함
             self.recent_tts_content = "" # 에코 방지 초기화

        # 상태 변경
        self.client_state['step'] = next_state
        if next_state == 'listening':
            await self.send_message("voice.mode", {"allow_short_input": False})
        elif next_state in ['await_additional_request', 'date_selection', 'waiting_for_issue_confirmation']:
             await self.send_message("voice.mode", {"allow_short_input": True})

        # 딜레이 후 마이크 켜기
        async def delayed_mic_on_final(wait_time):
            await asyncio.sleep(wait_time)
            # 현재 상태가 여전히 마이크를 켜야 하는 상태인지 확인
            if self.client_state.get('step') == next_state:
                self.logger.info(f"🎤 최종 안내 후 {wait_time}초 뒤 마이크 활성화 (상태: {next_state})")
                await self.send_message("mic.on")
            else:
                 self.logger.info(f"🎤 마이크 활성화 취소 (상태 변경됨: {self.client_state.get('step')})")

        asyncio.create_task(delayed_mic_on_final(delay))

    # ✅ 기존 send_tts_with_tracking 은 삭제하고 헬퍼 함수 사용으로 통일
    # async def send_tts_with_tracking(self, text): ...


    async def handle_recognition_failure(self, error: str):
        """음성 인식 실패 처리 (3회 이상 시 초기화)"""
        self.logger.warning(f"🎤 음성 인식 실패: {error}")
        self.recognition_failure_count += 1
        if self.recognition_failure_count >= 3:
            await self.send_tts_without_mic_reactivation("음성 인식에 3회 이상 실패하여 초기 화면으로 돌아갑니다.") # 마이크 켜지 않음
            await self.send_message("system.redirect", {"url": "/kiosk/main/"})
            await self.close(code=1000)
        else:
            # 실패 시 다시 안내하고 마이크 켜기 (현재 상태 유지)
            await self.send_tts_and_reactivate_mic("다시 한번 말씀해주세요.", self.client_state.get('step', 'listening'))