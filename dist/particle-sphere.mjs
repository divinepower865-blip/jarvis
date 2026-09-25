import {VoiceOrb} from './voice-orb.mjs';

// Seeded geometry keeps the visual stable across resize and refresh.
export function sphereGeometry(count=800){
  let seed=4791;const random=()=>{seed=(Math.imul(seed,1664525)+1013904223)>>>0;return seed/4294967296;};
  const points=[];
  for(let i=0;i<count;i++){
    const y=random()*2-1,a=random()*Math.PI*2,r=Math.sqrt(1-y*y);
    const x=r*Math.cos(a),z=r*Math.sin(a);
    // Uneven surface density gives the web its open, organic patches.
    if(Math.sin(x*7+z*4)*Math.cos(y*6-z*2)>.66&&random()>.16)continue;
    points.push([x,y,z,random()]);
  }
  const edges=[];
  for(let i=0;i<points.length;i++){
    const nearest=[];
    for(let j=i+1;j<points.length;j++){
      const d=points[i].slice(0,3).reduce((s,v,k)=>s+(v-points[j][k])**2,0);
      if(d<.065)nearest.push([j,d]);
    }
    nearest.sort((a,b)=>a[1]-b[1]);
    for(const [j] of nearest.slice(0,5))edges.push([i,j]);
  }
  return {points,edges};
}

export class ParticleSphere extends VoiceOrb {
  constructor(element,env=globalThis){
    super(element,env);this.canvas=element.querySelector('canvas');this.ctx=this.canvas.getContext('2d');
    this.geometry=sphereGeometry();this.angle=.35;this.lastPaint=0;
  }
  draw(now){
    super.draw(now);if(!this.running||this.env.document.hidden)return;
    // Cap canvas work at 30fps; audio level sampling stays in the parent loop.
    if(now-this.lastPaint<32)return;
    const dt=Math.min(.1,(now-this.lastPaint)/1000);this.lastPaint=now;
    const {width,height}=this.element.getBoundingClientRect();if(!width||!height)return;
    const dpr=Math.min(2,this.env.devicePixelRatio||1),w=Math.round(width*dpr),h=Math.round(height*dpr);
    if(this.canvas.width!==w||this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
    const c=this.ctx;c.setTransform(dpr,0,0,dpr,0,0);c.fillStyle='#050507';c.fillRect(0,0,width,height);
    const active=this.reduced.matches?0:this.level;
    if(!this.reduced.matches)this.angle+=dt*(.035+active*.07);
    const co=Math.cos(this.angle),si=Math.sin(this.angle),radius=Math.min(width*.36,height*.44)*(1+active*.045);
    const projected=this.geometry.points.map(([x,y,z,phase])=>{
      const rx=x*co+z*si,rz=z*co-x*si;
      const ripple=1+active*.018*Math.sin(phase*20+now*.004);
      return [width/2+rx*radius*ripple,height/2+y*radius*ripple,rz];
    });
    const hue=this.hue;
    c.lineWidth=.45;
    for(const [a,b] of this.geometry.edges){
      const p=projected[a],q=projected[b],front=(p[2]+q[2]+2)/4;
      c.strokeStyle=`hsla(${hue},65%,${44+active*15}%,${.06+front*.24+active*.1})`;
      c.beginPath();c.moveTo(p[0],p[1]);c.lineTo(q[0],q[1]);c.stroke();
    }
    for(let i=0;i<projected.length;i++){
      const [x,y,z]=projected[i],front=(z+1)/2;
      c.fillStyle=`hsla(${hue},80%,${55+front*20}%,${.22+front*.65})`;
      c.beginPath();c.arc(x,y,.35+front*.45+active*.18,0,Math.PI*2);c.fill();
    }
  }
}
