import asyncio
import json
import re
import os
import sys
import datetime
import logging
from typing import Optional
from django.utils import timezone
from django.conf import settings
from openai import OpenAI
from asgiref.sync import sync_to_async
from mysite.models import MedicalReceipt, Prescription, Medical_Certificate

# ==============================
# 🔧 로깅 UTF-8 환경 설정
# ==============================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger("kiosk")
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

# ==============================
# 🎯 GPT 의도 분류 프롬프트
# ==============================
INTENT_CLASSIFICATION_PROMPT = """
당신은 병원 키오스크의 AI 상담원입니다.
사용자의 음성 입력을 분석하여 아래의 네 가지 중 하나의 의도를 분류하세요.

의도 유형(mode):
1. "issue" — 사용자가 특정 서류(예: 진료확인서, 처방전, 진료영수증 등)를 발급하거나 요청하는 경우
2. "consult" — 사용자가 서류의 용도, 차이점, 필요성 등을 묻는 경우
3. "greeting" — 인사, 환영, 감사 표현
4. "other" — 그 외 일반 발화

JSON 형식으로만 답변하세요:
{
  "mode": "issue" | "consult" | "greeting" | "other",
  "document_type": "진료확인서" | "처방전" | "진료영수증" | null,
  "submit_to": "학교" | "회사" | "보험사" | null,
  "reason": "사용자가 말한 요약",
  "confidence": 0.0 ~ 1.0
}
"""

# ==============================
# 🧠 GPT 기반 의도 분석 함수
# ==============================
async def analyze_intent_with_gpt(user_text: str):
    """GPT를 사용해 의도(JSON) 분석"""
    try:
        logger.info(f"🧠 GPT 의도 분석 요청: '{user_text}'")

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": INTENT_CLASSIFICATION_PROMPT},
                {"role": "user", "content": user_text}
            ],
            temperature=0.3,
            max_tokens=300
        )

        content = response.choices[0].message.content.strip()
        logger.info(f"📥 GPT 원문 응답: {content}")

        try:
            result = json.loads(content)
        except json.JSONDecodeError:
            logger.warning("⚠️ GPT 응답이 JSON 형식이 아님 — 기본 구조로 복원")
            result = {
                "mode": "other",
                "document_type": None,
                "submit_to": None,
                "reason": user_text,
                "confidence": 0.5
            }

        logger.info(f"✅ 최종 의도 분석 결과: {result}")
        return result

    except Exception as e:
        logger.error(f"GPT intent analysis error: {str(e)}")
        return {
            "mode": "other",
            "document_type": None,
            "submit_to": None,
            "reason": f"오류: {str(e)}",
            "confidence": 0.0
        }

# ==============================
# ✨ GPT 간결 응답용 (TTS 최적화)
# ==============================
SYSTEM_PROMPT = """
당신은 병원 키오스크의 AI 상담원입니다.
환자의 서류 관련 질문에 짧고 정확히 답하세요.

- 답변은 1~2문장으로 간결하게
- 복잡한 설명보다는 핵심만
- TTS로 읽기 좋게 작성
"""

async def get_gpt_streaming_response(user_input, system_prompt=SYSTEM_PROMPT):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            max_tokens=300,
            temperature=0.6,
            stream=True
        )
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content, False
        yield "", True
    except Exception as e:
        logger.error(f"GPT streaming error: {str(e)}")
        yield "죄송합니다. 오류가 발생했습니다. 다시 시도해주세요.", True

# ==============================
# 🔹 전처리 / 유틸 함수
# ==============================
def clean_voice_input(text):
    if not text:
        return ""
    text = re.sub(r'[.,?!]', '', text).strip()
    for phrase in ['음', '어', '그', '저기', '그거']:
        text = text.replace(phrase + ' ', '').replace(' ' + phrase, '')
    return text

def is_greeting(text):
    greetings = ['안녕', '안녕하세요', '반갑', '처음', '시작']
    return any(greeting in text for greeting in greetings)

def get_appropriate_response(text):
    if is_greeting(text):
        return "안녕하세요! 원하시는 서류를 말씀해주시거나 궁금한 점을 물어보세요."
    if '감사' in text or '고마워' in text:
        return "천만에요! 도움이 필요하시면 말씀해주세요."
    return None

# ==============================
# 📅 날짜 처리 함수
# ==============================
def normalize_date_text(date_text: str) -> str:
    text = (date_text or "").strip()
    text = re.sub(r'[^0-9가-힣\s/.-]', '', text).replace(" ", "")
    text = text.replace("년", " ").replace("월", " ").replace("일", " ").strip()
    return text

def convert_date_to_db_format(date_text: str) -> Optional[str]:
    text = normalize_date_text(date_text)
    parts = re.findall(r'\d+', text)
    current_year = datetime.date.today().year

    if len(parts) == 1 and len(parts[0]) == 8:
        try:
            dt = datetime.datetime.strptime(parts[0], '%Y%m%d').date()
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    if len(parts) == 3:
        y, m, d = parts
        try:
            if len(y) == 2: y = '20' + y
            dt = datetime.date(int(y), int(m), int(d))
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    if len(parts) == 2:
        m, d = parts
        try:
            dt = datetime.date(current_year, int(m), int(d))
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            pass

    text_lower = date_text.strip().lower()
    if '오늘' in text_lower or '당일' in text_lower:
        return datetime.date.today().strftime('%Y-%m-%d')
    if '어제' in text_lower:
        return (datetime.date.today() - datetime.timedelta(days=1)).strftime('%Y-%m-%d')
    return None

def is_cancel_response(text: str) -> bool:
    t = (text or "").lower().replace(" ", "")
    return any(k in t for k in ["취소", "그만", "안해", "중단", "종료", "하지마"])

def is_issue_response(text: str) -> bool:
    t = (text or "").lower().replace(" ", "")
    issue_words = ["발급", "출력", "진행", "해줘", "해주세요", "출력해", "인쇄", "프린트", "8급", "팔급"]
    return any(word in t for word in issue_words)

# ==============================
# 📂 문서 파일 경로 탐색 함수 (비동기 안전)
# ==============================
BASE_DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "documents")

def find_document_path(patient_id: str, doc_type: str, selected_date: str):
    """
    문서 유형과 날짜에 맞는 파일 경로 탐색 (환자번호 포함)
    """
    try:
        if not doc_type or not selected_date:
            logger.warning(f"[경고] 잘못된 입력: doc_type={doc_type}, selected_date={selected_date}")
            return None

        folder_map = {
            "진료확인서": "Medical_Certificate_DOC",
            "처방전": "Prescription_DOC",
            "진료영수증": "Medical_receipt_DOC",
        }

        folder_name = folder_map.get(doc_type)
        if not folder_name:
            logger.warning(f"[경고] 알 수 없는 문서 타입: {doc_type}")
            return None

        target_dir = os.path.join(BASE_DOC_PATH, folder_name)
        if not os.path.exists(target_dir):
            logger.warning(f"[경고] 폴더 없음: {target_dir}")
            return None

        date_str = selected_date.replace("-", "")
        pattern = re.compile(rf"{patient_id}_.*_{date_str}\.pdf$", re.IGNORECASE)

        for filename in os.listdir(target_dir):
            if pattern.match(filename):
                file_path = os.path.join(target_dir, filename)
                if os.path.exists(file_path):
                    logger.info(f"[확인] 문서 파일 발견: {file_path}")
                    return file_path

        logger.warning(f"[경고] {selected_date} 날짜의 {doc_type} 파일을 찾을 수 없음.")
        return None

    except Exception as e:
        logger.error(f"[오류] find_document_path 실패: {e}")
        return None
