import asyncio
import logging
import re
from django.conf import settings
from openai import OpenAI
import azure.cognitiveservices.speech as speechsdk
from datetime import datetime


logger = logging.getLogger('kiosk')

# OpenAI 클라이언트 초기화
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))


SYSTEM_PROMPT = """너는 병원 키오스크 상담 도우미입니다.

역할:
- 사용자가 원하는 목적(보험 청구, 회사 제출 등)에 필요한 서류를 안내
- 키오스크 사용법 설명  
- 서류별 용도와 차이점 설명
- 직접 서류 발급은 하지 않음

답변 규칙:
- 짧고 핵심적으로 답변 (1-2문장)
- 친절하고 전문적인 톤 유지
- 감탄사나 이모지 사용하지 마세요
- 음성합성을 위해 문장을 지나치게 길게 만들지 마세요

주요 안내 사항:
- 진료확인서: 단순 진료 사실 확인 (보험청구, 회사제출)
- 소견서: 의사의 의학적 소견 포함 (상세한 보험청구)  
- 진단서: 정확한 진단명과 치료계획 (중요한 보험, 법적 용도)
- 처방전: 약물 처방 내역

상담 종료 조건:
사용자가 "감사합니다", "알겠습니다", "이해했습니다", "충분합니다", "됐습니다" 등을 말하면 반드시 다음 멘트로 상담 종료:
"도움이 되셨기를 바랍니다. 원하시는 서류를 발급받으시려면 주민번호 앞 6자리를 입력하시면 서류 발급이 가능합니다. 감사합니다!"
"""

async def azure_text_to_speech(text, websocket=None):
    """Azure TTS를 사용한 음성 합성 - 자기 음성 인식 방지 개선"""
    try:
        # TTS 시작 전 마이크 완전히 끄기
        if websocket:
            await websocket.send_message('mic.off')
            await asyncio.sleep(0.2)  # 마이크 끄기 확실히 대기
        
        # Azure Speech 설정
        speech_key = getattr(settings, 'AZURE_SPEECH_KEY', '')
        speech_region = getattr(settings, 'AZURE_SPEECH_REGION', '')
        
        if not speech_key or not speech_region:
            logger.warning("Azure Speech 키 또는 지역이 설정되지 않음 - 브라우저 TTS 사용")
            return await browser_fallback_tts(text, websocket)
        
        speech_config = speechsdk.SpeechConfig(subscription=speech_key, region=speech_region)
        speech_config.speech_synthesis_language = "ko-KR"
        speech_config.speech_synthesis_voice_name = "ko-KR-SunHiNeural"  # 여성 음성
        
        # 음성 합성기 생성
        audio_config = speechsdk.audio.AudioOutputConfig(use_default_speaker=True)
        synthesizer = speechsdk.SpeechSynthesizer(speech_config=speech_config, audio_config=audio_config)
        
        # 비동기로 음성 합성 실행
        def synthesis_task():
            result = synthesizer.speak_text_async(text).get()
            if result.reason == speechsdk.ResultReason.SynthesizingAudioCompleted:
                logger.info(f"Azure TTS 성공: {text[:50]}...")
                return True
            else:
                logger.error(f"Azure TTS 실패: {result.reason}")
                return False
        
        # 별도 스레드에서 실행 (blocking 함수이므로)
        success = await asyncio.get_event_loop().run_in_executor(None, synthesis_task)
        
        if success:
            # TTS 완료 후 적절한 지연 (너무 길지 않게 조정)
            text_delay = 0.1
            await asyncio.sleep(text_delay)

            
            # 띵 소리 재생
            await play_ding_sound(websocket)
            await asyncio.sleep(0.3)  # 띵 소리 후 짧은 지연
            
            # 마이크 켜기
            if websocket:
                await websocket.send_message('mic.on')
                
        return success
        
    except Exception as e:
        logger.error(f"Azure TTS 오류: {str(e)}")
        return await browser_fallback_tts(text, websocket)

async def browser_fallback_tts(text, websocket):
    """브라우저 TTS 폴백 - 자기 음성 인식 방지 개선"""
    logger.info("브라우저 TTS 폴백 사용")
    if websocket:
        # 마이크 끄기
        await websocket.send_message('mic.off')
        await asyncio.sleep(0.2)
        
        # TTS 실행
        await websocket.send_message('tts.say', {'text': text})
        
        # 적절한 대기 시간 (브라우저 TTS도 짧게 조정)
        text_delay = min(3.0, max(1.0, len(text) * 0.04))  # 최소 1초, 최대 3초
        await asyncio.sleep(text_delay)
        
        # 띵 소리 재생
        await play_ding_sound(websocket)
        await asyncio.sleep(0.3)
        
        # 마이크 켜기
        await websocket.send_message('mic.on')
    return True

async def play_ding_sound(websocket):
    """띵 효과음 재생"""
    if websocket:
        await websocket.send_message('audio.ding')
        
async def get_gpt_streaming_response(user_input):
    """GPT 스트리밍 응답 생성기"""
    try:
        # 스트리밍 요청
        stream = client.chat.completions.create(
            model=getattr(settings, 'LLM_MODEL', 'gpt-4o-mini'),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input}
            ],
            stream=True,
            max_tokens=200,
            temperature=0.7
        )
        
        # 스트리밍 토큰 처리
        for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                delta = chunk.choices[0].delta.content
                yield delta, False
            
            # 스트림 완료 확인
            if chunk.choices[0].finish_reason is not None:
                yield "", True
                break
                
    except Exception as e:
        logger.error(f"GPT streaming error: {str(e)}")
        # 에러 시 기본 응답
        yield "죄송합니다. 상담 서비스에 일시적 문제가 있습니다.", False
        yield "", True

def validate_id_format(id_string):
    """주민번호 앞 6자리(YYMMDD) 유효성 검사"""
    if not re.fullmatch(r'\d{6}', id_string):
        return False, "6자리 숫자가 아닙니다"
    try:
        datetime.strptime(id_string, "%y%m%d")
        return True, "유효함"
    except ValueError:
        return False, "날짜 형식(YYMMDD)이 유효하지 않습니다"

def mask_personal_info(text, show_chars=2):
    """개인정보 마스킹 (로그용)"""
    if len(text) <= show_chars:
        return "*" * len(text)
    return text[:show_chars] + "*" * (len(text) - show_chars)

def setup_kiosk_logging():
    """키오스크 전용 로깅 설정"""
    import os
    from django.conf import settings
    
    log_dir = os.path.join(settings.BASE_DIR, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    
    kiosk_logger = logging.getLogger('kiosk')
    kiosk_logger.setLevel(logging.INFO)
    
    # 파일 핸들러
    handler = logging.FileHandler(os.path.join(log_dir, 'kiosk.log'))
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    handler.setFormatter(formatter)
    kiosk_logger.addHandler(handler)
    
    return kiosk_logger

def is_consultation_request(text):
    """상담 요청 감지"""
    consultation_keywords = ['상담', '문의', '질문', '도움', '안내']
    text = text.lower().strip()
    return any(keyword in text for keyword in consultation_keywords)


def is_consultation_end_request(text):
    """상담 종료 요청 감지"""
    end_keywords = ['감사합니다', '고맙습니다', '알겠습니다', '이해했습니다', 
                   '종료', '끝', '그만', '충분합니다', '됐습니다', '고마워요', '알겠어요']
    text = text.lower().strip()
    return any(keyword in text for keyword in end_keywords)

def is_simple_agreement(text):
    """단순 동의 표현 감지 (네, 예, 응 등) - 개선된 버전"""
    agreement_keywords = ['네', '예', '응', '어', '맞아요', '맞습니다', '그럼요', '오케이', '알았어요']
    
    # 마침표, 쉼표 등 제거 후 정규화
    clean_text = re.sub(r'[.,!?]', '', text.lower().strip())
    
    # 정확히 일치하거나 매우 짧은 동의 표현
    return clean_text in agreement_keywords or (len(clean_text) <= 2 and any(keyword in clean_text for keyword in ['네', '예', '응', '어']))

def is_identity_confirmation(text):
    """'본인 확인' 또는 유사한 확인 표현 감지"""
    confirmation_keywords = ['본인 확인', '본인확인', '확인']
    clean_text = text.lower().strip().replace(" ", "")
    return any(keyword in clean_text for keyword in confirmation_keywords)