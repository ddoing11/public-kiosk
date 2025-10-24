import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from openai import OpenAI
from channels.db import database_sync_to_async
from datetime import date, datetime
from .models import Medical_Certificate, Prescription, MedicalReceipt
from .utils import get_gpt_streaming_response, SYSTEM_PROMPT
import os
import re
import subprocess  # ✅ subprocess 임포트
# import tempfile    # SumatraPDF는 필요 없음
# import shutil      # SumatraPDF는 필요 없음

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

    print(f"❌ 날짜 변환 실패, 원본 반환: '{text}'")
    return text


def print_document(patient_id: str, doc_type: str, issue_date: str) -> (str, str):
    """
    실제 PDF 파일을 찾아 인쇄 요청 (SumatraPDF 사용)
    """
    try:
        logger.info(f"🖨️ [발급요청] {doc_type} ({issue_date}) using SumatraPDF") # 로그 변경

        issue_date = normalize_date_input(issue_date)

        issue_date_str = issue_date.replace("-", "")
        if not issue_date.strip() or not issue_date_str.strip():
            logger.error(f"❌ 날짜 변환 최종 실패: {issue_date}")
            return ("not_found", f"날짜 인식 실패: {issue_date}")

        # --- (폴더 매핑, 파일 찾기 로직) ---
        folder_map = {
            "진료확인서": "Medical_Certificate_DOC", "처방전": "Prescription_DOC", "진료영수증": "Medical_receipt_DOC",
        }
        folder_name = folder_map.get(doc_type)
        if not folder_name: return ("not_found", f"지원되지 않는 문서 유형: {doc_type}")
        if "Certificate" in folder_name: prefix = "certificate"
        elif "receipt" in folder_name: prefix = "receipt"
        elif "Prescription" in folder_name: prefix = "prescription"
        else: prefix = "document"
        base_dir = os.path.join(settings.BASE_DIR, "documents", folder_name)
        patient_formats = [ patient_id, patient_id.replace('-', '') ]
        found_file = None
        for patient_fmt in patient_formats:
            file_path = os.path.join(base_dir, f"{patient_fmt}_{prefix}_{issue_date_str}.pdf")
            logger.info(f"📂 시도: {file_path}")
            if os.path.exists(file_path):
                found_file = file_path
                logger.info(f"✅ 파일 발견: {file_path}")
                break
        if not found_file:
            logger.error(f"❌ 모든 패턴에서 파일 찾기 실패")
            return ("not_found", "모든 패턴에서 파일 찾기 실패")
        # --- (여기까지 파일 찾기) ---

        printer_name = "L81H"
        # ✅ SumatraPDF 실행 파일 경로 (정확한 파일 이름 사용)
        sumatra_path = os.path.join(settings.BASE_DIR, "sumatra", "SumatraPDF-3.5.2-64.exe") # ✅ 파일 이름 수정됨

        if not os.path.exists(sumatra_path):
             logger.error(f"❌ SumatraPDF 실행 파일을 찾을 수 없습니다. (경로: {sumatra_path})")
             return ("print_failed", f"SumatraPDF 실행 파일({sumatra_path})을 찾을 수 없습니다.") # 로그 상세화

        # ✅ 명령어: SumatraPDF.exe -print-to "프린터이름" "파일경로"
        command = [
            sumatra_path, # 정확한 경로 사용
            "-print-to",
            printer_name,
            found_file
        ]

        logger.info(f"✅ SumatraPDF CLI 명령 실행 (Run): {command}")
        # run을 사용하고 timeout 설정
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, encoding='cp949', errors='ignore')

        # SumatraPDF 오류 확인
        if result.returncode != 0:
            error_detail = f"SumatraPDF 인쇄 오류 (코드: {result.returncode}): {result.stderr or result.stdout or 'No output'}"
            logger.error(f"❌ {error_detail}")
            return ("print_failed", error_detail)

        logger.info(f"✅ 프린트 명령 전송 완료 (SumatraPDF 종료 확인): {found_file} -> {printer_name}")
        return ("success", found_file)

    except subprocess.TimeoutExpired:
        logger.error(f"❌ SumatraPDF 인쇄 시간 초과 (15초)")
        return ("print_failed", "SumatraPDF 인쇄 시간 초과 (15초)")
    except Exception as e:
        logger.error(f"❌ 프린트 중 오류: {e}")
        return ("print_failed", str(e))


class KioskWebSocketConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_state = {}

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
                await self.start_user_confirmation(data.get('patient'))
            elif message_type == 'stt.result':
                await self.handle_stt_result(data.get('text', ''))
            elif message_type == 'document.print': # Kiosk Consumer도 print 요청 처리 가능하도록 추가
                await self.handle_print_request(data)
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
        """프린트 요청 처리 (TTS 버그 수정됨)"""
        try:
            patient_id = data.get('patient_id')
            doc_type = data.get('doc_type')
            issue_date = data.get('issue_date')
            consumer_name = self.__class__.__name__
            logger.info(f"🖨️ 출력 요청 ({consumer_name}): {patient_id}, {doc_type}, {issue_date}")

            if not all([patient_id, doc_type, issue_date]):
                await self.send_message('tts.text', {'text': '출력할 정보를 확인할 수 없습니다.'})
                return

            status, detail = await database_sync_to_async(print_document)(patient_id, doc_type, issue_date)

            final_tts_message = ""
            if status == "success":
                # 날짜 형식을 TTS에 맞게 조정 (예: 2025-07-16 -> 7월 16일) - 필요시 구현
                # date_obj = datetime.strptime(normalize_date_input(issue_date), "%Y-%m-%d")
                # tts_date_str = f"{date_obj.month}월 {date_obj.day}일"
                tts_date_str = issue_date # 우선 원본 사용
                await self.send_message('tts.text', {'text': f'{tts_date_str}의 {doc_type}을 출력합니다.'})
                await asyncio.sleep(0.5)
                final_tts_message = '출력이 완료되었습니다.'
            elif status == "not_found":
                final_tts_message = '서류 파일을 찾을 수 없습니다. 날짜를 다시 확인해주세요.'
                logger.error(f"파일 찾기 실패 ({consumer_name}): {detail}")
            elif status == "print_failed":
                final_tts_message = '프린터 오류가 발생했습니다. 관리자에게 문의해주세요.'
                logger.error(f"프린터 실패 세부 정보 ({consumer_name}): {detail}")

            if final_tts_message:
                await self.send_message('tts.text', {'text': final_tts_message})

            # 인쇄 후 listening 상태 복귀는 ServiceWebSocketConsumer에서만 필요할 수 있음
            # KioskWebSocketConsumer에서는 상태 변경 로직이 다를 수 있으므로 주석 처리
            # self.client_state['step'] = 'listening'
            # await self.send_message("voice.mode", {"allow_short_input": False})

        except Exception as e:
            consumer_name = self.__class__.__name__
            logger.error(f"출력 중 오류 ({consumer_name}): {e}")
            await self.send_message('tts.text', {'text': '출력 처리 중 오류가 발생했습니다.'})
            # 오류 발생 시에도 listening 상태 복귀는 ServiceWebSocketConsumer 에서만 필요할 수 있음
            # self.client_state['step'] = 'listening'
            # await self.send_message("voice.mode", {"allow_short_input": False})


class ServiceWebSocketConsumer(AsyncWebsocketConsumer):
    """GPT 기반 스마트 서류 인식 Consumer"""

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
                # self.logger.info("TTS 완료 신호 수신") # 로그가 너무 많으면 주석 처리
                self.tts_completed_time = asyncio.get_event_loop().time()
                # 여기에 mic.on 로직 추가 가능 (필요시)
                # await asyncio.sleep(0.2)
                # await self.send_message("mic.on")
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

    async def handle_print_request(self, data):
        """프린트 요청 처리 (TTS 버그 수정됨)"""
        try:
            patient_id = self.selected_patient.get("patient_id") if self.selected_patient else data.get('patient_id') # 세션 우선
            doc_type = data.get('doc_type')
            issue_date = data.get('issue_date')
            consumer_name = self.__class__.__name__
            logger.info(f"🖨️ 출력 요청 ({consumer_name}): {patient_id}, {doc_type}, {issue_date}")

            if not all([patient_id, doc_type, issue_date]):
                await self.send_tts_with_tracking('출력할 정보를 확인할 수 없습니다.') # send_tts_with_tracking 사용
                return

            status, detail = await database_sync_to_async(print_document)(patient_id, doc_type, issue_date)

            final_tts_message = ""
            if status == "success":
                # 날짜 형식을 TTS에 맞게 조정 (예: 2025-07-16 -> 7월 16일) - 필요시 구현
                # date_obj = datetime.strptime(normalize_date_input(issue_date), "%Y-%m-%d")
                # tts_date_str = f"{date_obj.month}월 {date_obj.day}일"
                tts_date_str = issue_date # 우선 원본 사용
                await self.send_tts_with_tracking(f'{tts_date_str}의 {doc_type}을 출력합니다.')
                await asyncio.sleep(0.5) # 실제 인쇄 시간 고려하여 sleep 추가 가능
                final_tts_message = '출력이 완료되었습니다.'
            elif status == "not_found":
                final_tts_message = '서류 파일을 찾을 수 없습니다. 날짜를 다시 확인해주세요.'
                logger.error(f"파일 찾기 실패 ({consumer_name}): {detail}")
            elif status == "print_failed":
                final_tts_message = '프린터 오류가 발생했습니다. 관리자에게 문의해주세요.'
                logger.error(f"프린터 실패 세부 정보 ({consumer_name}): {detail}")

            if final_tts_message:
                await self.send_tts_with_tracking(final_tts_message) # send_tts_with_tracking 사용

            # ✅ 인쇄 후 다시 listening 상태로
            self.client_state['step'] = 'listening'
            await self.send_message("voice.mode", {"allow_short_input": False})

        except Exception as e:
            consumer_name = self.__class__.__name__
            logger.error(f"출력 중 오류 ({consumer_name}): {e}")
            await self.send_tts_with_tracking('출력 처리 중 오류가 발생했습니다.') # send_tts_with_tracking 사용
            # 오류 발생 시에도 listening 상태로 복귀
            self.client_state['step'] = 'listening'
            await self.send_message("voice.mode", {"allow_short_input": False})


    async def start_voice_guidance(self):
        guidance_text = "진료확인서, 처방전, 진료영수증 중 원하는 서류를 말씀해주세요."
        await self.send_tts_with_tracking(guidance_text) # send_tts_with_tracking 사용

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
                await self.send_tts_with_tracking("어떤 서류를 발급하시겠습니까? 진료확인서, 처방전, 진료영수증 중에서 말씀해주세요.")
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
            await self.send_tts_with_tracking("원하는 날짜를 말씀하시거나 '취소'라고 말씀해주세요.")
            return
        if any(s in t for s in cancel_synonyms):
            self.logger.info("발급 취소")
            self.client_state["step"] = "listening"
            await self.send_message("voice.mode", {"allow_short_input": False})
            await self.send_tts_with_tracking("다른 서류가 필요하시면 말씀해주세요.")
            return
        await self.send_tts_with_tracking("발급 또는 취소라고 정확히 말씀해주세요.")


    async def process_voice_input(self, text):
        """음성 입력 처리 (service_consumers.py 버전 통합)"""
        current_step = self.client_state.get("step")
        now = asyncio.get_event_loop().time()

        # TTS 에코 방지 (최근 TTS 내용과 비교)
        if self.recent_tts_content and (now - self.tts_completed_time < 2.0): # TTS 완료 후 2초 이내 입력만 검사
            from difflib import SequenceMatcher
            similarity_threshold = 0.85
            if SequenceMatcher(None, self.recent_tts_content.replace(" ", ""), text.replace(" ", "")).ratio() > similarity_threshold:
                 self.logger.info(f"🎤 자기 TTS 에코로 판단하여 무시 (유사도 > {similarity_threshold}): '{text}'")
                 return

        self.logger.info(f"🎤 음성 입력: '{text}' (상태: {current_step})")
        self.recognition_failure_count = 0 # 성공 시 초기화

        try:
            # 1. 발급 의사 확인 단계 ("발급", "취소" 등)
            if current_step == "waiting_for_issue_confirmation":
                await self.handle_issue_confirmation(text)
                return

            # 2. 날짜 선택 단계 ("7월 16일", "16일" 등)
            if current_step == 'date_selection':
                logger.info(f"📅 날짜 선택 입력 감지: '{text}' → 직접 프린터 처리 요청")
                patient_id = self.selected_patient.get("patient_id")
                doc_type = self.client_state.get("selected_doc_type")
                # document.print 메시지로 인쇄 요청 위임
                await self.send_message('document.print', {
                    'patient_id': patient_id,
                    'doc_type': doc_type,
                    'issue_date': text # 날짜 변환은 print_document에서
                })
                # handle_print_request에서 상태를 listening으로 변경하므로 여기서는 변경 안 함
                return

            # 3. 그 외 (초기 단계, 상담 중 등) -> GPT 분석 수행
            analysis = await self.analyze_input_with_context(text)
            self.logger.info(f"🤖 분석 결과: {analysis}")
            await self.handle_analysis_result(analysis, text) # 분석 결과에 따라 query_database 또는 상담 함수 호출

        except Exception as e:
            self.logger.error(f"음성 입력 처리 오류: {str(e)}")
            await self.send_tts_with_tracking("오류가 발생했습니다. 다시 말씀해주세요.")


    async def start_smart_consultation(self, user_input, doc_hint=None):
        """스마트 상담 시작 (단문/스트리밍 구분)"""
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
                    await asyncio.sleep(0.5)
                    await self.send_message("mic.on")
        except Exception as e:
            self.logger.error(f"상담 오류: {e}")
            cta = f"발급을 원하시면 '{doc_hint}'라고 말씀해주세요." if doc_hint else "발급을 원하시면 서류명을 말씀해주세요."
            await self.send_tts_with_tracking("죄송합니다, 답변 중 오류가 발생했습니다. 다시 질문해주세요. " + cta)


    async def process_service_selection(self, service_name):
        """서비스 이름(카드 클릭 등)으로 직접 조회"""
        logger.info(f"🖱️ 서비스 선택: {service_name}")
        if service_name in ['진료확인서', '처방전', '진료영수증']:
            await self.send_message('document.recognized', {'document_type': service_name})
            await self.query_database(service_name)
        else:
            await self.send_tts_with_tracking(f"{service_name}는 현재 지원되지 않습니다.")


    async def query_database(self, doc_type, submit_to=None): # submit_to 인자 추가
        """DB 조회 후 TTS 안내 (발급 확인 단계로 이동)"""
        try:
            logger.info(f"🗃️ DB 조회: {doc_type} (제출처: {submit_to})")
            model_class = self.doc_type_model_map.get(doc_type)
            if not model_class:
                await self.send_tts_with_tracking(f'{doc_type}는 준비 중입니다.')
                self.client_state['step'] = 'listening'
                return

            patient_filter = {}
            if self.selected_patient:
                patient_filter['patient_id'] = self.selected_patient.get('patient_id')
            else:
                 await self.send_tts_with_tracking("환자 정보가 확인되지 않아 조회할 수 없습니다.")
                 self.client_state['step'] = 'listening'
                 return

            field_map = self.FIELD_MAP.get(doc_type, [])
            results = await self.fetch_all_generic(model_class, field_map, filters=patient_filter)
            logger.info(f"📊 조회 결과: {len(results)}건")

            await self.send_message('db.results', {'results': results})

            if results:
                self.client_state["step"] = "waiting_for_issue_confirmation"
                self.client_state["selected_doc_type"] = doc_type
                await self.send_message("voice.mode", {"allow_short_input": True})
                prefix = f"{submit_to} 제출용 " if submit_to else ""
                confirmation_text = f"{prefix}{doc_type} {len(results)}건을 찾았습니다. 발급을 원하시면 '발급' 취소하시려면 '취소'라고 말씀해주세요."
                await self.send_tts_with_tracking(confirmation_text)
            else:
                await self.send_tts_with_tracking(f"조회된 {doc_type}이 없습니다. 다른 서류를 원하시면 말씀해주세요.")
                self.client_state["step"] = "listening"
                await self.send_message("voice.mode", {"allow_short_input": False})

        except Exception as e:
            logger.exception("DB query error")
            await self.send_error("DB 조회 중 오류가 발생했습니다.")
            self.client_state["step"] = "listening"


    @database_sync_to_async
    def fetch_all_generic(self, model_class, field_map, filters=None):
        qs = model_class.objects.all()
        if filters: qs = qs.filter(**filters)

        order_fields = []
        candidates = ["prescription_date", "receipt_date", "id"]
        model_fields = {f.name for f in model_class._meta.get_fields()}
        for c in candidates:
            if c in model_fields:
                order_fields.append(f"-{c}")
                break
        if order_fields: qs = qs.order_by(*order_fields)

        rows = [{out_key: self._fmt(getattr(obj, attr, None)) for out_key, attr in field_map} for obj in qs[:100]]
        return rows

    async def send_message(self, msg_type, data=None):
        message = {'type': msg_type, **(data or {})}
        await self.send(text_data=json.dumps(message))

    async def send_error(self, error_message):
        await self.send_message('error', {'message': error_message})

    async def send_tts_with_tracking(self, text):
        """TTS 전송 및 에코 방지용 상태 업데이트, 완료 후 mic.on 전송"""
        if not text: return
        await self.send_message("mic.off")
        self.recent_tts_content = text
        self.tts_completed_time = 0.0

        await self.send_message("tts.text", {"text": text})

        async def delayed_mic_on(delay=1.5):
            await asyncio.sleep(delay)
            if self.tts_completed_time == 0.0 and self.recent_tts_content == text:
                self.logger.info(f"🎤 TTS 완료 신호 지연 감지, {delay}초 후 마이크 강제 활성화")
                await self.send_message("mic.on")

        asyncio.create_task(delayed_mic_on(2.0))


    async def handle_recognition_failure(self, error: str):
        """음성 인식 실패 처리 (3회 이상 시 초기화)"""
        self.logger.warning(f"🎤 음성 인식 실패: {error}")
        self.recognition_failure_count += 1
        if self.recognition_failure_count >= 3:
            await self.send_tts_with_tracking("음성 인식에 3회 이상 실패하여 초기 화면으로 돌아갑니다.")
            await self.send_message("system.redirect", {"url": "/kiosk/main/"})
            await self.close(code=1000)
        else:
            await self.send_tts_with_tracking("다시 한번 말씀해주세요.")