# mysite/service_consumers.py
# (GPT 우선 판정: 발급 vs 상담 → 발급은 조회/확인 플로우, 상담은 스트리밍 라우팅 + 상담 CTA 항상 부착)
# - on-device 히ュー리스틱 최소화, LLM이 모드를 먼저 결정
# - 물음표가 없어도 '내용 문의'를 상담으로 분류
# - 0건 시 단문 안내 고정
# - TTS → mic 제어 일관화(서버에서 mic.off 후 tts.text)
# - 상담 답변(단문/스트리밍) 모두 마지막 줄에 CTA 필수 부착

import json
import asyncio
import logging

from .consumers import print_document 
from .utils import convert_date_to_db_format, is_cancel_response, is_issue_response, clean_voice_input # 등 필요한 모든 유틸 함수

from datetime import date, datetime

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.conf import settings

from openai import OpenAI

from .models import Medical_Certificate, Prescription, MedicalReceipt
from .utils import get_gpt_streaming_response  # 프로젝트 내 스트리밍 유틸

logger = logging.getLogger("kiosk")

# 최종 TTS 프롬프트 정의
FINAL_ISSUE_PROMPT = "출력이 완료되었습니다. 추가로 필요한 서류가 있으신가요? 있다면 서류명을 말씀해주시고 없다면 종료라고 말씀해주세요."

client = OpenAI(api_key=getattr(settings, "OPENAI_API_KEY", ""))
DEFAULT_LLM_MODEL = getattr(settings, "LLM_MODEL", "gpt-4o-mini")

KIOSK_SYSTEM_PROMPT = """
당신은 병원 키오스크에서 서류 발급을 도와주는 AI 도우미입니다.

[중요]
- 이 키오스크에서 진료확인서, 처방전, 진료영수증을 바로 출력할 수 있습니다.
- "접수처로 가세요" 같은 오프라인 안내는 금지. 항상 키오스크 발급을 우선 안내하세요.

[서류별 기본 용도]
- 진료확인서: 회사 병가/휴가, 학교 결석, 일반 증명
- 처방전: 약국 약 수령
- 진료영수증: 의료비 공제/보험 환급

[응답 원칙]
- 한글, 1~2문장으로 간결하게.
- 핵심만 안내하고 불필요한 설명은 생략.
- 제출처/용도에 맞게 추천.
- 모르면 "모릅니다" 대신, 간단한 추가 질문 1문장 제안.
- 키오스크에서 바로 발급 가능함을 자연스럽게 덧붙임.
"""

# ------------------------ 조사 보정 ------------------------
def _josa_iga(noun: str) -> str:
    if not noun:
        return "가"
    code = ord(noun[-1])
    base = 0xAC00
    if not (0xAC00 <= code <= 0xD7A3):
        return "가"
    jong = (code - base) % 28
    return "이" if jong != 0 else "가"

def _josa_eulreul(noun: str) -> str:
    if not noun:
        return "를"
    code = ord(noun[-1])
    base = 0xAC00
    if not (0xAC00 <= code <= 0xD7A3):
        return "를"
    jong = (code - base) % 28
    return "을" if jong != 0 else "를"

# ------------------------ 상담 CTA ------------------------
def _build_consult_cta(doc_hint: str | None) -> str:
    if doc_hint in ("진료확인서", "처방전", "진료영수증"):
        return f"발급을 원하시면 '{doc_hint}'라고 말씀해주세요."
    return "발급을 원하시면 서류명을 말씀해주세요."

# ------------------------ 단발 LLM ------------------------
async def llm_chat_once(
    user_text: str,
    system_prompt: str = KIOSK_SYSTEM_PROMPT,
    max_tokens: int = 120,
    temperature: float = 0.3,
) -> str:
    try:
        resp = client.chat.completions.create(
            model=DEFAULT_LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        text = resp.choices[0].message.content.strip()
        text = text.replace("확인서이", "확인서가").replace("확인서을", "확인서를")
        return text
    except Exception as e:
        logger.error(f"LLM call error: {e}")
        return "요청을 처리하는 동안 문제가 발생했습니다. 다시 한 번 말씀해 주세요."

# ------------------------ 컨슈머 ------------------------
class ServiceWebSocketConsumer(AsyncWebsocketConsumer):
    """GPT가 '발급 vs 상담'을 1차 판정 → 발급이면 조회/확인, 상담이면 정보 답변(+CTA) 또는 스트리밍(+CTA)"""

    # LLM 프롬프트에 반영할 기준 키워드(질문/발급 의사)
    CONTENT_QUERY_KWS = ["포함", "들어가", "기재", "내용", "적혀", "들어있", "표시되", "성명", "주민번호", "주소", "비용", "기간", "유효", "차이", "언제", "왜", "방법"]
    ISSUE_KWS = ["발급", "출력", "진행", "해줘", "해주세요", "바로해", "진행해", "출력해", "뽑아", "인쇄", "프린트"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger = logger

        self.selected_patient = None
        self.client_state = {}  # step: listening/advising/waiting_for_issue_confirmation/date_selection
        self.recognition_failure_count = 0
        self.current_context = {}

        # 에코/쿨다운/마이크 관리
        self.recent_tts_content = ""
        self.tts_completed_time = 0.0
        self.voice_delay = 2.0  # TTS 직후 입력 무시 시간(초)

        # TTS → mic.on 통일 관리를 위한 플래그/디바운스
        self.awaiting_tts = False
        self.last_mic_on_ts = 0.0
        self.mic_on_min_interval = 0.8  # 초

        # 모델 매핑
        self.doc_type_model_map = {
            "진료확인서": Medical_Certificate,
            "처방전": Prescription,
            "진료영수증": MedicalReceipt,
        }
        self.FIELD_MAP = {
            "진료확인서": [
                ("환자명", "patient_name"),
                ("환자번호", "patient_id"),
                ("성별", "gender"),
                ("생년월일", "birth_date"),
                ("연락처", "contact"),
                ("주소", "address"),
            ],
            "처방전": [
                ("환자명", "patient_name"),
                ("환자번호", "patient_id"),
                ("성별", "gender"),
                ("생년월일", "birth_date"),
                ("연락처", "contact"),
                ("처방일", "prescription_date"),
                ("담당의", "doctor_name"),
                ("진료과", "department"),
                ("병원명", "hospital_name"),
            ],
            "진료영수증": [
                ("환자명", "patient_name"),
                ("환자번호", "patient_id"),
                ("성별", "gender"),
                ("생년월일", "birth_date"),
                ("영수일", "receipt_date"),
            ],
        }

    def _fmt(self, v):
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, date):
            return v.strftime("%Y-%m-%d")
        return v
    
    def get_valid_date_from_text(self, text):
        # text: "7월 16일" 같은 음성 입력
        # convert_date_to_db_format 함수는 utils.py에 있다고 가정합니다.
        
        # 날짜 변환 시도 (utils.py 사용)
        converted_date = convert_date_to_db_format(text)
        
        if converted_date:
            logger.info(f"날짜 변환 성공: '{text}' → '{converted_date}'")
            return converted_date
        
        logger.warning(f"날짜 변환 실패, 원본 반환: '{text}'")
        return None
    

    # ------------- 연결 -------------
    async def connect(self):
        await self.accept()
        self.client_state = {"step": "listening"}
        self.current_context = {"previous_inputs": [], "submit_to": None}
        self.logger.info("Service WebSocket connected")

        self.selected_patient = self.scope["session"].get("selected_patient", None)
        if self.selected_patient:
            self.logger.info(
                f"Authenticated user: {self.selected_patient.get('patient_name')}"
            )
            await self.send_message("patient.info", {"patient": self.selected_patient})

        await self.send_message("voice.mode", {"allow_short_input": False})
        await asyncio.sleep(1)
        await self.start_voice_guidance()

    async def disconnect(self, close_code):
        self.logger.info(f"Service WebSocket disconnected: {close_code}")

    # ------------- 수신 -------------
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            msg_type = data.get("type")

            if msg_type in ("voice.input", "stt.result"):
                await self.process_voice_input(data.get("text", ""))

            elif msg_type == "service.select":
                if data.get("service") == "상담":
                    await self.start_smart_consultation("", None)
                else:
                    await self.process_service_selection(data.get("service", ""))

            elif msg_type == "recognition.failed":
                await self.handle_recognition_failure(data.get("error", "unknown"))

            elif msg_type == "tts.complete":
                # 클라이언트에서 TTS가 실제 완료되었을 때만 마이크를 켠다
                self.logger.info("TTS 완료 신호 수신")
                self.tts_completed_time = asyncio.get_event_loop().time()
                self.awaiting_tts = False

                now = asyncio.get_event_loop().time()
                if (now - self.last_mic_on_ts) >= self.mic_on_min_interval:
                    await asyncio.sleep(0.2)
                    await self.send_message("mic.on")
                    self.last_mic_on_ts = asyncio.get_event_loop().time()
                else:
                    self.logger.info("mic.on 디바운스에 의해 생략")

            else:
                self.logger.warning(f"알 수 없는 메시지 타입: {msg_type}")
        except Exception as e:
            self.logger.error(f"Error processing message: {str(e)}")

    # ------------- 공통 송신 -------------
    async def send_message(self, msg_type, data=None):
        message = {"type": msg_type, **(data or {})}
        await self.send(text_data=json.dumps(message))

    async def send_error(self, error_message):
        await self.send_message("error", {"message": error_message})

    async def send_tts_with_tracking(self, text):
        # 서버에서 먼저 mic.off → TTS 텍스트 전송 (레이스 방지)
        await self.send_message("mic.off")
        self.recent_tts_content = text
        self.awaiting_tts = True
        await self.send_message("tts.text", {"text": text})

    # ------------- 최초 안내 -------------
    async def start_voice_guidance(self):
        guidance_text = "진료확인서, 처방전, 진료영수증 중 원하는 서류를 말씀해주세요."
        await self.send_tts_with_tracking(guidance_text)

    # ------------- 입력 처리 -------------
    async def process_voice_input(self, text):
        current_step = self.client_state.get("step")
        now = asyncio.get_event_loop().time()

        # ✅ TTS 직후 쿨다운 제거 (바로 입력 가능)
        # if (now - self.tts_completed_time) < self.voice_delay:
        #     self.logger.info(f"TTS 완료 후 {self.voice_delay}초 이내 입력 무시: '{text}'")
        #     return

        # ✅ 단, TTS 직후 에코로 동일 문장 반복될 경우만 방지
        if self.recent_tts_content:
            tts_keywords = set(self.recent_tts_content.split())
            input_keywords = set((text or "").split())
            if len(tts_keywords.intersection(input_keywords)) >= 3:
                self.logger.info(f"자기 TTS 에코로 판단하여 무시: '{text}'")
                return

        self.logger.info(f"음성 입력: '{text}' (상태: {current_step})")
        self.recognition_failure_count = 0


        # ★★★ 1. 입력 무시: 'busy_printing' 상태일 경우 무시 ★★★
        if current_step == 'busy_printing':
            self.logger.info(f"현재 BUSY 상태({current_step}), 입력 무시: '{text}'")
            return

        try:
            # ✅ 발급 의사 확인 단계 (waiting_for_issue_confirmation)
            if current_step == "waiting_for_issue_confirmation":
                await self.handle_issue_confirmation(text)
                return

            # ✅ 날짜 선택 단계 (date_selection)
            if current_step == 'date_selection':
                # 📌 2. 누락된 함수 호출: self.get_valid_date_from_text 사용
                issue_date_str = self.get_valid_date_from_text(text) 

                if issue_date_str:
                    logger.info(f"날짜 선택 입력 감지: '{text}' → 직접 프린터 처리로 이동") # 이모지 제거
                    
                    # 1. 상태를 'busy_printing'으로 변경하여 10초간 입력 무시 시작
                    self.client_state['step'] = 'busy_printing'
                    await self.send_message('status', {'state': 'busy_printing'}) # UI에게 인쇄 중임을 알림
                    
                    # 2. 프린트 명령 실행 (이 안에서 "발급을 시작합니다" TTS 나감)
                    success = await database_sync_to_async(print_document)(
                        self.scope, 
                        self.client_state.get("selected_doc_type"), 
                        issue_date_str, 
                        self.client_state.get('printer_name')
                    )
                    
                    if success:
                        # 3. 10초 딜레이 (발급 시간)
                        await asyncio.sleep(10) # ★★★ 10초 지연 ★★★

                        # 4. 최종 TTS 메시지 및 상태 업데이트
                        await self.send_tts_with_tracking(FINAL_ISSUE_PROMPT)
                        self.client_state['step'] = 'await_additional_issue'
                        await self.send_message('status', {'state': 'listening'}) # 상태 복구
                        
                    else:
                        # 5. 인쇄 실패 시
                        await self.send_tts_with_tracking(f"발급 중 오류가 발생했습니다. 다시 시도해주세요.")
                        self.client_state['step'] = 'date_selection'
                        await self.send_message('status', {'state': 'listening'}) # 상태 복구
                        
                    return # 처리 완료

                else:
                    # 날짜 변환 실패
                    await self.send_tts(f"유효한 날짜를 말씀해주세요.", voice_mode='date_selection')
                    self.client_state['step'] = 'date_selection'
                    return
            
            # ✅ 추가 발급 요청 단계 처리 (await_additional_issue) - NEW LOGIC
            if current_step == 'await_additional_issue':
                 await self.handle_additional_issue_request(text)
                 return

            # 나머지 단계만 GPT 분석 수행
            analysis = await self.analyze_input_with_context(text)
            self.logger.info(f"분석 결과: {analysis}")
            await self.handle_analysis_result(analysis, text)

        except Exception as e:
            self.logger.error(f"음성 입력 처리 오류: {str(e)}")
            await self.send_tts_with_tracking("다시 말씀해주세요.")

    # ★★★ 새로운 핸들러 추가: handle_additional_issue_request (기존 로직을 따름) ★★★
    async def handle_additional_issue_request(self, text):
        from .utils import is_cancel_response # is_issue_response는 consumers.py에서 가져와야 함.
        
        if "종료" in text or is_cancel_response(text):
            # 대화 종료
            await self.send_tts("이용해주셔서 감사합니다. 키오스크를 종료합니다.", voice_mode='completed')
            self.client_state['step'] = 'completed'
            # Optional: Close the websocket
            # await self.close() 
        else: # 서류명 등 다른 입력으로 간주 (발급 요청)
            await self.send_tts("어떤 서류를 발급하시겠습니까? 서류명을 말씀해주세요.", voice_mode='await_document_request')
            self.client_state['step'] = 'await_document_request'
            
    # ------------- LLM 분류 -------------
    async def analyze_input_with_context(self, text: str) -> dict:
        """LLM이 모드를 1차 판정: mode=issue|consult|other"""
        self.logger.info(f"컨텍스트 기반 GPT 분석 시작: '{text}'")
        self.current_context.setdefault("previous_inputs", []).append(text)
        if len(self.current_context["previous_inputs"]) > 5:
            self.current_context["previous_inputs"] = self.current_context["previous_inputs"][-5:]

        # 규칙 문자열
        content_kws = " | ".join(self.CONTENT_QUERY_KWS)
        issue_kws = " | ".join(self.ISSUE_KWS)

        try:
            prompt = f"""
당신은 병원 키오스크의 분류기입니다. 사용자의 한 문장을 보고 다음 중 하나로 판정하세요:
- "issue": 지금 당장 특정 서류를 발급/출력하려는 의도
- "consult": 서류의 '내용·차이·조건·방법·용도' 등을 묻는 상담 의도 (물음표가 없어도 가능)
- "other": 위 두 가지가 아닌 기타

반드시 JSON만 출력하세요.

입력: "{text}"

[도움 규칙]
- 아래 단어가 들어가면 보통 상담: {content_kws}
  (예: "진료확인서에 주민번호가 포함되나요", "처방전 내용에는 뭐가 들어가요")
- 아래 단어가 들어가면 보통 발급 의사: {issue_kws}
  (예: "처방전 발급 해줘", "진료영수증 출력해")
- 단일 명사만 말하면(예: "처방전", "진료확인서") → issue로 본다.
- '발급해줘?', '출력해?'처럼 물음표가 있어도 명시적 발급 요구면 issue.
- 제출처/용도가 명시되면 추론: 회사/병가/휴가 → 진료확인서, 학교/결석 → 진료확인서, 보험/환급/공제 → 진료영수증, 약국/처방/복용 → 처방전

반환 형식(JSON):
{{
  "mode": "issue" | "consult" | "other",
  "document_type": "진료확인서" | "처방전" | "진료영수증" | null,
  "submit_to": "회사" | "학교" | "보험사" | "약국" | "기타" | null,
  "reason": "간략한 판정 근거",
  "confidence": 0.0
}}

예시1) "처방전 발급해줘"
-> {{"mode":"issue","document_type":"처방전","submit_to":"약국","reason":"발급 의사","confidence":0.95}}

예시2) "진료확인서에는 내 이름이 들어가?"
-> {{"mode":"consult","document_type":"진료확인서","submit_to":null,"reason":"내용 문의","confidence":0.9}}

예시3) "보험 청구하려는데 뭐 필요해"
-> {{"mode":"consult","document_type":"진료영수증","submit_to":"보험사","reason":"필요 서류 상담","confidence":0.9}}

예시4) "처방전"
-> {{"mode":"issue","document_type":"처방전","submit_to":"약국","reason":"단일 명사","confidence":0.9}}
"""
            response = client.chat.completions.create(
                model=DEFAULT_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=120,
                temperature=0.1,
            )
            result_text = response.choices[0].message.content.strip()
            if result_text.startswith("```"):
                result_text = result_text.replace("```json", "").replace("```", "").strip()

            result = json.loads(result_text)

            # 제출처는 현재 발화에 '명시'된 경우만 유지 (없으면 null)
            explicit_map = {
                "회사": ["회사", "직장", "병가", "휴가"],
                "학교": ["학교", "대학", "결석", "수업"],
                "보험사": ["보험", "환급", "공제", "청구", "보상"],
                "약국": ["약국", "약", "처방", "복용"],
            }
            explicit_submit = None
            low = (text or "").lower()
            for k, kws in explicit_map.items():
                if any(kw in low for kw in kws):
                    explicit_submit = k
                    break
            if explicit_submit:
                result["submit_to"] = explicit_submit
            else:
                result["submit_to"] = None if result.get("submit_to") in ("null", "기타") else result.get("submit_to")

            # document_type 보정: 허용 값 외에는 None
            if result.get("document_type") not in ("진료확인서", "처방전", "진료영수증"):
                result["document_type"] = None

            # mode 보정
            if result.get("mode") not in ("issue", "consult", "other"):
                result["mode"] = "other"

            return result

        except Exception as e:
            self.logger.error(f"GPT 분석 오류: {e}")
            return self.fallback_analysis(text)

    def fallback_analysis(self, text: str) -> dict:
        """네트워크/파싱 실패 시 최소한의 분류(보수적)"""
        t = (text or "").lower().strip()
        self.logger.info(f"백업 분석 사용: '{t}'")

        # 단일 명사/명령문은 issue로
        if t in ("진료확인서", "확인서", "증명서"):
            return {"mode": "issue", "document_type": "진료확인서", "submit_to": None, "confidence": 0.7, "reason": "단일 명사"}
        if t in ("처방전", "처방서"):
            return {"mode": "issue", "document_type": "처방전", "submit_to": "약국", "confidence": 0.7, "reason": "단일 명사"}
        if t in ("진료영수증", "영수증"):
            return {"mode": "issue", "document_type": "진료영수증", "submit_to": "보험사", "confidence": 0.7, "reason": "단일 명사"}

        # 내용 문의 키워드가 있으면 consult
        if any(k in t for k in self.CONTENT_QUERY_KWS):
            return {"mode": "consult", "document_type": None, "submit_to": None, "confidence": 0.6, "reason": "내용 문의 키워드"}

        # 발급 키워드가 있으면 issue
        if any(k in t for k in self.ISSUE_KWS):
            # 대략적인 문서 추정
            if "처방" in t or "약" in t:
                doc = "처방전"
            elif "보험" in t or "공제" in t or "환급" in t:
                doc = "진료영수증"
            elif "확인서" in t or "증명서" in t:
                doc = "진료확인서"
            else:
                doc = "진료확인서"
            return {"mode": "issue", "document_type": doc, "submit_to": None, "confidence": 0.6, "reason": "발급 키워드"}

        return {"mode": "other", "document_type": None, "submit_to": None, "confidence": 0.3, "reason": "기타"}

    # ------------- 분석 결과 처리 -------------
    async def handle_analysis_result(self, analysis: dict, original_text: str):
        mode = analysis.get("mode", "other")
        doc_type = analysis.get("document_type")
        submit_to_analyzed = analysis.get("submit_to")

        # 현재 발화에서 제출처를 '명시'했을 때만 사용
        explicit_tokens = ["회사", "학교", "보험", "약국", "직장", "병가", "휴가", "결석", "공제", "청구", "보상"]
        explicit_present = any(tok in (original_text or "") for tok in explicit_tokens)
        submit_to = submit_to_analyzed if (explicit_present and submit_to_analyzed not in (None, "null", "기타")) else None

        if mode == "issue":
            # 문서타입 불명확하면 간단히 재질문(TTS)
            if doc_type not in ("진료확인서", "처방전", "진료영수증"):
                await self.send_tts_with_tracking("어떤 서류를 발급하시겠습니까? 진료확인서, 처방전, 진료영수증 중에서 말씀해주세요.")
                return

            self.logger.info(f"서류 직접 요청으로 처리: {doc_type} (제출처: {submit_to})")
            self.client_state["selected_doc_type"] = doc_type
            await self.send_message("document.recognized", {"document_type": doc_type})
            await self.query_database(doc_type, submit_to)
            return

        if mode == "consult":
            self.logger.info(f"상담 모드로 처리 (제출처: {submit_to}, 문서: {doc_type})")
            # 상담에서도 발급 유도 CTA를 넣기 위해 doc_hint 전달
            await self.start_smart_consultation(original_text, submit_to, doc_hint=doc_type)
            return

        # 그 외는 일반 상담으로 완충
        self.logger.info(f"기타 질문 → 일반 GPT 상담으로 처리: {original_text}")
        await self.start_general_gpt_consultation(original_text, doc_hint=None)

    # ------------- 발급 확인 -------------
    async def handle_issue_confirmation(self, text: str):
        self.logger.info(f"발급 의사 확인 응답(규칙 매칭): '{text}'")
        t = (text or "").replace(" ", "")

        issue_synonyms = ["발급", "출력", "진행", "해줘", "해주세요", "바로해", "진행해", "출력해", "8급", "팔급"]
        if any(s in t for s in issue_synonyms):
            self.logger.info("발급 의사 확정 → 날짜 선택")
            self.client_state["step"] = "date_selection"
            await self.send_message("voice.mode", {"allow_short_input": True})
            await self.send_tts_with_tracking(
                "원하는 날짜를 말씀하시거나 '취소'라고 말씀해주세요."
            )
            return

        cancel_synonyms = ["취소", "그만", "안해", "안해요", "안할래", "아니", "아니요", "중단"]
        if any(s in t for s in cancel_synonyms):
            self.logger.info("발급 취소")
            self.client_state["step"] = "listening"
            await self.send_message("voice.mode", {"allow_short_input": False})
            await self.send_tts_with_tracking("다른 서류가 필요하시면 말씀해주세요.")
            return

        await self.send_tts_with_tracking("발급 또는 취소라고 말씀해주세요.")

    # ------------- 날짜 선택 -------------
    async def handle_date_selection_input(self, text: str):
        analysis = await self.analyze_input_with_context(text)
        if analysis.get("mode") == "other" and analysis.get("document_type") is None:
            # 취소 외 기타 발화는 안내 후 리셋
            self.client_state["step"] = "listening"
            await self.send_message("voice.mode", {"allow_short_input": False})
            await self.send_tts_with_tracking("다른 서류가 필요하시면 말씀해주세요.")
            return

        # '취소' 계열 처리
        if any(s in (text or "") for s in ["취소", "그만", "안해", "아니", "중단"]):
            self.client_state["step"] = "listening"
            await self.send_message("voice.mode", {"allow_short_input": False})
            await self.send_tts_with_tracking("다른 서류가 필요하시면 말씀해주세요.")
        else:
            await self.send_message("voice.mode", {"allow_short_input": False})
            await self.send_tts_with_tracking("선택하신 날짜로 키오스크에서 발급 처리하겠습니다.")

    # ------------- 예기치 않은 확인/취소 -------------
    async def handle_unexpected_confirmation(self, category: str):
        # 기존 호환 유지(혹시 남아있는 '확인/취소' 분류에 대비)
        if category == "확인":
            await self.send_tts_with_tracking("발급 또는 취소라고 말씀해주세요.")
        else:
            self.client_state["step"] = "listening"
            await self.send_tts_with_tracking("원하는 서류를 말씀해주세요.")

    # ------------- 상담 -------------
    async def start_smart_consultation(self, user_input: str, submit_to: str | None = None, doc_hint: str | None = None):
        """상담 전용 루트: 단문/스트리밍 구분. 답변 마지막에 CTA(항상) 부착."""
        self.logger.info(f"스마트 상담 시작: '{user_input}' (제출처: {submit_to})")
        self.client_state["step"] = "advising"
        await self.send_message("status", {"state": "advising"})

        # 단순 정보 질문 → 1~2문장 정보 + CTA
        if not await self.is_complex_consultation_needed(user_input):
            self.logger.info("단순 정보 문의 → 단문 상담 답변")
            await self.answer_consult_brief(user_input, doc_hint)
            return

        # 복잡 → 스트리밍 상담(+마지막 줄 CTA)
        self.logger.info("복잡 → GPT 스트리밍 상담")
        await self.handle_general_consultation(user_input, doc_hint=doc_hint)

    async def start_general_gpt_consultation(self, user_input: str, doc_hint: str | None = None):
        self.logger.info(f"일반 GPT 상담 시작: '{user_input}'")
        self.client_state["step"] = "advising"
        await self.send_message("status", {"state": "advising"})
        await self.handle_general_consultation(user_input, doc_hint=doc_hint)

    async def is_complex_consultation_needed(self, user_input: str) -> bool:
        try:
            prompt = f"""
사용자 질문: "{user_input}"

이 질문이 다음 중 무엇인지 판단하세요:
1) 단순 서류 요청(issue)
2) 복잡한 상담 필요(consult)

JSON만: {{"needs_consultation": true/false, "reason": "설명"}}
"""
            resp = client.chat.completions.create(
                model=DEFAULT_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=80,
                temperature=0.1,
            )
            txt = resp.choices[0].message.content.strip()
            if txt.startswith("```"):
                txt = txt.replace("```json", "").replace("```", "").strip()
            data = json.loads(txt)
            self.logger.info(f"상담 복잡도 분석 결과: {data}")
            return bool(data.get("needs_consultation", False))
        except Exception as e:
            self.logger.error(f"상담 복잡도 분석 오류: {e}")
            return True

    async def answer_consult_brief(self, user_input: str, doc_hint: str | None):
        """상담 단문: 본문은 정보만, 마지막에 CTA 1줄을 반드시 붙임"""
        await self.send_message("mic.off")
        try:
            hint_text = f"(질문 관련 문서: {doc_hint})" if doc_hint else ""
            prompt = f"""
역할: 병원 키오스크 '상담 전용' 답변 도우미.

[원칙]
- 한국어 1~2문장.
- 질문에 대한 정보만 간단히 답하세요.
- 본문에서는 '조회/발급 진행' 같은 표현을 쓰지 마세요.
- 마지막 줄에 다음 중 하나의 CTA를 반드시 붙이세요:
  - 문서 힌트가 있으면: 발급을 원하시면 '<문서명>'라고 말씀해주세요.
  - 없으면: 발급을 원하시면 서류명을 말씀해주세요.

사용자 질문: "{user_input}"
{hint_text}
"""
            base = await llm_chat_once(prompt, system_prompt=KIOSK_SYSTEM_PROMPT, max_tokens=160, temperature=0.2)

            # 안전 보정: 본문 내 발급/조회 진행 문구 제거(CTA는 뒤에서 별도 추가)
            ban = ["조회해서", "출력해 드리겠습니다", "발급하겠습니다", "바로 발급해 드릴게요", "진행하겠습니다"]
            for b in ban:
                base = base.replace(b, "")
            base = base.strip()

            cta = _build_consult_cta(doc_hint)
            if cta not in base:
                if not base.endswith(("?", ".", "!", "요", "요.")):
                    base += "."
                base += f" {cta}"

            await self.send_tts_with_tracking(base.strip())
        except Exception as e:
            self.logger.error(f"단문 상담 생성 오류: {e}")
            await self.send_tts_with_tracking(_build_consult_cta(doc_hint))

    async def get_smart_document_recommendation(self, text: str, submit_to: str | None = None) -> dict:
        self.logger.info(f"GPT 서류 추천 분석: '{text}' (제출처: {submit_to})")
        try:
            prompt = f"""
사용자: "{text}"
제출처: {submit_to or "미지정"}

[키오스크] 진료확인서/처방전/진료영수증 바로 출력 가능.

JSON만: {{"document_type": "진료확인서|처방전|진료영수증|기타", "submit_to": "회사|학교|보험사|약국|기타"}}

[기준]
- 회사/직장 → 진료확인서
- 학교 → 진료확인서
- 보험/환급/공제 → 진료영수증
- 약국/처방 → 처방전
- 일반 증명/아프다 증명 → 진료확인서
"""
            resp = client.chat.completions.create(
                model=DEFAULT_LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=40,
                temperature=0.1,
            )
            txt = resp.choices[0].message.content.strip()
            if txt.startswith("```"):
                txt = txt.replace("```json", "").replace("```", "").strip()
            data = json.loads(txt)
            self.logger.info(f"GPT 추천 결과: {data}")
            return data
        except Exception as e:
            self.logger.error(f"GPT 추천 분석 오류: {e}")
            return {"document_type": "진료확인서", "submit_to": "기타"}

    async def handle_general_consultation(self, user_input: str, doc_hint: str | None = None):
        """스트리밍 상담. 마지막 줄에 CTA를 반드시 붙이도록 사용자 입력에 지침 주입."""
        await self.send_message("mic.off")
        try:
            cta = _build_consult_cta(doc_hint)
            injected_user = (
                f"{user_input}\n\n"
                "[응답지침]\n"
                "- 한국어 2~4문장으로 간결하게 설명하세요.\n"
                "- 본문에서는 발급/조회 진행 문구를 쓰지 마세요.\n"
                f"- 마지막 줄에는 반드시 다음 문장을 그대로 붙이세요: \"{cta}\"\n"
            )

            chunks = []
            async for delta, is_done in get_gpt_streaming_response(
                injected_user, system_prompt=KIOSK_SYSTEM_PROMPT
            ):
                if not is_done:
                    chunks.append(delta)
                    await self.send_message("gpt.stream", {"delta": delta})
                else:
                    full = "".join(chunks).strip()
                    self.recent_tts_content = full
                    self.awaiting_tts = True
                    await self.send_message("gpt.stream", {"event": "done", "full_text": full})
        except Exception as e:
            self.logger.error(f"상담 오류: {e}")
            await self.send_tts_with_tracking("다시 질문해주세요. " + _build_consult_cta(doc_hint))

    async def generate_contextual_response(self, document_type: str, submit_to: str | None, user_context=None) -> str:
        try:
            if submit_to and submit_to not in ("기타", "null"):
                mapping = {
                    "회사": f"회사 제출용은 {document_type}{_josa_eulreul(document_type)} 준비하시면 됩니다. 키오스크에서 바로 발급해 드릴게요.",
                    "학교": f"학교 제출용은 {document_type}{_josa_eulreul(document_type)} 준비하시면 됩니다. 즉시 출력 가능합니다.",
                    "보험사": f"보험 청구용은 {document_type}{_josa_eulreul(document_type)} 준비해 주세요. 바로 발급해 드릴게요.",
                    "약국": f"약국에서 약을 받으시려면 {document_type}{_josa_eulreul(document_type)} 지참하세요. 키오스크에서 출력해 드릴게요.",
                }
                return mapping.get(
                    submit_to,
                    f"{submit_to} 제출용으로 {document_type}{_josa_eulreul(document_type)} 준비해 주세요. 키오스크에서 발급 가능합니다.",
                )
            else:
                return f"{document_type}{_josa_eulreul(document_type)} 조회해서 키오스크에서 출력해 드리겠습니다."
        except Exception as e:
            self.logger.error(f"컨텍스트 응답 생성 오류: {e}")
            return f"{document_type}{_josa_eulreul(document_type)} 찾아서 발급해 드리겠습니다."

    # ------------- DB 조회 -------------
    async def query_database(self, doc_type: str, submit_to: str | None = None):
        """조회 결과에 맞춰 TTS 1회만 송출 (0건 시 GPT 호출 금지)"""
        try:
            self.logger.info(f"DB 조회: {doc_type} (제출처: {submit_to})")
            model_class = self.doc_type_model_map.get(doc_type)
            if not model_class:
                await self.send_tts_with_tracking(f"{doc_type}는 준비 중입니다.")
                return

            patient_filter = {}
            if self.selected_patient:
                patient_filter["patient_id"] = self.selected_patient.get("patient_id")

            field_map = self.FIELD_MAP.get(doc_type, [])
            results = await self.fetch_all_generic(model_class, field_map, filters=patient_filter)
            self.logger.info(f"조회 결과: {len(results)}건")

            await self.send_message("db.results", {"results": results})

            if results:
                confirmation_text = await self.generate_confirmation_message(doc_type, len(results), submit_to)
                self.client_state["step"] = "waiting_for_issue_confirmation"
                self.client_state["pending_document_type"] = doc_type
                await self.send_message("voice.mode", {"allow_short_input": True})
                await self.send_tts_with_tracking(confirmation_text)
            else:
                # 0건: 단문 + 명확한 CTA (제출처는 현재 발화에 명시된 경우에만)
                base = f"조회된 {doc_type}{_josa_iga(doc_type)} 없습니다. 다른 서류를 발급하시려면 서류명을 말씀해주세요."
                if doc_type == "진료확인서":
                    base = "조회된 진료확인서가 없습니다. 다른 서류를 발급하시려면 서류명을 말씀해주세요."
                if submit_to and submit_to not in ("기타", "null"):
                    base = f"{submit_to} 제출용 {doc_type}{_josa_eulreul(doc_type)} 찾지 못했어요. 다른 서류를 발급하시려면 서류명을 말씀해주세요."

                self.client_state["step"] = "listening"
                await self.send_message("voice.mode", {"allow_short_input": False})
                await self.send_tts_with_tracking(base)

        except Exception as e:
            self.logger.exception("DB query error")
            await self.send_error("DB 조회 중 오류가 발생했습니다.")

    async def generate_confirmation_message(self, doc_type: str, count: int, submit_to: str | None) -> str:
        prefix = f"{submit_to} 제출용 " if submit_to and submit_to not in ("기타", "null") else ""
        return f"{prefix}{doc_type} {count}건을 찾았습니다. 발급을 원하시면 '발급' 취소하시려면 '취소'라고 말씀해주세요."

    # ------------- ORM 접근 -------------
    @database_sync_to_async
    def fetch_all_generic(self, model_class, field_map, filters=None):
        qs = model_class.objects.all()
        if filters:
            qs = qs.filter(**filters)

        order_candidates = ["prescription_date", "receipt_date", "id"]
        model_fields = {f.name for f in model_class._meta.get_fields()}
        for c in order_candidates:
            if c in model_fields:
                qs = qs.order_by(f"-{c}")
                break

        rows = [
            {out_key: self._fmt(getattr(obj, attr, None)) for out_key, attr in field_map}
            for obj in qs[:100]
        ]
        return rows

    # --------- (선택) 누락될 수 있는 핸들러 안전 스텁 ---------
    async def process_service_selection(self, service: str):
        await self.send_tts_with_tracking(f"{service}는 준비 중입니다. 원하는 서류를 말씀해주세요.")

    async def handle_recognition_failure(self, error: str):
        self.logger.warning(f"recognition.failed: {error}")
        await self.send_tts_with_tracking("다시 말씀해주세요.")
