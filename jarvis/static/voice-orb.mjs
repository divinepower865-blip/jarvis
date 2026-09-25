// A decorative audio visualizer; the voice controller owns capture and playback.
export class VoiceOrb {
  constructor(element, env=globalThis){
    this.element=element;this.env=env;this.phase='off';this.level=0;this.inputLevel=0;
    this.reduced=env.matchMedia('(prefers-reduced-motion: reduce)');
    this.draw=this.draw.bind(this);this.hue=190;this.running=false;
  }
  async start(){if(this.running)return;this.running=true;this.previous=this.env.performance.now();this.frame=this.env.requestAnimationFrame(this.draw);}
  setState(phase){this.phase=phase;this.element.dataset.phase=phase;if(phase!=='listening')this.inputLevel=0;if(phase!=='speaking')this.detach();}
  setLevel(level){this.inputLevel=Number.isFinite(level)?Math.max(0,Math.min(1,level)):0;}
  detach(){if(this.source&&this.analyser){try{this.source.disconnect(this.analyser);}catch{}}this.analyser?.disconnect();this.source=this.analyser=this.samples=this.context=null;}
  setAudio(context,source){this.detach();this.context=context;this.source=source;this.analyser=context.createAnalyser();this.analyser.fftSize=512;this.samples=new Float32Array(512);source.connect(this.analyser);}
  draw(now){
    if(!this.running)return;
    const dt=Math.max(0,Math.min(.1,(now-this.previous)/1000));this.previous=now;
    let target=0;
    if(!this.env.document.hidden){
      if(this.phase==='listening')target=this.inputLevel;
      if(this.phase==='speaking'&&this.context?.state==='running'&&this.analyser){
        this.analyser.getFloatTimeDomainData(this.samples);let sum=0;for(const x of this.samples)sum+=x*x;
        target=Math.min(1,Math.max(0,Math.sqrt(sum/this.samples.length)-.008)*8);
      }
    }
    this.level+=(target-this.level)*(1-Math.exp(-(target>this.level?22:7)*dt));
    this.element.style.setProperty('--orb-scale',String(this.reduced.matches?1:1+this.level*.22));
    this.element.style.setProperty('--orb-glow',String(this.reduced.matches?.25:.2+this.level*.6));
    this.element.style.setProperty('--orb-hue',String(this.hue));
    this.frame=this.env.requestAnimationFrame(this.draw);
  }
  stop(){this.running=false;this.env.cancelAnimationFrame(this.frame);this.detach();this.element.style.setProperty('--orb-scale','1');this.element.style.setProperty('--orb-glow','.2');}
}
