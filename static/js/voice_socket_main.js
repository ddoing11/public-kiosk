/**
 * 병원 서류 출력 키오스크 - WebSocket 클라이언트
 * 
 * === WebSocket 메시지 계약 (Event Contract) ===
 * 
 * 클라이언트 → 서버 (송신):
 * - {"type": "ui.touch_start"}                    // 화면 터치 시작
 * - {"type": "stt.result", "text": "상담"}        // STT 최종 결과  
 * - {"type": "stt.partial", "text": "상..."}      // STT 부분 결과 (옵션)
 * 
 * 서버 → 클라이언트 (수신):
 * - {"type": "tts.say", "text": "안내멘트"}       // TTS 음성 출력 요청
 * - {"type": "audio.ding"}                       // 띵 효과음 재생
 * - {"type": "mic.on"}                          // 마이크 켜기
 * - {"type": "mic.off"}                         // 마이크 끄기  
 * - {"type": "gpt.stream", "delta": "토큰"}      // GPT 스트리밍 토큰
 * - {"type": "gpt.stream", "event": "done"}     // GPT 응답 완료
 * - {"type": "status", "state": "listening"}    // 상태 알림
 * 
 * === 플로우 순서 보장 ===
 * 1. ui.touch_start → mic.off → tts.say → audio.ding → (50~100ms) → mic.on
 * 2. stt.result("상담") → gpt.stream(연속) → gpt.stream(done) → audio.ding → mic.on
 * 3. 에러 시 → tts.say(사과) → audio.ding → mic.on (복귀)
 * 
 * === 마이크·TTS 타이밍 규칙 ===
 * - 모든 TTS 전에 반드시 mic.off 먼저 실행
 * - TTS 완료 후 audio.ding → 50~100ms 지연 → mic.on
 * - 겹침 방지는 서버에서 플래그/상태로 보장
 * - 클라이언트는 mic.on/off 메시지에 따라 UI만 업데이트
 */

// 전역 변수
let kioskWebSocket = null;
let microphoneActive = false;
let speechRecognition = null;
let isProcessing = false;

/**
 * WebSocket 연결 초기화
 */
function initializeWebSocket() {
    // 실제 구현은 프론트 담당 단계에서 완성
    console.log('WebSocket 연결 초기화 예정');
    
    // 예시 연결 코드 (실제로는 환경에 맞게 수정)
    // const wsUrl = `ws://${window.location.host}/ws/kiosk/`;
    // kioskWebSocket = new WebSocket(wsUrl);
    
    // kioskWebSocket.onopen = handleWebSocketOpen;
    // kioskWebSocket.onmessage = handleWebSocketMessage;
    // kioskWebSocket.onclose = handleWebSocketClose;
    // kioskWebSocket.onerror = handleWebSocketError;
}

/**
 * WebSocket 메시지 처리
 */
function handleWebSocketMessage(event) {
    // 실제 구현은 프론트 담당 단계에서 완성
    console.log('WebSocket 메시지 수신:', event.data);
    
    try {
        const data = JSON.parse(event.data);
        
        switch(data.type) {
            case 'tts.say':
                handleTTSRequest(data.text);
                break;
            case 'audio.ding':
                playDingSound();
                break;
            case 'mic.on':
                activateMicrophone();
                break;
            case 'mic.off':
                deactivateMicrophone();
                break;
            case 'gpt.stream':
                handleGPTStream(data);
                break;
            case 'status':
                updateStatus(data.state);
                break;
            default:
                console.warn('알 수 없는 메시지 타입:', data.type);
        }
    } catch (error) {
        console.error('WebSocket 메시지 파싱 오류:', error);
    }
}

/**
 * TTS 음성 출력 처리
 */
function handleTTSRequest(text) {
    // 실제 구현: Web Speech API 또는 외부 TTS 서비스 사용
    console.log('TTS 출력 요청:', text);
    
    // 임시 구현 (실제로는 음성 합성)
    if ('speechSynthesis' in window) {
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'ko-KR';
        utterance.rate = 0.9;
        speechSynthesis.speak(utterance);
    }
}

/**
 * 띵 효과음 재생
 */
function playDingSound() {
    // 실제 구현: audio 태그 또는 Web Audio API 사용
    console.log('띵 효과음 재생');
    
    // 임시 구현
    const audio = new Audio('/static/audio/ding.wav');
    audio.play().catch(error => {
        console.error('효과음 재생 실패:', error);
    });
}

/**
 * 마이크 활성화
 */
function activateMicrophone() {
    console.log('마이크 활성화');
    microphoneActive = true;
    
    // 실제 구현: Web Speech API STT 시작
    // if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
    //     startSpeechRecognition();
    // }
    
    // UI 업데이트
    updateMicrophoneStatus(true);
}

/**
 * 마이크 비활성화  
 */
function deactivateMicrophone() {
    console.log('마이크 비활성화');
    microphoneActive = false;
    
    // 실제 구현: STT 중지
    // if (speechRecognition) {
    //     speechRecognition.stop();
    // }
    
    // UI 업데이트
    updateMicrophoneStatus(false);
}

/**
 * GPT 스트리밍 응답 처리
 */
function handleGPTStream(data) {
    // 실제 구현: 스트리밍 텍스트를 화면에 점진적 출력
    console.log('GPT 스트리밍:', data);
    
    if (data.event === 'done') {
        console.log('GPT 응답 완료');
        // 스트리밍 완료 처리
    } else if (data.delta) {
        console.log('GPT 토큰:', data.delta);
        // 토큰을 화면에 추가
    }
}

/**
 * 상태 업데이트
 */
function updateStatus(state) {
    console.log('상태 변경:', state);
    
    // 실제 구현: UI 상태 표시 업데이트
    const statusElement = document.getElementById('status');
    if (statusElement) {
        statusElement.textContent = `상태: ${state}`;
    }
}

/**
 * 마이크 상태 UI 업데이트
 */
function updateMicrophoneStatus(active) {
    // 실제 구현: 마이크 아이콘 색상 변경, 애니메이션 등
    console.log('마이크 상태 UI 업데이트:', active ? '활성' : '비활성');
}

/**
 * 화면 터치 이벤트 전송
 */
function sendTouchStart() {
    // 실제 구현: WebSocket을 통해 서버에 터치 이벤트 전송
    console.log('터치 시작 이벤트 전송');
    
    if (kioskWebSocket && kioskWebSocket.readyState === WebSocket.OPEN) {
        const message = { type: 'ui.touch_start' };
        kioskWebSocket.send(JSON.stringify(message));
    }
}

/**
 * STT 결과 전송
 */
function sendSTTResult(text) {
    // 실제 구현: 음성 인식 결과를 서버로 전송
    console.log('STT 결과 전송:', text);
    
    if (kioskWebSocket && kioskWebSocket.readyState === WebSocket.OPEN) {
        const message = { 
            type: 'stt.result',
            text: text 
        };
        kioskWebSocket.send(JSON.stringify(message));
    }
}

/**
 * 초기화
 */
document.addEventListener('DOMContentLoaded', function() {
    console.log('키오스크 WebSocket 클라이언트 초기화');
    
    // WebSocket 연결 (실제 구현에서 활성화)
    // initializeWebSocket();
    
    // 이벤트 리스너 등록
    const touchButton = document.getElementById('touch');
    if (touchButton) {
        touchButton.addEventListener('click', sendTouchStart);
    }
});

// 전역 함수로 노출 (다른 스크립트에서 사용 가능)
window.KioskWebSocket = {
    sendTouchStart,
    sendSTTResult,
    activateMicrophone,
    deactivateMicrophone
};

/*
프론트 담당 단계에서 구현할 추가 기능들:
- 실제 WebSocket 연결 및 에러 처리
- Web Speech API를 이용한 STT 구현
- TTS 음성 출력 (Web Speech API 또는 외부 서비스)
- 마이크 권한 요청 및 상태 관리
- 시각적 피드백 (마이크 활성화 표시, 음성 인식 애니메이션)
- 접근성 개선 (키보드 내비게이션, 스크린 리더 지원)
- 에러 상황 처리 및 복구
- 연결 끊김 시 자동 재연결
- 성능 최적화 및 메모리 관리
*/