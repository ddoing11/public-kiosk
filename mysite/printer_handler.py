import os
import asyncio
import json
import platform
import subprocess
from datetime import datetime
from django.conf import settings
from asgiref.sync import sync_to_async  # ✅ 비동기 context 내 ORM 호출 안전화

# --- 기존 DB 모델 import (이미 있는 모델들 사용)
from .models import Medical_Certificate, Prescription, MedicalReceipt


class PrinterHandler:
    """
    실제 프린터 연동 및 발급 처리 담당 핸들러
    기존 DB에 저장된 서류 파일 경로를 불러와 인쇄함.
    """

    def __init__(self):
        self.media_root = getattr(settings, "MEDIA_ROOT", os.path.join(settings.BASE_DIR, "media"))

    # -------------------------------
    async def prepare_print_job(self, websocket, doc_type: str, date_str: str):
        """
        문서 발급 전체 프로세스:
        1) DB에서 문서 조회
        2) 프린터 출력
        3) 결과 TTS 반환
        """
        print(f"🖨️ [발급요청] {doc_type} ({date_str})")

        # ✅ ORM 조회는 async-safe 하게 실행
        file_path = await sync_to_async(self._get_file_path_from_db)(doc_type, date_str)

        if not file_path:
            await websocket.send(json.dumps({
                "type": "tts.text",
                "text": f"{date_str}의 {doc_type} 문서를 찾을 수 없습니다. 다시 시도해주세요."
            }))
            return False  # ✅ 실패 반환

        # 2️⃣ 안내: 발급 중
        await websocket.send(json.dumps({
            "type": "tts.text",
            "text": f"{date_str}의 {doc_type}을 발급하고 있습니다. 잠시만 기다려주세요."
        }))

        # 3️⃣ 실제 인쇄
        success = await self._print_document(file_path)

        # 4️⃣ 완료 안내
        if success:
            msg = f"{date_str}의 {doc_type} 발급이 완료되었습니다. 다른 서류가 필요하시면 말씀해주세요."
            print(f"✅ [인쇄성공] {file_path}")
        else:
            msg = f"{doc_type} 인쇄 중 오류가 발생했습니다. 관리자에게 문의해주세요."
            print(f"❌ [인쇄실패] {file_path}")

        await websocket.send(json.dumps({
            "type": "tts.text",
            "text": msg
        }))

        return success  # ✅ 성공 여부 반환

    # -------------------------------
    def _get_file_path_from_db(self, doc_type: str, date_str: str) -> str | None:
        """
        기존 DB의 테이블에서 문서 파일 경로를 가져옴.
        각 테이블의 'issued_date' 또는 'receipt_date' 기준으로 검색.
        """
        parsed_date = self._normalize_date(date_str)
        model = None

        # 문서 타입에 따라 모델 매핑
        if "확인서" in doc_type:
            model = Medical_Certificate
        elif "처방" in doc_type:
            model = Prescription
        elif "영수증" in doc_type:
            model = MedicalReceipt

        if not model:
            print(f"❌ 알 수 없는 문서 유형: {doc_type}")
            return None

        try:
            # 예: issued_date 또는 receipt_date 기준으로 탐색
            doc = model.objects.filter(receipt_date__startswith=parsed_date).last()

            if not doc:
                print(f"❌ {parsed_date}의 {doc_type} 데이터 없음")
                return None

            # 파일 경로 필드명 예시: file_path, pdf_file 등 (필요 시 수정)
            file_field = getattr(doc, "file_path", None) or getattr(doc, "pdf_file", None)
            if not file_field:
                print(f"❌ 문서에 파일 경로 필드 없음: {doc_type}")
                return None

            # 절대경로로 변환
            file_path = os.path.join(self.media_root, str(file_field))
            if os.path.exists(file_path):
                return file_path
            else:
                print(f"❌ 파일이 존재하지 않음: {file_path}")
                return None

        except Exception as e:
            print(f"❌ DB 조회 오류: {e}")
            return None

    # -------------------------------
    async def _print_document(self, file_path: str) -> bool:
        """
        실제 프린터 출력 수행 (Windows: os.startfile, mac/Linux: lpr)
        """
        try:
            if not os.path.exists(file_path):
                print(f"❌ 파일 없음: {file_path}")
                return False

            system = platform.system().lower()
            if "windows" in system:
                os.startfile(file_path, "print")
            else:
                subprocess.run(["lpr", file_path], check=True)

            await asyncio.sleep(2.5)
            return True

        except Exception as e:
            print(f"❌ 인쇄 오류: {e}")
            return False

    # -------------------------------
    def _normalize_date(self, spoken_date: str) -> str:
        """
        음성 입력 날짜 ('7월 16일') → YYYY-MM-DD 변환
        """
        spoken_date = spoken_date.replace(" ", "").replace("일", "")
        year = datetime.now().year
        try:
            if "월" in spoken_date:
                month, day = spoken_date.split("월")
                month, day = int(month), int(day)
                return f"{year}-{month:02d}-{day:02d}"
        except Exception:
            pass
        return f"{year}-{spoken_date}"
