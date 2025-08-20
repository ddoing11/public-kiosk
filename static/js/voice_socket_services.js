/**
 * 병원 서류 선택 키오스크 - WebSocket 클라이언트
 * services 페이지 전용 (수정된 버전)
 */
// ===== Guard: prevent double load =====
if (window.__svc_bootstrapped) {
  console.warn('[SERVICES] script already loaded, skip init');
} else {
  window.__svc_bootstrapped = true;

  // 전역 변수 선언
  let serviceWebSocket = null;
  let isListening = false;
  let recognition = null;
  let selectedService = null;
  let azureTokenInfo = null;
  let azureSynthesizer = null; // Synthesizer 인스턴스를 재사용하기 위한 변수

  // ===== Azure Speech SDK 설정 =====

  // 1. Azure Speech Token 가져오기 (9분마다 갱신)
  async function getAzureToken() {
    if (azureTokenInfo && Date.now() < azureTokenInfo.expireAt) {
      return azureTokenInfo;
    }
    try {
      const res = await fetch('/speech/token/');
      if (!res.ok) {
        throw new Error(`Token fetch failed with status: ${res.status}`);
      }
      const j = await res.json();
      azureTokenInfo = { token: j.token, region: j.region, expireAt: Date.now() + 9 * 60 * 1000 };
      console.log('Azure Speech Token successfully fetched.');
      return azureTokenInfo;
    } catch (error) {
      console.error('Failed to get Azure token:', error);
      updateStatus('오류: 음성 서비스 인증에 실패했습니다.');
      return null;
    }
  }

  // 2. Azure TTS로 텍스트를 음성으로 변환하는 함수
  async function speakOnClient(text) {
    if (!window.SpeechSDK) {
      console.error('SpeechSDK not loaded. Cannot play TTS.');
      return;
    }
    
    deactivateMic(); // 안내 시작 전 마이크 끄기
    updateVoiceStatus('🔊 안내 음성 출력 중...');

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
      activateMic();

    } catch (error) {
      console.error('An error occurred during Azure TTS process:', error);
      // 오류 발생 시에도 마이크를 다시 켤 수 있도록 처리
      activateMic();
    }
  }


  /**
   * WebSocket 초기화
   */
  function initWebSocket() {
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsProtocol}//${window.location.host}/ws/kiosk/services/`;
      serviceWebSocket = new WebSocket(wsUrl);

      serviceWebSocket.onopen = () => {
          console.log('✅ WebSocket 연결됨');
          updateStatus('음성 서비스 준비 완료');
      };

      serviceWebSocket.onmessage = (event) => {
          const data = JSON.parse(event.data);
          handleMessage(data);
      };

      serviceWebSocket.onclose = (event) => {
          console.warn('WebSocket 연결 종료:', event.code, event.reason);
          updateStatus('음성 서비스 연결이 끊겼습니다. 5초 후 재연결합니다.');
          setTimeout(initWebSocket, 5000);
      };

      serviceWebSocket.onerror = (error) => {
          console.error('WebSocket 오류:', error);
      };
  }

  /**
   * 서버 메시지 처리 라우터
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
              updateVoiceStatus(`"${data.document_type}" 문서를 선택하셨습니다.`);
              selectedService = data.document_type;
              break;
          case 'db.results':
              displayResults(data.results);
              break;
          case 'error':
              showError(data.message);
              break;
          case 'tts.text':
              speakOnClient(data.text);
              break;
          case 'audio.ding':
              playDingSound();
              break;
          default:
              console.warn(`Unknown message type received: ${data.type}`);
      }
  }

  /**
   * 마이크 활성화 및 음성 인식 시작
   */
  function activateMic() {
      isListening = true;
      document.getElementById('mic-indicator').classList.add('active');
      updateVoiceStatus('음성 인식 중... 원하시는 서류를 말씀해주세요.');
      startRecognition();
  }

  /**
   * 마이크 비활성화 및 음성 인식 중지
   */
  function deactivateMic() {
      isListening = false;
      document.getElementById('mic-indicator').classList.remove('active');
      updateVoiceStatus('음성 처리 중입니다...');
      stopRecognition();
  }

  /**
   * Web Speech API를 사용한 음성 인식 시작
   */
  function startRecognition() {
      if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
          updateStatus('오류: 이 브라우저는 음성 인식을 지원하지 않습니다.');
          return;
      }
      // 이미 인식이 실행 중이면 중복 실행 방지
      if (recognition && isListening) return;

      const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
      recognition = new SpeechRecognition();
      recognition.lang = 'ko-KR';
      recognition.interimResults = false; // 최종 결과만 받음
      recognition.continuous = false;

      recognition.onresult = (event) => {
          const finalTranscript = event.results[event.resultIndex][0].transcript.trim();
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
          if (isListening) { // isListening 플래그가 true일 때만 재시작
              setTimeout(() => {
                  if (isListening) startRecognition();
              }, 300); // 짧은 지연 후 재시작
          }
      };

      recognition.start();
  }

  /**
   * 음성 인식 명시적 중지
   */
  function stopRecognition() {
      if (recognition) {
          recognition.stop();
          recognition = null;
      }
  }

  /**
   * 인식된 음성 텍스트를 WebSocket으로 서버에 전송
   */
  function sendVoiceInput(text) {
      if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
          serviceWebSocket.send(JSON.stringify({
              type: 'voice.input',
              text: text
          }));
          showLoading(true);
      }
  }

  /**
   * UI에서 특정 서비스 카드를 선택된 상태로 변경
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
   * DB 조회 결과를 테이블 형태로 화면에 표시
   */
  function displayResults(results) {
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

      updateStatus(`총 ${results.length}건의 결과를 찾았습니다.`);
  }

  /**
   * 인식된 음성 텍스트를 화면에 표시
   */
  function showRecognized(text) {
      const box = document.getElementById('recognized-text');
      const content = document.getElementById('recognized-content');
      box.classList.add('show');
      content.textContent = text;
  }

  /**
   * 로딩 스피너 표시/숨김
   */
  function showLoading(show) {
      document.getElementById('loading-spinner').classList.toggle('show', show);
  }

  /**
   * 오류 메시지 표시
   */
  function showError(message) {
      showLoading(false);
      updateVoiceStatus(`오류: ${message}`);
      updateStatus('오류가 발생했습니다. 잠시 후 다시 시도해주세요.');
  }

  /**
   * UI 상태 업데이트 함수들
   */
  function updateVoiceStatus(message) {
      document.getElementById('voice-status').textContent = message;
  }
  function updateStatus(message) {
      document.getElementById('status-bar').textContent = message;
  }

  /**
   * 서비스 카드 클릭 이벤트 리스너 설정
   */
  function setupEventListeners() {
      document.querySelectorAll('.service-card').forEach(card => {
          card.addEventListener('click', function() {
              const serviceName = this.dataset.service;
              selectCard(serviceName);
              if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
                  serviceWebSocket.send(JSON.stringify({
                      type: 'service.select',
                      service: serviceName
                  }));
              }
          });
      });
  }
  
  /**
   * 띵 효과음 재생
   */
  function playDingSound() {
    const audio = new Audio('/static/audio/ding.wav');
    audio.play().catch(err => console.debug('효과음 재생 실패:', err));
  }


  // ===== 페이지 로드 시 실행 =====
  document.addEventListener('DOMContentLoaded', function() {
      setupEventListeners();
      updateStatus('시스템 초기화 중...');
      initWebSocket();
  });
}