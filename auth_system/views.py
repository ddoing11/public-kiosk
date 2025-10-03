from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from .models import PatientList

# Create your views here.


#테스트 페이지 접근 용
def test_page_view(request):
    if request.method == 'POST':
        # 주민번호 앞자리 6자리를 받아서 1차 필터링 수행
        birth_number = request.POST.get('birth_number', '')
        if len(birth_number) == 6 and birth_number.isdigit():
            # PatientList에서 patient_id가 입력받은 앞자리로 시작하는 환자들 검색
            filtered_results = get_patients_by_birth_number(birth_number)
            request.session['filtered_patients'] = filtered_results
            request.session['birth_number'] = birth_number

            return JsonResponse({
                'success': True,
                'message': f'{len(filtered_results)}명의 환자가 검색되었습니다.',
                'patient_count': len(filtered_results)
            })
        else:
            return JsonResponse({'success': False, 'message': '올바른 6자리 숫자를 입력해주세요.'})

    return render(request, 'test_page.html')

def get_patients_by_birth_number(birth_number):
    """주민번호 앞자리로 PatientList에서 환자 정보를 검색"""
    results = []

    # PatientList에서 patient_id가 입력받은 앞자리로 시작하는 환자들 검색
    patients = PatientList.objects.filter(patient_id__startswith=birth_number)

    for patient in patients:
        results.append({
            'id': patient.id,
            'patient_name': patient.patient_name,
            'patient_id': patient.patient_id,
            'gender': patient.gender,
            'birth_date': str(patient.birth_date),
            'contact': patient.contact
        })

    return results

@csrf_exempt
def search_by_name(request):
    """이름으로 2차 필터링하여 최종 결과 반환"""
    if request.method == 'POST':
        data = json.loads(request.body)
        name = data.get('name', '').strip()

        # 세션에서 1차 필터링 결과 가져오기
        filtered_patients = request.session.get('filtered_patients', [])

        if not name:
            return JsonResponse({'success': False, 'message': '이름을 입력해주세요.'})

        # 이름으로 2차 필터링 (부분 일치 검색)
        final_results = [
            patient for patient in filtered_patients
            if name in patient['patient_name']
        ]

        return JsonResponse({
            'success': True,
            'results': final_results,
            'total_count': len(final_results)
        })

    return JsonResponse({'success': False, 'message': 'POST 요청만 허용됩니다.'})
