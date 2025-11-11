/**
 * services.html 페이지의 UI 업데이트를 담당하는 모듈
 */

// 인증된 환자 정보 표시
export function displayPatientInfo(patient) {
    const container = document.getElementById('patient-info-display');
    if (container && patient) {
        container.innerHTML = `<h3><span class="highlight">${patient.patient_name}</span> 님, 환영합니다.</h3>`;
        container.style.display = 'block';
    }
}

// 음성 상태 메시지 업데이트
export function updateVoiceStatus(message) {
    document.getElementById('voice-status').textContent = message;
}

// 하단 상태 바 메시지 업데이트
export function updateStatus(message) {
    document.getElementById('status-bar').textContent = message;
}

// 음성으로 인식된 카드 선택 UI 처리
export function selectCard(serviceName) {
    document.querySelectorAll('.service-card').forEach(card => {
        card.classList.remove('selected');
    });
    const card = document.querySelector(`[data-service="${serviceName}"]`);
    if (card) {
        card.classList.add('selected');
    }
}

// DB 조회 결과를 테이블로 표시
export function displayResults(results) {
    const container = document.getElementById('result-table');
    const section = document.getElementById('db-results');

    if (!results || results.length === 0) {
        container.innerHTML = '<p>조회된 결과가 없습니다. 다른 서류를 선택해 주세요.</p>';
        section.classList.add('show');
        return;
    }

    let html = '<table><thead><tr>';
    const headers = Object.keys(results[0]);
    headers.forEach(h => html += `<th>${h}</th>`);
    html += '</tr></thead><tbody>';

    results.forEach(row => {
        html += '<tr>';
        headers.forEach(h => html += `<td>${row[h] || '-'}</td>`);
        html += '</tr>';
    });

    html += '</tbody></table>';
    container.innerHTML = html;
    section.classList.add('show');
}

// 로딩 스피너 표시/숨김
export function showLoading(show) {
    document.getElementById('loading-spinner').classList.toggle('show', show);
}