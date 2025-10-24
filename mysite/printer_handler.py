import os
import asyncio
import json
import platform
import subprocess
import re
import sys
import io
from datetime import datetime, date
from django.conf import settings
from asgiref.sync import sync_to_async
from kiosk.models import MedicalReceipt, Prescription, Medical_Certificate
from utils.logger import logger


# ✅ Windows 콘솔 인코딩 문제(📂❌🖨️ 등 이모지 포함 로그) 방지
sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8')


class PrinterHandler:
    """
    실제 프린터 연동 및 발급 처리 담당 핸들러
    기존 DB에 저장된 서류 파일 경로를 불러와 인쇄함.
    """

    def __init__(self):
        self.media_root = getattr(settings, "MEDIA_ROOT", os.path.join(settings.BASE_DIR, "media"))

    # =======================================================
    async def prepare_print_job(self, websocket, doc_type: str, patient_id: str, date_str: str):
        """
        문서 발급 전체 프로세스:
        1) DB에서 문서 조회
        2) 프린터 출력
        3) 결과 TTS 반환
        """
        logger.info(f"🖨️ [발급요청] {doc_type} ({date_str})")

        file_path = await sync_to_async(self._get_file_path_from_db)(doc_type, patient_id, date_str)
        if not file_path:
            await websocket.send(json.dumps({
                "type": "tts.text",
                "text": f"{date_str}의 {doc_type} 문서를 찾을 수 없습니다. 다시 시도해주세요."
            }))
            return False

        # 안내: 발급 중
        await websocket.send(json.dumps({
            "type": "tts.text",
            "text": f"{date_str}의 {doc_type}을 발급하고 있습니다. 잠시만 기다려주세요."
        }))

        success = await self._print_document(file_path)

        # 완료 안내
        msg = (f"{date_str}의 {doc_type} 발급이 완료되었습니다. 다른 서류가 필요하시면 말씀해주세요."
               if success else f"{doc_type} 인쇄 중 오류가 발생했습니다. 관리자에게 문의해주세요.")
        logger.info(f"{'✅' if success else '❌'} [인쇄{'성공' if success else '실패'}] {file_path}")

        await websocket.send(json.dumps({"type": "tts.text", "text": msg}))
        return success

    # =======================================================
    def _get_file_path_from_db(self, doc_type: str, patient_id: str, date_str: str):
        """
        DB에서 문서 유형 + 날짜로 파일 경로를 탐색
        (날짜 형식 및 하이픈 포함/비포함 ID 모두 탐색)
        """
        parsed_date = self._normalize_date(date_str)
        if not parsed_date:
            logger.error(f"❌ 날짜 파싱 실패: '{date_str}'")
            return None

        model_map = {
            "진료영수증": MedicalReceipt,
            "처방전": Prescription,
            "진료확인서": Medical_Certificate,
        }
        model = model_map.get(doc_type)
        if not model:
            logger.error(f"❌ 지원되지 않는 문서 유형: {doc_type}")
            return None

        # 날짜 필드명 매핑
        date_field = {
            "진료영수증": "receipt_date",
            "처방전": "prescription_date",
            "진료확인서": "birth_date",  # 진료확인서에는 날짜 필드 없음
        }[doc_type]

        date_obj = datetime.strptime(parsed_date, "%Y-%m-%d").date()
        filters = {
            f"{date_field}__year": date_obj.year,
            f"{date_field}__month": date_obj.month,
            f"{date_field}__day": date_obj.day,
        }
        if patient_id:
            filters["patient_id"] = patient_id

        doc = model.objects.filter(**filters).last()
        if not doc:
            logger.error(f"❌ {parsed_date}의 {doc_type} 데이터 없음")
            return None

        # 파일명 규칙에 따라 candidates 탐색
        folder_map = {
            "진료확인서": "Medical_Certificate_DOC",
            "처방전": "Prescription_DOC",
            "진료영수증": "Medical_receipt_DOC",
        }
        folder_name = folder_map.get(doc_type)
        issue_date_str = parsed_date.replace("-", "")
        patient_id_clean = patient_id.replace("-", "")

        candidate_files = [
            f"{patient_id_clean}_receipt_{issue_date_str}.pdf",
            f"{patient_id}_receipt_{issue_date_str}.pdf",
            f"{patient_id_clean}_certificate_{issue_date_str}.pdf",
            f"{patient_id}_certificate_{issue_date_str}.pdf",
        ]
        

        for filename in candidate_files:
            file_path = os.path.join(os.path.dirname(settings.BASE_DIR), "documents", folder_name, filename)


            if os.path.exists(file_path):
                logger.info(f"📂 찾는 파일 경로: {file_path}")
                return file_path

        logger.error(f"❌ 파일을 찾을 수 없음 (모든 후보): {candidate_files}")
        return None

    # =======================================================
    async def _print_document(self, file_path: str):
        """PDF 파일을 실제 인쇄 (시뮬레이션 버전)"""
        try:
            # 실제 프린터 명령 대신 로그로 대체
            logger.info(f"🖨️ 실제 인쇄 명령 실행 중: {file_path}")
            await asyncio.sleep(1)
            return True
        except Exception as e:
            logger.error(f"❌ 인쇄 오류: {str(e)}")
            return False

    # =======================================================
    def _normalize_date(self, date_text: str):
        """
        사용자가 말한 날짜(예: '7월 16일', '2025년 7월 16일', '7/16', '20250716')를
        DB DateField 형식(YYYY-MM-DD)으로 변환
        """
        numbers = re.findall(r'\d+', date_text)
        today = date.today()
        year, month, day = today.year, None, None

        if len(numbers) == 3:      # 예: 2025 7 16
            year, month, day = map(int, numbers)
        elif len(numbers) == 2:    # 예: 7월 16일
            month, day = map(int, numbers)
        elif len(numbers) == 1:    # 예: 16일
            month, day = today.month, int(numbers[0])

        if not (month and day):
            return None

        try:
            normalized = date(year, month, day)
            return normalized.strftime("%Y-%m-%d")
        except ValueError:
            return None
