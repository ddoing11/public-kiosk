// static/js/voice_socket_main.js (최종 안정화 버전)

import { speakOnClient } from './modules/azure-tts.js';
import { updateStatus, displayResults } from './modules/ui-main.js';

// ===== 전역 변수 및 상태 =====
let websocket = null;
let currentRecognition = null;
let clientState = 'listening'; // 초기 상태
let lastSpokenTTS = ""; // [핵심] 마지막으로 재생된 TTS 문장을 저장할 변수

// ===== 마이크 및 음성 인식 (안정성 강화) =====
function activateMicrophone() {
    document.getElementById('mic-indicator').classList.add('active');
    setTimeout(startSpeechRecognition, 300);
}

function deactivateMicrophone() {
    document.getElementById('mic-indicator').classList.remove('active');
    stopSpeechRecognition();
}

function startSpeechRecognition() {
    if (!('webkitSpeechRecognition' in window)) {
        updateStatus('오류: 이 브라우저는 음성 인식을 지원하지 않습니다.');
        return;
    }
    if (currentRecognition) return;
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    currentRecognition = new SpeechRecognition();
    currentRecognition.lang = 'ko-KR';
    currentRecognition.continuous = false;

    currentRecognition.onresult = function(event) {
        const result = event.results[0][0].transcript.trim();
        console.log('🎤 인식된 텍스트:', result);

        // [핵심 수정] 자기 음성 인식 무시 로직 개선
        // 인식된 텍스트가 TTS 문장 전체가 아니고, 5글자 이하의 짧은 단어가 아닐 때만
        // TTS 문장에 포함되는지 검사하여 무시합니다.
        if (result && lastSpokenTTS.includes(result) && result.length > 5 && result !== lastSpokenTTS) {
            console.warn(`자기 음성 인식 무시: "${result}"`);
            return;
        }

        if (result && websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
        }
    };

    currentRecognition.onerror = (event) => console.error('❌ 음성 인식 오류:', event.error);
    currentRecognition.onend = () => {
        currentRecognition = null;
        if (document.getElementById('mic-indicator').classList.contains('active')) {
            setTimeout(startSpeechRecognition, 500);
        }
    };
    currentRecognition.start();
}

function stopSpeechRecognition() {
    if (currentRecognition) {
        currentRecognition.onend = null;
        currentRecognition.stop();
        currentRecognition = null;
    }
}

// ===== WebSocket 핵심 로직 =====
function initializeWebSocket() {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/kiosk/`;
    websocket = new WebSocket(wsUrl);
    websocket.onopen = () => updateStatus('✅ 음성 서비스 연결됨');
    websocket.onmessage = (event) => handleWebSocketMessage(JSON.parse(event.data));
    websocket.onclose = () => updateStatus('❌ 음성 서비스 연결 끊김. 새로고침해주세요.');
    websocket.onerror = () => updateStatus('❌ 음성 서비스 오류');
}

function handleWebSocketMessage(data) {
    switch (data.type) {
        case 'tts.text':
            // [핵심] TTS가 재생되기 직전에 텍스트를 저장
            lastSpokenTTS = data.text;
            speakOnClient(data.text, deactivateMicrophone, activateMicrophone);
            break;
        case 'state.update':
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
            selectPatient(data.patient);
            break;
        case 'user.confirmation_failed':
            updateStatus('인증에 실패했습니다. 이름을 다시 말씀해주세요.');
            document.getElementById('nameInput').focus();
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

    birthForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const birthNumber = Array.from(digitInputs).map(input => input.value).join('');
        const csrfToken = birthForm.querySelector('[name=csrfmiddlewaretoken]').value;

        updateStatus('주민번호 확인 중...');
        const formData = new FormData();
        formData.append('birth_number', birthNumber);
        formData.append('csrfmiddlewaretoken', csrfToken);

        fetch('/kiosk/main/', {
            method: 'POST',
            body: new URLSearchParams(formData)
        })
        .then(res => res.json())
        .then(data => {
            if (data.success) {
                updateStatus('✅ 1차 인증 완료. 이름을 입력해주세요.');
                successMessage.textContent = data.message;
                nameSearchSection.style.display = 'block';
                nameInput.focus();
                speakOnClient('이름을 말씀해주세요.');
            } else {
                updateStatus('❌ 1차 인증 실패: ' + data.message);
            }
        });
    });

    nameSearchBtn.addEventListener('click', function() {
        const name = nameInput.value.trim();
        if (!name) { return; }
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
                    websocket.send(JSON.stringify({ type: 'user.confirmation_start', patient: data.patient }));
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
    updateStatus(`${patientData.patient_name}님 확인. 서비스 페이지로 이동합니다.`);
    fetch('/kiosk/select_patient/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ patient_data: patientData })
    })
    .then(res => res.json())
    .then(data => {
        if (data.success) {
            window.location.href = '/kiosk/services/';
        } else {
            alert('환자 선택 중 오류가 발생했습니다: ' + data.error);
        }
    });
}

function loadAzureSDK() {
    return new Promise((resolve, reject) => {
        if (window.SpeechSDK) { resolve(); return; }
        const script = document.createElement('script');
        script.src = 'https://aka.ms/csspeech/jsbrowserpackageraw';
        script.onload = () => resolve();
        script.onerror = () => reject(new Error('SDK 로드 실패'));
        document.head.appendChild(script);
    });
}

document.addEventListener('DOMContentLoaded', function() {
    const audioUnlockOverlay = document.getElementById('audio-unlock-overlay');

    async function initializeKiosk() {
        audioUnlockOverlay.style.display = 'none';
        updateStatus('음성 서비스 로딩 중...');
        try {
            await loadAzureSDK();
            updateStatus('시스템 초기화 중...');
            initializeWebSocket();
            setupAuthEventListeners();
            document.querySelectorAll('.birth-digit')[0]?.focus();
        } catch (error) {
            updateStatus('오류: 음성 서비스를 불러오지 못했습니다.');
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