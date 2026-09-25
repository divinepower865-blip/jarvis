import {test} from 'node:test';
import assert from 'node:assert/strict';
import {SpeechOutput,speechChunks} from '../jarvis/static/speech-output.mjs';

test('first speech segment is short and no text is dropped',()=>{
 const text='Hello there. '+('Here is another useful sentence. '.repeat(55)).trim();
 const chunks=speechChunks(text);
 assert.ok(chunks[0].length<=180);assert.ok(chunks.slice(1).every(c=>c.length<=650));
 assert.equal(chunks.join(' '),text);
});
test('plays automatically and prefetches while first segment is playing',async()=>{
 let calls=0,playing;
 class Context{state='running';destination={};async resume(){}async decodeAudioData(a){return a;}createBufferSource(){return {connect(){},disconnect(){},start(){playing=this;},stop(){}};}}
 const states=[];const speaker=new SpeechOutput(async()=>{calls++;return new ArrayBuffer(1);},s=>states.push(s),{AudioContext:Context});
 const done=speaker.say('Hello there. '+ 'A longer reply follows. '.repeat(12));
 await new Promise(r=>setImmediate(r));
 assert.equal(calls,2);assert.equal(states.at(-1),'speaking');
 playing.onended();await new Promise(r=>setImmediate(r));playing.onended();await done;
 assert.equal(states.at(-1),'off');
});
test('stop aborts generation and suppresses late playback',async()=>{
 let resolve,plays=0,signal;
 class Context{state='running';async resume(){}async decodeAudioData(a){return a;}createBufferSource(){plays++;}}
 const speaker=new SpeechOutput((text,s)=>{signal=s;return new Promise(r=>resolve=r);},()=>{},{AudioContext:Context});
 const done=speaker.say('Hello');await new Promise(r=>setImmediate(r));speaker.stop();
 assert.equal(signal.aborted,true);resolve(new ArrayBuffer(1));await done;assert.equal(plays,0);
});
