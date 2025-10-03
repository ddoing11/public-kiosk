// static/js/modules/ui-main.js

/**
 * main.html 페이지의 UI 업데이트를 담당하는 모듈
 */

import { speakOnClient as ttsSpeak } from './azure-tts.js';



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
        // [추가] 검색 결과가 없을 때 TTS 안내
        ttsSpeak('일치하는 환자가 없습니다. 이름을 다시 말씀해주세요.');
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
                onSelect(result);
            });
            resultsList.appendChild(item);
        });
    }
    resultsSection.style.display = 'block';
}