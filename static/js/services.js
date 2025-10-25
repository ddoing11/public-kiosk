// static/js/services.js (수정된 완전 버전)

// GPT full_text 3문장 그대로 표시/재생 + 마지막 CTA(발급 유도) 보장
// TTS 겹침 방지, 자기 TTS 에코 필터, 마이크 자동 재활성화 유지

import { speakOnClient } from './modules/azure-tts.js';
import { displayPatientInfo, updateVoiceStatus, updateStatus, selectCard, displayResults, showLoading } from './modules/ui-services.js';

// ===== 전역 변수 =====
let serviceWebSocket = null;
let recognition = null;
let lastSpokenTTS = "";              // 마지막 실제 발화된 TTS(자기음성 무시용)
let lastTTSRequestText = "";         // 최근 요청된(재생 대기 포함) TTS(중복요청 차단용)
let isListening = false;
let isTTSPlaying = false;            // TTS 재생 상태
let ttsTimeoutId = null;             // TTS 타임아웃 ID
let allowShortInput = false;         // 짧은 음성 인식 허용 여부

// 짧은 음성 버퍼링
let shortInputBuffer = [];
let shortInputTimer = null;
const SHORT_INPUT_WAIT_TIME = 2000;  // 2초 대기

// ===== 유틸 =====
function safeEl(id) { return document.getElementById(id); }
function normalize(s) { return (s || "").replace(/\s+/g, "").toLowerCase(); }
function nearDuplicate(a, b) {
  if (!a || !b) return false;
  const A = normalize(a), B = normalize(b);
  if (A === B) return true;
  return A.length >= 10 && (A.includes(B) || B.includes(A));
}
function shortKR(text, max = 60) {
  if (!text) return "";
  const t = String(text).replace(/\s+/g, " ").trim();
  if (t.length <= max) return t;
  const m = t.match(/(.+?[.!?])\s/);
  const head = m ? m[1] : t.slice(0, max);
  return head.length <= max ? head : head.slice(0, max) + "…";
}

// --- 한글 받침 체크 (이라고/라고) ---
function hasFinalConsonant(ch) {
  const code = ch.charCodeAt(0) - 0xAC00;
  if (code < 0 || code > 11171) return false;
  return code % 28 !== 0;
}
function iraGo(noun) {
  if (!noun) return '라고';
  const last = noun[noun.length - 1];
  return hasFinalConsonant(last) ? '이라고' : '라고';
}

// --- 문장 마무리 & CTA 보장 ---
function extractDocName(text) {
  const docs = ['진료확인서', '처방전', '진료영수증'];
  for (const d of docs) if ((text || '').includes(d)) return d;
  return null;
}
function ensureCTA(fullText) {
  let t = (fullText || '').trim();
  // 공백 정돈 & 문장부호 뒤 공백 정리
  t = t.replace(/\s+/g, ' ').replace(/\s*([.?!])\s*/g, '$1 ').trim();

  // 이미 CTA가 있다면 그대로
  if (/발급.*말씀해\s?주세요/.test(t) || /말씀해 주세요/.test(t)) return t;

  const doc = extractDocName(t);
  const cta = doc
    ? `발급을 원하시면 '${doc}'${iraGo(doc)} 말씀해주세요.`
    : `발급을 원하시면 원하는 서류명을 말씀해주세요.`;

  if (!/[.!?]\s*$/.test(t)) t += '.';
  return `${t} ${cta}`.trim();
}

// ===== 마이크 및 음성 인식 =====
function activateMic() {
  if (isListening) return; // 중복 방지
  const mic = safeEl('mic-indicator');
  if (mic) mic.classList.add('active');
  updateVoiceStatus('음성 인식 중... 말씀해주세요.');
  isListening = true;
  startRecognition();
}

function deactivateMic() {
  const mic = safeEl('mic-indicator');
  if (mic) mic.classList.remove('active');
  isListening = false;
  stopRecognition();
}

function startRecognition() {
  if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
    updateStatus('오류: 이 브라우저는 음성 인식을 지원하지 않습니다.');
    return;
  }
  if (recognition) return;             // 중복 방지
  if (isTTSPlaying) {                  // TTS 재생 중 차단
    console.log('🚫 TTS 재생 중이므로 음성 인식 시작 안함');
    return;
  }

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRecognition();
  recognition.lang = 'ko-KR';
  recognition.continuous = false;     // 한 문장
  recognition.interimResults = false; // 중간결과 X

  recognition.onresult = (event) => {
    const finalTranscript = event.results[0][0].transcript.trim().replace(/[.,?!]/g, "");
    console.log('🎤 인식된 텍스트:', finalTranscript);

    if (shouldIgnoreTranscript(finalTranscript)) {
      console.warn(`자기 음성/잡음 등으로 무시: "${finalTranscript}"`);
      return;
    }

    if (finalTranscript) {
      updateVoiceStatus(`"${finalTranscript}" 처리 중...`);
      sendVoiceInput(finalTranscript);
    }
  };

  recognition.onerror = (event) => {
    console.error('음성 인식 오류:', event.error);
    if (!['no-speech', 'aborted', 'network'].includes(event.error)) {
      updateVoiceStatus('음성 인식 오류가 발생했습니다. 다시 시도해주세요.');
      setTimeout(() => {
        if (isListening && !isTTSPlaying) updateVoiceStatus('음성 인식 중... 말씀해주세요.');
      }, 2000);
    }
  };

  recognition.onend = () => {
    recognition = null;
    const mic = safeEl('mic-indicator');
    if (mic && mic.classList.contains('active') && isListening && !isTTSPlaying) {
      setTimeout(startRecognition, 100); // 0.1초 뒤 재시작
    }
  };

  recognition.start();
  console.log('🎤 음성 인식 시작');
}

function stopRecognition() {
  if (recognition) {
    isListening = false;
    const mic = safeEl('mic-indicator');
    if (mic) mic.classList.remove('active');
    recognition.stop();
    recognition = null;
  }
}

// ===== 인식 필터링 =====
function shouldIgnoreTranscript(transcript) {
  if (!transcript) return true;

  // TTS 재생 중엔 무조건 무시
  if (isTTSPlaying) {
    console.log(`🚫 TTS 재생 중이므로 무시: "${transcript}"`);
    return true;
  }

  // 중요한 키워드는 길이에 상관없이 허용
  const importantKeywords = ['상담','문의','질문','도움','처방전','진료확인서','진료영수증','확인서','영수증'];
  for (const k of importantKeywords) {
    if (transcript.includes(k)) {
      console.log(`✅ 중요한 키워드 포함으로 허용: "${transcript}" (키워드: ${k})`);
      return false;
    }
  }

  // 발급 확인 모드에서는 "발급/취소"만 허용 (네/아니요 제거)
  if (allowShortInput) {
    const confirmKeywords = ['발급', '취소', '발급해', '취소해'];
    if (confirmKeywords.some(k => transcript.includes(k))) {
      console.log(`✅ 발급 확인 모드 - 허용: "${transcript}"`);
      return false;
    }
  }

  // 짧은 입력(<=4글자)은 버퍼링 (확인모드가 아니면 일단 보류)
  if (transcript.length <= 4 && !allowShortInput) {
    console.log(`📝 짧은 음성 버퍼링: "${transcript}"`);
    handleShortInput(transcript);
    return true;
  }

  // 자기 음성(직전 TTS) 완전일치 무시 (부분일치는 허용)
  if (normalize(transcript) === normalize(lastSpokenTTS)) {
    console.log(`🚫 직전 TTS와 완전 일치 → 무시: "${transcript}"`);
    return true;
  }

  // 너무 짧은 1글자/감탄사 무시
  if (transcript.length <= 1) return true;
  const noise = ['음','어','아','그','저'];
  if (noise.includes(transcript.trim())) return true;

  // 가벼운 상투구 무시(중요 키워드 없는 짧은 상투어)
  const common = ['죄송합니다','감사합니다','안녕하세요','말씀해','주세요','드리겠습니다','있습니다','필요하시면','궁금한'];
  for (const p of common) {
    if (transcript === p || (transcript.includes(p) && transcript.length < 8)) return true;
  }

  console.log(`✅ 유효한 음성 입력: "${transcript}"`);
  return false;
}

// ===== 짧은 입력 버퍼링 =====
function handleShortInput(transcript) {
  shortInputBuffer.push(transcript);
  console.log(`📝 버퍼에 추가: "${transcript}" (버퍼: [${shortInputBuffer.join(', ')}])`);

  if (shortInputTimer) clearTimeout(shortInputTimer);
  shortInputTimer = setTimeout(processBufferedInput, SHORT_INPUT_WAIT_TIME);
}

function processBufferedInput() {
  if (shortInputBuffer.length === 0) return;
  const combined = shortInputBuffer.join(' ').trim();
  console.log(`🔄 버퍼된 입력 처리: "${combined}"`);
  shortInputBuffer = [];
  shortInputTimer = null;

  if (combined.length >= 4) {
    updateVoiceStatus(`"${combined}" 처리 중...`);
    sendVoiceInput(combined);
  } else {
    console.log(`🚫 버퍼된 입력도 너무 짧아서 무시: "${combined}"`);
  }
}

// ===== TTS 상태 관리 =====
function setTTSPlaying(playing) {
  isTTSPlaying = playing;
  console.log(`🔊 TTS 재생 상태: ${playing ? '시작' : '종료'}`);

  if (playing) {
    if (recognition) {
      recognition.abort();
      recognition = null;
    }
    if (ttsTimeoutId) clearTimeout(ttsTimeoutId);
    ttsTimeoutId = setTimeout(() => {
      console.log('⚠️ TTS 타임아웃으로 강제 해제');
      isTTSPlaying = false;
      setTimeout(() => {
        console.log('🎤 TTS 타임아웃 후 마이크 강제 활성화');
        activateMic();
      }, 500);
    }, 10000);
  } else {
    if (ttsTimeoutId) {
      clearTimeout(ttsTimeoutId);
      ttsTimeoutId = null;
    }
    if (isListening && !recognition) {
      setTimeout(startRecognition, 500);
    } else if (!isListening) {
      setTimeout(() => {
        console.log('🎤 TTS 종료 후 마이크 활성화 (조건 완화)');
        activateMic();
      }, 500);
    }
  }
}

function updateTTSStatus(status) {
  if (status === 'start') setTTSPlaying(true);
  else if (status === 'end') setTTSPlaying(false);
}

// ===== WebSocket =====
function initializeWebSocket() {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/kiosk/services/`;
  serviceWebSocket = new WebSocket(wsUrl);

  serviceWebSocket.onopen = () => {
    updateStatus('음성 서비스 준비 완료');
    console.log('🔗 WebSocket 연결 성공');
    // azure-tts 모듈의 tts.complete 신호가 서버로 가도록 연결 노출
    window.websocket = serviceWebSocket;
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

  switch (data.type) {
    case 'patient.info':
      displayPatientInfo(data.patient);
      break;

    case 'tts.text': {
      const text = String(data.text || "");
      // 같은 요청 반복 방지(클라이언트 레벨)
      if (nearDuplicate(text, lastTTSRequestText)) {
        console.log('⏭️ 중복 TTS 요청 무시:', text);
        break;
      }
      lastTTSRequestText = text;

      console.log('🔊 TTS 재생:', text);
      updateTTSStatus('start');

      speakOnClient(
        text,
        () => {
          // onStart
          lastSpokenTTS = text; // 자기음성 무시용
          console.log('🔊 TTS 재생 상태: 시작');
          updateTTSStatus('start');
          deactivateMic();
        },
        () => {
          // onEnd
          console.log('🔊 TTS 재생 상태: 종료');
          updateTTSStatus('end');
          setTimeout(() => {
            if (!isTTSPlaying) {
              console.log('🎤 TTS 완료 후 마이크 자동 활성화');
              activateMic();
            }
          }, 300);
        }
      );
      break;
    }

    case 'mic.on':
      console.log('🎤 마이크 재활성화 신호 수신');
      if (!isListening && !isTTSPlaying) activateMic();
      break;

    case 'mic.off':
      console.log('🔇 마이크 비활성화 신호 수신');
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
      console.error('🚨 서버 오류:', data.message);
      break;

    case 'gpt.stream':
      handleGPTStream(data);
      break;

    case 'status':
      updateStatus('📡 ' + data.state);
      updateUIBasedOnState(data.state);
      break;

    case 'voice.mode':
      allowShortInput = !!data.allow_short_input;
      console.log(`🎤 짧은 음성 인식 ${allowShortInput ? '허용' : '비허용'}`);
      break;

    default:
      console.warn(`알 수 없는 메시지 타입: ${data.type}`);
  }
}

function handleGPTStream(data) {
  const gptResponseDiv = safeEl('gpt-response');
  if (!gptResponseDiv) return;
  const gptContentDiv = gptResponseDiv.querySelector('#gpt-content');

  if (data.delta) {
    if (gptResponseDiv.style.display === 'none') {
      gptContentDiv.textContent = '';
      gptResponseDiv.style.display = 'block';
    }
    gptContentDiv.textContent += data.delta;
    gptContentDiv.scrollTop = gptContentDiv.scrollHeight;
  }

  if (data.event === 'done' && data.full_text) {
    console.log('✅ GPT 스트리밍 완료');
    // 🎯 최종 문장 사용 + CTA 보장(마지막 문장 필수)
    const finalTextWithCTA = ensureCTA(String(data.full_text || ''));

    // UI에 최종 텍스트 전체 표시(3문장 그대로)
    gptContentDiv.textContent = finalTextWithCTA;
    gptResponseDiv.style.display = 'block';

    // 같은 내용 중복 재생 방지
    if (nearDuplicate(finalTextWithCTA, lastTTSRequestText) || nearDuplicate(finalTextWithCTA, lastSpokenTTS)) {
      console.log('⏭️ GPT 완료 TTS가 최근 내용과 유사 → 클라레벨 스킵');
      return;
    }
    lastTTSRequestText = finalTextWithCTA;

    // 전체 문장 그대로 TTS 재생 (azure-tts에서 문장 분절/순차 재생)
    speakOnClient(
      finalTextWithCTA,
      () => {
        lastSpokenTTS = finalTextWithCTA;
        updateTTSStatus('start');
        deactivateMic();
      },
      () => {
        updateTTSStatus('end');
        setTimeout(() => {
          if (!isTTSPlaying) activateMic();
        }, 500);
      }
    );
  }
}

function updateUIBasedOnState(state) {
  const title = safeEl('voice-section-title');
  if (!title) return;

  if (state === 'advising') {
    title.textContent = '💬 AI와 상담 중';
    updateVoiceStatus('상담 중입니다. 궁금한 점을 말씀해주세요.');
  } else {
    title.textContent = '🎤 음성으로 서류 선택 또는 문의';
    updateVoiceStatus('원하시는 서류를 말씀해주시거나 상담을 요청해주세요.');
  }
}

function sendVoiceInput(text) {
  if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
    console.log('📤 음성 입력 전송:', text);
    serviceWebSocket.send(JSON.stringify({ type: 'voice.input', text }));
    showLoading(true);
  } else {
    console.error('❌ WebSocket 연결 없음');
    updateStatus('서버 연결이 끊어졌습니다. 잠시 후 다시 시도해주세요.');
  }
}

// ===== Azure SDK 동적 로드 및 페이지 초기화 =====
function loadAzureSDK() {
  return new Promise((resolve, reject) => {
    if (window.SpeechSDK) return resolve();
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
document.addEventListener('DOMContentLoaded', async function () {
  console.log('🚀 서비스 페이지 초기화 시작');

  // 서비스 카드 클릭
  document.querySelectorAll('.service-card').forEach(card => {
    card.addEventListener('click', function () {
      const serviceName = this.dataset.service;
      console.log('🖱️ 카드 클릭:', serviceName);
      selectCard(serviceName);
      if (serviceWebSocket && serviceWebSocket.readyState === WebSocket.OPEN) {
        serviceWebSocket.send(JSON.stringify({ type: 'service.select', service: serviceName }));
        showLoading(true);
      }
    });
  });

  // 마이크 버튼
  const micButton = safeEl('mic-button');
  if (micButton) {
    micButton.addEventListener('click', function () {
      if (isListening) deactivateMic();
      else activateMic();
    });
  }

  // 시스템 초기화
  updateStatus('음성 서비스 로딩 중...');
  try {
    await loadAzureSDK();
    updateStatus('시스템 초기화 중...');
    initializeWebSocket();

    // WebSocket 연결 후 잠시 대기하고 마이크 자동 활성화
    setTimeout(() => {
      console.log('🎤 초기화 완료 후 마이크 자동 활성화');
      activateMic();
    }, 2000);

    console.log('✅ 초기화 완료');
  } catch (error) {
    console.error('❌ 초기화 실패:', error);
    updateStatus('오류: 음성 서비스를 불러오지 못했습니다.');
  }
});

// ===== 페이지 종료 시 정리 =====
window.addEventListener('beforeunload', function () {
  console.log('🧹 페이지 종료 - 리소스 정리');
  deactivateMic();
  if (serviceWebSocket) {
    try { serviceWebSocket.close(); } catch {}
  }
});
