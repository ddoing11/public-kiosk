# mysite/service_consumers.py
import json
import asyncio
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from openai import OpenAI
from django.db import models

logger = logging.getLogger('kiosk')
client = OpenAI(api_key=getattr(settings, 'OPENAI_API_KEY', ''))

# 모델을 여기서 직접 정의 (app_label 명시)
class MedicalCertificate(models.Model):
    """진료확인서"""
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=[('M', '남성'), ('F', '여성')])
    birth_date = models.DateField()
    contact = models.CharField(max_length=20)
    address = models.TextField()
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', '대기중'), ('processing', '처리중'), 
        ('completed', '완료'), ('failed', '실패')
    ], default='pending')
    quantity = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'Medical_Certificate'
        app_label = 'mysite'
        managed = False  # 기존 테이블 사용

class Prescription(models.Model):
    """처방전"""
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=[('M', '남성'), ('F', '여성')])
    birth_date = models.DateField()
    contact = models.CharField(max_length=20)
    prescription_date = models.DateField()
    doctor_name = models.CharField(max_length=100)
    department = models.CharField(max_length=50)
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', '대기중'), ('processing', '처리중'), 
        ('completed', '완료'), ('failed', '실패')
    ], default='pending')
    quantity = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'prescription'
        app_label = 'mysite'
        managed = False

class MedicalReceipt(models.Model):
    """진료비영수증"""
    patient_name = models.CharField(max_length=100)
    patient_id = models.CharField(max_length=50)
    gender = models.CharField(max_length=1, choices=[('M', '남성'), ('F', '여성')])
    birth_date = models.DateField()
    receipt_date = models.DateField()
    requested_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=[
        ('pending', '대기중'), ('processing', '처리중'), 
        ('completed', '완료'), ('failed', '실패')
    ], default='pending')
    quantity = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'medical_receipt'
        app_label = 'mysite'
        managed = False

class ServiceWebSocketConsumer(AsyncWebsocketConsumer):
    """서비스 선택 페이지 전용 WebSocket Consumer"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.doc_type_model_map = {
            "진료확인서": MedicalCertificate,
            "처방전": Prescription,
            "진료영수증": MedicalReceipt,
            "진료비영수증": MedicalReceipt,  # 별칭
            "소견서": None,  # 아직 모델 없음
            "진단서": None,  # 아직 모델 없음
        }
    
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
            # TTS 메시지 전송
            guidance_text = "원하시는 서류를 말씀해주세요. 진료확인서, 처방전, 진료영수증 중에서 선택하실 수 있습니다."
            await self.send_message('tts.say', {'text': guidance_text})
            
            # TTS 예상 시간 후 띵 소리 및 마이크 활성화
            await asyncio.sleep(4)
            await self.send_message('audio.ding')
            await asyncio.sleep(0.5)
            await self.send_message('mic.on')
            
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
                await self.send_message('tts.say', {'text': '죄송합니다. 다시 한번 서류명을 말씀해주세요.'})
                await asyncio.sleep(3)
                await self.send_message('audio.ding')
                await asyncio.sleep(0.5)
                await self.send_message('mic.on')
                return
            
            # 문서 종류 인식 성공
            await self.send_message('document.recognized', {
                'document_type': doc_type,
                'original_text': text
            })
            
            # DB 조회
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
- 소견서
- 진단서

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
        """서비스 직접 선택 처리"""
        try:
            await self.query_database(service_name)
        except Exception as e:
            logger.error(f"Service selection error: {str(e)}")
            await self.send_error("서비스 처리 중 오류가 발생했습니다.")
    
    async def query_database(self, doc_type):
        """데이터베이스 조회 (시뮬레이션)"""
        try:
            model_class = self.doc_type_model_map.get(doc_type)
            
            if not model_class:
                # 모델이 없는 경우 (소견서, 진단서 등)
                await self.send_message('db.results', {
                    'results': [],
                    'message': f'{doc_type}은(는) 준비 중인 서비스입니다.'
                })
                return
            
            # 실제 DB 조회 대신 더미 데이터 사용 (테스트용)
            # 실제 환경에서는 아래 주석 해제하고 더미 데이터 부분 삭제
            """
            # 실제 DB 조회 코드
            records = model_class.objects.all()[:10]
            results = []
            for record in records:
                # ... (기존 코드)
            """
            
            # 더미 데이터 생성 (테스트용)
            results = []
            if doc_type == "진료확인서":
                results = [
                    {
                        '환자명': '홍길동',
                        '환자번호': '2024****',
                        '성별': '남성',
                        '생년월일': '1990-01-01',
                        '연락처': '010****',
                        '요청시간': '2024-01-15 10:30',
                        '상태': '완료'
                    },
                    {
                        '환자명': '김영희',
                        '환자번호': '2024****',
                        '성별': '여성',
                        '생년월일': '1985-05-15',
                        '연락처': '010****',
                        '요청시간': '2024-01-15 11:00',
                        '상태': '대기중'
                    }
                ]
            elif doc_type == "처방전":
                results = [
                    {
                        '환자명': '이철수',
                        '환자번호': '2024****',
                        '처방일': '2024-01-15',
                        '담당의': '김의사',
                        '진료과': '내과',
                        '상태': '완료'
                    }
                ]
            elif doc_type in ["진료영수증", "진료비영수증"]:
                results = [
                    {
                        '환자명': '박민수',
                        '환자번호': '2024****',
                        '영수일': '2024-01-15',
                        '성별': '남성',
                        '생년월일': '1975-03-20',
                        '상태': '완료'
                    }
                ]
            
            # 결과 전송
            await self.send_message('db.results', {'results': results})
            
            # 음성 안내
            if results:
                await self.send_message('tts.say', {
                    'text': f'{doc_type} 조회 결과 {len(results)}건을 찾았습니다. 발급을 원하시면 확인 버튼을 눌러주세요.'
                })
            else:
                await self.send_message('tts.say', {
                    'text': f'{doc_type} 조회 결과가 없습니다. 다른 서류를 선택해주세요.'
                })
            
        except Exception as e:
            logger.error(f"Database query error: {str(e)}")
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
        await self.send_message('tts.say', {'text': error_message})