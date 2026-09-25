import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SpeechGate,VoiceChat} from '../jarvis/static/voice-chat.mjs';

test('silence is discarded rather than transcribed',()=>{
 const gate=new SpeechGate(0);
 assert.equal(gate.sample(0,1200),null);
 assert.equal(gate.sample(0,25000),'discard');
});
test('speech is submitted only after an utterance and pause',()=>{
 const gate=new SpeechGate(0);
 for(let t=50;t<=500;t+=50)assert.equal(gate.sample(.1,t),null);
 assert.equal(gate.sample(0,1200),null);
 assert.equal(gate.sample(0,1600),'send');
});
test('short noise does not become a message',()=>{
 const gate=new SpeechGate(0);gate.sample(.1,50);gate.sample(.1,100);
 assert.equal(gate.sample(0,1500),null);assert.equal(gate.sample(0,25000),'discard');
});
function make(hooks={}){
 const states=[],errors=[];
 const voice=new VoiceChat({onState:p=>states.push(p),onError:e=>errors.push(e.message),onTranscript:()=>{},cancelRun:async()=>{},...hooks});
 voice.active=true;voice.epoch=1;
 return {voice,states,errors};
}
test('ending a call drops a late transcription before it can send',async()=>{
 let resolve,sends=0;
 const {voice}=make({transcribe:()=>new Promise(r=>resolve=r),sendMessage:()=>{sends++;}});
 const processing=voice.process(new Blob(['audio']),'webm',1);
 await voice.end();resolve('late speech');await processing;
 assert.equal(sends,0);
});
test('ending while a reply is pending prevents synthesis',async()=>{
 let resolve,synthesis=0;
 const {voice}=make({transcribe:async()=> 'hello',sendMessage:()=>new Promise(r=>resolve=r),synthesize:()=>{synthesis++;}});
 const processing=voice.process(new Blob(['audio']),'webm',1);
 await new Promise(r=>setImmediate(r));await voice.end();resolve('late reply');await processing;
 assert.equal(synthesis,0);
});
test('provider failures end the call and release microphone tracks',async()=>{
 let stopped=0;
 const track={enabled:true,stop:()=>stopped++};
 const {voice,errors}=make({transcribe:async()=>{throw new Error('Speech unavailable');}});
 voice.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};
 await voice.process(new Blob(['audio']),'webm',1);
 assert.equal(voice.active,false);assert.equal(stopped,1);assert.equal(track.enabled,false);assert.deepEqual(errors,['Speech unavailable']);
});
test('mute disables microphone and interrupt cancels pending work',async()=>{
 let cancelled=0;
 const track={enabled:true,stop:()=>{}};
 const {voice}=make({cancelRun:async()=>cancelled++});
 voice.stream={getAudioTracks:()=>[track],getTracks:()=>[track]};voice.phase='listening';
 voice.toggleMute();assert.equal(track.enabled,false);assert.equal(voice.phase,'muted');
 await voice.interrupt();assert.equal(cancelled,1);assert.equal(voice.phase,'muted');
});
test('completed speech returns to listening after audio ends',async()=>{
 const {voice,states}=make({transcribe:async()=> 'Hello',sendMessage:async()=> 'Hi there',synthesize:async()=>new ArrayBuffer(8)});
 voice.context={state:'running',decodeAudioData:async()=>({}),resume:async()=>{},destination:{},createBufferSource(){return {connect(){},disconnect(){},start(){queueMicrotask(()=>this.onended());}};}};
 voice.listen=()=>voice.state('listening');
 await voice.process(new Blob(['audio']),'webm',1);
 assert.deepEqual(states,['transcribing','thinking','preparing','speaking','listening']);
});
