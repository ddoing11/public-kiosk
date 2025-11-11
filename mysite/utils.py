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

# ==============================
# 📂 문서 파일 경로 탐색 함수 (비동기 안전)
# ==============================
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


BASE_DOC_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "documents")

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

####################################################################################################

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