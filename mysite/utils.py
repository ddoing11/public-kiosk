import asyncio
import logging
import json
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger('kiosk')
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

# ==========================
# 🎯 GPT 의도 분류 시스템 프롬프트
# ==========================
INTENT_CLASSIFICATION_PROMPT = """
당신은 병원 키오스크의 AI 상담원입니다.
사용자의 음성 입력을 분석하여 아래의 네 가지 중 하나의 의도를 분류하세요.

의도 유형(mode):
1. "issue" — 사용자가 특정 서류(예: 진료확인서, 처방전, 진료영수증 등)를 발급하거나 요청하는 경우
2. "consult" — 사용자가 서류의 용도, 차이점, 필요성 등을 묻는 경우 (예: '학교에 제출할 서류가 필요해')
3. "greeting" — 인사, 환영, 감사 표현 (예: 안녕하세요, 고마워요)
4. "other" — 위에 해당하지 않는 일반적인 발화

반드시 다음 JSON 형식으로만 답변하세요:
{
  "mode": "issue" | "consult" | "greeting" | "other",
  "document_type": "진료확인서" | "처방전" | "진료영수증" | null,
   "submit_to": 사용자가 언급한 제출 대상(예: 학교, 회사, 보험사 등). 
  명확하지 않으면 null로 둡니다.,
  "reason": "사용자가 한 말의 간단한 요약",
  "confidence": 0.0 ~ 1.0 사이 점수
}
"""

# ==========================
# 🎯 GPT 기반 의도 분석 함수
# ==========================
async def analyze_intent_with_gpt(user_text: str):
    """GPT가 직접 사용자의 발화를 분석하고 intent JSON으로 반환"""
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
            logger.warning("⚠️ GPT 응답이 JSON 형식이 아님, 기본 구조로 복원 시도")
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

# ==========================
# ✨ 기존 간결 응답용 GPT (유지)
# ==========================
SYSTEM_PROMPT = """
당신은 병원 키오스크의 AI 상담원입니다. 환자들의 의료 서류 조회 및 일반적인 병원 이용 관련 질문에 친절하고 정확하게 답변해주세요.

중요한 답변 원칙:
- 답변은 반드시 1-2문장으로 간결하게
- TTS로 읽기에 적합하도록 짧고 명확하게
- 복잡한 설명보다는 핵심만 간단히

주요 역할:
1. 진료확인서: 회사, 학교 제출용 진료 증명서류
2. 처방전: 의사가 처방한 약물 정보 서류  
3. 진료영수증: 진료비 결제 내역 서류 (보험청구용)
"""

async def get_gpt_streaming_response(user_input, system_prompt=SYSTEM_PROMPT):
    """GPT 스트리밍 응답 생성"""
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
        yield "죄송합니다. 일시적인 오류가 발생했습니다. 다시 시도해주세요.", True

# ==========================
# 🔹 텍스트 전처리 및 단순 패턴 함수
# ==========================
def clean_voice_input(text):
    if not text:
        return ""
    text = text.strip()
    for phrase in ['음', '어', '그', '저기', '그거']:
        text = text.replace(phrase + ' ', '').replace(' ' + phrase, '')
    return text

def is_greeting(text):
    greetings = ['안녕', '안녕하세요', '반갑', '처음', '시작']
    return any(greeting in text for greeting in greetings)

def get_appropriate_response(text):
    if is_greeting(text):
        return "안녕하세요! 원하시는 서류를 말씀해주시거나 궁금한 점이 있으시면 상담을 요청해주세요."
    if '감사' in text or '고마워' in text:
        return "천만에요! 다른 도움이 필요하시면 언제든 말씀해주세요."
    return None

# ==========================
# ⚙️ 기존 인터페이스와 호환되는 래퍼
# ==========================
async def analyze_voice_intent(text):
    """
    ✅ 기존 시스템과의 호환 유지용 래퍼
    GPT가 반환한 mode를 그대로 전달
    """
    result = await analyze_intent_with_gpt(text)
    mode = result.get("mode", "other")
    logger.info(f"🎯 analyze_voice_intent 결과 → {mode}")
    return mode, result
