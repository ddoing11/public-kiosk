from .state_manager import HospitalStateManager
from .patient_handler import PatientInfoHandler
from .document_handler import DocumentHandler
from .audio_handler import HospitalAudioHandler
from .utils import clean_speech_input, is_positive_response, is_negative_response

class HospitalMessageRouter:
    def __init__(self):
        self.state_manager = HospitalStateManager()
        self.patient_handler = PatientInfoHandler()
        self.document_handler = DocumentHandler()
        self.audio_handler = HospitalAudioHandler()

    async def handle_connection(self, websocket):
        state = self.state_manager.add_client(websocket)
        await self.audio_handler.welcome_message(websocket)
        state["step"] = "await_voice_confirmation"
        return state

    async def process_message(self, websocket, message):
        state = self.state_manager.get_state(websocket)
        if not state:
            return

        cleaned_text = clean_speech_input(message)
        current_step = state.get("step", "init")
        
        # 단계별 처리
        if current_step == "await_voice_confirmation":
            if is_positive_response(cleaned_text):
                await self.audio_handler.patient_info_prompt(websocket)
                state["step"] = "await_patient_name"
            elif is_negative_response(cleaned_text):
                await websocket.send("일반 키오스크를 이용해 주세요.")
                state["step"] = "completed"
        
        elif current_step == "await_patient_name":
            new_step = await self.patient_handler.handle_patient_name_input(websocket, cleaned_text, state)
            state["step"] = new_step
        
        # ... 다른 단계들