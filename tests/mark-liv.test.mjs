import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {parseFace} from '../jarvis/static/avatar.mjs';
import {VoiceChat} from '../jarvis/static/voice-chat.mjs';

test('bundled face contains valid drawable geometry',()=>{
 const mesh=parseFace(readFileSync(new URL('../jarvis/static/mark-liv/face_model.obj',import.meta.url),'utf8'));
 assert.equal(mesh.vertices.length,468);assert.ok(mesh.edges.length>800);
 assert.ok(mesh.vertices.every(v=>v.length===3&&v.every(Number.isFinite)));
 assert.ok(mesh.edges.every(e=>e.every(i=>Number.isInteger(i)&&i>=0&&i<468)));
});
test('push to talk waits with disabled microphone until pressed',()=>{
 const track={enabled:true};const voice=new VoiceChat({onState:()=>{}});
 voice.active=true;voice.pushToTalk=true;voice.stream={getAudioTracks:()=>[track]};
 voice.listen();assert.equal(voice.phase,'ready');assert.equal(track.enabled,false);
 let opened=0;voice.listen=()=>opened++;voice.press();assert.equal(opened,1);assert.equal(voice.held,true);
 let sent=0;voice.phase='listening';voice.finishCapture=()=>sent++;voice.release();assert.equal(sent,1);assert.equal(voice.held,false);
});
test('push to talk does not open microphone during replies or while muted',()=>{
 const voice=new VoiceChat({onState:()=>{}});voice.active=true;voice.pushToTalk=true;
 let opened=0;voice.listen=()=>opened++;
 for(const phase of ['speaking','thinking','transcribing','connecting']){voice.phase=phase;voice.press();}
 voice.phase='ready';voice.muted=true;voice.press();assert.equal(opened,0);
});
