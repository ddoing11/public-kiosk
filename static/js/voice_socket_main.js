// static/js/voice_socket_main.js (최종 통합 버전)

// ===== 전역 변수 =====
let websocket = null;
let currentRecognition = null;

// ===== Azure Speech SDK 설정 =====
let azureTokenInfo = null;
let azureSynthesizer = null;

async function getAzureToken() {
    if (azureTokenInfo && Date.now() < azureTokenInfo.expireAt) {
        return azureTokenInfo;
    }
    try {
        const res = await fetch('/speech/token/');
        if (!res.ok) throw new Error(`Token fetch failed: ${res.status}`);
        const j = await res.json();
        azureTokenInfo = { token: j.token, region: j.region, expireAt: Date.now() + 9 * 60 * 1000 };
        console.log('Azure Speech Token successfully fetched.');
        return azureTokenInfo;
    } catch (error) {
        console.error('Failed to get Azure token:', error);
        return null;
    }
}

async function speakOnClient(text) {
    if (!window.SpeechSDK) {
        console.error('SpeechSDK not loaded.');
        return;
    }
    deactivateMicrophone(); // 안내 시작 전 마이크 끄기
    updateConsultationStatus('🔊 음성 안내 중...');

    try {
        const tokenInfo = await getAzureToken();
        if (!tokenInfo) return;

        const speechConfig = SpeechSDK.SpeechConfig.fromAuthorizationToken(tokenInfo.token, tokenInfo.region);
        speechConfig.speechSynthesisLanguage = 'ko-KR';
        speechConfig.speechSynthesisVoiceName = 'ko-KR-SunHiNeural';

        const audioConfig = SpeechSDK.AudioConfig.fromDefaultSpeakerOutput();
        if (!azureSynthesizer) {
            azureSynthesizer = new SpeechSDK.SpeechSynthesizer(speechConfig, audioConfig);
        }

        // Promise를 사용해 TTS 재생이 완전히 끝날 때까지 기다립니다.
        const speakPromise = new Promise((resolve, reject) => {
            azureSynthesizer.speakTextAsync(
                text,
                (result) => {
                    if (result.reason === SpeechSDK.ResultReason.SynthesizingAudioCompleted) {
                        resolve();
                    } else {
                        reject(`Speech synthesis canceled: ${result.errorDetails}`);
                    }
                },
                (err) => {
                    reject(`Error synthesizing speech: ${err}`);
                }
            );
        });
        
        // TTS 재생이 완료될 때까지 여기서 대기합니다.
        await speakPromise;

        console.log(`TTS playback fully completed for: "${text}"`);

        // 재생이 완료된 후 마이크를 활성화합니다.
        activateMicrophone();

    } catch (error) {
        console.error('An error occurred during Azure TTS process:', error);
        // 오류 발생 시에도 마이크를 다시 켤 수 있도록 처리
        activateMicrophone();
    }
}

// ===== WebSocket 핵심 로직 =====
function initializeWebSocket() {
    try {
        const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const wsUrl = `${wsProtocol}//${window.location.host}/ws/kiosk/`;
        websocket = new WebSocket(wsUrl);

        websocket.onopen = function(event) {
            console.log('✅ WebSocket 연결됨');
            updateStatus('✅ 음성 서비스 연결됨');
            updateConsultationStatus('음성 안내 시작...');
        };

        websocket.onmessage = function(event) {
            const data = JSON.parse(event.data);
            handleWebSocketMessage(data);
        };

        websocket.onclose = function(event) {
            console.warn('⚠️ WebSocket 연결 종료:', event.code);
            updateStatus('❌ 음성 서비스 연결 끊김');
            updateConsultationStatus('연결이 끊어졌습니다. 페이지를 새로고침해주세요.');
        };

        websocket.onerror = function(error) {
            console.error('❌ WebSocket 오류:', error);
            updateStatus('❌ 음성 서비스 오류');
            updateConsultationStatus('서비스 오류가 발생했습니다.');
        };

    } catch (error) {
        console.error('WebSocket 초기화 실패:', error);
        updateStatus('❌ 음성 서비스 초기화 실패');
    }
}

function handleWebSocketMessage(data) {
    console.log('📥 WebSocket 메시지:', data);

    switch (data.type) {
        case 'tts.text':
            speakOnClient(data.text);
            break;
        case 'audio.ding':
            // '띵' 소리는 제거되었으므로 이 부분은 비워둡니다.
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
                updateConsultationStatus('💬 상담원 응답 완료');
                const fullResponse = contentDiv.textContent;
                speakOnClient(fullResponse);
            } else if (data.delta) {
                contentDiv.textContent += data.delta;
                document.getElementById('gpt-response').classList.add('show');
            }
            break;
        case 'status':
            // 'listening' 상태일 때 UI 텍스트를 직접 바꾸는 대신, 상태 바만 업데이트합니다.
            updateStatus('📡 ' + data.state);
            break;
        default:
            console.warn('알 수 없는 메시지 타입:', data.type);
    }
}

// ===== 마이크 및 음성 인식 =====
function activateMicrophone() {
    console.log('🎤 마이크 활성화');
    document.getElementById('mic-indicator').classList.add('active');
    
    // ▼▼▼ 이 함수가 호출될 때만 UI 텍스트를 변경하도록 수정합니다. ▼▼▼
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
    if (currentRecognition) {
        currentRecognition.stop();
    }
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    currentRecognition = new SpeechRecognition();
    currentRecognition.lang = 'ko-KR';
    currentRecognition.continuous = false; // 한 문장씩 인식

    currentRecognition.onresult = function(event) {
        const result = event.results[0][0].transcript.trim();
        console.log('🎤 인식된 텍스트:', result);
        if (result.length > 1 && websocket && websocket.readyState === WebSocket.OPEN) {
            websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
        }
    };

    currentRecognition.onerror = function(event) {
        if (event.error !== 'no-speech') {
            console.error('❌ 음성 인식 오류:', event.error);
        }
    };

    currentRecognition.onend = function() {
        const micIndicator = document.getElementById('mic-indicator');
        // 마이크가 여전히 활성 상태여야 할 때만 재시작
        if (micIndicator.classList.contains('active')) {
            setTimeout(() => {
                if (micIndicator.classList.contains('active')) startSpeechRecognition();
            }, 500);
        }
    };
    
    currentRecognition.start();
}

function stopSpeechRecognition() {
    if (currentRecognition) {
        currentRecognition.onend = null; // 재시작 로직 방지
        currentRecognition.stop();
        currentRecognition = null;
    }
}

// ===== 유틸리티 및 UI 함수 =====
function playDingSound() {
    console.log('🔔 띵 효과음 재생');
    const audio = new Audio('/static/audio/ding.wav');
    audio.play().catch(error => console.error('효과음 재생 실패:', error));
}

function verifyResidentId() {
    const ridValue = document.getElementById('resident-id').value;
    if (ridValue.length !== 6) {
        updateStatus('⚠️ 주민번호 앞 6자리를 정확히 입력해주세요');
        return;
    }
    updateStatus('신원 확인 중...');
    fetch('/kiosk/id-verify/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', },
        body: JSON.stringify({ value: ridValue })
    })
    .then(response => response.json())
    .then(data => {
        if (data.ok) {
            updateStatus('✅ 신원 확인 완료 - 서비스 페이지로 이동합니다');
            setTimeout(() => { window.location.href = '/kiosk/services/'; }, 1500);
        } else {
            updateStatus('❌ 신원 확인 실패: ' + (data.error || '다시 시도해주세요'));
        }
    })
    .catch(error => {
        console.error('API 오류:', error);
        updateStatus('❌ 네트워크 오류가 발생했습니다');
    });
}

function updateConsultationStatus(message) {
    document.getElementById('consultation-status').textContent = message;
    console.log('💬 상담 상태:', message);
}

function updateStatus(message) {
    document.getElementById('status-bar').textContent = message;
    console.log('📊 상태:', message);
}

function setupEventListeners() {
    document.getElementById('verify-button').addEventListener('click', verifyResidentId);
    document.getElementById('resident-id').addEventListener('input', function(e) {
        e.target.value = e.target.value.replace(/[^0-9]/g, '');
    });
    document.getElementById('resident-id').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') verifyResidentId();
    });
}

// ===== 페이지 로드 시 실행 =====
document.addEventListener('DOMContentLoaded', function() {
    updateStatus('시스템 초기화 중...');
    updateConsultationStatus('WebSocket 연결 중...');
    initializeWebSocket();
    setupEventListeners();
});