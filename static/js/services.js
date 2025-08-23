// static/js/services.js (개선된 버전)

import { speakOnClient } from './modules/azure-tts.js';
import { displayPatientInfo, updateVoiceStatus, updateStatus, selectCard, displayResults, showLoading } from './modules/ui-services.js';

// ===== 전역 변수 =====
let serviceWebSocket = null;
let recognition = null;
let lastSpokenTTS = "";
let isListening = false;
let isTTSPlaying = false;  // TTS 재생 상태 추가
let ttsTimeoutId = null;   // TTS 타임아웃 ID

// ===== 마이크 및 음성 인식 =====
function activateMic() {
    if (isListening) return; // 중복 실행 방지
    
    document.getElementById('mic-indicator').classList.add('active');
    updateVoiceStatus('음성 인식 중... 말씀해주세요.');
    isListening = true;
    startRecognition();
}

function deactivateMic() {
    document.getElementById('mic-indicator').classList.remove('active');
    isListening = false;
    stopRecognition();
}

function startRecognition() {
    if (!('webkitSpeechRecognition' in window)) {
        updateStatus('오류: 이 브라우저는 음성 인식을 지원하지 않습니다.');
        return;
    }
    if (recognition) { // 중복 실행 방지
        return;
    }
    if (isTTSPlaying) { // TTS 재생 중이면 시작하지 않음
        console.log('🚫 TTS 재생 중이므로 음성 인식 시작 안함');
        return;
    }
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.lang = 'ko-KR';
    recognition.continuous = false; // 한 문장만 인식
    recognition.interimResults = false; // 중간 결과 비활성화

    recognition.onresult = (event) => {
        const finalTranscript = event.results[0][0].transcript.trim();
        console.log('🎤 인식된 텍스트:', finalTranscript);

        // 자기 음성 인식 무시 로직 개선
        if (shouldIgnoreTranscript(finalTranscript)) {
            console.warn(`자기 음성 인식 무시: "${finalTranscript}"`);
            return;
        }

        if (finalTranscript) {
            // 음성 상태 업데이트
            updateVoiceStatus(`"${finalTranscript}" 처리 중...`);
            sendVoiceInput(finalTranscript);
        }
    };

    recognition.onerror = (event) => {
        console.error('음성 인식 오류:', event.error);
        // 특정 오류가 아닌 경우에만 사용자에게 알림
        if (event.error !== 'no-speech' && event.error !== 'aborted' && event.error !== 'network') {
            updateVoiceStatus('음성 인식 오류가 발생했습니다. 다시 시도해주세요.');
            setTimeout(() => {
                if (isListening && !isTTSPlaying) {
                    updateVoiceStatus('음성 인식 중... 말씀해주세요.');
                }
            }, 2000);
        }
    };

    // 인식 세션이 끝나면 자동으로 재시작
    recognition.onend = () => {
        recognition = null;
        const micIndicator = document.getElementById('mic-indicator');
        if (micIndicator.classList.contains('active') && isListening && !isTTSPlaying) {
            setTimeout(startRecognition, 100); // 0.1초 후 재시작
        }
    };
    
    recognition.start();
    console.log('🎤 음성 인식 시작');
}

function shouldIgnoreTranscript(transcript) {
    // TTS 재생 중이면 무조건 무시
    if (isTTSPlaying) {
        console.log(`🚫 TTS 재생 중이므로 무시: "${transcript}"`);
        return true;
    }
    
    // 중요한 키워드들은 짧아도 허용
    const importantKeywords = [
        '상담', '문의', '질문', '도움', '처방전', 
        '진료확인서', '진료영수증', '확인서', '영수증'
    ];
    
    for (const keyword of importantKeywords) {
        if (transcript.includes(keyword)) {
            console.log(`✅ 중요한 키워드 포함으로 허용: "${transcript}" (키워드: ${keyword})`);
            return false;
        }
    }
    
    // TTS와 동일한 텍스트이거나 포함된 경우 무시 (더 엄격하게)
    const cleanTranscript = transcript.replace(/\s+/g, '').toLowerCase();
    const cleanTTS = lastSpokenTTS.replace(/\s+/g, '').toLowerCase();
    
    if (cleanTranscript === cleanTTS) {
        console.log(`🚫 완전 일치로 무시: "${transcript}"`);
        return true;
    }
    
    // TTS에 포함된 문구라면 무시 (3글자 이상일 때만)
    if (cleanTranscript.length >= 3 && cleanTTS.includes(cleanTranscript)) {
        console.log(`🚫 TTS 포함으로 무시: "${transcript}" in "${lastSpokenTTS}"`);
        return true;
    }
    
    // 너무 짧은 텍스트 무시 (1글자만)
    if (transcript.length <= 1) {
        console.log(`🚫 너무 짧아서 무시: "${transcript}"`);
        return true;
    }
    
    // 의미없는 소음 패턴 무시
    const noisePatterns = ['음', '어', '아', '그', '저', '네', '예', '응'];
    if (noisePatterns.includes(transcript.trim())) {
        console.log(`🚫 소음으로 무시: "${transcript}"`);
        return true;
    }
    
    // 일반적인 감탄사나 답변 패턴 무시 (하지만 중요 키워드 포함시 제외)
    const commonPhrases = [
        '죄송합니다', '감사합니다', '안녕하세요', '말씀해', '주세요',
        '드리겠습니다', '있습니다', '필요하시면', '궁금한'
    ];
    
    for (const phrase of commonPhrases) {
        if (transcript === phrase || (transcript.includes(phrase) && transcript.length < 8)) {
            console.log(`🚫 일반적인 응답으로 무시: "${transcript}"`);
            return true;
        }
    }
    
    console.log(`✅ 유효한 음성 입력: "${transcript}"`);
    return false;
}

// TTS 재생 시작/종료 관리
function setTTSPlaying(playing) {
    isTTSPlaying = playing;
    console.log(`🔊 TTS 재생 상태: ${playing ? '시작' : '종료'}`);
    
    if (playing) {
        // TTS 시작 시 음성 인식 일시 중단
        if (recognition) {
            recognition.abort();
            recognition = null;
        }
        
        // 안전을 위한 타임아웃 (10초 후 강제 해제)
        if (ttsTimeoutId) clearTimeout(ttsTimeoutId);
        ttsTimeoutId = setTimeout(() => {
            console.log('⚠️ TTS 타임아웃으로 강제 해제');
            isTTSPlaying = false;
            if (isListening && !recognition) {
                setTimeout(startRecognition, 500);
            }
        }, 10000);
    } else {
        // TTS 종료 시 타임아웃 클리어
        if (ttsTimeoutId) {
            clearTimeout(ttsTimeoutId);
            ttsTimeoutId = null;
        }
        
        // TTS 종료 후 잠시 대기 후 음성 인식 재시작
        if (isListening && !recognition) {
            setTimeout(startRecognition, 500);  // 0.5초 대기
        }
    }
}

function stopRecognition() {
    if (recognition) {
        // onend에서 자동 재시작되는 것을 막기 위해 먼저 상태 변경
        isListening = false;
        document.getElementById('mic-indicator').classList.remove('active');
        recognition.stop();
        recognition = null;
    }
}

// ===== WebSocket 핵심 로직 =====
function initializeWebSocket() {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/kiosk/services/`;
    serviceWebSocket = new WebSocket(wsUrl);

    serviceWebSocket.onopen = () => {
        updateStatus('음성 서비스 준비 완료');
        console.log('🔗 WebSocket 연결 성공');
    };
    
    serviceWebSocket.onmessage = (event) => handleMessage(JSON.parse(event.data));
    
    serviceWebSocket.onclose = (event) => {
        console.warn('🔌 WebSocket 연결 종료:', event.code);
        updateStatus('서비스 연결이 끊겼습니다. 5초 후 재연결합니다.');
        setTimeout(initializeWebSocket, 5000);
    };
    
    serviceWebSocket.onerror = (error) => {
        console.error('❌ WebSocket 오류:', error);
        updateStatus('연결 오류가 발생했습니다.');
    };
}

function handleMessage(data) {
    showLoading(false);
    console.log('📨 수신된 메시지:', data.type);
    
    switch(data.type) {
        case 'patient.info':
            displayPatientInfo(data.patient);
            break;
            
        case 'tts.text':
            lastSpokenTTS = data.text;
            console.log('🔊 TTS 재생:', data.text);
            setTTSPlaying(true);  // TTS 시작
            
            // TTS 완료 콜백 함수들
            const onTTSStart = () => {
                deactivateMic();
                setTTSPlaying(true);
            };
            const onTTSComplete = () => {
                setTTSPlaying(false);  // TTS 종료
                activateMic();
            };
            
            speakOnClient(data.text, onTTSStart, onTTSComplete);
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
            console.error('🚨 서버 오류:', data.message);
            break;
            
        case 'gpt.stream':
            handleGPTStream(data);
            break;
            
        case 'status':
            updateStatus('📡 ' + data.state);
            updateUIBasedOnState(data.state);
            break;
            
        case 'mic.off':
            // 서버에서 마이크 비활성화 요청
            deactivateMic();
            break;
            
        default:
            console.warn(`알 수 없는 메시지 타입: ${data.type}`);
    }
}

function handleGPTStream(data) {
    const gptResponseDiv = document.getElementById('gpt-response');
    const gptContentDiv = gptResponseDiv.querySelector('#gpt-content');
    
    if (data.delta) {
        // 첫 번째 델타인 경우 화면에 표시
        if (gptResponseDiv.style.display === 'none') {
            gptContentDiv.textContent = '';
            gptResponseDiv.style.display = 'block';
        }
        gptContentDiv.textContent += data.delta;
        
        // 자동 스크롤
        gptContentDiv.scrollTop = gptContentDiv.scrollHeight;
    }
    
    if (data.event === 'done' && data.full_text) {
        // 스트리밍 완료 후 TTS 재생
        console.log('✅ GPT 스트리밍 완료');
        speakOnClient(data.full_text, deactivateMic, activateMic);
    }
}

function updateUIBasedOnState(state) {
    const voiceSectionTitle = document.getElementById('voice-section-title');
    
    if (state === 'advising') {
        voiceSectionTitle.textContent = '💬 AI와 상담 중';
        updateVoiceStatus('상담 중입니다. 궁금한 점을 말씀해주세요.');
    } else {
        voiceSectionTitle.textContent = '🎤 음성으로 서류 선택 또는 문의';
        updateVoiceStatus('원하시는 서류를 말씀해주시거나 상담을 요청해주세요.');
    }
}

function sendVoiceInput(text) {
    if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
        console.log('📤 음성 입력 전송:', text);
        serviceWebSocket.send(JSON.stringify({ type: 'voice.input', text: text }));
        showLoading(true);
    } else {
        console.error('❌ WebSocket 연결 없음');
        updateStatus('서버 연결이 끊어졌습니다. 잠시 후 다시 시도해주세요.');
    }
}

// ===== Azure SDK 동적 로드 및 페이지 초기화 =====
function loadAzureSDK() {
    return new Promise((resolve, reject) => {
        if (window.SpeechSDK) { 
            resolve(); 
            return; 
        }
        
        const script = document.createElement('script');
        script.src = 'https://aka.ms/csspeech/jsbrowserpackageraw';
        script.onload = () => {
            console.log('✅ Azure SDK 로드 완료');
            resolve();
        };
        script.onerror = () => {
            console.error('❌ Azure SDK 로드 실패');
            reject(new Error('SDK 로드 실패'));
        };
        document.head.appendChild(script);
    });
}

// ===== 페이지 초기화 =====
document.addEventListener('DOMContentLoaded', async function() {
    console.log('🚀 서비스 페이지 초기화 시작');
    
    // 서비스 카드 클릭 이벤트 등록
    document.querySelectorAll('.service-card').forEach(card => {
        card.addEventListener('click', function() {
            const serviceName = this.dataset.service;
            console.log('🖱️ 카드 클릭:', serviceName);
            
            selectCard(serviceName);
            if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
                serviceWebSocket.send(JSON.stringify({ 
                    type: 'service.select', 
                    service: serviceName 
                }));
                showLoading(true);
            }
        });
    });
    
    // 마이크 버튼 이벤트 (있는 경우)
    const micButton = document.getElementById('mic-button');
    if (micButton) {
        micButton.addEventListener('click', function() {
            if (isListening) {
                deactivateMic();
            } else {
                activateMic();
            }
        });
    }
    
    // 시스템 초기화
    updateStatus('음성 서비스 로딩 중...');
    try {
        await loadAzureSDK();
        updateStatus('시스템 초기화 중...');
        initializeWebSocket();
        console.log('✅ 초기화 완료');
    } catch(error) {
        console.error('❌ 초기화 실패:', error);
        updateStatus('오류: 음성 서비스를 불러오지 못했습니다.');
    }
});

// ===== 페이지 종료 시 정리 =====
window.addEventListener('beforeunload', function() {
    console.log('🧹 페이지 종료 - 리소스 정리');
    deactivateMic();
    if (serviceWebSocket) {
        serviceWebSocket.close();
    }
});