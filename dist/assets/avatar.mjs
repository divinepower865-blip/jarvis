// Browser adaptation using the face geometry distributed with FatihMakes' Mark LIV.
// See mark-liv/NOTICE.txt for attribution. Motion is audio-reactive, not phoneme lip-sync.
export function parseFace(obj){
  const vertices=[],edges=[],seen=new Set();
  for(const line of obj.split('\n')){
    const fields=line.trim().split(/\s+/);
    if(fields[0]==='v')vertices.push(fields.slice(1,4).map(Number));
    if(fields[0]==='f'){
      const face=fields.slice(1).map(x=>Number(x.split('/')[0])-1);
      for(let i=0;i<face.length;i++){const pair=[face[i],face[(i+1)%face.length]].sort((a,b)=>a-b),key=pair.join(',');if(!seen.has(key)){seen.add(key);edges.push(pair);}}
    }
  }
  return {vertices,edges};
}
export class Hologram {
  constructor(canvas){this.canvas=canvas;this.ctx=canvas.getContext('2d');this.phase='off';this.level=0;this.hue=190;this.reduced=matchMedia('(prefers-reduced-motion: reduce)').matches;this.draw=this.draw.bind(this);}
  async start(){const response=await fetch('/assets/mark-liv/face_model.obj');if(!response.ok)throw new Error('Avatar geometry unavailable.');this.mesh=parseFace(await response.text());this.frame=requestAnimationFrame(this.draw);}
  setState(phase){this.phase=phase;}
  setAudio(context,node){this.context=context;this.analyser=context.createAnalyser();this.analyser.fftSize=256;node.connect(this.analyser);this.samples=new Float32Array(256);}
  draw(time){
    const c=this.ctx,w=this.canvas.width=600,h=this.canvas.height=640;c.clearRect(0,0,w,h);
    let amplitude=0;if(this.phase==='speaking'&&this.context?.state==='running'&&this.analyser){this.analyser.getFloatTimeDomainData(this.samples);amplitude=Math.min(1,Math.sqrt(this.samples.reduce((a,v)=>a+v*v,0)/256)*7);}
    this.level+=(amplitude-this.level)*.4;
    const t=time/1000,yaw=this.reduced?0:Math.sin(t*.45)*.12+(this.phase==='thinking'?.2:0),co=Math.cos(yaw),si=Math.sin(yaw);
    const points=this.mesh.vertices.map(([x,y,z])=>{const jaw=Math.max(0,Math.min(1,(-y-2)/5));y-=jaw*this.level*1.6;const xx=x*co+z*si,zz=z*co-x*si;return [w/2+xx*22,h/2-y*22,zz];});
    c.strokeStyle=`hsla(${this.hue},85%,65%,.18)`;c.lineWidth=.7;
    for(const [a,b] of this.mesh.edges){c.beginPath();c.moveTo(points[a][0],points[a][1]);c.lineTo(points[b][0],points[b][1]);c.stroke();}
    c.fillStyle=`hsla(${this.hue},95%,78%,.8)`;
    for(let i=0;i<points.length;i+=3){const [x,y]=points[i];c.fillRect(x,y,1.7,1.7);}
    c.strokeStyle=`hsla(${this.hue},90%,65%,.65)`;c.lineWidth=1.5;
    for(const ring of [[33,160,158,133,153,144,33],[263,387,385,362,380,373,263],[61,40,37,0,267,270,291,321,314,17,84,91,61]]){c.beginPath();ring.forEach((id,i)=>{const [x,y]=points[id];i?c.lineTo(x,y):c.moveTo(x,y);});c.stroke();}
    c.strokeStyle=`hsla(${this.hue},90%,55%,.25)`;c.beginPath();c.ellipse(300,565,140+this.level*10,20,0,0,Math.PI*2);c.stroke();
    this.frame=requestAnimationFrame(this.draw);
  }
  stop(){cancelAnimationFrame(this.frame);this.analyser?.disconnect();}
}
