// static/js/modules/azure-tts.js
/**
 * Azure Text-to-Speech 모듈 (큐 + 문장분절 + 중복차단 + 간격 강화)
 * - 한 번의 호출로 여러 문장을 순서대로 모두 재생
 * - 그룹(한 번의 speakOnClient 호출) 내에서는 중복 스킵하지 않음
 * - 그룹의 마지막 문장 완료 시에만 서버로 tts.complete 전송
 * - 연속 발화 최소 간격/문장 간 간격/그룹 테일 간격 보장 (겹침 방지 강화)
 * - 과도한 큐 길이 시 오래된 항목 정리
 */

let azureTokenInfo = null;

// 재생 큐 & 상태
let ttsQueue = [];                 // [{ text, onStart, onEnd, groupId, isGroupFirst, isGroupLast, skipDedup }]
let ttsProcessing = false;         // 큐 처리 중 여부
let currentSynth = null;           // 현재 사용 중인 Synthesizer (stop용)
let lastTtsText = "";              // 마지막으로 말한 문장(개별 문장 기준)
let lastTtsAt = 0;                 // 마지막 발화 완료 시각(ms)

// 파라미터 (겹침 느낌 줄이기 위해 간격 상향)
const MAX_QUEUE = 10;                  // 큐 최대 길이
const MIN_GAP_MS = 1600;              // 연속 발화 최소 간격(ms) (기존 1200 → 1600)
const INTER_SENTENCE_DELAY_MS = 1400; // 한 그룹 내 문장 간 간격 (기존 1100 → 1400)
const GROUP_TAIL_DELAY_MS = 250;      // 그룹 마지막 문장 후 살짝 쉬고 complete 전송

// --------------------------- 유틸 ---------------------------

function now() { return Date.now(); }

function normalize(s) {
  return (s || "").replace(/\s+/g, "");
}

/** 비슷한/중복 문장 판별 (간단한 부분 포함/동일 비교) */
function isNearDuplicate(a, b) {
  if (!a || !b) return false;
  const A = normalize(a);
  const B = normalize(b);
  if (A === B) return true;
  return A.length >= 10 && (A.includes(B) || B.includes(A));
}

/** 한국어/일반 문장 분리 (문장부호 기준, 부호를 유지) */
function splitSentencesKR(text) {
  if (!text) return [];
  const t = String(text).replace(/\s+/g, " ").trim();
  if (!t) return [];

  // 문장부호(. ! ?)를 포함해 분리. 마지막 조각도 포함.
  // 예: ["문장1.", "문장2?", "문장3"]
  const parts = [];
  const regex = /[^.!?]+[.!?]?/g;
  let m;
  while ((m = regex.exec(t)) !== null) {
    const s = m[0].trim();
    if (s) parts.push(s);
  }
  return parts;
}

// ---------------------- 토큰/SDK 준비 ----------------------

async function getAzureToken() {
  if (azureTokenInfo && Date.now() < azureTokenInfo.expireAt) {
    return azureTokenInfo;
  }
  try {
    const res = await fetch("/speech/token/");
    if (!res.ok) throw new Error(`Token fetch failed: ${res.status}`);
    const j = await res.json();
    azureTokenInfo = {
      token: j.token || j.access_token || j.speech_key,
      region: j.region || j.location || "koreacentral",
      expireAt: Date.now() + 9 * 60 * 1000, // 9분 후 갱신
    };
    console.log("Azure Speech Token successfully fetched. region =", azureTokenInfo.region);
    return azureTokenInfo;
  } catch (error) {
    console.error("Failed to get Azure token:", error);
    return null;
  }
}

function ensureSpeechSDK() {
  if (!window.SpeechSDK) {
    console.error("SpeechSDK not loaded.");
    return null;
  }
  return window.SpeechSDK;
}

// ---------------------- 서버 신호 전송 ----------------------

function sendTTSCompleteSignal() {
  try {
    if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
      window.websocket.send(JSON.stringify({ type: "tts.complete" }));
      console.log("📤 TTS 완료 신호 전송");
    }
  } catch (error) {
    console.error("TTS 완료 신호 전송 실패:", error);
  }
}

// ------------------------ 핵심 재생 ------------------------

async function playOnce(text) {
  const SpeechSDK = ensureSpeechSDK();
  if (!SpeechSDK) throw new Error("Speech SDK not available");

  const tokenInfo = await getAzureToken();
  if (!tokenInfo) throw new Error("Azure Token 획득 실패");

  const speechConfig = SpeechSDK.SpeechConfig.fromAuthorizationToken(
    tokenInfo.token,
    tokenInfo.region
  );
  speechConfig.speechSynthesisLanguage = "ko-KR";
  speechConfig.speechSynthesisVoiceName = "ko-KR-SunHiNeural";
  speechConfig.speechSynthesisOutputFormat =
    SpeechSDK.SpeechSynthesisOutputFormat.Audio24Khz48KBitRateMonoMp3;

  const audioConfig = SpeechSDK.AudioConfig.fromDefaultSpeakerOutput();
  const synthesizer = new SpeechSDK.SpeechSynthesizer(speechConfig, audioConfig);
  currentSynth = synthesizer;

  return new Promise((resolve, reject) => {
    let completed = false;

    const cleanup = () => {
      if (synthesizer) {
        try { synthesizer.close(); } catch (e) { console.warn("Synthesizer close 오류:", e); }
      }
      if (currentSynth === synthesizer) currentSynth = null;
    };

    // 타임아웃(30초)
    const timeout = setTimeout(() => {
      if (!completed) {
        completed = true;
        cleanup();
        reject(new Error("TTS 타임아웃 (30초)"));
      }
    }, 30000);

    synthesizer.speakTextAsync(
      text,
      (result) => {
        clearTimeout(timeout);
        if (completed) return;
        completed = true;

        console.log("TTS Result Reason:", result.reason);
        if (result.reason === SpeechSDK.ResultReason.SynthesizingAudioCompleted) {
          console.log(`🔊 합성 완료: "${text}"`);
          cleanup();
          resolve();
        } else {
          console.error("TTS 실패:", result.errorDetails);
          cleanup();
          reject(new Error(`TTS 실패: ${result.errorDetails}`));
        }
      },
      (error) => {
        clearTimeout(timeout);
        if (completed) return;
        completed = true;

        console.error("TTS 오류:", error);
        cleanup();
        reject(new Error(`TTS 오류: ${error}`));
      }
    );
  });
}

// ------------------------ 큐 처리 ------------------------

async function processQueue() {
  if (ttsProcessing) return;
  ttsProcessing = true;

  try {
    while (ttsQueue.length > 0) {
      const item = ttsQueue.shift();
      if (!item || !item.text) continue;

      // 연속 발화 최소 간격 보장 (이전 발화와 충분한 텀)
      const gap = now() - lastTtsAt;
      if (gap < MIN_GAP_MS) {
        await new Promise((r) => setTimeout(r, MIN_GAP_MS - gap));
      }

      // 그룹 외에서는 유사/중복 차단
      if (!item.skipDedup && isNearDuplicate(item.text, lastTtsText)) {
        console.log("⏭️ 유사/중복 발화 건너뜀:", item.text);
        try { item.onStart && item.onStart(); } catch (e) {}
        if (item.isGroupLast) {
          // 그룹 마지막 문장이 중복으로 스킵되더라도 complete 보장
          await new Promise((r) => setTimeout(r, GROUP_TAIL_DELAY_MS));
          try { item.onEnd && item.onEnd(); } catch (e) {}
          sendTTSCompleteSignal();
        }
        lastTtsAt = now();
        continue;
      }

      // onStart(마이크 오프 등) — 그룹의 첫 문장에서 한 번만 호출
      try { item.onStart && item.onStart(); } catch (e) { console.error("onStart 콜백 오류:", e); }

      try {
        await playOnce(item.text);
      } catch (e) {
        console.error("Azure TTS 처리 중 오류 발생:", e);
      } finally {
        // 문장 간 짧은 딜레이(에코/겹침 방지) — 상향
        await new Promise((r) => setTimeout(r, INTER_SENTENCE_DELAY_MS));

        // 그룹의 마지막 문장에서만 tts.complete + onEnd 호출 (테일 딜레이 포함)
        if (item.isGroupLast) {
          await new Promise((r) => setTimeout(r, GROUP_TAIL_DELAY_MS));
          sendTTSCompleteSignal();
          try { item.onEnd && item.onEnd(); } catch (e) { console.error("onEnd 콜백 오류:", e); }
        }

        lastTtsText = item.text;
        lastTtsAt = now();
      }
    }
  } finally {
    ttsProcessing = false;
  }
}

// ------------------------ 공개 API ------------------------

/**
 * 클라이언트에서 호출: 말하기 요청
 * @param {string} text            - 전체 텍스트(여러 문장 가능)
 * @param {function=} onStart      - 그룹 시작 시 1회 호출(마이크 끄기 등)
 * @param {function=} onEnd        - 그룹 마지막 문장 완료 시 1회 호출(마이크 켜기 등)
 */
export function speakOnClient(text, onStart, onEnd) {
  const raw = String(text || "").replace(/\s+/g, " ").trim();
  if (!raw) {
    console.warn("빈 텍스트는 TTS 처리하지 않습니다.");
    try { onEnd && onEnd(); } catch (e) {}
    return;
  }

  const sentences = splitSentencesKR(raw);
  if (sentences.length === 0) {
    // 문장 분리가 안되면 전체를 그대로 1회 재생
    ttsQueue.push({
      text: raw,
      onStart,
      onEnd,
      groupId: cryptoRandomId(),
      isGroupFirst: true,
      isGroupLast: true,
      skipDedup: false,
    });
  } else {
    const gid = cryptoRandomId();
    sentences.forEach((s, idx) => {
      ttsQueue.push({
        text: s,
        onStart: idx === 0 ? onStart : null,                       // 그룹 시작에서만 onStart
        onEnd:  idx === sentences.length - 1 ? onEnd : null,       // 그룹 끝에서만 onEnd
        groupId: gid,
        isGroupFirst: idx === 0,
        isGroupLast: idx === sentences.length - 1,
        // 그룹 내부 문장은 중복이라도 스킵하지 않기 위해 dedup 해제
        skipDedup: true
      });
    });
  }

  // 큐 길이 초과 시 오래된 항목 제거 (새로 추가된 그룹은 보존)
  if (ttsQueue.length > MAX_QUEUE) {
    ttsQueue = ttsQueue.slice(-MAX_QUEUE);
  }

  // 즉시 큐 처리 시도
  processQueue();
}

/** 전체 TTS 중단 및 큐 비우기 */
export function stopTTS() {
  try {
    if (currentSynth) {
      currentSynth.close();
      currentSynth = null;
    }
  } catch (error) {
    console.error("TTS 중단 오류:", error);
  }
  ttsQueue = [];
  console.log("🔇 모든 TTS 강제 중단 & 큐 비움");
}

/** 진행 중 여부 */
export function isTTSInProgress() {
  return ttsProcessing || !!currentSynth;
}

/** 모듈 초기화(선택) */
export function initializeTTS() {
  console.log("🎤 Azure TTS 모듈 초기화");
  window.addEventListener("beforeunload", () => {
    stopTTS();
  });
  // 디버깅용 전역 노출
  window.ttsModule = { speakOnClient, stopTTS, isTTSInProgress };
}

// ------------------------ 기타 ------------------------

function cryptoRandomId() {
  // 브라우저 지원 시 crypto 사용
  if (window.crypto && window.crypto.getRandomValues) {
    const arr = new Uint32Array(4);
    window.crypto.getRandomValues(arr);
    return Array.from(arr).map(n => n.toString(16)).join('');
  }
  // 폴백
  return String(Math.random()).slice(2) + String(Date.now());
}
