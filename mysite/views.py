import json
import re
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import os, requests
from django.conf import settings

# 로거 설정
logger = logging.getLogger('kiosk')

def idle(request):
    """대기화면 템플릿 반환 (터치만)"""
    return render(request, 'kiosk/idle.html')

def main(request):
    """메인화면 템플릿 반환 (주민번호 입력 + 상담)"""
    return render(request, 'kiosk/main.html')

@csrf_exempt
def id_verify(request):
    """주민번호 앞 6자리 검증 API"""
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'POST method required'}, status=405)
    
    try:
        data = json.loads(request.body)
        value = data.get('value', '').strip()
        
        # 정규식 검증: 6자리 숫자만 허용
        if not re.match(r'^\d{6}$', value):
            logger.info(f"Invalid format: {value[:2]}****")
            return JsonResponse({'ok': False, 'error': 'Invalid format'})
        
        # 날짜 유효성 검사 (느슨한 체크)
        year = int(value[:4])
        month = int(value[4:6])
        
        if not (1900 <= year <= 2099):
            logger.info(f"Invalid year: {value[:2]}****")
            return JsonResponse({'ok': False, 'error': 'Invalid year'})
        
        if not (1 <= month <= 12):
            logger.info(f"Invalid month: {value[:2]}****")
            return JsonResponse({'ok': False, 'error': 'Invalid month'})
        
        # 검증 성공 로그 (마스킹 처리)
        logger.info(f"ID verification success: {value[:2]}****")
        
        # 필요시 세션에 메타 정보만 저장 (원문은 저장 안함)
        # request.session["age_hint"] = True  # 선택사항
        
        return JsonResponse({'ok': True})
        
    except json.JSONDecodeError:
        logger.error("Invalid JSON in id_verify request")
        return JsonResponse({'ok': False, 'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        logger.error(f"Error in id_verify: {str(e)}")
        return JsonResponse({'ok': False, 'error': 'Server error'}, status=500)

def services(request):
    """서비스 선택 페이지 (스켈레톤)"""
    return render(request, 'kiosk/services.html')

def speech_token(request):
    key = getattr(settings, 'AZURE_SPEECH_KEY', '')
    region = getattr(settings, 'AZURE_SPEECH_REGION', '')
    if not key or not region:
        return JsonResponse({"error": "Azure speech key/region missing."}, status=500)

    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    try:
        r = requests.post(url, headers={"Ocp-Apim-Subscription-Key": key}, timeout=5)
        r.raise_for_status()
        return JsonResponse({"token": r.text, "region": region})
    except Exception as e:
        return JsonResponse({"error": f"Token issue failed: {e}"}, status=500)
    