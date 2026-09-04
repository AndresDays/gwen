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
let recorder = null;
let chunks = [];
let startedAt = 0;
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
      const amount = document.createElement('span'); amount.textContent = `${value.toLocaleString()} / ${limit.toLocaleString()} ${unit}`;
      head.append(title, amount);
      const bar = document.createElement('div'); bar.className = 'bar';
      const fill = document.createElement('span'); fill.style.width = `${Math.min(100, value / limit * 100)}%`;
      bar.append(fill); row.append(head, bar); return row;
    }));
    usageDialog.showModal();
  } catch (error) { showToast(error.message); }
});
document.querySelector('#closeUsage').addEventListener('click', () => usageDialog.close());

micButton.addEventListener('click', async () => {
  if (recorder?.state === 'recording') { recorder.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    chunks = []; startedAt = Date.now();
    const preferredTypes = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'];
    const mimeType = preferredTypes.find(type => MediaRecorder.isTypeSupported(type));
    recorder = new MediaRecorder(stream, mimeType ? { mimeType, audioBitsPerSecond: 128000 } : undefined);
    recorder.addEventListener('dataavailable', event => chunks.push(event.data));
    recorder.addEventListener('stop', async () => {
      stream.getTracks().forEach(track => track.stop());
      clearInterval(timer); recordingStatus.hidden = true; micButton.classList.remove('recording');
      const duration = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
      const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
      if (blob.size < 1000) { showToast('La grabación quedó vacía. Mantén pulsado un poco más.'); return; }
      const extension = blob.type.includes('ogg') ? 'ogg' : blob.type.includes('mp4') ? 'm4a' : 'webm';
      const form = new FormData(); form.append('audio', blob, `voice.${extension}`); form.append('duration', String(duration));
      const voiceMessage = addMessage('user', '🎙️ Procesando tu voz…'); setBusy(true);
      try {
        const result = await request('/api/voice', { method: 'POST', body: form });
        voiceMessage.textContent = result.transcript ? `🎙️ ${result.transcript}` : '🎙️ Mensaje de voz';
        addMessage('assistant', result.answer);
        if (result.audio) new Audio(`data:${result.audio_type};base64,${result.audio}`).play().catch(() => {});
      } catch (error) { showToast(error.message); }
      finally { setBusy(false); }
    });
    recorder.start(); micButton.classList.add('recording'); recordingStatus.hidden = false;
    timer = setInterval(() => {
      const seconds = Math.floor((Date.now() - startedAt) / 1000);
      recordingTime.textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
      if (seconds >= 600) recorder.stop();
    }, 250);
  } catch (_) { showToast('Necesito permiso para usar el micrófono.'); }
});

loadState();
