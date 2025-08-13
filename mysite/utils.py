import re
import difflib
from datetime import datetime

def is_positive_response(text: str) -> bool:
    """긍정 응답 감지"""
    text = text.strip().lower()
    positive_words = ["네", "예", "응", "그래", "맞아", "좋아", "확인", "진행", "웅", "예스", "yes"]
    
    if text in positive_words:
        return True
    
    for word in positive_words:
        if text.endswith(word):
            return True
    
    return False

def is_negative_response(text: str) -> bool:
    """부정 응답 감지"""
    text = text.strip().lower()
    negative_words = ["아니", "아니요", "싫어", "안돼", "그만", "취소", "no"]
    return any(word in text for word in negative_words)

def clean_speech_input(text: str) -> str:
    """음성 입력 텍스트 정제"""
    if not text:
        return ""
    
    # 특수문자 제거
    text = re.sub(r'[^\w가-힣\s]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    
    # 시스템 메시지 제거
    system_phrases = ["말씀해주세요", "선택해주세요", "확인해주세요", "입력해주세요"]
    for phrase in system_phrases:
        text = text.replace(phrase, "").strip()
    
    return text

def extract_name_from_text(text: str) -> str:
    """텍스트에서 이름 추출"""
    text = clean_speech_input(text)
    
    # 패턴 매칭
    name_patterns = [
        r'(?:저는|이름은|성함은)\s*([가-힣]{2,4})',
        r'([가-힣]{2,4})(?:입니다|이에요|예요)',
        r'^([가-힣]{2,4})$'
    ]
    
    for pattern in name_patterns:
        match = re.search(pattern, text)
        if match:
            name = match.group(1).strip()
            if is_valid_korean_name(name):
                return name
    
    # 한글 2-4자 추출
    korean_words = re.findall(r'[가-힣]{2,4}', text)
    for word in korean_words:
        if is_valid_korean_name(word):
            return word
    
    return None

def extract_birth_date_from_text(text: str) -> str:
    """생년월일 추출 (YYYYMMDD)"""
    text = re.sub(r'[^\d]', '', text)
    
    if len(text) == 8 and text.isdigit():
        try:
            year = int(text[:4])
            month = int(text[4:6])
            day = int(text[6:8])
            
            if 1900 <= year <= 2024 and 1 <= month <= 12 and 1 <= day <= 31:
                datetime(year, month, day)
                return text
        except ValueError:
            pass
    
    return None

def extract_document_type_from_text(text: str) -> str:
    """서류 유형 추출"""
    text = clean_speech_input(text).lower()
    
    document_mappings = {
        '진료확인서': ['진료확인서', '진료 확인서', '진료확인', '확인서'],
        '처방전': ['처방전', '처방', '약처방전'],
        '진료비영수증': ['진료비영수증', '영수증', '진료비', '영수증']
    }
    
    for doc_type, keywords in document_mappings.items():
        for keyword in keywords:
            if keyword in text:
                return doc_type
    
    return None

def extract_number_from_text(text: str) -> int:
    """텍스트에서 숫자 추출"""
    text = clean_speech_input(text)
    
    korean_numbers = {
        '하나': 1, '한': 1, '일': 1,
        '둘': 2, '두': 2, '이': 2,
        '셋': 3, '세': 3, '삼': 3,
        '넷': 4, '네': 4, '사': 4,
        '다섯': 5, '오': 5,
    }
    
    for korean, number in korean_numbers.items():
        if korean in text:
            return number
    
    numbers = re.findall(r'\d+', text)
    if numbers:
        num = int(numbers[0])
        if 1 <= num <= 10:
            return num
    
    return None

def is_valid_korean_name(name: str) -> bool:
    """한글 이름 유효성 검사"""
    if not name or len(name) < 2 or len(name) > 4:
        return False
    
    if not re.match(r'^[가-힣]+$', name):
        return False
    
    return True