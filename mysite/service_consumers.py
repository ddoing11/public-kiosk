# mysite/service_consumers.py
import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from openai import OpenAI
from django.db import models
from channels.db import database_sync_to_async
from datetime import date, datetime


logger = logging.getLogger('kiosk')
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

# ✅ Medical_Certificate: id, patient_name, patient_id, gender, birth_date, contact, address
class MedicalCertificate(models.Model):
    patient_name = models.CharField(max_length=100, null=True, blank=True)
    patient_id   = models.CharField(max_length=50,  null=True, blank=True)
    gender       = models.CharField(max_length=1,   null=True, blank=True, choices=[('M','남성'),('F','여성')])
    birth_date   = models.DateField(null=True, blank=True)
    contact      = models.CharField(max_length=20,  null=True, blank=True)
    address      = models.TextField(null=True, blank=True)

    class Meta:
        db_table  = 'Medical_Certificate'
        app_label = 'mysite'
        managed   = False

# ✅ prescription: id, patient_name, patient_id, gender, birth_date, contact, prescription_date, doctor_name, department, hospital_name, notes
class Prescription(models.Model):
    patient_name      = models.CharField(max_length=100, null=True, blank=True)
    patient_id        = models.CharField(max_length=50,  null=True, blank=True)
    gender            = models.CharField(max_length=1,   null=True, blank=True, choices=[('M','남성'),('F','여성')])
    birth_date        = models.DateField(null=True, blank=True)
    contact           = models.CharField(max_length=20,  null=True, blank=True)
    prescription_date = models.DateField(null=True, blank=True)
    doctor_name       = models.CharField(max_length=100, null=True, blank=True)
    department        = models.CharField(max_length=50,  null=True, blank=True)
    hospital_name     = models.CharField(max_length=100, null=True, blank=True)
    notes             = models.TextField(null=True, blank=True)

    class Meta:
        db_table  = 'prescription'
        app_label = 'mysite'
        managed   = False

# ✅ medical_receipt: id, patient_name, patient_id, gender, birth_date, receipt_date
class MedicalReceipt(models.Model):
    patient_name = models.CharField(max_length=100, null=True, blank=True)
    patient_id   = models.CharField(max_length=50,  null=True, blank=True)
    gender       = models.CharField(max_length=1,   null=True, blank=True, choices=[('M','남성'),('F','여성')])
    birth_date   = models.DateField(null=True, blank=True)
    receipt_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table  = 'medical_receipt'
        app_label = 'mysite'
        managed   = False


class ServiceWebSocketConsumer(AsyncWebsocketConsumer):
    """서비스 선택 페이지 전용 WebSocket Consumer"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_type_model_map = {
            "진료확인서": MedicalCertificate,
            "처방전": Prescription,
            "진료영수증": MedicalReceipt,
        }
    
        self.FIELD_MAP = {
            "진료확인서": [
                ("환자명",   "patient_name"),
                ("환자번호", "patient_id"),
                ("성별",     "gender"),
                ("생년월일", "birth_date"),
                ("연락처",   "contact"),
                ("주소",     "address"),
            ],
            "처방전": [
                ("환자명",   "patient_name"),
                ("환자번호", "patient_id"),
                ("성별",     "gender"),
                ("생년월일", "birth_date"),
                ("연락처",   "contact"),
                ("처방일",   "prescription_date"),
                ("담당의",   "doctor_name"),
                ("진료과",   "department"),
                ("병원명",   "hospital_name"),
                ("비고",     "notes"),
            ],
            "진료영수증": [
                ("환자명",   "patient_name"),
                ("환자번호", "patient_id"),
                ("성별",     "gender"),
                ("생년월일", "birth_date"),
                ("영수일",   "receipt_date"),
            ],
        }

    def _fmt(self, v):
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M:%S")
        if isinstance(v, date):
            return v.strftime("%Y-%m-%d")
        return v



    async def connect(self):
        await self.accept()
        logger.info("Service WebSocket connected")
        
        # 연결 즉시 자동으로 음성 안내 시작
        await asyncio.sleep(1)
        await self.start_voice_guidance()
    
    async def disconnect(self, close_code):
        logger.info(f"Service WebSocket disconnected: {close_code}")
    
    async def receive(self, text_data):
        """클라이언트로부터 메시지 수신"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'voice.input':
                # 음성 입력 처리 (GPT로 서류 종류 판단)
                await self.process_voice_input(data.get('text', ''))
            
            elif message_type == 'service.select':
                # 직접 선택한 서비스 처리
                await self.process_service_selection(data.get('service', ''))
            
            else:
                logger.warning(f"Unknown message type: {message_type}")
                
        except json.JSONDecodeError:
            logger.error("Invalid JSON received")
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}")
            await self.send_error("처리 중 오류가 발생했습니다.")
    
    async def start_voice_guidance(self):
        """음성 안내 시작"""
        try:
            # 마이크 먼저 끄기
            await self.send_message('mic.off')
            
            # Azure TTS로 안내 멘트 출력
            guidance_text = "원하시는 서류를 말씀해주세요. 진료확인서, 처방전, 진료영수증 중에서 선택하실 수 있습니다."
            
            # Azure TTS 실행 (내부에서 띵 소리 + 마이크 ON 처리)
            from .utils import azure_text_to_speech
            await azure_text_to_speech(guidance_text, self)
            
            logger.info("Service guidance completed")
            
        except Exception as e:
            logger.error(f"Voice guidance error: {str(e)}")
    
    async def process_voice_input(self, text):
        """음성 입력을 GPT로 분석하여 서류 종류 판단"""
        try:
            # 마이크 끄기
            await self.send_message('mic.off')
            
            # GPT를 이용한 문서 종류 판단
            doc_type = await self.analyze_document_type(text)
            
            if doc_type == "알수없음":
                # 다시 안내
                from .utils import azure_text_to_speech
                await azure_text_to_speech('죄송합니다. 다시 한번 서류명을 말씀해주세요.', self)
                return
            
            # 문서 종류 인식 성공
            await self.send_message('document.recognized', {
                'document_type': doc_type,
                'original_text': text
            })
            
            # DB 조회 및 음성 안내
            await self.query_database(doc_type)
            
        except Exception as e:
            logger.error(f"Voice processing error: {str(e)}")
            await self.send_error("음성 처리 중 오류가 발생했습니다.")
    
    async def analyze_document_type(self, text):
        """GPT를 사용하여 문서 종류 판단"""
        try:
            prompt = f"""사용자의 발화 내용: "{text}"

위 발화 내용에서 사용자가 요청하는 문서 종류를 다음 목록에서 정확히 하나만 골라 응답해 주십시오:
- 진료확인서
- 처방전
- 진료영수증


만약 목록에 해당하는 문서가 없거나, 발화 내용이 불분명하여 판단할 수 없는 경우에는, 
다른 어떤 말도 하지 말고 "알수없음" 이라고만 응답해 주십시오.

응답은 반드시 위 목록의 정확한 명칭 또는 "알수없음" 중 하나여야 합니다."""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "당신은 문서 종류를 분류하는 도우미입니다."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.1,
                max_tokens=20
            )
            
            doc_type = response.choices[0].message.content.strip()
            logger.info(f"GPT identified document type: {doc_type} from input: {text}")
            
            return doc_type
            
        except Exception as e:
            logger.error(f"GPT analysis error: {str(e)}")
            return "알수없음"
    
    async def process_service_selection(self, service_name):
        """서비스 직접 선택 처리 (카드 클릭)"""
        try:
            # 마이크 끄기
            await self.send_message('mic.off')
            
            # DB 조회 및 음성 안내
            await self.query_database(service_name)
            
        except Exception as e:
            logger.error(f"Service selection error: {str(e)}")
            await self.send_error("서비스 처리 중 오류가 발생했습니다.")
    
    # ServiceWebSocketConsumer 안의 query_database() 전체 교체
    async def query_database(self, doc_type):
        """사용자가 말한 서류 타입의 전체 목록을 DB에서 조회"""
        try:
            model_class = self.doc_type_model_map.get(doc_type)
            from .utils import azure_text_to_speech

            if not model_class:
                await self.send_message('db.results', {
                    'results': [],
                    'message': f'{doc_type}은(는) 준비 중인 서비스입니다.'
                })
                await azure_text_to_speech(f'{doc_type}은 준비 중인 서비스입니다.', self)
                return

            field_map = self.FIELD_MAP.get(doc_type, [])
            results = await self.fetch_all_generic(model_class, field_map, limit=100)

            # 결과 전송
            await self.send_message('db.results', {'results': results})

            # 음성 안내
            if results:
                await azure_text_to_speech(f"{doc_type} 리스트입니다. 총 {len(results)}건의 결과를 찾았습니다.", self)
                await asyncio.sleep(1)
                await azure_text_to_speech("날짜를 선택해 주세요.", self)
            else:
                await azure_text_to_speech(f"{doc_type} 조회 결과가 없습니다. 다른 서류를 선택해 주세요.", self)

        except Exception:
            logger.exception("Database query error")
            await self.send_error("데이터베이스 조회 중 오류가 발생했습니다.")

    async def send_message(self, msg_type, data=None):
        """클라이언트로 메시지 전송"""
        message = {'type': msg_type}
        if data:
            message.update(data)
        await self.send(text_data=json.dumps(message))
    
    async def send_error(self, error_message):
        """에러 메시지 전송"""
        await self.send_message('error', {'message': error_message})
        from .utils import azure_text_to_speech
        await azure_text_to_speech(error_message, self)


    @database_sync_to_async
    def fetch_all_generic(self, model_class, field_map, limit=100):
        # 존재 가능성 있는 날짜 컬럼 우선 정렬, 없으면 id 역순
        order_fields = []
        candidates = ["prescription_date", "receipt_date", "birth_date", "id"]
        model_fields = {f.attname for f in model_class._meta.get_fields() if hasattr(f, "attname")}
        for c in candidates:
            if c in model_fields:
                order_fields.append(f"-{c}" if c != "id" else "-id")
                break

        qs = model_class.objects.all()
        if order_fields:
            qs = qs.order_by(*order_fields)

        rows = []
        for obj in qs[:limit]:
            item = {}
            for out_key, attr in field_map:
                if attr == "gender":
                    g = getattr(obj, "gender", None)
                    item[out_key] = "남성" if str(g or "").upper().startswith("M") else ("여성" if g else None)
                else:
                    item[out_key] = self._fmt(getattr(obj, attr, None))
            rows.append(item)
        return rows
