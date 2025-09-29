// static/js/voice_socket_main.js
// (최종 안정화 v3 — 본인확인 단계에서 “특정 이름만” 빠르게 통과하지 않도록 일반화)
// - 이름단계/본인확인단계 에코 차단 + 쿨다운 보장
// - 본인확인: 확인 어구, 대상 이름, **임의의 그럴듯한 이름** 모두 즉시 서버 전송(+즉시 stop)
// - 서버 TTS 완료 신호 및 클라 TTS 완료 모두 처리
// - 초기화/재연결/에러 로그 강화

import { speakOnClient } from './modules/azure-tts.js';
import { updateStatus, displayResults } from './modules/ui-main.js';

/* ==============================
 * 전역 변수 및 상태
 * ============================== */
let websocket = null;
let currentRecognition = null;
let clientState = 'listening'; // 'listening' | 'confirming_user' 등
let lastSpokenTTS = "";        // 마지막으로 재생된 TTS 문장
let microphoneEnabled = true;  // 마이크 활성화 상태
let ttsPlaying = false;        // TTS 재생 상태

// 에코/쿨다운 방지용
let lastTTSEndAt = 0;                 // 마지막 TTS 종료 시각(ms)
let TTS_COOLDOWN_MS = 1400;           // 기본 쿨다운
const TTS_COOLDOWN_MS_DEFAULT = 1400; // 복구용
const TTS_COOLDOWN_MS_CONFIRM = 250;  // 본인확인 프롬프트 직후 빠른 응답 허용
let ttsHistory = [];                  // 최근 TTS 문장 히스토리
const TTS_HISTORY_LIMIT = 5;          // 히스토리 최대 개수
const ECHO_MIN_LEN = 6;               // 에코 판정 최소 길이
const ECHO_SIM_THRESHOLD = 0.78;      // 에코 판정 유사도 기준

// 본인 확인 대상 이름(서버가 안내 TTS에서 말해준 이름을 파싱해 저장)
let confirmTargetName = null;         // 예: "이서준"
let confirmTargetNameNorm = null;     // 예: "이서준" (정규화본)

/* ==============================
 * 유틸 함수
 * ============================== */
function normalize(s = '') {
  return s.replace(/\s+/g, '').replace(/\u200b/g, '').trim().toLowerCase();
}
function charOverlapRatio(a, b) {
  const A = normalize(a), B = normalize(b);
  if (!A || !B) return 0;
  let inter = 0;
  for (const ch of A) if (B.includes(ch)) inter++;
  const denom = Math.max(A.length, B.length);
  return denom ? inter / denom : 0;
}
function pushTTSHistory(text) {
  if (!text) return;
  ttsHistory.unshift(text);
  if (ttsHistory.length > TTS_HISTORY_LIMIT) ttsHistory = ttsHistory.slice(0, TTS_HISTORY_LIMIT);
}
function isWithinCooldown() {
  return (Date.now() - lastTTSEndAt) < TTS_COOLDOWN_MS;
}
function isNameStageActive() {
  const nameSearchSection = document.getElementById('nameSearchSection');
  return !!(nameSearchSection && window.getComputedStyle(nameSearchSection).display !== 'none');
}
function isLikelyName(text) {
  const t = (text || '').trim();

  // 시스템 유도/업무 단어가 포함되면 이름 아님
  const notNameTokens = [
    '서비스','이동','입력','주세요','말씀','주민번호','앞여섯자리','확인','검색',
    // 의료 도메인 키워드(이름과 혼동 방지)
    '진료','확인서','증명서','처방','처방전','영수증','서류','발급','병원','약국'
  ];
  if (notNameTokens.some(w => t.includes(w))) return false;

  // 한글 2~6자 (공백X)
  if (/^[가-힣]{2,6}$/.test(t)) return true;

  // "저는 {이름}(입니다/이에요/예요/이요)" / "{이름}입니다" / "{이름}이요"
  if (/^저(는)?\s*[가-힣]{2,6}(입니다|이에요|예요|이요)?$/.test(t)) return true;
  if (/^[가-힣]{2,6}\s*(입니다|이에요|예요|이요)$/.test(t)) return true;

  // 영문 짧은 이름(2~20자, 공백/하이픈 허용)
  if (/^[a-zA-Z][a-zA-Z\-\s]{1,18}[a-zA-Z]$/.test(t) && t.split(' ').join('').length <= 20) return true;

  return false;
}
function containsLikelyName(text) {
  // 문장 안에 2~6자의 한글 이름 토큰이 있는지(도메인 단어 제외)
  const raw = (text || '').trim();
  const tokens = raw.split(/\s+/);
  const deny = new Set(['진료','확인서','증명서','처방전','처방','영수증','서류','발급','약국','병원']);
  for (const tok of tokens) {
    if (/^[가-힣]{2,6}$/.test(tok) && !deny.has(tok)) return true;
  }
  return isLikelyName(raw);
}
function isLikelyEcho(transcript) {
  const t = normalize(transcript);
  if (!t) return false;
  if (ttsPlaying) return true;
  if (isWithinCooldown()) return true;

  for (const prev of ttsHistory) {
    const P = normalize(prev);
    if (!P) continue;
    if (t.length >= ECHO_MIN_LEN) {
      const ratio = charOverlapRatio(t, P);
      if (ratio >= ECHO_SIM_THRESHOLD) return true;
      if (t.length >= 10 && (P.includes(t) || t.includes(P))) return true;
    }
  }
  // 시스템 유도 문구가 들어오면 이름 단계가 아닐 때는 에코로 간주
  const systemWords = ['서비스', '이동', '입력', '말씀', '주세요', '주민번호', '앞여섯자리', '이름을'];
  if (systemWords.some(w => transcript.includes(w)) && !isNameStageActive()) return true;
  return false;
}

// '이서준 님이 맞으시면…' 형태의 TTS에서 이름 파싱
function parseConfirmNameFromTTS(text) {
  if (!text) return null;
  // 패턴 1: {이름} 님이 맞으시면
  let m = text.match(/^\s*([가-힣]{2,6})\s*님이\s*맞으시면/);
  if (m && m[1]) return m[1];
  // 패턴 2: {이름}님이 맞으시면
  m = text.match(/^\s*([가-힣]{2,6})님이\s*맞으시면/);
  if (m && m[1]) return m[1];
  return null;
}

// 본인확인 발화 탐지 (clientState === 'confirming_user'에서 사용)
function isConfirmUtterance(text) {
  const raw = (text || '').trim();
  const norm = normalize(raw);

  // 1) 고정 구문
  const confirmTokens = ['본인확인','본인이야','본인입니다','저예요','저에요','접니다','맞아요','맞습니다','맞다','맞소','네','예'];
  if (confirmTokens.some(tok => norm.includes(normalize(tok)))) return true;

  // 2) 대상 이름 + 확정 서술(있는 경우)
  if (confirmTargetName) {
    const name = confirmTargetName;
    const nameNorm = confirmTargetNameNorm || normalize(name);
    const nRaw = raw.replace(/\s+/g, '');
    if (nRaw.includes(name)) {
      if (/(이요|이에요|예요|입니다|이다)$/.test(nRaw)) return true;
      if (new RegExp(`저(는)?\\s*${name}(입니다|이에요|예요|이요)?$`).test(raw)) return true;
      if (new RegExp(`${name}\\s*(맞아요|맞습니다)$`).test(raw)) return true;
    }
    const nNorm = normalize(raw);
    if (nNorm.includes(nameNorm) && /(이요|이에요|예요|입니다|이다)$/.test(nNorm)) return true;
  }

  return false;
}

/* ==============================
 * 마이크 및 음성 인식 (안정성 강화)
 * ============================== */
function activateMicrophone() {
  console.log('🎤 activateMicrophone 호출 - microphoneEnabled:', microphoneEnabled, 'ttsPlaying:', ttsPlaying);
  if (!microphoneEnabled) {
    console.log('🔇 마이크 활성화 무시 - microphoneEnabled=false');
    return;
  }
  if (ttsPlaying) {
    console.log('🔇 마이크 활성화 무시 - TTS 재생 중');
    return;
  }
  if (isWithinCooldown()) {
    const wait = TTS_COOLDOWN_MS - (Date.now() - lastTTSEndAt) + 120;
    console.log(`⏳ TTS 쿨다운 대기 후 마이크 활성화: ${wait}ms`);
    setTimeout(() => {
      if (microphoneEnabled && !ttsPlaying && !currentRecognition) startSpeechRecognition();
    }, wait);
    return;
  }

  console.log('✅ 마이크 활성화 진행');
  const micIndicator = document.getElementById('mic-indicator');
  if (micIndicator) micIndicator.classList.add('active');
  setTimeout(startSpeechRecognition, 300);
}

function deactivateMicrophone() {
  console.log('🔇 deactivateMicrophone 호출');
  const micIndicator = document.getElementById('mic-indicator');
  if (micIndicator) micIndicator.classList.remove('active');
  stopSpeechRecognition();
}

function startSpeechRecognition() {
  if (!('webkitSpeechRecognition' in window)) {
    updateStatus('오류: 이 브라우저는 음성 인식을 지원하지 않습니다.');
    return;
  }
  if (currentRecognition || !microphoneEnabled || ttsPlaying) {
    console.log('🔇 음성 인식 시작 불가 - currentRecognition:', !!currentRecognition, 'microphoneEnabled:', microphoneEnabled, 'ttsPlaying:', ttsPlaying);
    return;
  }
  if (isWithinCooldown()) {
    console.log('⏳ 쿨다운으로 음성 인식 지연');
    setTimeout(() => {
      if (!currentRecognition && microphoneEnabled && !ttsPlaying) startSpeechRecognition();
    }, TTS_COOLDOWN_MS);
    return;
  }

  console.log('🎤 음성 인식 시작');
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  currentRecognition = new SpeechRecognition();
  currentRecognition.lang = 'ko-KR';
  currentRecognition.continuous = false;

  currentRecognition.onresult = function (event) {
    const result = event.results[0][0].transcript.trim();
    console.log('🎤 인식된 텍스트:', result);

    // 쿨다운/에코 차단 (단, 본인확인 문구/이름은 예외 적용)
    if (clientState !== 'confirming_user' && isWithinCooldown()) {
      console.warn('⏳ TTS 쿨다운 내 입력 무시:', result);
      return;
    }

    if (clientState === 'confirming_user') {
      // ✅ 본인확인 단계: 확인 어구 or 대상 이름 or **임의의 그럴듯한 이름** → 즉시 서버 전송 + 즉시 stop
      const pass =
        isConfirmUtterance(result) ||
        (confirmTargetName && normalize(result).includes(confirmTargetNameNorm)) ||
        containsLikelyName(result);

      if (pass) {
        console.log('✅ 본인확인 통과 발화로 판단 → 서버 전송 & recognition.stop()');
        if (websocket && websocket.readyState === WebSocket.OPEN) {
          websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
        }
        try { currentRecognition.stop(); } catch (_) {}
        currentRecognition = null;
        return;
      }

      // 그 외에도, 과도한 에코만 아니면 서버로 전달(서버에서 부적합 처리)
      if (!isLikelyEcho(result)) {
        console.log('➡️ 본인확인 단계 일반 발화 → 서버 전송(서버에서 판별)');
        if (websocket && websocket.readyState === WebSocket.OPEN) {
          websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
        }
        try { currentRecognition.stop(); } catch (_) {}
        currentRecognition = null;
      } else {
        console.warn('⛔ 본인확인 단계 에코로 판단 → 무시:', result);
      }
      return;
    }

    // 일반 단계: 에코 차단
    if (isLikelyEcho(result)) {
      console.warn(`자기 TTS 에코/시스템 유도 문구로 판단하여 무시: "${result}"`);
      return;
    }

    // 이름 입력 단계: 이름 형태만 통과
    if (isNameStageActive() && !isLikelyName(result)) {
      console.warn('이름 단계에서 이름으로 보이지 않아 무시:', result);
      return;
    }

    if (result && websocket && websocket.readyState === WebSocket.OPEN) {
      websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
    }
  };

  currentRecognition.onerror = (event) => {
    console.error('❌ 음성 인식 오류:', event.error);
    // 'no-speech'는 조용히 무시
  };

  currentRecognition.onend = () => {
    console.log('🎤 음성 인식 종료');
    currentRecognition = null;
    const micIndicator = document.getElementById('mic-indicator');
    if (micIndicator && micIndicator.classList.contains('active') && microphoneEnabled && !ttsPlaying) {
      setTimeout(startSpeechRecognition, 500);
    }
  };

  try {
    currentRecognition.start();
  } catch (error) {
    console.error('음성 인식 시작 오류:', error);
    currentRecognition = null;
  }
}

function stopSpeechRecognition() {
  if (currentRecognition) {
    console.log('🛑 음성 인식 중단');
    try {
      currentRecognition.onend = null;
      currentRecognition.stop();
    } catch (_) {}
    currentRecognition = null;
  }
}

/* ==============================
 * WebSocket 핵심 로직
 * ============================== */
function initializeWebSocket() {
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/kiosk/`;
  websocket = new WebSocket(wsUrl);
  window.websocket = websocket; // azure-tts.js에서 사용

  websocket.onopen = () => {
    updateStatus('✅ 음성 서비스 연결됨');
    console.log('📡 WebSocket 연결됨 - 초기 상태:', { microphoneEnabled, ttsPlaying });
  };

  websocket.onmessage = (event) => handleWebSocketMessage(JSON.parse(event.data));

  websocket.onclose = () => updateStatus('❌ 음성 서비스 연결 끊김. 새로고침해주세요.');
  websocket.onerror = () => updateStatus('❌ 음성 서비스 오류');
}

function handleWebSocketMessage(data) {
  console.log('📨 WebSocket 메시지 수신:', data.type, data);

  switch (data.type) {
    case 'tts.text': {
      // TTS 시작 전에 저장 + 히스토리 푸시 + 마이크 비활성화
      lastSpokenTTS = data.text || '';
      pushTTSHistory(lastSpokenTTS);

      // 본인확인 이름 파싱 (가능하면 저장)
      const nameFromTTS = parseConfirmNameFromTTS(lastSpokenTTS);
      if (nameFromTTS) {
        confirmTargetName = nameFromTTS;
        confirmTargetNameNorm = normalize(nameFromTTS);
        console.log('🔎 본인확인 대상 이름 파싱:', confirmTargetName);
      }

      // 본인확인 안내 문구 감지 시 쿨다운을 짧게 (빠른 응답)
      if (/님이\s*맞으시면.*본인\s*확인/.test(lastSpokenTTS)) {
        TTS_COOLDOWN_MS = TTS_COOLDOWN_MS_CONFIRM;
        console.log('⚙️ 확인 프롬프트 감지 → TTS_COOLDOWN_MS=', TTS_COOLDOWN_MS);
      } else {
        TTS_COOLDOWN_MS = TTS_COOLDOWN_MS_DEFAULT;
      }

      ttsPlaying = true;
      console.log('🔊 TTS 시작 - ttsPlaying:', ttsPlaying, 'microphoneEnabled:', microphoneEnabled);
      deactivateMicrophone();

      speakOnClient(
        data.text,
        // onStart
        () => {
          console.log('🔊 TTS 재생 시작 콜백');
          deactivateMicrophone();
        },
        // onEnd
        () => {
          console.log('🔊 TTS 재생 완료 콜백 - 상태 업데이트');
          ttsPlaying = false;
          microphoneEnabled = true; // 강제 활성화
          lastTTSEndAt = Date.now();
          console.log('🔊 상태 업데이트 - ttsPlaying:', ttsPlaying, 'microphoneEnabled:', microphoneEnabled);

          // 쿨다운 후 마이크 재활성화
          setTimeout(() => {
            console.log('🎤 마이크 재활성화 시도 - ttsPlaying:', ttsPlaying, 'microphoneEnabled:', microphoneEnabled);
            if (microphoneEnabled && !ttsPlaying) {
              activateMicrophone();
            } else {
              console.warn('🚫 마이크 재활성화 실패 - microphoneEnabled:', microphoneEnabled, 'ttsPlaying:', ttsPlaying);
            }
          }, TTS_COOLDOWN_MS);
        }
      );
      break;
    }

    case 'mic.off':
      console.log('🔇 마이크 강제 비활성화 신호 수신');
      microphoneEnabled = false;
      deactivateMicrophone();
      break;

    case 'mic.on':
      console.log('🎤 마이크 재활성화 신호 수신');
      microphoneEnabled = true;
      console.log('🎤 mic.on 받음 - microphoneEnabled:', microphoneEnabled, 'ttsPlaying:', ttsPlaying);
      if (!ttsPlaying) {
        setTimeout(() => {
          console.log('🎤 mic.on 딜레이 후 활성화 시도');
          activateMicrophone();
        }, 500);
      } else {
        console.log('🎤 mic.on - TTS 재생 중이므로 대기');
      }
      break;

    case 'state.update':
      clientState = data.step;
      console.log('📝 상태 업데이트:', clientState);
      if (clientState === 'confirming_user') {
        updateStatus('음성으로 답변해주세요. (예: "본인 확인", "저 예요", "이름 말하기")');
        // 확인 단계 진입 시 빠른 응답 모드 유지
        TTS_COOLDOWN_MS = TTS_COOLDOWN_MS_CONFIRM;
      } else {
        TTS_COOLDOWN_MS = TTS_COOLDOWN_MS_DEFAULT;
      }
      break;

    case 'stt.forward_to_input': {
      // 서버 지시에도 로컬에서 2차 필터(에코/이름형태) 후 반영
      const text = (data.text || '').trim();
      const nameSearchSection = document.getElementById('nameSearchSection');
      if (nameSearchSection && window.getComputedStyle(nameSearchSection).display !== 'none') {
        if (isLikelyEcho(text)) {
          console.warn('서버 forward 텍스트가 에코로 의심되어 입력란 반영 생략:', text);
          return;
        }
        if (!isLikelyName(text)) {
          console.warn('서버 forward 텍스트가 이름 형태가 아니라 반영 생략:', text);
          return;
        }
        const nameInput = document.getElementById('nameInput');
        if (nameInput) {
          nameInput.value = text;
          updateStatus(`음성으로 이름 "${text}" 입력됨`);
          const nameSearchBtn = document.getElementById('nameSearchBtn');
          if (nameSearchBtn) nameSearchBtn.click();
        }
      }
      break;
    }

    case 'user.confirmed':
      selectPatient(data.patient);
      break;

    case 'user.confirmation_failed': {
      updateStatus('인증에 실패했습니다. 이름을 다시 말씀해주세요.');
      const nameInput = document.getElementById('nameInput');
      if (nameInput) nameInput.focus();
      break;
    }

    case 'status':
      updateStatus('📡 ' + data.state);
      break;

    case 'tts.complete':
      // 서버 신호 수신 시에도 동일 처리
      console.log('📨 TTS 완료 신호 수신 - 서버에서 온 신호');
      ttsPlaying = false;
      microphoneEnabled = true;
      lastTTSEndAt = Date.now();
      // 상태에 따라 쿨다운 조정
      TTS_COOLDOWN_MS = (clientState === 'confirming_user') ? TTS_COOLDOWN_MS_CONFIRM : TTS_COOLDOWN_MS_DEFAULT;
      if (microphoneEnabled) {
        setTimeout(() => {
          console.log('📨 서버 tts.complete 딜레이 후 마이크 활성화 시도');
          activateMicrophone();
        }, TTS_COOLDOWN_MS);
      }
      break;

    default:
      console.warn('알 수 없는 메시지 타입:', data.type);
  }
}

/* ==============================
 * 인증 흐름 및 UI 이벤트
 * ============================== */
function setupAuthEventListeners() {
  const birthForm = document.getElementById('birthForm');
  const digitInputs = document.querySelectorAll('.birth-digit');
  const birthSubmitBtn = document.getElementById('birthSubmitBtn');
  const nameSearchSection = document.getElementById('nameSearchSection');
  const nameInput = document.getElementById('nameInput');
  const nameSearchBtn = document.getElementById('nameSearchBtn');
  const successMessage = document.getElementById('birthSuccessMessage');

  if (digitInputs.length > 0) {
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
  }

  function checkBirthFormValidity() {
    if (birthSubmitBtn && digitInputs.length > 0) {
      const allFilled = Array.from(digitInputs).every(input => input.value.match(/^[0-9]$/));
      birthSubmitBtn.disabled = !allFilled;
    }
  }

  if (birthForm) {
    birthForm.addEventListener('submit', function (e) {
      e.preventDefault();
      const birthNumber = Array.from(digitInputs).map(input => input.value).join('');
      const csrfToken = birthForm.querySelector('[name=csrfmiddlewaretoken]')?.value;

      updateStatus('주민번호 확인 중...');
      const formData = new FormData();
      formData.append('birth_number', birthNumber);
      if (csrfToken) formData.append('csrfmiddlewaretoken', csrfToken);

      fetch('/kiosk/main/', {
        method: 'POST',
        body: new URLSearchParams(formData)
      })
        .then(res => res.json())
        .then(data => {
          if (data.success) {
            updateStatus('✅ 1차 인증 완료. 이름을 입력해주세요.');
            if (successMessage) successMessage.textContent = data.message;
            if (nameSearchSection) nameSearchSection.style.display = 'block';
            if (nameInput) nameInput.focus();

            speakOnClient(
              '이름을 말씀해주세요.',
              () => {
                console.log('🔊 이름 입력 TTS 시작 콜백');
                deactivateMicrophone();
              },
              () => {
                console.log('🔊 이름 입력 TTS 완료 콜백');
                microphoneEnabled = true;
                ttsPlaying = false;
                lastTTSEndAt = Date.now();
                setTimeout(() => {
                  if (microphoneEnabled) {
                    console.log('🎤 이름 입력 후 마이크 활성화');
                    activateMicrophone();
                  }
                }, TTS_COOLDOWN_MS);
              }
            );
          } else {
            updateStatus('❌ 1차 인증 실패: ' + data.message);
          }
        })
        .catch(error => {
          console.error('인증 요청 오류:', error);
          updateStatus('❌ 인증 요청 중 오류가 발생했습니다.');
        });
    });
  }

  if (nameSearchBtn) {
    nameSearchBtn.addEventListener('click', function () {
      const name = nameInput?.value?.trim();
      if (!name) return;

      // 이름 단계에서도 기본 유효성 체크
      if (!isLikelyName(name)) {
        updateStatus('이름 형식이 올바르지 않습니다. (한글 2~6자 권장)');
        return;
      }

      updateStatus('이름으로 검색 중...');
      fetch('/kiosk/search_by_name/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name })
      })
        .then(res => res.json())
        .then(data => {
          if (data.success) {
            if (data.confirmation_needed) {
              updateStatus('음성으로 본인 확인을 진행합니다.');
              if (websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({ type: 'user.confirmation_start', patient: data.patient }));
              }
            } else {
              updateStatus(`✅ ${data.results.length}명의 환자를 찾았습니다.`);
              displayResults(data.results, selectPatient);
            }
          } else {
            updateStatus('❌ 이름 검색 실패: ' + data.message);
            // 검색 실패 시에도 TTS로 안내
            speakOnClient(
              data.message,
              () => {
                console.log('🔊 검색 실패 TTS 시작');
                deactivateMicrophone();
              },
              () => {
                console.log('🔊 검색 실패 TTS 완료');
                microphoneEnabled = true;
                ttsPlaying = false;
                lastTTSEndAt = Date.now();
                setTimeout(() => {
                  if (microphoneEnabled) {
                    console.log('🎤 검색 실패 후 마이크 활성화');
                    activateMicrophone();
                  }
                }, TTS_COOLDOWN_MS);
              }
            );
          }
        })
        .catch(error => {
          console.error('이름 검색 오류:', error);
          updateStatus('❌ 이름 검색 중 오류가 발생했습니다.');
        });
    });
  }

  if (nameInput) {
    nameInput.addEventListener('keypress', e => {
      if (e.key === 'Enter' && nameSearchBtn) {
        nameSearchBtn.click();
      }
    });
  }
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
    })
    .catch(error => {
      console.error('환자 선택 오류:', error);
      alert('환자 선택 중 오류가 발생했습니다.');
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

/* ==============================
 * 디버깅용 전역 함수
 * ============================== */
window.forceActivateMic = function () {
  console.log('🚨 강제 마이크 활성화');
  microphoneEnabled = true;
  ttsPlaying = false;
  lastTTSEndAt = Date.now() - TTS_COOLDOWN_MS_DEFAULT; // 바로 활성화 허용
  activateMicrophone();
};

window.showMicStatus = function () {
  console.log('📊 현재 마이크 상태:', {
    microphoneEnabled,
    ttsPlaying,
    currentRecognition: !!currentRecognition,
    websocketReady: websocket && websocket.readyState === WebSocket.OPEN,
    cooldownRemainingMs: Math.max(0, TTS_COOLDOWN_MS - (Date.now() - lastTTSEndAt)),
    ttsHistory,
    clientState,
    confirmTargetName,
    TTS_COOLDOWN_MS
  });
};

/* ==============================
 * 메인 초기화 로직
 * ============================== */
document.addEventListener('DOMContentLoaded', function () {
  console.log('📱 DOM 로드 완료');
  const audioUnlockOverlay = document.getElementById('audio-unlock-overlay');

  async function initializeKiosk() {
    console.log('🚀 키오스크 초기화 시작');
    if (audioUnlockOverlay) audioUnlockOverlay.style.display = 'none';

    // 상태 초기화
    microphoneEnabled = true;
    ttsPlaying = false;
    lastTTSEndAt = 0;
    ttsHistory = [];
    confirmTargetName = null;
    confirmTargetNameNorm = null;
    TTS_COOLDOWN_MS = TTS_COOLDOWN_MS_DEFAULT;
    console.log('📊 초기 상태 설정 - microphoneEnabled:', microphoneEnabled, 'ttsPlaying:', ttsPlaying);

    updateStatus('음성 서비스 로딩 중...');
    try {
      await loadAzureSDK();
      updateStatus('시스템 초기화 중...');
      initializeWebSocket();
      setupAuthEventListeners();

      const firstDigitInput = document.querySelector('.birth-digit');
      if (firstDigitInput) firstDigitInput.focus();

      // 초기화 완료 후 마이크 활성화 (충분한 딜레이)
      setTimeout(() => {
        console.log('🎤 초기화 완료 후 마이크 활성화 시도');
        activateMicrophone();
      }, 2000);

    } catch (error) {
      console.error('키오스크 초기화 오류:', error);
      updateStatus('오류: 음성 서비스를 불러오지 못했습니다.');
    }
  }

  if (document.referrer && document.referrer.includes('/kiosk/idle/')) {
    console.log('🔄 idle 페이지에서 이동 - 즉시 초기화');
    initializeKiosk();
  } else {
    console.log('👆 터치 대기 모드');
    if (audioUnlockOverlay) {
      audioUnlockOverlay.style.cssText = "position:fixed; top:0; left:0; width:100%; height:100%; z-index:1000; background-color: rgba(0,0,0,0.5); color:white; display:flex; justify-content:center; align-items:center; font-size: 2rem; cursor: pointer;";
      audioUnlockOverlay.innerHTML = "<h1>화면을 터치하여 시작하세요</h1>";
      audioUnlockOverlay.addEventListener('click', initializeKiosk, { once: true });
    }
  }
});
