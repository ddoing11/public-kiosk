import asyncio
import logging
import threading
import re
from django.conf import settings
from openai import OpenAI
import azure.cognitiveservices.speech as speechsdk

logger = logging.getLogger('kiosk')

# OpenAI 클라이언트 초기화
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

SYSTEM_PROMPT = """너는 병원 서류 출력 키오스크 상담원입니다. 

규칙:
- 짧고 핵심적으로 답변하세요 (한두 문장 단위)
- 서류 발급 절차, 이용방법, 오류대응을 단계적으로 간단히 설명
- 감탄사나 이모지는 사용하지 마세요
- 음성합성을 위해 문장을 지나치게 길게 만들지 마세요
- 친절하고 전문적인 톤을 유지하세요

주요 안내 사항:
- 진료확인서, 소견서, 진단서 등 각종 서류 발급 가능
- 신분증과 진료카드 지참 필수
- 수수료는 서류 종류에 따라 상이
- 발급 소요시간은 보통 5-10분
"""

async def azure_text_to_speech(text, websocket=None):
    """Azure TTS를 사용한 음성 합성"""
    try:
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
            # TTS 완료 후 띵 소리 재생
            await play_ding_sound(websocket)
            await asyncio.sleep(0.1)  # 100ms 지연
            
            # 마이크 켜기
            if websocket:
                await websocket.send_message('mic.on')
                
        return success
        
    except Exception as e:
        logger.error(f"Azure TTS 오류: {str(e)}")
        return await browser_fallback_tts(text, websocket)

async def browser_fallback_tts(text, websocket):
    """브라우저 TTS 폴백"""
    logger.info("브라우저 TTS 폴백 사용")
    if websocket:
        await websocket.send_message('tts.say', {'text': text})
        await asyncio.sleep(len(text) * 0.05)  # 대략적인 TTS 시간 추정
        await play_ding_sound(websocket)
        await asyncio.sleep(0.1)
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
    """주민번호 앞 6자리 유효성 검사"""
    if not re.match(r'^\d{6}$', id_string):
        return False, "6자리 숫자가 아닙니다"
    
    try:
        year = int(id_string[:4])
        month = int(id_string[4:6])
        
        if not (1900 <= year <= 2099):
            return False, "연도가 유효하지 않습니다"
        
        if not (1 <= month <= 12):
            return False, "월이 유효하지 않습니다"
            
        return True, "유효함"
        
    except ValueError:
        return False, "숫자 변환 오류"

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