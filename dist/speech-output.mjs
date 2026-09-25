export function speechChunks(text){
  const chunks=[];let rest=text.trim(),limit=180;
  while(rest){
    if(rest.length<=limit){chunks.push(rest);break;}
    const head=rest.slice(0,limit);const endings=[...head.matchAll(/[.!?](?:\s|$)/g)];
    let cut=endings.length?endings.at(-1).index+1:head.lastIndexOf(' ');
    if(cut<40)cut=limit;
    chunks.push(rest.slice(0,cut).trim());rest=rest.slice(cut).trim();limit=650;
  }
  return chunks;
}

export class SpeechOutput{
  constructor(synthesize,onState=()=>{},env=globalThis){this.synthesize=synthesize;this.onState=onState;this.env=env;this.epoch=0;this.context=null;this.enabled=true;}
  async unlock(){const Context=this.env.AudioContext||this.env.webkitAudioContext;if(!Context)throw new Error('Audio is unavailable in this browser. Try Chrome or Edge.');if(!this.context)this.context=new Context();await this.context.resume();}
  stop(){++this.epoch;this.abort?.abort();if(this.player){this.player.onended=null;try{this.player.stop();}catch{}this.player.disconnect();this.player=null;}this.done?.();this.done=null;this.onState('off');}
  async say(text){
    if(!this.enabled||!text)return;
    this.stop();const epoch=this.epoch,abort=new AbortController();this.abort=abort;
    const current=()=>epoch===this.epoch&&!abort.signal.aborted;
    // Prefetch one segment, never the whole answer. Capture rejection immediately.
    const fetchPart=part=>Promise.resolve().then(()=>this.synthesize(part,abort.signal)).then(bytes=>({bytes}),error=>({error}));
    try{
      await this.unlock();if(!current())return;
      if(this.context.state!=='running')throw new Error('Click Enable sound to let JARVIS speak.');
      const chunks=speechChunks(text);if(!chunks.length)return;
      this.onState('preparing');let pending=fetchPart(chunks[0]);
      for(let i=0;i<chunks.length;i++){
        const result=await pending;if(!current())return;if(result.error)throw result.error;
        const decoded=await this.context.decodeAudioData(result.bytes);if(!current())return;
        if(i+1<chunks.length)pending=fetchPart(chunks[i+1]);
        this.onState('speaking');await new Promise(resolve=>{const p=this.context.createBufferSource();this.player=p;p.buffer=decoded;p.connect(this.context.destination);this.onAudio?.(this.context,p);this.done=resolve;p.onended=()=>{p.disconnect();if(this.player===p)this.player=null;this.done=null;resolve();};p.start();});
        if(!current())return;
      }
    }finally{if(current()){abort.abort();this.abort=null;this.onState('off');}}
  }
}
