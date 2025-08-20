// static/js/voice_socket_main.js (최종 통합 버전)

// ===== 전역 변수 =====
let websocket = null;
let currentRecognition = null;
let isWaitingForConfirmation = false;

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
            // TTS 내용에 확인 질문이 포함되어 있는지 확인
            if (data.text.includes('님 맞으신가요?')) {
                isWaitingForConfirmation = true;
                updateStatus('음성으로 답변해주세요. (예: "네")');
            }
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
        case 'user.confirmed':
            isWaitingForConfirmation = false; // [추가] 확인 완료 후 상태 초기화
            updateStatus(`✅ ${data.patient.patient_name}님 확인. 서비스 페이지로 이동합니다.`);
            setTimeout(() => {
                window.location.href = '/kiosk/services/';
            }, 1500);
            break;
        
        case 'user.confirmation_failed':
            isWaitingForConfirmation = false; // [추가] 확인 실패 후 상태 초기화
            updateStatus('인증에 실패했습니다. 이름을 다시 말씀해주세요.');
            document.getElementById('nameInput').focus(); // 이름 입력창에 다시 포커스
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

        // [수정된 부분 시작]
        if (isWaitingForConfirmation) {
            // "네/아니요" 대답을 처리해야 할 때
            websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
        } 
        else {
            // 이름을 입력받아야 할 때
            const nameSearchSection = document.getElementById('nameSearchSection');
            const nameInput = document.getElementById('nameInput');
            const nameSearchBtn = document.getElementById('nameSearchBtn');

            if (window.getComputedStyle(nameSearchSection).display !== 'none' && result.length > 1) {
                updateStatus(`"이름: ${result}" 음성 인식됨. 검색을 시작합니다.`);
                nameInput.value = result;
                nameSearchBtn.click();
            } 
            // 기존 상담 기능
            else if (result.length > 1 && websocket && websocket.readyState === WebSocket.OPEN) {
                websocket.send(JSON.stringify({ type: 'stt.result', text: result }));
            }
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
// static/js/voice_socket_main.js

// ... (파일 상단의 다른 모든 함수는 그대로 둡니다) ...

// ===== 페이지 로드 시 실행 (이 부분을 통째로 교체하세요) =====
document.addEventListener('DOMContentLoaded', function() {
    const audioUnlockOverlay = document.getElementById('audio-unlock-overlay');

    // --- 초기화 함수 ---
    function initializeKiosk() {
        // [중요] 오버레이를 숨겨서 다른 버튼과 입력을 가능하게 함
        audioUnlockOverlay.style.display = 'none';

        // WebSocket 연결 및 음성 안내 시작
        updateStatus('시스템 초기화 중...');
        updateConsultationStatus('WebSocket 연결 중...');
        initializeWebSocket(); 
        
        // 인증 관련 이벤트 리스너 설정
        setupAuthEventListeners(); 

        // 첫 번째 주민번호 입력 칸에 자동으로 포커스
        const digitInputs = document.querySelectorAll('.birth-digit');
        if(digitInputs.length > 0) {
            digitInputs[0].focus();
        }
    }

    // --- idle 페이지를 거쳐왔는지 확인 ---
    // document.referrer는 현재 페이지로 이동하기 직전 페이지의 URL을 담고 있습니다.
    if (document.referrer && document.referrer.includes('/kiosk/idle/')) {
        // idle 페이지에서 왔으면 바로 키오스크 기능 시작
        initializeKiosk();
    } else {
        // 새로고침 또는 직접 접속 시에는 터치 대기 화면 표시
        audioUnlockOverlay.style.cssText = "position:fixed; top:0; left:0; width:100%; height:100%; z-index:1000; background-color: rgba(0,0,0,0.5); color:white; display:flex; justify-content:center; align-items:center; font-size: 2rem; cursor: pointer;";
        audioUnlockOverlay.innerHTML = "<h1>화면을 터치하여 시작하세요</h1>";
        
        // 터치를 기다렸다가 키오스크 기능 시작
        audioUnlockOverlay.addEventListener('click', initializeKiosk, { once: true });
    }

    // --- 인증 관련 이벤트 리스너 설정 함수 ---
    function setupAuthEventListeners() {
        const birthForm = document.getElementById('birthForm');
        const digitInputs = document.querySelectorAll('.birth-digit');
        const birthSubmitBtn = document.getElementById('birthSubmitBtn');
        const nameSearchSection = document.getElementById('nameSearchSection');
        const nameInput = document.getElementById('nameInput');
        const nameSearchBtn = document.getElementById('nameSearchBtn');
        const resultsSection = document.getElementById('resultsSection');
        const resultsList = document.getElementById('resultsList');
        const successMessage = document.getElementById('birthSuccessMessage');

        // (1단계) 주민번호 입력 필드 자동 이동 및 유효성 검사
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

        function checkBirthFormValidity() {
            const allFilled = Array.from(digitInputs).every(input => input.value.match(/^[0-9]$/));
            birthSubmitBtn.disabled = !allFilled;
        }

        // (2단계) 주민번호 폼 제출
        birthForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const birthNumber = Array.from(digitInputs).map(input => input.value).join('');
            const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

            updateStatus('주민번호 확인 중...');
            
            const formData = new FormData();
            formData.append('birth_number', birthNumber);
            formData.append('csrfmiddlewaretoken', csrfToken);

            fetch('/kiosk/main/', {
                method: 'POST',
                body: new URLSearchParams(formData)
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    updateStatus('✅ 1차 인증 완료. 이름을 입력해주세요.');
                    successMessage.textContent = data.message;
                    nameSearchSection.style.display = 'block';
                    nameInput.focus();
                } else {
                    updateStatus('❌ 1차 인증 실패: ' + data.message);
                }
            });
        });

        // (3단계) 이름 검색 버튼 클릭
        nameSearchBtn.addEventListener('click', function() {
            const name = nameInput.value.trim();
            if (!name) {
                updateStatus('⚠️ 이름을 입력해주세요.');
                return;
            }
            updateStatus('이름으로 검색 중...');
            fetch('/kiosk/search_by_name/', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ name: name })
            })
            .then(res => res.json())
            .then(data => {
                if (data.success) {
                    // [수정된 부분 시작]
                    if (data.confirmation_needed) {
                        // 결과가 한 명이라 확인이 필요한 경우
                        updateStatus('음성으로 본인 확인을 진행합니다.');
                        // [수정] WebSocket으로 환자 정보를 직접 전달
                        websocket.send(JSON.stringify({ 
                            type: 'user.confirmation_start',
                            patient: data.patient // <-- 서버에서 받은 환자 정보를 추가
                        }));
                    } else {
                        // 결과가 여러 명이거나 없는 경우
                        updateStatus(`✅ ${data.results.length}명의 환자를 찾았습니다.`);
                        displayResults(data.results);
                    }
                } else {
                    updateStatus('❌ 이름 검색 실패: ' + data.message);
                    resultsList.innerHTML = `<p>${data.message}</p>`;
                }
            });
        });
        
        nameInput.addEventListener('keypress', e => {
            if (e.key === 'Enter') nameSearchBtn.click();
        });
    }

    // --- 결과 표시 및 다음 단계 이동 함수 ---
    function displayResults(results) {
        const resultsList = document.getElementById('resultsList');
        const resultsSection = document.getElementById('resultsSection');
        resultsList.innerHTML = '';
        if (results.length === 0) {
            resultsList.innerHTML = '<p>일치하는 환자가 없습니다.</p>';
        } else {
            results.forEach(result => {
                const item = document.createElement('div');
                item.className = 'result-item';
                item.innerHTML = `
                    <div><strong>${result.patient_name}</strong> (${result.gender}, ${result.birth_date})</div>
                    <div>환자번호: ${result.patient_id}</div>
                    <button class="verify-button select-patient-btn">이 환자 선택</button>
                `;
                item.querySelector('.select-patient-btn').addEventListener('click', () => {
                    selectPatient(result);
                });
                resultsList.appendChild(item);
            });
        }
        resultsSection.style.display = 'block';
    }

    function selectPatient(patientData) {
        updateStatus(`${patientData.patient_name}님 선택. 서비스 페이지로 이동합니다.`);
        alert(`${patientData.patient_name}님을 선택했습니다. 이제 이 환자의 서류만 조회됩니다.`);
        window.location.href = '/kiosk/services/';
    }
});