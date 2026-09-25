import {VoiceChat} from './voice-chat.mjs';
import {SpeechOutput} from './speech-output.mjs';
import {ParticleSphere} from './particle-sphere.mjs';
let hologram=null;
let replySpeaker=null, replyPhase='off';
const $ = id => document.getElementById(id);
let voice = null, pendingRunStart = null;
let hudSentCount = 0;
const hudStartedAt = Date.now();
function updateHUD(){
  const phase=voice?.active?voice.phase:(busy?'thinking':replyPhase);
  const labels={off:'Ready when you are',connecting:'Connecting microphone…',listening:'Listening to you…',muted:'Microphone muted',transcribing:'Understanding your words…',thinking:'Thinking…',preparing:'Preparing voice…',speaking:'JARVIS is speaking',stopping:'Stopping…'};
  $('hud-reactor').dataset.phase=phase;$('hud-status').textContent=labels[phase]||'Standing by';
  hologram?.setState(phase);
  if(phase==='ready')$('hud-status').textContent='Hold to talk · Ctrl + Space';
  $('hud-assistant').textContent=busy?'Working':voice?.active?'Voice chat':'Standby';
  $('hud-microphone').textContent=phase==='listening'?'Listening':voice?.active?'Paused':'Off';
  $('hud-call-state').textContent=voice?.active?'Active':'Standby';
  $('hud-status-track').classList.toggle('enabled',busy||Boolean(voice?.active));
  $('hud-mic-track').classList.toggle('enabled',phase==='listening');
  $('hud-hint').textContent=voice?.active?(voice.pushToTalk?'Hold the talk button or Ctrl + Space in this page.':'Pause after speaking. JARVIS will reply aloud.'):'Start a voice chat to speak with JARVIS.';
  $('voice-mode').disabled=Boolean(voice?.active);
  $('push-talk').hidden=!(voice?.active&&voice.pushToTalk);
}
let token = sessionStorage.getItem('jarvis.token') || '', conversation = null, run = null;
let busy = false, capabilities = null, recorder = null, recordingTimer = null, audio = null, voiceAbort = null;
const localTools = ['task_create','task_update','task_complete','task_delete','note_create','note_update','note_delete','reminder_create','reminder_pause','reminder_resume','reminder_cancel','memory_create'];
const dialog = $('connect-dialog');
const notice = text => { $('notice').textContent = text; };
function errorText(error) { return error?.message || 'Could not connect. Check that JARVIS is running.'; }
async function api(path, options = {}) {
  const response = await fetch(path, { ...options, headers: { 'Authorization': `Bearer ${token}`, ...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers } });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    if (response.status === 401 && !dialog.open) dialog.showModal();
    throw new Error(data.error?.message || `Request failed (${response.status}).`);
  }
  return response;
}
async function json(path, method='GET', body) { return (await api(path, {method, body:body === undefined ? undefined : JSON.stringify(body)})).json(); }
function setBusy(value) { busy=value; const locked=value||Boolean(voice?.active); $('send').disabled=locked; $('new-chat').disabled=locked; $('stop').hidden=!(value||replyPhase!=='off'); $('microphone').disabled=locked||capabilities?.integrations.speech_recognition.status==='unconfigured'; $('voice-start').disabled=value||Boolean(voice?.active); $('allow-actions').disabled=locked; for(const el of $('conversations').querySelectorAll('button')) el.disabled=locked; updateHUD(); }
function clearChat() { $('messages').replaceChildren(); $('approvals').replaceChildren(); }
function addMessage(role, text='') {
  $('welcome')?.remove();
  const row=document.createElement('article'); row.className=`message ${role}`;
  const speaker=document.createElement('div'); speaker.className='speaker'; speaker.textContent=role==='user'?'YOU':'JARVIS';
  const content=document.createElement('div'); content.className='text'; content.textContent=text;
  row.append(speaker,content); $('messages').append(row); scrollBottom(); return {row,content};
}
function scrollBottom() { $('messages').scrollTop=$('messages').scrollHeight; }
function stopVoice() { replySpeaker?.stop(); voiceAbort?.abort();voiceAbort=null;if(audio){audio.pause();URL.revokeObjectURL(audio.src);audio=null;} }
async function loadList() {
  const rows=await json('/v1/conversations'); $('conversations').replaceChildren();
  for(const c of rows){ const b=document.createElement('button');b.textContent=c.title;b.title=c.title;b.classList.toggle('selected',c.id===conversation);b.disabled=busy||Boolean(voice?.active);b.onclick=()=>openConversation(c.id).catch(e=>notice(errorText(e)));$('conversations').append(b); }
}
async function openConversation(id) {
  if(busy)return; stopVoice();const c=await json(`/v1/conversations/${id}`);conversation=id;sessionStorage.setItem('jarvis.conversation',id);$('chat-title').textContent=c.title;clearChat();
  for(const m of c.messages){const message=addMessage(m.role,m.content);}
  notice('');await loadList();
}
async function connect(){
  capabilities=await json('/v1/capabilities');sessionStorage.setItem('jarvis.token',token);$('token').value='';dialog.close();$('connection-label').textContent=capabilities.integrations.model.mock?'Offline demo':'Connected';$('status-dot').classList.add('online');$('model-badge').textContent=capabilities.integrations.model.mock?'MOCK DEMO':'GROQ · CONNECTED';
  const voice=capabilities.integrations.speech_synthesis.status; $('voice-status').textContent=voice==='unconfigured'?'Choose a Fish voice to enable speech.':'Replies speak automatically.';
  $('hud-model').textContent=capabilities.integrations.model.mock?'Mock':'Groq';$('hud-speech').textContent=voice==='unconfigured'?'Not set':'Configured';
  $('microphone').disabled=capabilities.integrations.speech_recognition.status==='unconfigured';
  await loadList();
  const saved=sessionStorage.getItem('jarvis.conversation'); if(saved){try{await openConversation(saved);}catch{sessionStorage.removeItem('jarvis.conversation');}}
  const pending=sessionStorage.getItem('jarvis.run'); if(pending){try{const r=await json(`/v1/runs/${pending}`);if(!['completed','failed','cancelled'].includes(r.status)){await openConversation(r.conversation);run=r.id;setBusy(true);await follow(r.id);}else sessionStorage.removeItem('jarvis.run');}catch(e){notice(errorText(e));setBusy(false);}}
}
$('connect-form').onsubmit=async e=>{e.preventDefault();replySpeaker.unlock().catch(()=>{});token=$('token').value.trim().replace(/^['"]|['"]$/g,'');$('connect-error').textContent='';try{await connect();}catch(error){$('connect-error').textContent=errorText(error);}};
$('settings-button').onclick=async()=>{await endCall();dialog.showModal();};
$('disconnect').onclick=()=>{if(busy){$('connect-error').textContent='Stop the current response before disconnecting.';return;}stopVoice();token='';sessionStorage.removeItem('jarvis.token');sessionStorage.removeItem('jarvis.conversation');sessionStorage.removeItem('jarvis.run');conversation=null;clearChat();$('connection-label').textContent='Not connected';$('status-dot').classList.remove('online');$('conversations').replaceChildren();$('token').value='';};
// Browsing appearance settings does not require an authenticated API session.
$('new-chat').onclick=()=>{if(busy)return;stopVoice();conversation=null;sessionStorage.removeItem('jarvis.conversation');$('chat-title').textContent='New conversation';clearChat();notice('');loadList().catch(e=>notice(errorText(e)));$('message').focus();};
for(const b of document.querySelectorAll('[data-prompt]'))b.onclick=()=>{$('message').value=b.dataset.prompt;$('message').focus();};
$('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();$('composer').requestSubmit();}});
$('composer').onsubmit=async e=>{
  e.preventDefault();if(voice?.active)return;replySpeaker.unlock().catch(e=>notice(errorText(e)));await sendText($('message').value.trim());
};
async function sendText(text, signal){
  if(!text||busy)return null;if(!token){dialog.showModal();return null;}notice('');stopVoice();setBusy(true);
  try {
    if(!conversation){const c=await json('/v1/conversations','POST',{title:text.slice(0,65)});conversation=c.id;sessionStorage.setItem('jarvis.conversation',c.id);$('chat-title').textContent=c.title;}
    if(signal?.aborted){setBusy(false);return null;}
    pendingRunStart=json(`/v1/conversations/${conversation}/runs`,'POST',{message:text,permitted_tools:$('allow-actions').checked?localTools:[]});
    const r=await pendingRunStart;pendingRunStart=null;
    addMessage('user',text);$('message').value='';run=r.id;sessionStorage.setItem('jarvis.run',run);
    $('hud-count').textContent=String(++hudSentCount);
    if(signal?.aborted)await json(`/v1/runs/${run}/cancel`,'POST');
    loadList().catch(e=>notice(errorText(e)));return await follow(run);
  } catch(error){pendingRunStart=null;notice(errorText(error));setBusy(false);return null;}
}
async function showApproval(p){
  const id=p.approval_id||p.id;if(document.getElementById(`approval-${id}`))return;
  const card=document.createElement('div');card.className='approval';card.id=`approval-${id}`;
  const title=document.createElement('h3');title.textContent='Your approval is needed';
  const preview=document.createElement('pre');preview.textContent=JSON.stringify({action:p.tool,arguments:p.arguments,...(p.target?{target:p.target}:{}),...(p.previous?{previous:p.previous}:{})},null,2);
  card.append(title,preview);
  for(const [label,approve] of [['Approve action',true],['Reject',false]]){const b=document.createElement('button');b.textContent=label;b.onclick=async()=>{for(const x of card.querySelectorAll('button'))x.disabled=true;try{await json(`/v1/approvals/${id}/decision`,'POST',{approve,digest:p.digest});card.remove();}catch(e){notice(errorText(e));card.remove();}};card.append(b);}
  $('approvals').append(card);
}
const failures={provider_rejected:'Groq could not accept this request. Check the configured model and account access.',provider_timeout:'Groq took too long to respond. Please try again.',integration_unconfigured:'This feature needs to be configured in your local .env file.',run_timeout:'This request reached its time limit.',service_restarted:'The service restarted during this request. Please send it again.'};
async function follow(id){
  let cursor=0,finished=false,reply=addMessage('assistant'),attempt=0,seenText='',finalAnswer=null;$('activity').textContent='JARVIS is thinking…';
  try {
    while(!finished&&attempt<4){
      try{
        const response=await api(`/v1/runs/${id}/events`,{headers:{'Last-Event-ID':String(cursor)}});const reader=response.body.getReader();const decoder=new TextDecoder();let buffer='';
        try{while(true){const {value,done}=await reader.read();buffer+=decoder.decode(value,{stream:!done});let end;while((end=buffer.indexOf('\n\n'))>=0){const block=buffer.slice(0,end);buffer=buffer.slice(end+2);const line=block.split('\n').find(x=>x.startsWith('data: '));if(!line)continue;const event=JSON.parse(line.slice(6));if(event.event_id<=cursor)continue;cursor=event.event_id;const p=event.payload;
          if(event.event_type==='response.delta'){seenText+=p.text;reply.content.textContent=seenText;scrollBottom();}
          if(event.event_type==='tool.started')$('activity').textContent=`Working: ${p.tool.replaceAll('_',' ')}…`;
          if(event.event_type==='tool.completed')$('activity').textContent='Action completed. Preparing a reply…';
          if(event.event_type==='tool.failed')notice(p.error.message);
          if(event.event_type==='approval.required'){await showApproval(p);$('activity').textContent='Waiting for your decision…';}
          if(event.event_type.startsWith('run.')&&['completed','failed','cancelled'].includes(p.status)){finished=true;reply.content.textContent=p.result||(p.status==='cancelled'?'Response stopped. Completed actions have been kept.':failures[p.error_code]||`The request failed (${p.error_code}).`);if(p.status==='completed'){finalAnswer=p.result;}}
        }if(done)break;}}finally{reader.releaseLock();}
        if(!finished){const r=await json(`/v1/runs/${id}`);if(['completed','failed','cancelled'].includes(r.status)){finished=true;reply.content.textContent=r.result||failures[r.error]||`Request ${r.status}.`;if(r.result){finalAnswer=r.result;}}}
      }catch(e){attempt++;if(attempt>=4)throw e;$('activity').textContent='Reconnecting…';await new Promise(r=>setTimeout(r,1000));}
    }
    if(finished){sessionStorage.removeItem('jarvis.run');run=null;}
    if(finalAnswer&&!voice?.active)replySpeaker.say(finalAnswer).catch(e=>notice(errorText(e)));
    return finalAnswer;
  }finally{setBusy(false);$('activity').textContent='';$('approvals').replaceChildren();scrollBottom();}
}
$('stop').onclick=async()=>{if(voice?.active){await voice.interrupt();return;}if(run){try{await json(`/v1/runs/${run}/cancel`,'POST');}catch(e){notice(errorText(e));}}stopVoice();};
$('microphone').onclick=async()=>{
  if(recorder?.state==='recording'){recorder.stop();return;}
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){notice('Voice recording is not supported in this browser. You can still type a message.');return;}
  let stream;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true});const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus'].find(x=>MediaRecorder.isTypeSupported(x));if(!mime)throw new Error('This browser cannot record a supported audio format.');
    const chunks=[];let size=0;recorder=new MediaRecorder(stream,{mimeType:mime,audioBitsPerSecond:64000});$('microphone').classList.add('recording');$('microphone').title='Stop recording';notice('Listening… Click the microphone again to finish (maximum 30 seconds).');
    recorder.ondataavailable=e=>{chunks.push(e.data);size+=e.data.size;if(size>900000&&recorder.state==='recording')recorder.stop();};
    recorder.onstop=async()=>{clearTimeout(recordingTimer);stream.getTracks().forEach(t=>t.stop());$('microphone').classList.remove('recording');$('microphone').title='Record a voice message';$('microphone').disabled=true;notice('Transcribing your voice…');try{const blob=new Blob(chunks,{type:mime});if(blob.size>1048576)throw new Error('That recording is too large. Try a shorter message.');const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);const result=await json('/v1/voice/transcribe','POST',{audio_base64:btoa(binary),format:mime.includes('ogg')?'ogg':'webm'});$('message').value=result.text;notice('Voice transcribed. Review it, then press Send.');$('message').focus();}catch(e){notice(errorText(e));}finally{$('microphone').disabled=busy;}};
    recorder.start(500);recordingTimer=setTimeout(()=>{if(recorder.state==='recording')recorder.stop();},30000);
  }catch(e){stream?.getTracks().forEach(t=>t.stop());notice(errorText(e));}
};
async function cancelVoiceRun(){
  const pending=pendingRunStart;const r=pending?await pending:null;const id=r?.id||run;
  if(id)await json(`/v1/runs/${id}/cancel`,'POST');
}
async function endCall(){
  if(!voice?.active)return;
  await voice.end();
  try{await cancelVoiceRun();}catch(e){notice(errorText(e));}
}
const callLabels={connecting:'Connecting microphone…',listening:'Listening — speak naturally',muted:'Microphone muted',transcribing:'Understanding what you said…',thinking:'JARVIS is thinking…',preparing:'Preparing his voice…',speaking:'JARVIS is speaking',stopping:'Stopping the response…',off:'Voice chat ended'};
voice=new VoiceChat({
  onAudio:(context,node)=>hologram?.setAudio(context,node),
  onState(phase,muted){$('voice-panel').hidden=phase==='off';$('voice-state').textContent=phase==='ready'?'Ready — hold to talk':callLabels[phase];$('voice-mute').textContent=muted?'Unmute':'Mute';$('voice-mute').setAttribute('aria-pressed',String(muted));$('voice-interrupt').disabled=['connecting','listening','muted','stopping','ready'].includes(phase);setBusy(busy);},
  onError(error){notice(errorText(error));},
  onTranscript(text){$('voice-transcript').textContent=`You said: ${text}`;},
  async transcribe(blob,format,signal){const bytes=new Uint8Array(await blob.arrayBuffer());let binary='';for(const b of bytes)binary+=String.fromCharCode(b);const response=await api('/v1/voice/transcribe',{method:'POST',body:JSON.stringify({audio_base64:btoa(binary),format}),signal});return (await response.json()).text;},
  sendMessage:(text,signal)=>sendText(text,signal),
  async synthesize(text,signal){const response=await api('/v1/voice/speech',{method:'POST',body:JSON.stringify({text,format:'mp3'}),signal});return response.arrayBuffer();},
  cancelRun:cancelVoiceRun,
});
$('voice-start').onclick=async()=>{
  if(!token){dialog.showModal();return;}
  if(busy||recorder?.state==='recording'){notice('Finish the current message first.');return;}
  notice('');stopVoice();$('voice-transcript').textContent='Your microphone pauses while JARVIS thinks and speaks.';
  voice.pushToTalk=$('voice-mode').value==='push';
  try{capabilities=await json('/v1/capabilities');if(['speech_recognition','speech_synthesis'].some(k=>capabilities.integrations[k].status==='unconfigured'))throw new Error('Voice chat needs your Groq key and Fish Audio key and voice ID configured.');await voice.start();}catch(e){notice(errorText(e));}
};
$('voice-end').onclick=endCall;
$('voice-mute').onclick=()=>voice.toggleMute();
$('voice-interrupt').onclick=()=>voice.interrupt();
window.addEventListener('pagehide',()=>{voice.end();stopVoice();});
replySpeaker=new SpeechOutput(async(text,signal)=>{const response=await api('/v1/voice/speech',{method:'POST',body:JSON.stringify({text,format:'mp3'}),signal});return response.arrayBuffer();},phase=>{replyPhase=phase;setBusy(busy);});
replySpeaker.onAudio=(context,node)=>hologram?.setAudio(context,node);
hologram=new ParticleSphere($('hud-reactor'));hologram.start().catch(e=>notice(errorText(e)));
function setAvatarHue(value){const hue=Math.max(0,Math.min(360,Number(value)));if(!Number.isFinite(hue))return;hologram.hue=hue;$('hud-hue').value=String(hue);document.documentElement.style.setProperty('--accent',`hsl(${hue} 65% 43%)`);try{localStorage.setItem('jarvis.avatarHue',String(hue));}catch{}for(const swatch of document.querySelectorAll('[data-hue]'))swatch.setAttribute('aria-pressed',String(Number(swatch.dataset.hue)===hue));}
$('hud-hue').oninput=e=>setAvatarHue(e.target.value);
for(const swatch of document.querySelectorAll('[data-hue]'))swatch.onclick=()=>setAvatarHue(swatch.dataset.hue);
try{setAvatarHue(localStorage.getItem('jarvis.avatarHue')??210);}catch{setAvatarHue(210);}
$('push-talk').onpointerdown=e=>{e.preventDefault();e.currentTarget.setPointerCapture(e.pointerId);voice.press();};
for(const event of ['pointerup','pointercancel','lostpointercapture'])$('push-talk').addEventListener(event,()=>voice.release());
window.addEventListener('keydown',e=>{if(e.ctrlKey&&e.code==='Space'&&voice.active&&voice.pushToTalk){e.preventDefault();if(!e.repeat)voice.press();}});
window.addEventListener('keyup',e=>{if(e.code==='Space'||e.key==='Control')voice.release();});
window.addEventListener('blur',()=>voice.release());
window.addEventListener('pagehide',()=>hologram.stop());
$('hud-sound').onclick=async()=>{try{await replySpeaker.unlock();notice('Sound enabled. New replies will speak automatically.');$('hud-sound').textContent='Sound enabled';}catch(e){notice(errorText(e));}};
function updateClock(){const now=new Date();$('hud-clock').textContent=now.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit',second:'2-digit'});$('hud-date').textContent=now.toLocaleDateString(undefined,{month:'short',day:'numeric',year:'numeric'});const elapsed=Math.floor((Date.now()-hudStartedAt)/1000);$('hud-uptime').textContent=[Math.floor(elapsed/3600),Math.floor(elapsed/60)%60,elapsed%60].map(n=>String(n).padStart(2,'0')).join(':');}
updateClock();setInterval(updateClock,1000);
$('hud-keyboard').onclick=()=>{if(voice?.active){notice('End the call to type a message.');return;}$('message').focus();$('message').scrollIntoView({block:'nearest'});};
$('hud-export').onclick=async()=>{if(!conversation){notice('Start a conversation before exporting it.');return;}try{const c=await json(`/v1/conversations/${conversation}`);const text=c.messages.map(m=>`${m.role==='user'?'YOU':'JARVIS'}\n${m.content}`).join('\n\n');const url=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='jarvis-conversation.txt';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){notice(errorText(e));}};
if(token)connect().catch(e=>{notice(errorText(e));dialog.showModal();});
