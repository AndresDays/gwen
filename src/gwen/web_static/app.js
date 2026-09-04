const conversation = document.querySelector('#conversation');
const messages = document.querySelector('#messages');
const welcome = document.querySelector('#welcome');
const thinking = document.querySelector('#thinking');
const composer = document.querySelector('#composer');
const input = document.querySelector('#messageInput');
const micButton = document.querySelector('#micButton');
const recordingStatus = document.querySelector('#recordingStatus');
const recordingTime = document.querySelector('#recordingTime');
const usageDialog = document.querySelector('#usageDialog');
const usageDetails = document.querySelector('#usageDetails');
const toast = document.querySelector('#toast');
let state = null;
let audioSession = null;
let startedAt = 0;
const microphoneSelect = document.querySelector('#microphoneSelect');
const inputLevel = document.querySelector('#inputLevel');
const recordingLabel = document.querySelector('#recordingLabel');
const sessionButton = document.querySelector('#sessionButton');
let continuousSession = null;
let gwenAudio = null;
let timer = null;

function showToast(text) {
  toast.textContent = text;
  toast.hidden = false;
  window.setTimeout(() => { toast.hidden = true; }, 3200);
}

function addMessage(role, content) {
  welcome.hidden = true;
  const article = document.createElement('article');
  article.className = `message ${role}`;
  const label = document.createElement('span');
  label.className = 'label';
  label.textContent = role === 'user' ? 'Tú' : 'Gwen';
  const body = document.createElement('p');
  body.textContent = content;
  article.append(label, body);
  messages.append(article);
  conversation.scrollTo({ top: conversation.scrollHeight, behavior: 'smooth' });
  return body;
}

function setBusy(busy) {
  thinking.hidden = !busy;
  input.disabled = busy;
  document.querySelector('#sendButton').disabled = busy;
  if (busy) conversation.scrollTo({ top: conversation.scrollHeight, behavior: 'smooth' });
}

async function request(url, options = {}) {
  const response = await fetch(url, options);
  if (!response.ok) {
    let detail = 'No pude completar eso.';
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

async function loadState() {
  try {
    state = await request('/api/state');
    messages.replaceChildren();
    state.messages.forEach(item => addMessage(item.role, item.content));
    welcome.hidden = state.messages.length > 0;
    micButton.hidden = !state.voice_enabled;
  } catch (error) { showToast(error.message); }
}

composer.addEventListener('submit', async event => {
  event.preventDefault();
  const message = input.value.trim();
  if (!message) return;
  addMessage('user', message);
  input.value = '';
  input.style.height = 'auto';
  setBusy(true);
  try {
    const result = await request('/api/chat', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message })
    });
    addMessage('assistant', result.answer);
  } catch (error) { showToast(error.message); }
  finally { setBusy(false); input.focus(); }
});

input.addEventListener('keydown', event => {
  if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); composer.requestSubmit(); }
});
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = `${Math.min(input.scrollHeight, 150)}px`;
});

document.querySelector('#newButton').addEventListener('click', async () => {
  if (!window.confirm('¿Empezar una conversación nueva? Tus recuerdos se conservarán.')) return;
  try {
    await request('/api/new', { method: 'POST' });
    messages.replaceChildren(); welcome.hidden = false; showToast('Conversación nueva');
  } catch (error) { showToast(error.message); }
});

document.querySelector('#usageButton').addEventListener('click', async () => {
  try {
    state = await request('/api/state');
    const u = state.usage;
    const rows = [
      ['Claude', u.tokens, u.token_limit, 'tokens'],
      ['Transcripción', u.voice_seconds, u.voice_seconds_limit, 'segundos'],
      ['Voz de Gwen', u.tts_characters, u.tts_character_limit, 'caracteres']
    ];
    usageDetails.replaceChildren(...rows.map(([name, value, limit, unit]) => {
      const row = document.createElement('div'); row.className = 'usage-row';
      const head = document.createElement('div');
      const title = document.createElement('span'); title.textContent = name;
      const amount = document.createElement('span');
      amount.textContent = limit == null
        ? `${value.toLocaleString()} ${unit} · sin límite`
        : `${value.toLocaleString()} / ${limit.toLocaleString()} ${unit}`;
      head.append(title, amount);
      const bar = document.createElement('div'); bar.className = 'bar';
      const fill = document.createElement('span');
      fill.style.width = limit == null ? '100%' : `${Math.min(100, value / limit * 100)}%`;
      if (limit == null) fill.style.opacity = '.28';
      bar.append(fill); row.append(head, bar); return row;
    }));
    usageDialog.showModal();
  } catch (error) { showToast(error.message); }
});
document.querySelector('#closeUsage').addEventListener('click', () => usageDialog.close());

async function refreshMicrophones() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  const current = microphoneSelect.value;
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audioinput');
  microphoneSelect.replaceChildren(new Option('Predeterminado', ''));
  devices.forEach((device, index) => microphoneSelect.add(new Option(device.label || `Micrófono ${index + 1}`, device.deviceId)));
  if ([...microphoneSelect.options].some(option => option.value === current)) microphoneSelect.value = current;
}

function mergeBuffers(buffers) {
  const length = buffers.reduce((total, buffer) => total + buffer.length, 0);
  const merged = new Float32Array(length);
  let offset = 0;
  buffers.forEach(buffer => { merged.set(buffer, offset); offset += buffer.length; });
  return merged;
}

function resample(input, sourceRate, targetRate = 16000) {
  if (sourceRate === targetRate) return input;
  const ratio = sourceRate / targetRate;
  const output = new Float32Array(Math.round(input.length / ratio));
  for (let index = 0; index < output.length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.min(input.length, Math.floor((index + 1) * ratio));
    let sum = 0;
    for (let cursor = start; cursor < end; cursor += 1) sum += input[cursor];
    output[index] = sum / Math.max(1, end - start);
  }
  return output;
}

function encodeWav(samples, sampleRate = 16000) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const write = (offset, text) => [...text].forEach((char, index) => view.setUint8(offset + index, char.charCodeAt(0)));
  write(0, 'RIFF'); view.setUint32(4, 36 + samples.length * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, 'data');
  view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, index) => view.setInt16(44 + index * 2, Math.max(-1, Math.min(1, sample)) * (sample < 0 ? 32768 : 32767), true));
  return new Blob([buffer], { type: 'audio/wav' });
}

async function startAudioRecording() {
  const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
  if (microphoneSelect.value) audio.deviceId = { exact: microphoneSelect.value };
  const stream = await navigator.mediaDevices.getUserMedia({ audio });
  await refreshMicrophones();
  const context = new AudioContext();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(4096, 1, 1);
  const silent = context.createGain(); silent.gain.value = 0;
  const buffers = [];
  audioSession = { stream, context, source, processor, silent, buffers, maxLevel: 0 };
  processor.onaudioprocess = event => {
    if (!audioSession) return;
    const samples = new Float32Array(event.inputBuffer.getChannelData(0));
    buffers.push(samples);
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    const rms = Math.sqrt(energy / samples.length);
    audioSession.maxLevel = Math.max(audioSession.maxLevel, rms);
    inputLevel.style.width = `${Math.min(100, rms * 900)}%`;
  };
  source.connect(processor); processor.connect(silent); silent.connect(context.destination);
  startedAt = Date.now(); micButton.classList.add('recording'); recordingStatus.hidden = false;
  timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - startedAt) / 1000);
    recordingTime.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    if (seconds >= 600) stopAudioRecording();
  }, 250);
}

async function stopAudioRecording() {
  const session = audioSession;
  if (!session) return;
  audioSession = null; clearInterval(timer); session.processor.disconnect(); session.source.disconnect();
  session.stream.getTracks().forEach(track => track.stop()); await session.context.close();
  recordingStatus.hidden = true; micButton.classList.remove('recording'); inputLevel.style.width = '0';
  const duration = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
  const samples = resample(mergeBuffers(session.buffers), session.context.sampleRate);
  if (session.maxLevel < 0.006 || samples.length < 8000) {
    showToast('No llegó señal del micrófono. Elige otra entrada en MICRÓFONO y prueba de nuevo.');
    return;
  }
  const form = new FormData(); form.append('audio', encodeWav(samples), 'voice.wav'); form.append('duration', String(duration));
  const voiceMessage = addMessage('user', '🎙️ Procesando tu voz…'); setBusy(true);
  try {
    const result = await request('/api/voice', { method: 'POST', body: form });
    voiceMessage.textContent = result.transcript ? `🎙️ ${result.transcript}` : '🎙️ Mensaje de voz';
    addMessage('assistant', result.answer);
    if (result.audio) new Audio(`data:${result.audio_type};base64,${result.audio}`).play().catch(() => {});
  } catch (error) { voiceMessage.textContent = '🎙️ Grabación no procesada'; showToast(error.message); }
  finally { setBusy(false); }
}

function pcmBase64(samples) {
  const pcm = new Int16Array(samples.length);
  for (let index = 0; index < samples.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[index]));
    pcm[index] = sample * (sample < 0 ? 32768 : 32767);
  }
  const bytes = new Uint8Array(pcm.buffer);
  let binary = '';
  for (let index = 0; index < bytes.length; index += 1) binary += String.fromCharCode(bytes[index]);
  return btoa(binary);
}

function base64Buffer(encoded) {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function stopStreamingAudio(session) {
  session.audioQueue = [];
  session.serverDone = false;
  session.playbackToken += 1;
  if (session.audioSource) {
    try { session.audioSource.stop(); } catch (_) {}
    session.audioSource.disconnect();
    session.audioSource = null;
  }
  session.audioPlaying = false;
}

function finishStreamingTurn(session) {
  if (continuousSession !== session || !session.serverDone || session.audioQueue.length || session.audioPlaying) return;
  session.processing = false;
  session.speaking = false;
  session.speechFrames = 0;
  session.silenceStarted = 0;
  session.startedSpeakingAt = 0;
  recordingLabel.textContent = 'ESCUCHANDO · HABLA CUANDO QUIERAS';
  setBusy(false);
}

async function playNextStreamingAudio(session) {
  if (continuousSession !== session || session.audioPlaying || !session.audioQueue.length) {
    finishStreamingTurn(session);
    return;
  }
  const item = session.audioQueue.shift();
  const token = session.playbackToken;
  session.audioPlaying = true;
  recordingLabel.textContent = 'GWEN ESTÁ HABLANDO · PUEDES INTERRUMPIR';
  try {
    if (session.context.state === 'suspended') await session.context.resume();
    const decoded = await session.context.decodeAudioData(base64Buffer(item.audio));
    if (continuousSession !== session || token !== session.playbackToken) return;
    const source = session.context.createBufferSource();
    session.audioSource = source;
    source.buffer = decoded;
    source.connect(session.context.destination);
    source.onended = () => {
      if (session.audioSource === source) session.audioSource = null;
      session.audioPlaying = false;
      playNextStreamingAudio(session);
    };
    source.start(0);
  } catch (_) {
    session.audioPlaying = false;
    if (token === session.playbackToken) playNextStreamingAudio(session);
  }
}
function handleRealtimeEvent(session, event) {
  if (continuousSession !== session) return;
  if (event.type === 'ready') {
    session.ready = true;
    recordingLabel.textContent = 'ESCUCHANDO · HABLA CUANDO QUIERAS';
  } else if (event.type === 'partial' && !session.processing) {
    recordingLabel.textContent = event.text ? `TE ESCUCHO · ${event.text.slice(-52)}` : 'TE ESCUCHO';
  } else if (event.type === 'transcript') {
    session.generation = event.generation;
    addMessage('user', `🎙️ ${event.text}`);
  } else if (event.type === 'answer_delta' && event.generation === session.generation) {
    if (!session.answerBody) session.answerBody = addMessage('assistant', '');
    session.answerBody.textContent += event.text;
    conversation.scrollTo({ top: conversation.scrollHeight, behavior: 'smooth' });
  } else if (event.type === 'audio' && event.generation === session.generation) {
    session.audioQueue.push({ audio: event.audio, audioType: event.audio_type });
    playNextStreamingAudio(session);
  } else if (event.type === 'turn_done' && event.generation === session.generation) {
    session.serverDone = true;
    finishStreamingTurn(session);
  } else if (event.type === 'interrupted') {
    stopStreamingAudio(session);
  } else if (event.type === 'error') {
    stopStreamingAudio(session);
    session.processing = false;
    setBusy(false);
    recordingLabel.textContent = 'ESCUCHANDO · HABLA CUANDO QUIERAS';
    showToast(event.message || 'La voz no está disponible ahora mismo.');
  }
}

async function openRealtimeSocket() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${scheme}://${location.host}/ws/voice`);
  await new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => reject(new Error('Tiempo de conexión agotado')), 10000);
    socket.addEventListener('open', () => { window.clearTimeout(timeout); resolve(); }, { once: true });
    socket.addEventListener('error', () => { window.clearTimeout(timeout); reject(new Error('Sin conexión')); }, { once: true });
  });
  return socket;
}

async function startContinuousSession() {
  const AudioEngine = window.AudioContext || window.webkitAudioContext;
  const context = new AudioEngine();
  await context.resume();
  const unlock = context.createBufferSource();
  unlock.buffer = context.createBuffer(1, 1, context.sampleRate);
  unlock.connect(context.destination);
  unlock.start(0);
  if (audioSession) await stopAudioRecording();
  let socket;
  try { socket = await openRealtimeSocket(); }
  catch (error) { await context.close(); throw error; }
  const audio = { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 };
  if (microphoneSelect.value) audio.deviceId = { exact: microphoneSelect.value };
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio }); }
  catch (error) { socket.close(); throw error; }
  await refreshMicrophones();
  const source = context.createMediaStreamSource(stream);
  const processor = context.createScriptProcessor(2048, 1, 1);
  const silent = context.createGain(); silent.gain.value = 0;
  const session = {
    socket, stream, context, source, processor, silent, ready: false, processing: false,
    speaking: false, speechFrames: 0, silenceStarted: 0, startedSpeakingAt: 0,
    noiseFloor: .003, generation: 0, answerBody: null, audioQueue: [], serverDone: false,
    audioSource: null, audioPlaying: false, playbackToken: 0
  };
  continuousSession = session;
  socket.addEventListener('message', event => {
    try { handleRealtimeEvent(session, JSON.parse(event.data)); } catch (_) {}
  });
  socket.addEventListener('close', () => {
    if (continuousSession === session) {
      showToast('Se cerró la voz en vivo. Puedes seguir usando el botón de micrófono.');
      stopContinuousSession();
    }
  });
  processor.onaudioprocess = event => {
    if (continuousSession !== session || socket.readyState !== WebSocket.OPEN) return;
    const samples = new Float32Array(event.inputBuffer.getChannelData(0));
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    const rms = Math.sqrt(energy / samples.length);
    inputLevel.style.width = `${Math.min(100, rms * 900)}%`;
    const threshold = Math.max(.006, session.noiseFloor * 3);

    if (session.processing) {
      const interruptionThreshold = Math.max(.014, session.noiseFloor * 4.5);
      session.speechFrames = rms > interruptionThreshold ? session.speechFrames + 1 : 0;
      if (session.speechFrames < 3) return;
      stopStreamingAudio(session);
      socket.send(JSON.stringify({ type: 'interrupt' }));
      session.processing = false;
      session.speaking = true;
      session.startedSpeakingAt = performance.now();
      session.silenceStarted = 0;
      session.answerBody = null;
      recordingLabel.textContent = 'TE ESCUCHO';
    }

    socket.send(JSON.stringify({ type: 'audio', audio: pcmBase64(resample(samples, context.sampleRate)) }));
    if (!session.speaking) {
      if (rms < .02) session.noiseFloor = session.noiseFloor * .98 + rms * .02;
      session.speechFrames = rms > threshold ? session.speechFrames + 1 : 0;
      if (session.speechFrames >= 2) {
        session.speaking = true;
        session.startedSpeakingAt = performance.now();
        session.silenceStarted = 0;
        recordingLabel.textContent = 'TE ESCUCHO';
      }
      return;
    }
    if (rms < threshold * .72) {
      if (!session.silenceStarted) session.silenceStarted = performance.now();
    } else session.silenceStarted = 0;
    const utteranceSeconds = (performance.now() - session.startedSpeakingAt) / 1000;
    if ((session.silenceStarted && performance.now() - session.silenceStarted > 420 && utteranceSeconds > .45) || utteranceSeconds > 45) {
      socket.send(JSON.stringify({ type: 'commit', duration: Math.max(1, Math.round(utteranceSeconds)) }));
      session.processing = true;
      session.speaking = false;
      session.speechFrames = 0;
      session.answerBody = null;
      session.serverDone = false;
      recordingLabel.textContent = 'GWEN ESTÁ PENSANDO';
      setBusy(true);
    }
  };
  source.connect(processor); processor.connect(silent); silent.connect(context.destination);
  sessionButton.classList.add('active'); sessionButton.innerHTML = '<span></span> Finalizar voz';
  document.querySelector('.shell').classList.add('voice-session');
  micButton.disabled = true; recordingStatus.hidden = false; recordingTime.textContent = 'LIVE';
  recordingLabel.textContent = 'CONECTANDO VOZ EN VIVO…';
}

async function stopContinuousSession() {
  const session = continuousSession;
  if (!session) return;
  continuousSession = null;
  stopStreamingAudio(session);
  if (session.socket.readyState === WebSocket.OPEN) {
    session.socket.send(JSON.stringify({ type: 'close' }));
    session.socket.close();
  }
  session.processor.disconnect(); session.source.disconnect(); session.stream.getTracks().forEach(track => track.stop());
  await session.context.close();
  setBusy(false);
  sessionButton.classList.remove('active'); sessionButton.innerHTML = '<span></span> Iniciar voz';
  document.querySelector('.shell').classList.remove('voice-session');
  micButton.disabled = false; recordingStatus.hidden = true; inputLevel.style.width = '0';
  showToast('Sesión de voz finalizada');
}
sessionButton.addEventListener('click', async () => {
  try { if (continuousSession) await stopContinuousSession(); else await startContinuousSession(); }
  catch (_) { showToast('No pude iniciar la sesión. Revisa el micrófono seleccionado.'); }
});
micButton.addEventListener('click', async () => {
  try {
    if (continuousSession) return;
    if (audioSession) await stopAudioRecording(); else await startAudioRecording();
  }
  catch (_) { showToast('No pude abrir ese micrófono. Revisa el permiso o elige otra entrada.'); }
});
navigator.mediaDevices?.addEventListener?.('devicechange', refreshMicrophones);
refreshMicrophones();
loadState();
