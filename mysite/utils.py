import asyncio
import logging
from openai import OpenAI
from django.conf import settings

logger = logging.getLogger('kiosk')
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

# 병원/의료 관련 시스템 프롬프트 - 간결한 답변용
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

답변 스타일:
- "진료확인서는 회사 제출용 서류입니다."
- "처방전은 약국에서 필요한 서류예요."
- "더 자세한 내용은 접수처에 문의하세요."

절대 긴 설명이나 나열하지 마세요. 1-2문장으로 핵심만!
"""

def is_consultation_request(text):
    """상담 요청인지 판단하는 함수 - 개선된 버전"""
    logger.info(f"🤔 상담 요청 체크: '{text}'")
    
    # 명확한 상담 키워드들
    strong_consultation_keywords = [
        '상담', '문의', '질문', '궁금', '도움', '설명'
    ]
    
    # 상담을 암시하는 표현들
    consultation_patterns = [
        '필요한데', '필요해', '어떤', '뭐', '무엇', '어떻게',
        '가르쳐', '알려줘', '도와줘', '모르겠어', '헷갈려'
    ]
    
    # 서류 관련 질문 패턴
    document_question_patterns = [
        '서류가', '서류를', '서류는', '제출', '낼', '내야', '필요'
    ]
    
    # 명확한 상담 키워드가 있으면 즉시 True
    for keyword in strong_consultation_keywords:
        if keyword in text:
            logger.info(f"✅ 명확한 상담 키워드 발견: '{keyword}'")
            return True
    
    # 서류명만 단독으로 나온 경우는 상담이 아님
    document_only_patterns = [
        '진료확인서', '처방전', '진료영수증',
        '확인서', '증명서', '영수증'
    ]
    
    text_clean = text.strip()
    for pattern in document_only_patterns:
        if text_clean == pattern or text_clean.replace(" ", "") == pattern.replace(" ", ""):
            logger.info(f"❌ 서류명 단독: '{text_clean}' -> 상담 아님")
            return False
    
    # 서류 관련 질문인지 체크
    has_document_question = any(pattern in text for pattern in document_question_patterns)
    has_consultation_pattern = any(pattern in text for pattern in consultation_patterns)
    
    if has_document_question and has_consultation_pattern:
        logger.info(f"✅ 서류 관련 상담 질문 패턴 발견")
        return True
    
    # 상담을 암시하는 표현들이 있으면 상담으로 판단
    if has_consultation_pattern and len(text) > 3:
        logger.info(f"✅ 상담 암시 표현 발견")
        return True
    
    logger.info(f"❌ 상담 요청 아님: '{text}'")
    return False

def contains_document_keyword(text):
    """문서 키워드가 포함되어 있는지 확인"""
    document_keywords = ['진료확인서', '처방전', '진료영수증']
    for keyword in document_keywords:
        if keyword in text:
            return keyword
    return None

def extract_document_type(text):
    """텍스트에서 문서 타입 추출"""
    document_keywords = {
        '진료확인서': ['진료확인서', '확인서', '진료증명서', '증명서'],
        '처방전': ['처방전', '처방서', '약처방전'],
        '진료영수증': ['진료영수증', '영수증', '수납증', '결제증']
    }
    
    for doc_type, keywords in document_keywords.items():
        for keyword in keywords:
            if keyword in text:
                return doc_type
    return None

async def get_gpt_streaming_response(user_input, system_prompt=SYSTEM_PROMPT):
    """GPT 스트리밍 응답을 생성하는 비동기 제너레이터"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input}
            ],
            max_tokens=500,
            temperature=0.7,
            stream=True
        )
        
        for chunk in response:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content, False
        
        # 스트리밍 완료 신호
        yield "", True
        
    except Exception as e:
        logger.error(f"GPT streaming error: {str(e)}")
        yield "죄송합니다. 일시적인 오류가 발생했습니다. 다시 질문해주시겠어요?", True

def clean_voice_input(text):
    """음성 입력 텍스트 정리"""
    if not text:
        return ""
    
    # 공백 정리
    text = text.strip()
    
    # 불필요한 문구 제거
    remove_phrases = ['음', '어', '그', '저기', '그거']
    for phrase in remove_phrases:
        text = text.replace(phrase + ' ', '').replace(' ' + phrase, '')
    
    return text

def is_greeting(text):
    """인사말인지 판단"""
    greetings = ['안녕', '안녕하세요', '반갑', '처음', '시작']
    return any(greeting in text for greeting in greetings)

def get_appropriate_response(text):
    """입력에 따른 적절한 응답 반환"""
    if is_greeting(text):
        return "안녕하세요! 원하시는 서류를 말씀해주시거나 궁금한 점이 있으시면 상담을 요청해주세요."
    
    if '감사' in text or '고마워' in text:
        return "천만에요! 다른 도움이 필요하시면 언제든 말씀해주세요."
    
    return None

# ===== 기존 함수들 (consumers.py에서 import하는 함수들) =====

def is_identity_confirmation(text):
    """신원 확인 관련 텍스트인지 판단"""
    identity_keywords = [
        '신원', '신분', '확인', '인증', '본인', '신분증', 
        '주민등록', '등록번호', '생년월일', '이름', '주소'
    ]
    return any(keyword in text for keyword in identity_keywords)

def extract_patient_info(text):
    """텍스트에서 환자 정보 추출"""
    # 간단한 패턴 매칭으로 이름, 생년월일 등 추출
    import re
    
    # 생년월일 패턴 (YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD)
    date_patterns = [
        r'\d{4}[-/]\d{2}[-/]\d{2}',
        r'\d{8}'
    ]
    
    # 전화번호 패턴
    phone_pattern = r'010-?\d{4}-?\d{4}'
    
    extracted_info = {}
    
    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            extracted_info['birth_date'] = match.group()
            break
    
    phone_match = re.search(phone_pattern, text)
    if phone_match:
        extracted_info['phone'] = phone_match.group()
    
    return extracted_info

def validate_patient_info(patient_info):
    """환자 정보 유효성 검증"""
    required_fields = ['patient_name', 'birth_date']
    
    for field in required_fields:
        if field not in patient_info or not patient_info[field]:
            return False, f"{field} 정보가 필요합니다."
    
    # 생년월일 형식 검증
    birth_date = patient_info.get('birth_date', '')
    if birth_date:
        import re
        if not re.match(r'\d{4}[-/]?\d{2}[-/]?\d{2}', birth_date):
            return False, "생년월일 형식이 올바르지 않습니다."
    
    return True, "유효한 정보입니다."

def analyze_voice_intent(text):
    """음성 입력의 의도를 분석"""
    text = text.lower().strip()
    
    # 서류 요청 의도
    document_intent = extract_document_type(text)
    if document_intent:
        return 'document_request', document_intent
    
    # 상담 요청 의도
    if is_consultation_request(text):
        return 'consultation_request', text
    
    # 신원 확인 의도
    if is_identity_confirmation(text):
        return 'identity_confirmation', text
    
    # 인사 의도
    if is_greeting(text):
        return 'greeting', text
    
    # 기타
    return 'unknown', text

def format_patient_data(patient):
    """환자 데이터 포맷팅"""
    if not patient:
        return None
    
    formatted = {
        'patient_id': patient.get('patient_id', ''),
        'patient_name': patient.get('patient_name', ''),
        'birth_date': patient.get('birth_date', ''),
        'gender': patient.get('gender', ''),
        'contact': patient.get('contact', ''),
        'address': patient.get('address', '')
    }
    
    return formatted

def get_error_message(error_type, detail=None):
    """오류 타입에 따른 적절한 오류 메시지 반환"""
    error_messages = {
        'database_error': '데이터베이스 연결에 문제가 발생했습니다.',
        'recognition_error': '음성 인식 중 오류가 발생했습니다.',
        'validation_error': '입력 정보가 올바르지 않습니다.',
        'network_error': '네트워크 연결에 문제가 발생했습니다.',
        'auth_error': '인증에 실패했습니다.',
        'not_found': '요청하신 정보를 찾을 수 없습니다.'
    }
    
    base_message = error_messages.get(error_type, '알 수 없는 오류가 발생했습니다.')
    
    if detail:
        return f"{base_message} ({detail})"
    
    return base_message

def is_negative_response(text):
    """부정적인 응답인지 판단"""
    negative_keywords = [
        '아니', '아니요', '아뇨', '노', '싫어', '거절', 
        '안돼', '안해', '안할래', '아니다', '틀렸', '잘못',
        '취소', '그만', '멈춰', '중단', '끝'
    ]
    return any(keyword in text for keyword in negative_keywords)

def is_positive_response(text):
    """긍정적인 응답인지 판단"""
    positive_keywords = [
        '네', '예', '맞아', '맞습니다', '좋아', '좋습니다',
        '응', '오케이', '확인', '동의', '승인', '진행',
        '계속', '다음', '시작'
    ]
    return any(keyword in text for keyword in positive_keywords)

def extract_numbers(text):
    """텍스트에서 숫자 추출"""
    import re
    numbers = re.findall(r'\d+', text)
    return [int(num) for num in numbers]

def extract_korean_name(text):
    """텍스트에서 한국 이름 패턴 추출"""
    import re
    # 한글 이름 패턴 (2-4글자)
    name_pattern = r'[가-힣]{2,4}'
    matches = re.findall(name_pattern, text)
    
    # 일반적인 이름이 아닌 단어들 필터링
    common_words = ['진료', '확인서', '처방전', '영수증', '상담', '문의', '질문']
    filtered_names = [name for name in matches if name not in common_words]
    
    return filtered_names[0] if filtered_names else None

def is_service_request(text):
    """서비스 요청인지 판단"""
    service_keywords = [
        '서비스', '도움', '지원', '요청', '신청', '발급',
        '출력', '인쇄', '복사', '조회', '검색', '찾아'
    ]
    return any(keyword in text for keyword in service_keywords)

def clean_text(text):
    """텍스트 정리 (공백, 특수문자 등)"""
    if not text:
        return ""
    
    import re
    # 연속된 공백을 하나로
    text = re.sub(r'\s+', ' ', text)
    # 앞뒤 공백 제거
    text = text.strip()
    # 특수문자 일부 제거 (필요시)
    # text = re.sub(r'[^\w\s가-힣]', '', text)
    
    return text

def is_exit_request(text):
    """종료 요청인지 판단"""
    exit_keywords = [
        '종료', '끝', '나가', '그만', '취소', '중단',
        '홈으로', '처음으로', '메인', '돌아가'
    ]
    return any(keyword in text for keyword in exit_keywords)

def get_time_greeting():
    """시간에 따른 인사말 반환"""
    from datetime import datetime
    
    now = datetime.now()
    hour = now.hour
    
    if 6 <= hour < 12:
        return "좋은 아침입니다!"
    elif 12 <= hour < 18:
        return "안녕하세요!"
    elif 18 <= hour < 22:
        return "좋은 저녁입니다!"
    else:
        return "안녕하세요!"

def format_date(date_obj):
    """날짜 객체를 문자열로 포맷팅"""
    if not date_obj:
        return ""
    
    try:
        if hasattr(date_obj, 'strftime'):
            return date_obj.strftime("%Y년 %m월 %d일")
        else:
            return str(date_obj)
    except:
        return str(date_obj)

def is_help_request(text):
    """도움말 요청인지 판단"""
    help_keywords = [
        '도움', '도움말', '사용법', '방법', '어떻게',
        '설명', '안내', '가이드', '메뉴얼', '사용'
    ]
    return any(keyword in text for keyword in help_keywords)