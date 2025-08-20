// static/js/modules/azure-tts.js

/**
 * Azure Text-to-Speech 관련 기능을 담당하는 모듈
 */
let azureTokenInfo = null;
let azureSynthesizer = null;

// Azure Speech Token 가져오기 (9분마다 갱신)
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

// Azure TTS로 텍스트를 음성으로 변환하는 핵심 함수
export async function speakOnClient(text, onStart, onEnd) {
    if (!window.SpeechSDK) {
        console.error('SpeechSDK not loaded.');
        return;
    }
    if (onStart) onStart(); // 음성 안내 시작 콜백 (예: 마이크 끄기)

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

        const speakPromise = new Promise((resolve, reject) => {
            azureSynthesizer.speakTextAsync(text,
                (result) => {
                    if (result.reason === SpeechSDK.ResultReason.SynthesizingAudioCompleted) resolve();
                    else reject(`Speech synthesis canceled: ${result.errorDetails}`);
                },
                (err) => reject(`Error synthesizing speech: ${err}`)
            );
        });
        
        await speakPromise;
        console.log(`TTS playback fully completed for: "${text}"`);

    } catch (error) {
        console.error('An error occurred during Azure TTS process:', error);
    } finally {
        if (onEnd) onEnd(); // 음성 안내 종료 콜백 (예: 마이크 켜기)
    }
}