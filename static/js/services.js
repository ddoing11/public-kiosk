// static/js/services.js (모듈화 적용 버전)

import { speakOnClient } from './modules/azure-tts.js';
import { displayPatientInfo, updateVoiceStatus, updateStatus, selectCard, displayResults, showLoading } from './modules/ui-services.js';

// ===== 전역 변수 =====
let serviceWebSocket = null;
let recognition = null;

// ===== 마이크 및 음성 인식 =====
function activateMic() {
    document.getElementById('mic-indicator').classList.add('active');
    updateVoiceStatus('음성 인식 중... 원하시는 서류를 말씀해주세요.');
    startRecognition();
}

function deactivateMic() {
    document.getElementById('mic-indicator').classList.remove('active');
    stopRecognition();
}

function startRecognition() {
    if (!('webkitSpeechRecognition' in window)) return;
    if (recognition) return;

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = 'ko-KR';
    recognition.continuous = false;

    recognition.onresult = (event) => {
        const finalTranscript = event.results[0][0].transcript.trim();
        if (finalTranscript) {
            sendVoiceInput(finalTranscript);
        }
    };

    recognition.onerror = (event) => {
        if (event.error !== 'no-speech') console.error('음성 인식 오류:', event.error);
    };

    recognition.onend = () => {
        recognition = null;
        if (document.getElementById('mic-indicator').classList.contains('active')) {
            setTimeout(startRecognition, 300);
        }
    };
    recognition.start();
}

function stopRecognition() {
    if (recognition) {
        recognition.stop();
        recognition = null;
    }
}

// ===== WebSocket 핵심 로직 =====
function initializeWebSocket() {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/kiosk/services/`;
    serviceWebSocket = new WebSocket(wsUrl);

    serviceWebSocket.onopen = () => updateStatus('음성 서비스 준비 완료');
    serviceWebSocket.onmessage = (event) => handleMessage(JSON.parse(event.data));
    serviceWebSocket.onclose = () => {
        updateStatus('음성 서비스 연결이 끊겼습니다. 5초 후 재연결합니다.');
        setTimeout(initializeWebSocket, 5000);
    };
    serviceWebSocket.onerror = (error) => console.error('WebSocket 오류:', error);
}

function handleMessage(data) {
    showLoading(false);
    switch(data.type) {
        case 'patient.info':
            displayPatientInfo(data.patient);
            break;
        case 'tts.text':
            speakOnClient(data.text, deactivateMic, activateMic);
            break;
        case 'mic.on':
            activateMic();
            break;
        case 'mic.off':
            deactivateMic();
            break;
        case 'document.recognized':
            selectCard(data.document_type);
            updateVoiceStatus(`"${data.document_type}" 문서를 선택하셨습니다.`);
            break;
        case 'db.results':
            displayResults(data.results);
            break;
        case 'error':
            updateVoiceStatus(`오류: ${data.message}`);
            break;
        default:
            console.warn(`Unknown message type received: ${data.type}`);
    }
}

function sendVoiceInput(text) {
    if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
        serviceWebSocket.send(JSON.stringify({ type: 'voice.input', text: text }));
        showLoading(true);
    }
}

// ===== 페이지 로드 시 실행 =====
document.addEventListener('DOMContentLoaded', function() {
    document.querySelectorAll('.service-card').forEach(card => {
        card.addEventListener('click', function() {
            const serviceName = this.dataset.service;
            selectCard(serviceName);
            if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
                serviceWebSocket.send(JSON.stringify({ type: 'service.select', service: serviceName }));
                showLoading(true);
            }
        });
    });
    updateStatus('시스템 초기화 중...');
    initializeWebSocket();
});