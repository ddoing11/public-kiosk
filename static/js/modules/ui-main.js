// static/js/modules/ui-main.js (전체 파일)

/**
 * main.html 페이지의 UI 업데이트를 담당하는 모듈
 */

import { speakOnClient as ttsSpeak } from './azure-tts.js';

// ★★★ 추가됨: voice_socket_main.js에서 import된 함수를 사용합니다 ★★★
// voice_socket_main.js가 이 모듈을 import하므로, 여기서 selectPatient를 직접 호출하지 않습니다.
// 대신, 전역 변수와 voice_socket_main.js의 함수를 참조합니다.
// selectPatient 함수는 voice_socket_main.js에 정의되어 있습니다.


export function updateStatus(message) {
    // ui-main.js:12 Uncaught (in promise) TypeError: Cannot set properties of null (setting 'textContent')
    // 이전에 해결한 오류입니다. status-bar가 HTML에 추가되었는지 확인해주세요.
    const statusBar = document.getElementById('status-bar');
    if (statusBar) {
         statusBar.textContent = message;
         console.log('📊 상태:', message);
    } else {
         console.warn('UI 오류: status-bar 요소를 찾을 수 없습니다.');
    }
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

// ★★★ 추가된 함수: 본인 확인 팝업을 표시합니다 ★★★
export function showNameConfirmationPopup(name) {
    const modal = document.getElementById('confirmationModal');
    const confirmationText = document.getElementById('confirmationText');
    const mainContent = document.querySelector('.main-content');
    
    if (modal && confirmationText) {
        confirmationText.textContent = `${name} 님이 맞으신가요?`;
        modal.style.display = 'flex'; // flex로 변경하여 중앙 정렬

        if (mainContent) {
            mainContent.style.filter = 'blur(5px)'; 
        }

        // 팝업 안의 버튼에 이벤트 리스너 추가 (음성으로 처리되지만 클릭 대비)
        const confirmBtn = modal.querySelector('.confirm-btn');
        const cancelBtn = modal.querySelector('.cancel-btn');
        const closeBtn = modal.querySelector('.close-btn');
        
        // ★★★ 주의: selectPatient 함수는 voice_socket_main.js에 정의되어 있으므로 window를 통해 참조 ★★★
        if (confirmBtn && window.selectPatient) {
             confirmBtn.onclick = () => window.selectPatient(window.confirmedPatientData);
        }
        if (cancelBtn) {
             cancelBtn.onclick = hideNameConfirmationPopup; // 취소 버튼 클릭 시 팝업 닫기
        }
        if (closeBtn) {
             closeBtn.onclick = hideNameConfirmationPopup; // X 버튼 클릭 시 팝업 닫기
        }
    }
}

// ★★★ 추가된 함수: 본인 확인 팝업을 숨깁니다 ★★★
export function hideNameConfirmationPopup() {
    const modal = document.getElementById('confirmationModal');
    const mainContent = document.querySelector('.main-content');
    if (modal) {
        modal.style.display = 'none';
        if (mainContent) {
            mainContent.style.filter = 'none';
        }
        // 팝업 닫을 때 상태 초기화 또는 다음 단계로 전환 로직 추가 (필요시)
    }
}