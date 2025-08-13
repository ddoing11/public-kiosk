import asyncio
import threading
from playsound import playsound
from django.conf import settings
from azure.cognitiveservices.speech import (
    SpeechConfig, SpeechSynthesizer, AudioConfig, ResultReason
)

AZURE_SPEECH_KEY = getattr(settings, 'AZURE_SPEECH_KEY', None)
AZURE_SPEECH_REGION = getattr(settings, 'AZURE_SPEECH_REGION', None)

def play_notification_sound():
    try:
        playsound("C:/SoundAssets/ding.wav")
    except Exception as e:
        print(f"⚠️ 알림음 재생 실패: {e}")

async def synthesize_speech(text, websocket=None, activate_mic=True):
    """Azure TTS 음성 합성"""
    if not AZURE_SPEECH_KEY:
        print("⚠️ Azure Speech 설정 없음")
        return False
    
    speech_config = SpeechConfig(subscription=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION)
    speech_config.speech_synthesis_language = "ko-KR"
    speech_config.speech_synthesis_voice_name = "ko-KR-SunHiNeural"
    
    synthesizer = SpeechSynthesizer(speech_config=speech_config)
    result = synthesizer.speak_text_async(text).get()

    if result.reason == ResultReason.SynthesizingAudioCompleted:
        print(f"✅ TTS 완료: {text[:50]}...")
        
        if activate_mic:
            threading.Thread(target=play_notification_sound).start()
            
        if activate_mic and websocket:
            await asyncio.sleep(0.1)
            for attempt in range(3):
                try:
                    await websocket.send("mic_on")
                    break
                except Exception as e:
                    await asyncio.sleep(0.2)
        return True
    
    return False

class HospitalAudioHandler:
    """병원 키오스크 음성 핸들러"""
    
    async def welcome_message(self, websocket):
        message = "안녕하세요. 병원 서류 발급 키오스크입니다. 음성으로 진행하시겠습니까?"
        await websocket.send(message)
        await synthesize_speech(message, websocket, activate_mic=True)
    
    async def patient_info_prompt(self, websocket):
        message = "환자분의 성함을 말씀해 주세요."
        await websocket.send(message)
        await synthesize_speech(message, websocket, activate_mic=True)
    
    async def birth_date_prompt(self, websocket, name):
        message = f"{name}님의 생년월일을 8자리 숫자로 말씀해 주세요."
        await websocket.send(message)
        await synthesize_speech(message, websocket, activate_mic=True)
    
    async def document_selection_prompt(self, websocket):
        message = "어떤 서류를 발급받으시겠습니까? 진료확인서, 처방전, 진료비영수증 중 선택해 주세요."
        await websocket.send(message)
        await synthesize_speech(message, websocket, activate_mic=True)
    
    async def completion_message(self, websocket):
        message = "서류 발급이 완료되었습니다. 감사합니다."
        await websocket.send(message)
        await synthesize_speech(message, websocket, activate_mic=False)