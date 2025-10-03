import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from openai import OpenAI
from channels.db import database_sync_to_async
from datetime import date, datetime
from .models import Medical_Certificate, Prescription, MedicalReceipt
from .utils import get_gpt_streaming_response, SYSTEM_PROMPT

logger = logging.getLogger('kiosk')
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

class KioskWebSocketConsumer(AsyncWebsocketConsumer):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_state = {}

    async def connect(self):
        await self.accept()
        self.client_state = {'step': 'idle'}
        logger.info("WebSocket client connected")
        await self.start_automatic_guidance()

    async def disconnect(self, close_code):
        logger.info(f"WebSocket client disconnected: {close_code}")

    async def start_automatic_guidance(self):
        try:
            self.client_state['step'] = 'prompting'
            await self.send_message('mic.off')
            guidance_text = "주민번호 앞 여섯자리를 입력하여 서류 출력 서비스로 이동해주세요."
            await self.send_message('tts.text', {'text': guidance_text})
            
            self.client_state['step'] = 'listening'
            await self.send_message('status', {'state': 'listening'})
            logger.info("Automatic guidance sent. State -> listening")
        except Exception as e:
            logger.error(f"Automatic guidance error: {str(e)}")

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'user.confirmation_start':
                await self.start_user_confirmation(data.get('patient'))
            elif message_type == 'stt.result':
                await self.handle_stt_result(data.get('text', ''))
            else:
                logger.warning(f"Unknown message type: {message_type}")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")

    async def start_user_confirmation(self, patient):
        if patient:
            self.client_state['patient_to_confirm'] = patient
            self.client_state['step'] = 'confirming_user'
            confirmation_text = f"{patient.get('patient_name')} 님이 맞으시면, '본인 확인'이라고 말씀해주세요."
            await self.send_message('tts.text', {'text': confirmation_text})
            await self.send_message('state.update', {'step': 'confirming_user'})
            logger.info(f"User confirmation started: {confirmation_text}")
        else:
            await self.send_message('error', {'message': "확인할 환자 정보가 없습니다."})

    async def handle_stt_result(self, text):
        text = text.strip().lower()
        current_step = self.client_state.get('step', 'idle')
        logger.info(f"STT Result: '{text}', State: '{current_step}'")

        if current_step == 'confirming_user':
            if any(word in text for word in ['본인', '확인', '맞', '네', '예']):
                patient = self.client_state.get('patient_to_confirm')
                await self.send_message('user.confirmed', {'patient': patient})
            elif any(word in text for word in ['아니', '틀렸', '다른']):
                await self.send_message('user.confirmation_failed')
                await self.send_message('tts.text', {'text': '다른 이름을 말씀해주세요.'})
            else:
                await self.send_message('tts.text', {'text': "'본인 확인' 또는 '아니요'로 말씀해주세요."})
                return

            self.client_state['step'] = 'listening'
            await self.send_message('state.update', {'step': 'listening'})
            return

        if current_step == 'listening' and len(text) > 0:
            await self.send_message('stt.forward_to_input', {'text': text})
            return

        logger.warning(f"STT result received in unhandled state: {current_step}")

    async def send_message(self, msg_type, data=None):
        message = {'type': msg_type}
        if data:
            message.update(data)
        await self.send(text_data=json.dumps(message))


class ServiceWebSocketConsumer(AsyncWebsocketConsumer):
    """GPT 기반 스마트 서류 인식 Consumer"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_patient = None
        self.client_state = {}
        self.doc_type_model_map = {
            "진료확인서": Medical_Certificate,
            "처방전": Prescription,
            "진료영수증": MedicalReceipt,
        }
        self.FIELD_MAP = {
            "진료확인서": [("환자명", "patient_name"), ("환자번호", "patient_id"), ("성별", "gender"), ("생년월일", "birth_date"), ("연락처", "contact"), ("주소", "address")],
            "처방전": [("환자명", "patient_name"), ("환자번호", "patient_id"), ("성별", "gender"), ("생년월일", "birth_date"), ("연락처", "contact"), ("처방일", "prescription_date"), ("담당의", "doctor_name"), ("진료과", "department"), ("병원명", "hospital_name")],
            "진료영수증": [("환자명", "patient_name"), ("환자번호", "patient_id"), ("성별", "gender"), ("생년월일", "birth_date"), ("영수일", "receipt_date")],
        }

    def _fmt(self, v):
        if isinstance(v, datetime): return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, date): return v.strftime("%Y-%m-%d")
        return v

    async def connect(self):
        await self.accept()
        self.client_state = {'step': 'listening'}
        logger.info("🚀 Service WebSocket connected")
        self.selected_patient = self.scope['session'].get('selected_patient', None)
        if self.selected_patient:
            logger.info(f"👤 인증된 사용자: {self.selected_patient.get('patient_name')}")
            await self.send_message('patient.info', {'patient': self.selected_patient})
        await asyncio.sleep(1)
        await self.start_voice_guidance()
    
    async def disconnect(self, close_code):
        logger.info(f"Service WebSocket disconnected: {close_code}")
    
    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            if message_type == 'voice.input':
                await self.process_voice_input(data.get('text', ''))
            elif message_type == 'service.select':
                if data.get('service') == '상담':
                    await self.start_consultation()
                else:
                    await self.process_service_selection(data.get('service', ''))
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
    
    async def start_voice_guidance(self):
        guidance_text = "진료확인서, 처방전, 진료영수증 중 원하는 서류를 말씀해주세요."
        await self.send_message('tts.text', {'text': guidance_text})

    async def analyze_input_with_gpt(self, text):
        """GPT를 이용해 사용자 입력을 분석"""
        logger.info(f"🤖 GPT 분석 시작: '{text}'")
        
        try:
            prompt = f"""
사용자 입력: "{text}"

다음 중 하나로 분류해주세요:
1. 진료확인서 - 회사, 학교, 직장 제출용 서류나 진료확인서/증명서를 요청하는 경우
2. 처방전 - 처방전, 처방서, 약 관련 서류를 요청하는 경우  
3. 진료영수증 - 영수증, 진료영수증, 보험 관련 서류를 요청하는 경우
4. 상담 - 문의, 질문, 도움, 설명을 요청하거나 어떤 서류인지 묻는 경우
5. 기타 - 위에 해당하지 않는 경우

정확히 하나의 단어로만 답변하세요: 진료확인서, 처방전, 진료영수증, 상담, 기타
"""
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.1
            )
            
            result = response.choices[0].message.content.strip()
            logger.info(f"🤖 GPT 분석 결과: '{result}'")
            
            return result
            
        except Exception as e:
            logger.error(f"GPT 분석 오류: {str(e)}")
            return "기타"

    async def process_voice_input(self, text):
        """GPT 기반 스마트 음성 입력 처리"""
        current_step = self.client_state.get('step')
        logger.info(f"🎤 음성 입력: '{text}' (상태: {current_step})")
        
        try:
            # GPT로 입력 분석
            analysis_result = await self.analyze_input_with_gpt(text)
            logger.info(f"📊 분석 결과: {analysis_result}")
            
            # 서류 타입인 경우 바로 조회
            if analysis_result in ['진료확인서', '처방전', '진료영수증']:
                logger.info(f"📋 서류 조회: {analysis_result}")
                await self.send_message('document.recognized', {'document_type': analysis_result})
                await self.query_database(analysis_result)
                return
            
            # 상담 요청인 경우
            if analysis_result == '상담':
                logger.info("💬 상담 모드 시작")
                await self.start_smart_consultation(text)
                return
            
            # 상담 모드에서의 추가 입력 처리
            if current_step == 'advising':
                logger.info("💬 상담 중 추가 입력 처리")
                await self.continue_smart_consultation(text)
                return
            
            # 기타인 경우
            logger.info("❓ 알 수 없는 입력")
            await self.send_message('tts.text', {'text': "진료확인서, 처방전, 진료영수증 중 하나를 말씀해주세요."})
            
        except Exception as e:
            logger.error(f"음성 입력 처리 오류: {str(e)}")
            await self.send_message('tts.text', {'text': "다시 말씀해주세요."})

    async def start_smart_consultation(self, user_input):
        """스마트 상담 시작 - 질문에 따라 바로 서류 추천"""
        logger.info(f"🧠 스마트 상담 시작: '{user_input}'")
        self.client_state['step'] = 'advising'
        await self.send_message('status', {'state': 'advising'})
        
        # 서류 추천을 위한 GPT 분석
        recommendation = await self.get_document_recommendation(user_input)
        
        if recommendation != '기타':
            # 바로 서류 추천하고 조회
            messages = {
                '진료확인서': '회사 제출용으로는 진료확인서가 필요합니다.',
                '처방전': '약국에서는 처방전이 필요합니다.',
                '진료영수증': '보험 청구용으로는 진료영수증이 필요합니다.'
            }
            await self.send_message('tts.text', {'text': messages[recommendation]})
            await self.query_database(recommendation)
        else:
            # 일반 상담
            await self.send_message('tts.text', {'text': '무엇을 도와드릴까요?'})

    async def get_document_recommendation(self, text):
        """사용자 질문에서 추천할 서류 분석"""
        logger.info(f"🔍 서류 추천 분석: '{text}'")
        
        try:
            prompt = f"""
사용자 질문: "{text}"

이 질문에서 추천할 서류를 판단해주세요:

- 회사, 직장, 학교, 제출, 증명 관련 → 진료확인서
- 약, 처방, 약국, 복용 관련 → 처방전  
- 보험, 청구, 환급, 비용, 돈 관련 → 진료영수증
- 위에 해당 없으면 → 기타

정확히 하나의 단어로만 답변: 진료확인서, 처방전, 진료영수증, 기타
"""
            
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=10,
                temperature=0.1
            )
            
            result = response.choices[0].message.content.strip()
            logger.info(f"📋 추천 결과: {result}")
            return result
            
        except Exception as e:
            logger.error(f"추천 분석 오류: {str(e)}")
            return "기타"

    async def continue_smart_consultation(self, user_input):
        """스마트 상담 진행"""
        logger.info(f"💭 스마트 상담 진행: '{user_input}'")
        
        # 먼저 서류 요청인지 다시 확인
        analysis_result = await self.analyze_input_with_gpt(user_input)
        if analysis_result in ['진료확인서', '처방전', '진료영수증']:
            logger.info(f"📋 상담 중 서류 요청: {analysis_result}")
            await self.send_message('document.recognized', {'document_type': analysis_result})
            await self.query_database(analysis_result)
            return
        
        # 서류 추천 가능한지 확인
        recommendation = await self.get_document_recommendation(user_input)
        if recommendation != '기타':
            logger.info(f"📋 상담 중 서류 추천: {recommendation}")
            messages = {
                '진료확인서': '회사 제출용으로는 진료확인서가 필요합니다.',
                '처방전': '약국에서는 처방전이 필요합니다.',
                '진료영수증': '보험 청구용으로는 진료영수증이 필요합니다.'
            }
            await self.send_message('tts.text', {'text': messages[recommendation]})
            await self.query_database(recommendation)
            return
        
        # 일반적인 GPT 상담
        await self.send_message('mic.off')
        try:
            chunks = []
            async for delta, is_done in get_gpt_streaming_response(user_input, system_prompt=SYSTEM_PROMPT):
                if not is_done:
                    chunks.append(delta)
                    await self.send_message('gpt.stream', {'delta': delta})
                else:
                    full_response = ''.join(chunks)
                    await self.send_message('gpt.stream', {'event': 'done', 'full_text': full_response})
        except Exception as e:
            logger.error(f"상담 오류: {str(e)}")
            await self.send_message('tts.text', {'text': '다시 질문해주세요.'})

    async def process_service_selection(self, service_name):
        await self.query_database(service_name)
    
    async def query_database(self, doc_type):
        try:
            logger.info(f"🗃️ DB 조회: {doc_type}")
            model_class = self.doc_type_model_map.get(doc_type)
            if not model_class:
                await self.send_message('tts.text', {'text': f'{doc_type}는 준비 중입니다.'})
                return

            patient_filter = {}
            if self.selected_patient:
                patient_filter['patient_id'] = self.selected_patient.get('patient_id')
            
            field_map = self.FIELD_MAP.get(doc_type, [])
            results = await self.fetch_all_generic(model_class, field_map, filters=patient_filter)
            logger.info(f"📊 조회 결과: {len(results)}건")
            
            await self.send_message('db.results', {'results': results})

            if results:
                await self.send_message('tts.text', {'text': f"{doc_type} {len(results)}건을 찾았습니다."})
            else:
                await self.send_message('tts.text', {'text': f"조회된 {doc_type}이 없습니다."})
        except Exception as e:
            logger.exception("DB query error")
            await self.send_error("DB 조회 중 오류가 발생했습니다.")
            
    @database_sync_to_async
    def fetch_all_generic(self, model_class, field_map, filters=None):
        qs = model_class.objects.all()
        if filters: qs = qs.filter(**filters)
        
        order_fields = []
        candidates = ["prescription_date", "receipt_date", "id"]
        model_fields = {f.name for f in model_class._meta.get_fields()}
        for c in candidates:
            if c in model_fields:
                order_fields.append(f"-{c}")
                break
        if order_fields: qs = qs.order_by(*order_fields)

        rows = [{out_key: self._fmt(getattr(obj, attr, None)) for out_key, attr in field_map} for obj in qs[:100]]
        return rows
    
    async def send_message(self, msg_type, data=None):
        message = {'type': msg_type, **(data or {})}
        await self.send(text_data=json.dumps(message))
    
    async def send_error(self, error_message):
        await self.send_message('error', {'message': error_message})