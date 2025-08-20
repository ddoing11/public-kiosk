// static/js/voice_socket_main.js (모듈화 적용 버전)

import { speakOnClient } from './modules/azure-tts.js';
import { updateConsultationStatus, updateStatus, displayResults } from './modules/ui-main.js';

// ===== 전역 변수 및 상태 =====
let websocket = null;
let currentRecognition = null;
let clientState = 'listening'; // 초기 상태는 listening으로 설정

// ===== 마이크 및 음성 인식 =====
function activateMicrophone() {
    console.log('🎤 마이크 활성화');
    document.getElementById('mic-indicator').classList.add('active');
    updateConsultationStatus('🎤 음성 인식 중... 말씀해주세요');
    setTimeout(startSpeechRecognition, 300);
}

function deactivateMicrophone() {
    console.log('🎤 마이크 비활성화');
    document.getElementById('mic-indicator').classList.remove('active');
    stopSpeechRecognition();
}
function startSpeechRecognition() {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        console.warn('이 브라우저는 음성 인식을 지원하지 않습니다.');
        return;
    }
    // 이미 인식 인스턴스가 존재하면 중복 실행 방지
    if (currentRecognition) {
        return;
    }
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    currentRecognition = new SpeechRecognition();
    currentRecognition.lang = 'ko-KR';
    currentRecognition.continuous = false;

    currentRecognition.onresult = function(event) {
        const result = event.results[0][0].transcript.trim();
        console.log('🎤 인식된 텍스트:', result);
        if (result && websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
        }
    };

    // ===== [수정] 에러 핸들링 강화 =====
    currentRecognition.onerror = function(event) {
        console.error('❌ 음성 인식 오류:', event.error);
        // 네트워크 오류나 다른 문제 발생 시, 잠시 후 재시작을 시도합니다.
    };

    // ===== [수정] 재시작 로직 개선 =====
    currentRecognition.onend = function() {
        console.log('🎤 음성 인식 세션 종료');
        currentRecognition = null; // 인스턴스 정리
        
        const micIndicator = document.getElementById('mic-indicator');
        // 마이크가 활성화 상태일 때만 500ms 후에 재시작
        if (micIndicator.classList.contains('active')) {
            setTimeout(startSpeechRecognition, 500);
        }
    };
    
    try {
        currentRecognition.start();
        console.log('🎤 음성 인식 세션 시작');
    } catch (e) {
        console.error("음성 인식 시작 오류:", e);
        currentRecognition = null;
    }
}


function stopSpeechRecognition() {
    if (currentRecognition) {
        currentRecognition.onend = null; // 자동 재시작 방지
        currentRecognition.stop();
        currentRecognition = null;
    }
}

// ===== WebSocket 핵심 로직 =====
function initializeWebSocket() {
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/kiosk/`;
    websocket = new WebSocket(wsUrl);

    websocket.onopen = () => {
        console.log('✅ WebSocket 연결됨');
        updateStatus('✅ 음성 서비스 연결됨');
    };

    websocket.onmessage = (event) => handleWebSocketMessage(JSON.parse(event.data));

    websocket.onclose = (event) => {
        console.warn('⚠️ WebSocket 연결 종료:', event.code);
        updateStatus('❌ 음성 서비스 연결 끊김');
        updateConsultationStatus('연결이 끊어졌습니다. 페이지를 새로고침해주세요.');
    };

    websocket.onerror = (error) => {
        console.error('❌ WebSocket 오류:', error);
        updateStatus('❌ 음성 서비스 오류');
        updateConsultationStatus('서비스 오류가 발생했습니다.');
    };
}

function handleWebSocketMessage(data) {
    console.log('📥 WebSocket 메시지:', data);
    switch (data.type) {
        case 'tts.text':
            speakOnClient(data.text,
                () => { // onStart: TTS 시작 시
                    deactivateMicrophone();
                    updateConsultationStatus('🔊 음성 안내 중...');
                },
                () => { // onEnd: TTS 종료 시
                    activateMicrophone();
                }
            );
            break;
        case 'state.update':
            console.log('Client state updated to:', data.step);
            clientState = data.step;
            if (clientState === 'confirming_user') {
                updateStatus('음성으로 답변해주세요. (예: "본인 확인")');
            }
            break;
        case 'stt.forward_to_input':
            const nameSearchSection = document.getElementById('nameSearchSection');
            if (window.getComputedStyle(nameSearchSection).display !== 'none') {
                const nameInput = document.getElementById('nameInput');
                nameInput.value = data.text;
                updateStatus(`음성으로 이름 "${data.text}" 입력됨`);
                document.getElementById('nameSearchBtn').click();
            }
            break;
        case 'user.confirmed':
            updateStatus(`✅ ${data.patient.patient_name}님 확인. 서비스 페이지로 이동합니다.`);
            selectPatient(data.patient); // 확인된 환자로 선택 처리
            break;
        case 'user.confirmation_failed':
            updateStatus('인증에 실패했습니다. 이름을 다시 말씀해주세요.');
            document.getElementById('nameInput').focus();
            break;
        case 'mic.on':
            activateMicrophone();
            break;
        case 'mic.off':
            deactivateMicrophone();
            break;
        case 'gpt.stream':
            const contentDiv = document.getElementById('gpt-content');
            if (data.event === 'done') {
                const fullResponse = contentDiv.textContent;
                speakOnClient(fullResponse, () => deactivateMicrophone(), () => activateMicrophone());
            } else if (data.delta) {
                contentDiv.textContent += data.delta;
                document.getElementById('gpt-response').classList.add('show');
            }
            break;
        case 'status':
            updateStatus('📡 ' + data.state);
            break;
        default:
            console.warn('알 수 없는 메시지 타입:', data.type);
    }
}

// ===== 인증 흐름 및 UI 이벤트 =====
function setupAuthEventListeners() {
    const birthForm = document.getElementById('birthForm');
    const digitInputs = document.querySelectorAll('.birth-digit');
    const birthSubmitBtn = document.getElementById('birthSubmitBtn');
    const nameSearchSection = document.getElementById('nameSearchSection');
    const nameInput = document.getElementById('nameInput');
    const nameSearchBtn = document.getElementById('nameSearchBtn');
    const successMessage = document.getElementById('birthSuccessMessage');

    digitInputs.forEach((input, index) => {
        input.addEventListener('input', () => {
            if (input.value.length === 1 && index < digitInputs.length - 1) {
                digitInputs[index + 1].focus();
            }
            checkBirthFormValidity();
        });
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Backspace' && input.value === '' && index > 0) {
                digitInputs[index - 1].focus();
            }
        });
    });

    function checkBirthFormValidity() {
        const allFilled = Array.from(digitInputs).every(input => input.value.match(/^[0-9]$/));
        birthSubmitBtn.disabled = !allFilled;
    }

    // (2단계) 주민번호 폼 제출
    birthForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const birthNumber = Array.from(digitInputs).map(input => input.value).join('');
        
        // ===== [수정] CSRF 토큰을 form에서 가져옵니다. =====
        const csrfToken = birthForm.querySelector('[name=csrfmiddlewaretoken]').value;

        updateStatus('주민번호 확인 중...');
        
        const formData = new FormData();
        formData.append('birth_number', birthNumber);
        
        // ===== [수정] FormData에 CSRF 토큰을 추가합니다. =====
        formData.append('csrfmiddlewaretoken', csrfToken);

        fetch('/kiosk/main/', {
            method: 'POST',
            body: new URLSearchParams(formData)
        })
        .then(res => {
            if (!res.ok) { // 403 에러 등 HTTP 에러 처리
                throw new Error(`HTTP error! status: ${res.status}`);
            }
            return res.json();
        })
        .then(data => {
            if (data.success) {
                updateStatus('✅ 1차 인증 완료. 이름을 입력해주세요.');
                successMessage.textContent = data.message;
                nameSearchSection.style.display = 'block';
                nameInput.focus();
                speakOnClient('이름을 말씀해주세요.', () => deactivateMicrophone(), () => activateMicrophone());
            } else {
                updateStatus('❌ 1차 인증 실패: ' + data.message);
            }
        })
        .catch(error => {
            console.error('1차 검색 중 오류 발생:', error);
            updateStatus('❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요.');
        });
    });

    nameSearchBtn.addEventListener('click', function() {
        const name = nameInput.value.trim();
        if (!name) {
            updateStatus('⚠️ 이름을 입력해주세요.');
            return;
        }
        updateStatus('이름으로 검색 중...');
        fetch('/kiosk/search_by_name/', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ name: name })
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                if (data.confirmation_needed) {
                    updateStatus('음성으로 본인 확인을 진행합니다.');
                    websocket.send(JSON.stringify({ 
                        type: 'user.confirmation_start',
                        patient: data.patient
                    }));
                } else {
                    updateStatus(`✅ ${data.results.length}명의 환자를 찾았습니다.`);
                    displayResults(data.results, selectPatient);
                }
            } else {
                updateStatus('❌ 이름 검색 실패: ' + data.message);
            }
        });
    });
    
    nameInput.addEventListener('keypress', e => {
        if (e.key === 'Enter') nameSearchBtn.click();
    });
}

function selectPatient(patientData) {
    updateStatus(`${patientData.patient_name}님 선택. 서비스 페이지로 이동합니다.`);
    fetch('/kiosk/select_patient/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patient_data: patientData })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            setTimeout(() => window.location.href = '/kiosk/services/', 1500);
        } else {
            alert('환자 선택 중 오류가 발생했습니다: ' + data.error);
        }
    });
}

// ===== 페이지 로드 시 실행 =====
document.addEventListener('DOMContentLoaded', function() {
    const audioUnlockOverlay = document.getElementById('audio-unlock-overlay');

    function initializeKiosk() {
        audioUnlockOverlay.style.display = 'none';
        updateStatus('시스템 초기화 중...');
        updateConsultationStatus('WebSocket 연결 중...');
        initializeWebSocket();
        setupAuthEventListeners();
        const digitInputs = document.querySelectorAll('.birth-digit');
        if(digitInputs.length > 0) {
            digitInputs[0].focus();
        }
    }

    if (document.referrer && document.referrer.includes('/kiosk/idle/')) {
        initializeKiosk();
    } else {
        audioUnlockOverlay.style.cssText = "position:fixed; top:0; left:0; width:100%; height:100%; z-index:1000; background-color: rgba(0,0,0,0.5); color:white; display:flex; justify-content:center; align-items:center; font-size: 2rem; cursor: pointer;";
        audioUnlockOverlay.innerHTML = "<h1>화면을 터치하여 시작하세요</h1>";
        audioUnlockOverlay.addEventListener('click', initializeKiosk, { once: true });
    }
});