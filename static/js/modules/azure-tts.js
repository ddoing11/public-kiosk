/**
 * Azure Text-to-Speech 모듈 (최종 안정형, 동적 마이크 타이밍형)
 * - SDK 내부 오디오 출력 완전 차단 (이중 재생 제거)
 * - 문장별 완전 종료 후 다음 문장 재생
 * - tts.complete 신호 1회 전송 (문단 단위)
 * - 문장 길이에 따라 마이크 켜짐 타이밍 자동 조절
 */

let azureTokenInfo = null;
let ttsQueue = [];
let ttsProcessing = false;
let currentSynth = null;
let lastTtsText = "";
let lastTtsAt = 0;

// ------------------ 설정 ------------------
const MAX_QUEUE = 10;
const MIN_GAP_MS = 500;
const INTER_SENTENCE_DELAY_MS = 80;
const GROUP_TAIL_DELAY_MS = 50;

// ------------------ 유틸 ------------------
function now() { return Date.now(); }
function normalize(s) { return (s || "").replace(/\s+/g, ""); }

function isNearDuplicate(a, b) {
  if (!a || !b) return false;
  const A = normalize(a);
  const B = normalize(b);
  if (A === B) return true;
  return A.length >= 10 && (A.includes(B) || B.includes(A));
}

function splitSentencesKR(text) {
  if (!text) return [];
  const t = String(text).replace(/\s+/g, " ").trim();
  if (!t) return [];
  // ✅ 마침표·물음표·느낌표까지만 문장 경계로 취급 (콤마는 무시)
  const regex = /[^.!?]+[.!?]?/g;
  return t.match(regex) || [t];
}

// ------------------ Token ------------------
async function getAzureToken() {
  if (azureTokenInfo && Date.now() < azureTokenInfo.expireAt) return azureTokenInfo;
  try {
    const res = await fetch("/speech/token/");
    if (!res.ok) throw new Error(`Token fetch failed: ${res.status}`);
    const j = await res.json();
    azureTokenInfo = {
      token: j.token || j.access_token || j.speech_key,
      region: j.region || j.location || "koreacentral",
      expireAt: Date.now() + 9 * 60 * 1000,
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

function sendTTSCompleteSignal() {
  try {
    if (window.websocket && window.websocket.readyState === WebSocket.OPEN) {
      window.websocket.send(JSON.stringify({ type: "tts.complete" }));
      console.log("📤 실제 오디오 재생 완전 종료 → tts.complete 전송");
    }
  } catch (error) {
    console.error("TTS 완료 신호 전송 실패:", error);
  }
}

// ------------------ 핵심 재생 ------------------
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

  // ✅ SDK 내부 오디오 출력 완전 비활성화 (이중 재생 방지)
  const synthesizer = new SpeechSDK.SpeechSynthesizer(speechConfig, null);
  currentSynth = synthesizer;

  const ssml = `
  <speak version="1.0" xml:lang="ko-KR">
    <voice name="ko-KR-SunHiNeural">
      ${text}
      <audio src="http://127.0.0.1:8000/static/audio/silence_100ms.mp3"/>
    </voice>
  </speak>`;

  return new Promise((resolve, reject) => {
    synthesizer.speakSsmlAsync(
      ssml,
      async (result) => {
        if (result.reason === SpeechSDK.ResultReason.SynthesizingAudioCompleted) {
          console.log(`🔊 합성 완료: "${text}"`);
          try {
            const blob = new Blob([result.audioData], { type: "audio/mp3" });
            const url = URL.createObjectURL(blob);
            const audio = new Audio(url);

            // ✅ 오디오 재생 종료까지 완전 대기
            await new Promise((done) => {
              audio.addEventListener("ended", () => {
                console.log("🎵 오디오 재생 완전 종료 감지");
                sendTTSCompleteSignal();

                // 🎤 오디오 종료 즉시 마이크 활성화 (문장 길이 기반 지연)
                try {
                  if (window.activateMic && typeof window.activateMic === "function") {
                    const len = text.length;
                    const delay = len < 20 ? 100 : 180; // ✅ 수정 완료
                    console.log(`🎤 오디오 종료 후 ${delay}ms 뒤 마이크 활성화`);
                    setTimeout(() => window.activateMic(), delay);
                  }
                } catch (err) {
                  console.warn("⚠️ 로컬 마이크 즉시 활성화 실패:", err);
                }

                URL.revokeObjectURL(url);
                done();
              });

              audio.play()
                .then(() => console.log("🎧 오디오 재생 시작"))
                .catch((err) => {
                  console.error("🔴 오디오 재생 오류:", err);
                  done();
                });
            });
          } catch (err) {
            console.error("🔴 오디오 재생 오류:", err);
          }

          try { synthesizer.close(); } catch {}
          resolve(); // ✅ 완전 종료 후 resolve
        } else {
          console.error("TTS 실패:", result.errorDetails);
          try { synthesizer.close(); } catch {}
          reject(new Error(`TTS 실패: ${result.errorDetails}`));
        }
      },
      (error) => {
        console.error("TTS 오류:", error);
        try { synthesizer.close(); } catch {}
        reject(new Error(`TTS 오류: ${error}`));
      }
    );
  });
}

// ------------------ 큐 처리 ------------------
async function processQueue() {
  if (ttsProcessing) return;
  ttsProcessing = true;

  try {
    while (ttsQueue.length > 0) {
      const item = ttsQueue.shift();
      if (!item || !item.text) continue;

      const gap = now() - lastTtsAt;
      if (gap < MIN_GAP_MS) {
        await new Promise((r) => setTimeout(r, MIN_GAP_MS - gap));
      }

      if (!item.skipDedup && isNearDuplicate(item.text, lastTtsText)) {
        console.log("⏭️ 중복 건너뜀:", item.text);
        continue;
      }

      try {
        item.onStart && item.onStart();
        await playOnce(item.text); // 🎯 문장 끝까지 재생
        // 👉 INTER_SENTENCE_DELAY_MS 제거
      } catch (e) {
        console.error("Azure TTS 오류:", e);
      }

      lastTtsText = item.text;
      lastTtsAt = now();

      if (item.isGroupLast) {
        await new Promise((r) => setTimeout(r, 30)); // 🔽 50 → 30
        try { item.onEnd && item.onEnd(); } catch {}
        sendTTSCompleteSignal();
      }
    }
  } finally {
    ttsProcessing = false;
  }
}


// ------------------ 공개 API ------------------
export function speakOnClient(text, onStart, onEnd) {
  const raw = String(text || "").replace(/\s+/g, " ").trim();
  if (!raw) return;

  const sentences = splitSentencesKR(raw);
  const gid = cryptoRandomId();

  sentences.forEach((s, idx) => {
    ttsQueue.push({
      text: s,
      onStart: idx === 0 ? onStart : null,
      onEnd: idx === sentences.length - 1 ? onEnd : null,
      groupId: gid,
      isGroupFirst: idx === 0,
      isGroupLast: idx === sentences.length - 1,
      skipDedup: true,
    });
  });

  if (ttsQueue.length > MAX_QUEUE) {
    ttsQueue = ttsQueue.slice(-MAX_QUEUE);
  }

  processQueue();
}

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

export function isTTSInProgress() {
  return ttsProcessing || !!currentSynth;
}

export function initializeTTS() {
  console.log("🎤 Azure TTS 모듈 초기화 (이중 재생 차단형 + 동적 마이크 타이밍)");
  window.addEventListener("beforeunload", () => stopTTS());
  window.ttsModule = { speakOnClient, stopTTS, isTTSInProgress };
}

function cryptoRandomId() {
  if (window.crypto && window.crypto.getRandomValues) {
    const arr = new Uint32Array(4);
    window.crypto.getRandomValues(arr);
    return Array.from(arr).map((n) => n.toString(16)).join("");
  }
  return String(Math.random()).slice(2) + String(Date.now());
}
