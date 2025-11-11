import json
import re
import logging
from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import os, requests
from django.conf import settings
from auth_system.models import PatientList # auth_system의 모델을 가져옵니다.

# 로거 설정
logger = logging.getLogger('kiosk')

def idle(request):
    """대기화면 템플릿 반환 (터치만)"""
    return render(request, 'kiosk/idle.html')

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
    

def main(request):
    """메인화면 + 주민번호 1차 검색 처리"""
    if request.method == 'POST':
        birth_number = request.POST.get('birth_number', '').strip()
        
        # DEBUG 로그
        logger.error(f"DEBUG: Received birth_number='{birth_number}', Length={len(birth_number)}, IsDigit={birth_number.isdigit()}")
        
        if len(birth_number) == 13 and birth_number.isdigit():
            # 13자리수를 주민번호 규격에 맞게 재구성
            full_patient_id = f"{birth_number[:6]}-{birth_number[6:]}"
            
            # DB 조회: 주민번호 전체가 일치하는 환자 검색
            patients = PatientList.objects.filter(patient_id=full_patient_id) 

            # 검색 결과를 세션에 저장하기 위해 직렬화
            filtered_results = [
                {
                    'patient_name': p.patient_name, 'patient_id': p.patient_id,
                    'gender': p.gender, 'birth_date': str(p.birth_date),
                    'contact': p.contact
                } for p in patients
            ]
            
            request.session['filtered_patients'] = filtered_results

            # ★★★ 수정된 로직: 1명일 경우 바로 확인 요청 데이터 반환 ★★★
            if len(filtered_results) == 1:
                 patient = filtered_results[0]
                 return JsonResponse({
                    'success': True,
                    'confirmation_needed': True, # 확인 필요 플래그
                    'patient_name': patient['patient_name'],
                    'patient_data': patient # 모든 환자 정보
                 })

            # 0명 또는 다수일 경우
            return JsonResponse({
                'success': True,
                'message': f'{len(filtered_results)}명의 환자가 검색되었습니다.',
                'confirmation_needed': False,
                'results_count': len(filtered_results)
            })
        else:
            # 13자리가 아니면 오류 메시지 반환
            return JsonResponse({'success': False, 'message': '올바른 13자리 주민등록번호를 입력해주세요.'})

    return render(request, 'kiosk/main.html')

# ===== 선택된 환자 정보를 세션에 저장하는 함수 =====
@csrf_exempt
def select_patient_view(request):
    """선택된 환자 정보를 세션에 저장하는 API"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            patient_data = data.get('patient_data')
            
            if not patient_data:
                return JsonResponse({'success': False, 'error': '환자 정보가 없습니다.'}, status=400)
            
            # [핵심] Django 세션에 선택된 환자 정보 저장
            request.session['selected_patient'] = patient_data
            logger.info(f"환자 선택됨: {patient_data.get('patient_name')}, 세션에 저장.")
            
            return JsonResponse({'success': True})
            
        except json.JSONDecodeError:
            return JsonResponse({'success': False, 'error': '잘못된 요청 형식입니다.'}, status=400)
            
    return JsonResponse({'success': False, 'error': 'POST 요청만 가능합니다.'}, status=405)



@csrf_exempt
def search_by_name_view(request):
    """이름으로 2차 검색 처리"""
    if request.method == 'POST':
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        
        filtered_patients = request.session.get('filtered_patients', [])
        
        if not name:
            return JsonResponse({'success': False, 'message': '이름을 입력해주세요.'})
        
        # 이름으로 2차 필터링
        final_results = [p for p in filtered_patients if name in p['patient_name']]

        # [수정된 부분 시작]
        if len(final_results) == 1:
            # 결과가 한 명이면, 환자 정보를 응답에 직접 담아서 보냄
            return JsonResponse({
                'success': True, 
                'confirmation_needed': True,
                'patient': final_results[0]  
            })
        
        return JsonResponse({'success': True, 'results': final_results})
    return JsonResponse({'success': False, 'message': '잘못된 요청입니다.'})