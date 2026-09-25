import {speechChunks} from './speech-output.mjs';
// Explicitly started, half-duplex voice chat. Never listens during playback.
export class SpeechGate {
  constructor(now) { this.started=now; this.lastVoice=now; this.speechMs=0; this.previous=now; }
  sample(rms, now) {
    const elapsed=Math.min(150,now-this.previous);this.previous=now;
    if(rms>0.025){this.speechMs+=elapsed;this.lastVoice=now;}
    if(this.speechMs>=300 && now-this.lastVoice>=1100)return 'send';
    if(now-this.started>=25000)return this.speechMs>=300?'send':'discard';
    return null;
  }
}

export class VoiceChat {
  constructor(hooks, env=globalThis) {
    this.hooks=hooks;this.env=env;this.active=false;this.muted=false;this.epoch=0;
    this.stream=null;this.context=null;this.recorder=null;this.timer=null;this.player=null;
    this.pushToTalk=false;this.held=false;
  }
  state(name){this.phase=name;this.hooks.onState(name,this.muted);}
  tracks(enabled){this.stream?.getAudioTracks().forEach(track=>{track.enabled=enabled;});}
  async start(){
    if(this.active)return;
    if(!this.env.navigator?.mediaDevices?.getUserMedia || !this.env.MediaRecorder)throw new Error('Voice chat needs microphone support. Open JARVIS in Chrome or Edge.');
    this.active=true;const epoch=++this.epoch;this.muted=false;this.state('connecting');
    try{
      const Context=this.env.AudioContext||this.env.webkitAudioContext;
      if(!Context)throw new Error('Audio playback is not supported in this browser.');
      this.context=new Context();await this.context.resume();
      // Resume audio in the start-button gesture before asynchronous provider calls.
      const stream=await this.env.navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      if(!this.current(epoch)){stream.getTracks().forEach(t=>t.stop());return;}
      this.stream=stream;this.input=this.context.createMediaStreamSource(stream);this.analyser=this.context.createAnalyser();this.analyser.fftSize=2048;this.input.connect(this.analyser);
      this.listen();
    }catch(error){if(this.current(epoch)){await this.end();throw error;}}
  }
  current(epoch){return this.active&&epoch===this.epoch;}
  stopCapture(){
    this.finishCapture=null;
    this.env.clearInterval(this.timer);this.timer=null;
    if(this.recorder){this.recorder.onstop=null;if(this.recorder.state!=='inactive')this.recorder.stop();this.recorder=null;}
    this.tracks(false);
  }
  stopPlayback(){if(this.player){this.player.onended=null;try{this.player.stop();}catch{}this.player.disconnect();this.player=null;}}
  listen(){
    if(!this.active)return;
    if(this.muted){this.tracks(false);this.state('muted');return;}
    if(this.pushToTalk&&!this.held){this.stopCapture();this.state('ready');return;}
    this.stopCapture();this.tracks(true);
    const mime=['audio/webm;codecs=opus','audio/ogg;codecs=opus'].find(t=>this.env.MediaRecorder.isTypeSupported(t));
    if(!mime){this.fail(new Error('This browser cannot record voice chat. Try Chrome or Edge.'));return;}
    const epoch=this.epoch,parts=[],gate=new SpeechGate(Date.now());let size=0,send=false;
    const recorder=new this.env.MediaRecorder(this.stream,{mimeType:mime,audioBitsPerSecond:64000});this.recorder=recorder;
    recorder.ondataavailable=e=>{parts.push(e.data);size+=e.data.size;if(size>900000&&recorder.state==='recording')finish(gate.speechMs>=300);};
    recorder.onerror=()=>this.fail(new Error('Microphone recording stopped unexpectedly.'));
    const finish=shouldSend=>{if(recorder.state!=='recording')return;send=shouldSend;this.env.clearInterval(this.timer);this.timer=null;recorder.stop();this.tracks(false);};
    this.finishCapture=()=>finish(gate.speechMs>=300);
    recorder.onstop=()=>{
      this.recorder=null;if(!this.current(epoch))return;
      if(!send){this.listen();return;}
      const blob=new Blob(parts,{type:mime});
      this.process(blob,mime.includes('ogg')?'ogg':'webm',epoch);
    };
    recorder.start(200);this.state('listening');const samples=new Float32Array(this.analyser.fftSize);
    this.timer=this.env.setInterval(()=>{
      if(!this.current(epoch))return;
      this.analyser.getFloatTimeDomainData(samples);
      const rms=Math.sqrt(samples.reduce((sum,x)=>sum+x*x,0)/samples.length);
      this.hooks.onLevel?.(Math.min(1,rms*12));
      const action=gate.sample(rms,Date.now());if(action&&(!this.pushToTalk||Date.now()-gate.started>=25000))finish(action==='send');
    },50);
  }
  async process(blob,format,epoch){
    const controller=new AbortController();this.request=controller;
    try{
      if(blob.size>1048576)throw new Error('That recording was too long. Please try a shorter message.');
      this.state('transcribing');const text=(await this.hooks.transcribe(blob,format,controller.signal)).trim();
      if(!this.current(epoch))return;
      if(!text){this.listen();return;}
      this.hooks.onTranscript(text);this.state('thinking');
      const answer=await this.hooks.sendMessage(text,controller.signal);
      if(!this.current(epoch))return;
      if(!answer)throw new Error('JARVIS could not complete that reply. Check the chat for details.');
      this.state('preparing');
      // Read long replies in bounded segments instead of silently truncating them.
      const chunks=speechChunks(answer);
      const fetchPart=chunk=>Promise.resolve().then(()=>this.hooks.synthesize(chunk,controller.signal)).then(bytes=>({bytes}),error=>({error}));
      let pending=chunks.length?fetchPart(chunks[0]):null;
      for(let index=0;index<chunks.length;index++){
        const result=await pending;if(result.error)throw result.error;const bytes=result.bytes;
        if(!this.current(epoch))return;
        const decoded=await this.context.decodeAudioData(bytes);
        if(!this.current(epoch))return;
        await this.context.resume();
        if(this.context.state!=='running')throw new Error('Audio is blocked. End the call and start it again, or open JARVIS in Chrome or Edge.');
        if(index+1<chunks.length)pending=fetchPart(chunks[index+1]);
        this.state('speaking');
        await new Promise(resolve=>{
          const player=this.context.createBufferSource();this.player=player;player.buffer=decoded;player.connect(this.context.destination);
          this.hooks.onAudio?.(this.context,player);
          this.playbackDone=resolve;player.onended=()=>{player.disconnect();if(this.player===player)this.player=null;this.playbackDone=null;resolve();};player.start();
        });
        if(!this.current(epoch))return;
      }
      this.listen();
    }catch(error){if(this.current(epoch)&&error.name!=='AbortError')await this.fail(error);}
  }
  toggleMute(){
    this.muted=!this.muted;
    if(this.muted&&this.phase==='listening'){this.stopCapture();this.state('muted');}
    else if(!this.muted&&this.phase==='muted')this.listen();
    else this.state(this.phase);
  }
  press(){if(!this.active||this.muted||this.phase!=='ready')return;this.held=true;this.listen();}
  release(){this.held=false;if(this.pushToTalk&&this.phase==='listening')this.finishCapture?.();}
  async interrupt(){
    if(!this.active)return;
    ++this.epoch;this.request?.abort();this.stopCapture();this.stopPlayback();this.playbackDone?.();this.playbackDone=null;
    this.state('stopping');
    try{await this.hooks.cancelRun();if(this.active)this.listen();}catch(error){await this.fail(error);}
  }
  async fail(error){await this.end();this.hooks.onError(error);}
  async end(){
    this.held=false;
    this.active=false;++this.epoch;this.request?.abort();this.stopCapture();this.stopPlayback();this.playbackDone?.();this.playbackDone=null;
    this.stream?.getTracks().forEach(t=>t.stop());this.stream=null;this.input?.disconnect();
    const context=this.context;this.context=null;if(context)await context.close().catch(()=>{});
    this.state('off');
  }
}
