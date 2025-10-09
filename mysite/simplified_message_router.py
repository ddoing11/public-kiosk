import sys
sys.stdout.reconfigure(encoding='utf-8')  # ✅ Windows 콘솔 유니코드 에러 방지

import json
import asyncio
import re
import difflib
import time
from datetime import datetime
from .state_manager import HospitalStateManager
from .patient_handler import PatientInfoHandler
from .audio_handler import HospitalAudioHandler
from .utils import (
    clean_speech_input,
    is_positive_response,
    is_negative_response,
    analyze_intent_with_gpt,
)
from .printer_handler import PrinterHandler


# ------------------------------------------------
# 🧠 발급 인식 보정 ("8급", "팔급", "출력" 등 포함)
# ------------------------------------------------
def is_issue_response(text: str) -> bool:
    text = text.strip().replace(" ", "")
    issue_words = ["발급", "8급", "8금", "팔급", "밝읍", "받급", "출력", "인쇄", "프린트"]
    return any(word in text for word in issue_words)


class HospitalMessageRouter:
    def __init__(self):
        self.state_manager = HospitalStateManager()
        self.patient_handler = PatientInfoHandler()
        self.audio_handler = HospitalAudioHandler()
        self.printer_handler = PrinterHandler()
        self.last_tts_completed = True
        self.last_tts_text = ""
        self.last_tts_done_time = 0
        self.pending_tts_futures = {}

    # ------------------------------------------------
    # 🔹 클라이언트 연결 시
    # ------------------------------------------------
    async def handle_connection(self, websocket):
        state = self.state_manager.add_client(websocket)
        await self.audio_handler.welcome_message(websocket)
        state["step"] = "await_voice_confirmation"
        state["last_input_time"] = datetime.now()
        return state

    # ------------------------------------------------
    # 🔹 메인 메시지 처리
    # ------------------------------------------------
    async def process_message(self, websocket, message):
        try:
            data = json.loads(message) if isinstance(message, str) else message
        except json.JSONDecodeError:
            data = {"type": "speech.text", "text": message}

        # ✅ TTS 완료 신호
        if data.get("type") == "tts.complete":
            group_id = data.get("group_id")
            self.last_tts_completed = True
            self.last_tts_done_time = time.time()
            print(f"✅ 실제 TTS 종료 신호 수신 (group={group_id})")
            if group_id and group_id in self.pending_tts_futures:
                fut = self.pending_tts_futures.pop(group_id)
                if not fut.done():
                    fut.set_result(True)
            return

        # ✅ 일반 텍스트 메시지
        state = self.state_manager.get_state(websocket)
        if not state:
            print("⚠️ 상태를 찾을 수 없음 (세션 만료)")
            return

        cleaned_text = clean_speech_input(data.get("text", message))
        state["last_input_time"] = datetime.now()
        current_step = state.get("step", "init")

        # 🔎 자기 에코 방지
        if self._is_self_tts_echo(cleaned_text):
            print(f"🧩 자기 TTS 에코로 판단, 임시 무시: '{cleaned_text}'")
            asyncio.create_task(self._check_for_silence_and_prompt(websocket))
            return

        # ------------------------------------------------
        # 🎯 GPT 분석 (특정 단계는 생략)
        # ------------------------------------------------
        if current_step not in [
            "await_patient_name",
            "await_document_request",
            "await_date_selection",
            "date_selection",  # ✅ 날짜 선택 단계는 GPT 분석 생략
            "await_additional_issue",
            "waiting_for_issue_confirmation",
        ]:
            print(f"🧠 GPT 의도 분석 시작: '{cleaned_text}'")
            intent_result = await analyze_intent_with_gpt(cleaned_text)
            mode = intent_result.get("mode")
            doc_type = intent_result.get("document_type")
            submit_to = intent_result.get("submit_to")
            print(f"🎯 분석 결과: {intent_result}")
        else:
            print(f"⏭️ GPT 분석 생략 (단계: {current_step})")
            intent_result = {}

        # ------------------------------------------------
        # ✅ 단계별 처리
        # ------------------------------------------------
        if current_step == "await_voice_confirmation":
            if is_positive_response(cleaned_text):
                await self._send_tts_safe_grouped(websocket, "이름을 말씀해주세요.")
                await self.audio_handler.patient_info_prompt(websocket)
                state["step"] = "await_patient_name"
            elif is_negative_response(cleaned_text):
                await self._send_tts_safe_grouped(websocket, "일반 키오스크를 이용해 주세요.")
                state["step"] = "completed"

        elif current_step == "await_patient_name":
            new_step = await self.patient_handler.handle_patient_name_input(
                websocket, cleaned_text, state
            )
            state["step"] = new_step

        elif current_step == "await_document_request":
            state["doc_type"] = cleaned_text
            await self._send_tts_safe_grouped(
                websocket, f"{cleaned_text}을(를) 언제 발급받으셨나요? 날짜를 말씀해주세요."
            )
            state["step"] = "await_date_selection"

        # ------------------------------------------------
        # 🔹 날짜 입력 시 (출력 로직)
        # ------------------------------------------------
        elif current_step in ["await_date_selection", "date_selection"]:
            print(f"🧩 날짜 입력 감지: '{cleaned_text}' (단계: {current_step})")
            date_str = cleaned_text.strip()
            doc_type = state.get("selected_doc_type") or state.get("doc_type")

            # ✅ doc_type 복원
            if not doc_type and state.get("recognized_doc_type"):
                doc_type = state["recognized_doc_type"]
                state["doc_type"] = doc_type
                print(f"🧩 recognized_doc_type 복원됨: {doc_type}")

            if not doc_type:
                await self._send_tts_safe_grouped(
                    websocket, "어떤 서류의 날짜인지 알 수 없습니다. 다시 말씀해주세요."
                )
                return

            # ✅ 프린터 로직
            print(f"📄 프린터 처리 시작: {doc_type} ({date_str})")
            await self._send_tts_safe_grouped(
                websocket, f"{date_str}의 {doc_type} 발급을 준비 중입니다. 잠시만 기다려주세요."
            )

            try:
                await self.printer_handler.prepare_print_job(websocket, doc_type, date_str)
                await asyncio.sleep(5)
                await self._send_tts_safe_grouped(
                    websocket, "발급이 완료되었습니다. 다른 서류를 발급하시겠습니까?"
                )
                state["step"] = "await_additional_issue"
            except Exception as e:
                print(f"❌ 프린터 처리 중 오류: {e}")
                await self._send_tts_safe_grouped(
                    websocket, "발급 중 오류가 발생했습니다. 다시 시도해주세요."
                )
            return  # ✅ GPT 분석으로 절대 안 내려가도록 완전 차단

        elif current_step == "await_additional_issue":
            if is_positive_response(cleaned_text):
                await self._send_tts_safe_grouped(websocket, "어떤 서류를 발급하시겠습니까?")
                state["step"] = "await_document_request"
            elif is_negative_response(cleaned_text):
                await self._send_tts_safe_grouped(
                    websocket, "이용해주셔서 감사합니다. 대화가 종료됩니다."
                )
                state["step"] = "completed"

        # ------------------------------------------------
        # 🧩 발급 확인 단계 (8급 / 팔급 / 출력 등 포함)
        # ------------------------------------------------
        elif current_step == "waiting_for_issue_confirmation":
            if is_issue_response(cleaned_text) or "8급" in cleaned_text.replace(" ", "") or "팔급" in cleaned_text.replace(" ", ""):
                print("✅ 발급 의사 확정 → 날짜 선택 단계 진입 (8급/팔급 포함)")
                recognized_doc = state.get("recognized_doc_type")
                if recognized_doc:
                    state["doc_type"] = recognized_doc
                    print(f"🧩 recognized_doc_type 유지됨: {recognized_doc}")
                else:
                    print("⚠️ recognized_doc_type 없음 → 진료확인서 기본값 사용")
                    state["doc_type"] = "진료확인서"

                state["step"] = "date_selection"
                await self._send_tts_safe_grouped(
                    websocket, "원하는 날짜를 말씀하시거나 '취소'라고 말씀해주세요."
                )
                return  # ✅ 반드시 return 추가

            # 🔹 취소 응답 처리
            if is_negative_response(cleaned_text):
                await self._send_tts_safe_grouped(
                    websocket, "발급을 취소하셨습니다. 다른 서류가 필요하신가요?"
                )
                state["step"] = "await_additional_issue"
                return

            # 🔹 위 두 조건 모두 아니라면 안내 반복
            await self._send_tts_safe_grouped(
                websocket, "발급 또는 취소라고 말씀해주세요."
            )
            return

        # ------------------------------------------------
        # 🧩 전역 발급 명령 처리
        # ------------------------------------------------
        elif is_issue_response(cleaned_text):
            if current_step in ["await_date_selection", "date_selection"]:
                print(f"⚠️ 이미 날짜 선택 단계이므로 '발급' 명령 무시: {cleaned_text}")
                return
            state["step"] = "date_selection"
            await self._send_tts_safe_grouped(
                websocket, "원하는 날짜를 말씀하시거나 '취소'라고 말씀해주세요."
            )

    # ------------------------------------------------
    # 🔊 안정형 TTS
    # ------------------------------------------------
    async def _send_tts_safe(self, websocket, text: str, activate_mic=True):
        if not text:
            return
        waited = 0
        while not self.last_tts_completed and waited < 5.0:
            await asyncio.sleep(0.1)
            waited += 0.1
        self.last_tts_completed = False
        self.last_tts_text = text.strip()
        group_id = f"tts_{int(asyncio.get_event_loop().time() * 1000)}"
        tts_future = asyncio.get_event_loop().create_future()
        self.pending_tts_futures[group_id] = tts_future
        payload = {"type": "tts.text", "text": text, "group_id": group_id}
        await websocket.send(json.dumps(payload))
        print(f"🔊 [TTS 전송] {text} (group={group_id})")

        try:
            await asyncio.wait_for(tts_future, timeout=20.0)
        except asyncio.TimeoutError:
            print(f"⚠️ [TTS 완료 신호 타임아웃: {group_id}]")
        finally:
            self.pending_tts_futures.pop(group_id, None)
            self.last_tts_completed = True

        if activate_mic:
            await asyncio.sleep(0.4)
            await websocket.send(json.dumps({"type": "mic.on"}))

    # ------------------------------------------------
    # 🔊 문장 단위 그룹 TTS
    # ------------------------------------------------
    async def _send_tts_safe_grouped(self, websocket, full_text: str):
        sentences = re.split(r"(?<=[\.!?]|요\.|니다\.|세요\.)\s*", full_text.strip())
        sentences = [s.strip() for s in sentences if s.strip()]
        for i, sentence in enumerate(sentences):
            await self._send_tts_safe(websocket, sentence, activate_mic=False)
            if i < len(sentences) - 1:
                await asyncio.sleep(0.05)
        await asyncio.sleep(0.05)
        await websocket.send(json.dumps({"type": "mic.on"}))
        print("🎤 모든 문장 재생 완료 후 마이크 재활성화")

    # ------------------------------------------------
    # 🧩 자기 TTS 에코 판별
    # ------------------------------------------------
    def _is_self_tts_echo(self, cleaned_text: str) -> bool:
        if not cleaned_text or not self.last_tts_text:
            return False
        t1, t2 = self.last_tts_text.strip(), cleaned_text.strip()
        if len(t2) < 5:
            return False
        similarity = difflib.SequenceMatcher(None, t1, t2).ratio()
        if similarity > 0.95:
            return True
        s1 = set(t1.replace(" ", ""))
        s2 = set(t2.replace(" ", ""))
        if len(s1) and len(s2):
            ratio = len(s1 & s2) / max(len(s1), len(s2))
            if ratio > 0.96:
                return True
        return False

    # ------------------------------------------------
    # 🔚 연결 종료
    # ------------------------------------------------
    async def cleanup_connection(self, websocket):
        try:
            self.state_manager.remove_client(websocket)
            print("🧹 연결 정리 완료")
        except Exception as e:
            print(f"⚠️ 연결 정리 중 오류 발생: {e}")
