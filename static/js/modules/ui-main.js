// static/js/modules/ui-main.js

/**
 * main.html 페이지의 UI 업데이트를 담당하는 모듈
 */

export function updateConsultationStatus(message) {
    document.getElementById('consultation-status').textContent = message;
    console.log('💬 상담 상태:', message);
}

export function updateStatus(message) {
    document.getElementById('status-bar').textContent = message;
    console.log('📊 상태:', message);
}

export function displayResults(results, onSelect) {
    const resultsList = document.getElementById('resultsList');
    const resultsSection = document.getElementById('resultsSection');
    resultsList.innerHTML = '';
    
    if (results.length === 0) {
        resultsList.innerHTML = '<p>일치하는 환자가 없습니다.</p>';
    } else {
        results.forEach(result => {
            const item = document.createElement('div');
            item.className = 'result-item';
            item.innerHTML = `
                <div><strong>${result.patient_name}</strong> (${result.gender}, ${result.birth_date})</div>
                <div>환자번호: ${result.patient_id}</div>
                <button class="verify-button select-patient-btn">이 환자 선택</button>
            `;
            item.querySelector('.select-patient-btn').addEventListener('click', () => {
                onSelect(result); // 환자 선택 시 콜백 함수 호출
            });
            resultsList.appendChild(item);
        });
    }
    resultsSection.style.display = 'block';
}