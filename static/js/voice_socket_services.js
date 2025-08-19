/**
 * 병원 서류 선택 키오스크 - WebSocket 클라이언트
 * services 페이지 전용
 */
// ===== Guard: prevent double load =====
if (window.__svc_bootstrapped) {
  console.warn('[SERVICES] script already loaded, skip init');
} else {
  window.__svc_bootstrapped = true;


  // ===== Azure Speech SDK wiring =====
  let azureTokenInfo = null;
  async function getAzureToken() {
    if (azureTokenInfo && Date.now() < azureTokenInfo.expireAt) return azureTokenInfo;
    const res = await fetch('/speech/token');
    const j = await res.json();
    azureTokenInfo = { token: j.token, region: j.region, expireAt: Date.now() + 9 * 60 * 1000 };
    return azureTokenInfo;
  }

  async function speakAzure(text) {
    if (!window.SpeechSDK) {
      console.error('SpeechSDK not loaded');
      return;
    }
    const { token, region } = await getAzureToken();
    const speechConfig = SpeechSDK.SpeechConfig.fromAuthorizationToken(token, region);
    speechConfig.speechSynthesisLanguage = 'ko-KR';
    speechConfig.speechSynthesisVoiceName = 'ko-KR-SunHiNeural';
    const audioConfig = SpeechSDK.AudioConfig.fromDefaultSpeakerOutput();

    // 재선언 금지: 전역에 한 번만 유지
    if (!window.__azureSynth) {
      window.__azureSynth = new SpeechSDK.SpeechSynthesizer(speechConfig, audioConfig);
    }

    return new Promise((resolve, reject) => {
      window.__azureSynth.speakTextAsync(
        text,
        (result) => {
          if (result.reason === SpeechSDK.ResultReason.SynthesizingAudioCompleted) {
            resolve();
          } else {
            reject(result.errorDetails);
          }
        },
        (err) => reject(err)
      );
    });
  }

    // 전역 변수
    let serviceWebSocket = null;
    let isListening = false;
    let recognition = null;
    let selectedService = null;

    /**
     * WebSocket 초기화
     */
    function initWebSocket() {
        const wsUrl = `ws://${window.location.host}/ws/kiosk/services/`;
        serviceWebSocket = new WebSocket(wsUrl);
        
        serviceWebSocket.onopen = () => {
            console.log('✅ WebSocket 연결됨');
            updateStatus('음성 서비스 준비 완료');
        };
        
        serviceWebSocket.onmessage = (event) => {
            const data = JSON.parse(event.data);
            handleMessage(data);
        };
        
        serviceWebSocket.onclose = () => {
            console.log('WebSocket 연결 종료');
            updateStatus('음성 서비스 연결 끊김');
            setTimeout(initWebSocket, 5000); // 5초 후 재연결
        };
        
        serviceWebSocket.onerror = (error) => {
            console.error('WebSocket 오류:', error);
        };
    }

    /**
     * 메시지 처리
     */
    function handleMessage(data) {
        switch(data.type) {
            case 'mic.on':
                activateMic();
                break;
            case 'mic.off':
                deactivateMic();
                break;
            case 'document.recognized':
                selectCard(data.document_type);
                updateVoiceStatus(`${data.document_type} 선택됨`);
                selectedService = data.document_type;
                break;
            case 'db.results':
                displayResults(data.results);
                break;
            case 'error':
                showError(data.message);
                break;

            // 🔥 서버가 텍스트만 보내면 여기서 합성
            case 'tts.text':
                speakOnClient(data.text);
                break;

            
            case 'audio.ding':
                playDingSound();
                break;
        }
    }

    /**
     * 마이크 활성화
     */
    function activateMic() {
        isListening = true;
        document.getElementById('mic-indicator').classList.add('active');
        updateVoiceStatus('음성 인식 중... 서류명을 말씀해주세요');
        startRecognition();
    }

    /**
     * 마이크 비활성화
     */
    function deactivateMic() {
        isListening = false;
        document.getElementById('mic-indicator').classList.remove('active');
        updateVoiceStatus('음성 처리 중...');
        stopRecognition();
    }

    /**
     * 음성 인식 시작
     */
    function startRecognition() {
        if (!('webkitSpeechRecognition' in window)) return;
        
        recognition = new webkitSpeechRecognition();
        recognition.lang = 'ko-KR';
        recognition.interimResults = true;
        recognition.continuous = false;
        
        recognition.onresult = (event) => {
            let finalTranscript = '';
            
            for (let i = event.resultIndex; i < event.results.length; i++) {
                if (event.results[i].isFinal) {
                    finalTranscript = event.results[i][0].transcript;
                }
            }
            
            if (finalTranscript) {
                showRecognized(finalTranscript);
                sendVoiceInput(finalTranscript);
            }
        };
        
        recognition.onerror = (event) => {
            if (event.error !== 'no-speech') {
                console.error('음성 인식 오류:', event.error);
            }
        };
        
        recognition.onend = () => {
            if (isListening) {
                setTimeout(() => {
                    if (isListening) startRecognition();
                }, 500);
            }
        };
        
        recognition.start();
    }

    /**
     * 음성 인식 중지
     */
    function stopRecognition() {
        if (recognition) {
            recognition.stop();
            recognition = null;
        }
    }

    /**
     * 음성 입력 전송
     */
    function sendVoiceInput(text) {
        if (serviceWebSocket.readyState === WebSocket.OPEN) {
            serviceWebSocket.send(JSON.stringify({
                type: 'voice.input',
                text: text
            }));
            showLoading(true);
        }
    }

    /**
     * 서비스 카드 선택
     */
    function selectCard(serviceName) {
        document.querySelectorAll('.service-card').forEach(card => {
            card.classList.remove('selected');
        });
        
        const card = document.querySelector(`[data-service="${serviceName}"]`);
        if (card) {
            card.classList.add('selected');
        }
        showLoading(false);
    }

    /**
     * DB 결과 표시
     */
    function displayResults(results) {
        const container = document.getElementById('result-table');
        const section = document.getElementById('db-results');
        
        if (!results || results.length === 0) {
            container.innerHTML = '<p>조회된 결과가 없습니다.</p>';
            section.classList.add('show');
            return;
        }
        
        // 테이블 생성
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
        
        updateStatus(`${results.length}건의 결과를 찾았습니다.`);
    }

    /**
     * 인식된 텍스트 표시
     */
    function showRecognized(text) {
        const box = document.getElementById('recognized-text');
        const content = document.getElementById('recognized-content');
        box.classList.add('show');
        content.textContent = text;
    }

    /**
     * 로딩 표시
     */
    function showLoading(show) {
        const spinner = document.getElementById('loading-spinner');
        spinner.classList.toggle('show', show);
    }

    /**
     * 에러 표시
     */
    function showError(message) {
        showLoading(false);
        updateVoiceStatus('오류: ' + message);
        updateStatus('처리 중 오류가 발생했습니다.');
    }

    /**
     * 상태 업데이트
     */
    function updateVoiceStatus(message) {
        document.getElementById('voice-status').textContent = message;
    }

    function updateStatus(message) {
        document.getElementById('status-bar').textContent = message;
    }

    /**
     * 이벤트 리스너 설정
     */
    function setupEventListeners() {
        // 서비스 카드 클릭
        document.querySelectorAll('.service-card').forEach(card => {
            card.addEventListener('click', function() {
                selectCard(this.dataset.service);
                
                if (serviceWebSocket.readyState === WebSocket.OPEN) {
                    serviceWebSocket.send(JSON.stringify({
                        type: 'service.select',
                        service: this.dataset.service
                    }));
                }
            });
        });
    }

    /**
     * 초기화
     */
    document.addEventListener('DOMContentLoaded', function() {
        initWebSocket();
        setupEventListeners();
        updateStatus('시스템 초기화 중...');
        initWebSocket();
    });

    // 전역 노출 (디버깅용)
    window.ServiceKiosk = {
        reconnect: initWebSocket,
        selectService: selectCard,
        getSelected: () => selectedService
    };


    function playDingSound() {
    const audio = new Audio('/static/audio/ding.wav');
    audio.play().catch(err => console.debug('효과음 재생 차단/실패:', err));
    }

    function playBrowserTTS(text) {
    if ('speechSynthesis' in window) {
        const u = new SpeechSynthesisUtterance(text);
        u.lang = 'ko-KR';
        u.rate = 0.95;
        speechSynthesis.speak(u);
    }
    }

    document.addEventListener('DOMContentLoaded', function() {
        initWebSocket();
        setupEventListeners();
        updateStatus('시스템 초기화 중...');
    });
}

function speakOnClient(text) {
  // 안내 중 자기 음성 인식 방지: 일단 STT 중지
  if (recognition) {
    try { recognition.abort(); } catch (_) {}
    recognition = null;
  }
  isListening = false;
  document.getElementById('mic-indicator').classList.remove('active');
  updateVoiceStatus('🔊 안내 중...');

  // 브라우저 TTS (간단/즉시 테스트용)
  if ('speechSynthesis' in window) {
    try { speechSynthesis.cancel(); } catch (_) {}
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ko-KR';
    u.rate = 0.95;
    u.onend = () => {
      // 안내 끝 → 띵 → 잠깐 딜레이 → 마이크 켜기
      playDingSound();
      setTimeout(() => {
        activateMic();
      }, 250);
    };
    speechSynthesis.speak(u);
  } else {
    // TTS 미지원 브라우저면 그냥 ding 후 마이크 ON
    playDingSound();
    setTimeout(() => {
      activateMic();
    }, 250);
  }
}
