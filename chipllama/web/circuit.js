/* ChipLlama - Circuit tab: KiCad-style schematic editor (pure SVG, no third-party library, works offline).
   Model (schema 2): {schema:2, name, device, parts:[{id,type,params,x,y,rot,mx,my}], wires:[{a:"REF.PIN", b:"REF.PIN", pts:[{x,y}]}], assign:{pin:fn}}
   - part endpoints are PHYSICAL pin numbers (a tactile button has pins 1-4, 1-2 and 3-4 shorted inside); MCU endpoints are package pins.
   - power symbols (GND, +3V3, +5V, VDD) join by name, net labels join by name, junctions join every wire ending on them.
   Hotkeys (KiCad): W wire · L label · P power · A add part · M move · G drag · R rotate · X/Y mirror · E edit · Del delete ·
   C copy · Ctrl+D duplicate · Ctrl+Z / Ctrl+Y undo/redo · Esc cancel · Home fit.  ERC, netlist, auto-wire and codegen run server-side. */
(function(){
const NS='http://www.w3.org/2000/svg',GRID=10,PP=20;
let D=null,META=null,LIB={},CHK=null,PRJ=null,CL=null,SEL=new Set(),SELW=new Set(),VIEW={x:40,y:20,k:.8},T=null,READY=false,DRAG=null,FILE='';
let MODE=null,HOVER=null,MOUSE={x:0,y:0},UNDO=[],REDO=[],PKG=false,NOTES=[],NET={};
const $=id=>document.getElementById(id);
const el=(t,a={},p)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e};
const h=(t,a={},...c)=>{const e=document.createElement(t);for(const k in a){if(k==='on')for(const ev in a.on)e.addEventListener(ev,a.on[ev]);else if(k==='cls')e.className=a[k];else e.setAttribute(k,a[k])}e.append(...c);return e};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const snap=v=>Math.round(v/GRID)*GRID;
const CATC={Power:'#f5b84b',Passive:'#94a3b8',Output:'#3ddc97',Input:'#8ea2ff',Analog:'#c2457c',Sensor:'#2dd4bf',Comms:'#5a8dee',Memory:'#a78bfa',Motion:'#fb923c',Switching:'#f472b6',Connector:'#cbd5e1'};
const ANN=new Set(['pwr','gnd','label','glabel','junction','nc']);
const PREFIX={pwr:'PWR',gnd:'PWR',label:'LBL',glabel:'GLB',junction:'JN',nc:'NC'};
const kind=pt=>pt.type==='mcu'?'mcu':((LIB[pt.type]||{}).sym||'module');
const isAnn=pt=>ANN.has(kind(pt));
const toast=m=>window.toast?window.toast(m):console.log(m);
const fmtOhm=o=>o>=1e6?+(o/1e6).toFixed(2)+'M':o>=1e3?+(o/1e3).toFixed(2)+'k':o+'Ω';
const fmtF=f=>f>=1e-6?+(f*1e6).toFixed(2)+'µF':f>=1e-9?+(f*1e9).toFixed(2)+'nF':+(f*1e12).toFixed(1)+'pF';
function valueOf(pt){const p=pt.params||{},l=LIB[pt.type]||{label:pt.type};if(p.value)return p.value;if(pt.type==='mcu')return D.device;
 if(l.sym==='pwr'||l.sym==='gnd')return l.power_name||l.label;if(l.sym==='label'||l.sym==='glabel')return p.name||'NET';
 if(['resistor','pot','ldr'].includes(pt.type)&&p.ohms)return fmtOhm(p.ohms);if(pt.type==='ntc')return fmtOhm(p.r25||10000)+' NTC';
 if(p.farads)return fmtF(p.farads);if(pt.type==='crystal')return (p.hz>=1e6?p.hz/1e6+'MHz':p.hz/1e3+'kHz');
 if(p.part)return p.part;if(pt.type==='led')return 'LED '+(p.color||'');return l.label}
const footprintOf=pt=>(pt.params&&pt.params.footprint)||(LIB[pt.type]||{}).footprint||'';

/* ================================================================ symbol geometry (local coords, origin = anchor)
   pin: {x,y} = connection point (wire end), dx,dy = outward direction, len = pin line length (root = end - dir*len) */
function P(num,name,x,y,dx,dy,len){return{num,name,x,y,dx,dy,len}}
const SYMCACHE={};
function symGeo(pt){
 const lib=LIB[pt.type]||{pins:[],sym:'module'},k=lib.sym||'module',pins=lib.pins||[],key=pt.type;
 if(SYMCACHE[key])return SYMCACHE[key];
 if(META&&META.geo&&META.geo.sym&&META.geo.sym[key]){const q=META.geo.sym[key],g={kind:q.kind,pins:q.pins,box:q.box,body:q.body,pin:{}};g.pins.forEach(p=>g.pin[p.num]=p);return SYMCACHE[key]=g}
 const by=n=>pins.find(p=>p.name===n)||{},num=n=>by(n).num;let g={pins:[],kind:k};
 const two=(len,half)=>{g.pins=[P(pins[0].num,pins[0].name,0,-half,0,-1,len),P(pins[1].num,pins[1].name,0,half,0,1,len)]};
 switch(k){
 case 'res':case 'ldr':case 'ntc':two(15,40);g.box=[-12,-40,12,40];break;
 case 'cap':case 'ecap':two(26,30);g.box=[-14,-30,14,30];break;
 case 'led':case 'diode':case 'zener':g.pins=[P(num('A'),'A',0,-40,0,-1,25),P(num('K'),'K',0,40,0,1,25)];g.box=[-14,-40,14,40];break;
 case 'battery':g.pins=[P(num('+'),'+',0,-40,0,-1,30),P(num('-'),'-',0,40,0,1,30)];g.box=[-16,-40,16,40];break;
 case 'buzzer':case 'motor':g.pins=[P(pins[0].num,pins[0].name,0,-50,0,-1,30),P(pins[1].num,pins[1].name,0,50,0,1,30)];g.box=[-20,-50,20,50];break;
 case 'xtal':g.pins=[P(pins[0].num,pins[0].name,-40,0,-1,0,30),P(pins[1].num,pins[1].name,40,0,1,0,30)];g.box=[-40,-14,40,14];break;
 case 'spst':g.pins=[P(pins[0].num,pins[0].name,-40,0,-1,0,20),P(pins[1].num,pins[1].name,40,0,1,0,20)];g.box=[-40,-14,40,8];break;
 case 'spdt':g.pins=[P(num('COM'),'COM',-40,0,-1,0,20),P(num('A'),'A',40,-20,1,0,20),P(num('B'),'B',40,20,1,0,20)];g.box=[-40,-24,40,24];break;
 case 'btn4':g.pins=[P('1','1',-40,-20,-1,0,20),P('2','2',-40,20,-1,0,20),P('3','3',40,-20,1,0,20),P('4','4',40,20,1,0,20)];g.box=[-40,-34,40,24];break;
 case 'pot':g.pins=[P(pins[2].num,pins[2].name,0,-40,0,-1,15),P(pins[0].num,pins[0].name,0,40,0,1,15),P(num('W'),'W',40,0,1,0,26)];g.box=[-12,-40,40,40];break;
 case 'npn':case 'pnp':{const up=k==='npn'?'C':'E',dn=k==='npn'?'E':'C';
  g.pins=[P(num('B'),'B',-40,0,-1,0,26),P(num(up),up,10,-40,0,-1,22),P(num(dn),dn,10,40,0,1,22)];g.box=[-40,-40,30,40];break}
 case 'nmos':case 'pmos':{const up=k==='nmos'?'D':'S',dn=k==='nmos'?'S':'D';
  g.pins=[P(num('G'),'G',-40,0,-1,0,26),P(num(up),up,10,-40,0,-1,22),P(num(dn),dn,10,40,0,1,22)];g.box=[-40,-40,30,40];break}
 case 'relay':g.pins=[P(num('COIL+'),'COIL+',-30,-50,0,-1,20),P(num('COIL-'),'COIL-',-30,50,0,1,20),P(num('COM'),'COM',30,50,0,1,20),
  P(num('NC'),'NC',20,-50,0,-1,20),P(num('NO'),'NO',40,-50,0,-1,20)];g.box=[-55,-50,60,50];break;
 case 'reg':g.pins=[P(num('VIN'),'VIN',-70,0,-1,0,20),P(num('VOUT'),'VOUT',70,0,1,0,20),P(num('GND'),'GND',0,50,0,1,20)];g.box=[-70,-30,70,50];break;
 case 'pwr':g.pins=[P(pins[0].num,pins[0].name,0,0,0,1,0)];g.box=[-14,-30,14,0];break;
 case 'gnd':g.pins=[P(pins[0].num,pins[0].name,0,0,0,-1,0)];g.box=[-14,0,14,30];break;
 case 'label':case 'glabel':g.pins=[P(pins[0].num,'~',0,0,-1,0,0)];g.box=[0,-12,80,4];break;
 case 'junction':g.pins=[P(pins[0].num,'~',0,0,0,0,0)];g.box=[-5,-5,5,5];break;
 case 'nc':g.pins=[P(pins[0].num,'~',0,0,0,0,0)];g.box=[-7,-7,7,7];break;
 case 'dip':{const n=pins.length,L=Math.ceil(n/2),W=Math.max(80,Math.max(...pins.map(p=>p.name.length))*14+20),H=L*PP+20;
  pins.forEach((p,i)=>{const left=i<L,kk=left?i:n-1-i,y=-H/2+20+kk*PP;g.pins.push(P(p.num,p.name,left?-W/2-30:W/2+30,snap(y),left?-1:1,0,30))});
  g.body=[-W/2,-H/2,W/2,H/2];g.box=[-W/2-30,-H/2,W/2+30,H/2];break}
 case 'conn':{const n=pins.length,H=n*PP;pins.forEach((p,i)=>g.pins.push(P(p.num,p.name,-40,-H/2+10+i*PP,-1,0,30)));g.body=[-10,-H/2,10,H/2];g.box=[-40,-H/2,10,H/2];break}
 default:{const n=pins.length,H=n*PP+20,W=Math.max(90,Math.max(...pins.map(p=>p.name.length))*8+60);
  pins.forEach((p,i)=>g.pins.push(P(p.num,p.name,-W/2-30,-H/2+20+i*PP,-1,0,30)));g.body=[-W/2,-H/2,W/2,H/2];g.box=[-W/2-30,-H/2,W/2,H/2];g.kind='module'}}
 g.pin={};g.pins.forEach(p=>g.pin[p.num]=p);return SYMCACHE[key]=g}
function xf(pt,x,y){if(pt.mx)x=-x;if(pt.my)y=-y;const r=((pt.rot||0)%360+360)%360;let X=x,Y=y;
 if(r===90){X=-y;Y=x}else if(r===180){X=-x;Y=-y}else if(r===270){X=y;Y=-x}return{x:pt.x+X,y:pt.y+Y}}
function xd(pt,dx,dy){const o=xf({x:0,y:0,rot:pt.rot,mx:pt.mx,my:pt.my},dx,dy);return{dx:Math.round(o.x),dy:Math.round(o.y)}}
function bbox(pt){if(pt.type==='mcu'){const m=GEO.mcu;return[pt.x-30,pt.y-30,pt.x+m.w+30,pt.y+m.h+30]}
 const g=symGeo(pt),b=g.box.slice(),c0=0;if(g.kind==='label'||g.kind==='glabel')b[2]=labelW(pt,g.kind);const c=[xf(pt,b[0],b[1]),xf(pt,b[2],b[3])];return[Math.min(c[0].x,c[1].x),Math.min(c[0].y,c[1].y),Math.max(c[0].x,c[1].x),Math.max(c[0].y,c[1].y)]}

const labelW=(pt,k)=>{const n=String((pt.params||{}).name||'NET').length;return k==='glabel'?Math.max(40,n*7+18):n*7+14};
/* fields (Ref/Value) like geometry.field_place: on the first box side without pins (right, left, top, bottom), always upright */
function fieldPlace(pt){const bb=bbox(pt),ref=pt.id,val=valueOf(pt),w=Math.max(ref.length*7.5,String(val).length*6.2)+4;
 const has=new Set(symGeo(pt).pins.map(q=>{const d=xd(pt,q.dx,q.dy);return d.dx+','+d.dy})),cx=(bb[0]+bb[2])/2,cy=(bb[1]+bb[3])/2;
 if(!has.has('1,0'))return{ref:{x:bb[2]+6,y:cy-3,a:'start'},val:{x:bb[2]+6,y:cy+11,a:'start'}};
 if(!has.has('-1,0'))return{ref:{x:bb[0]-6,y:cy-3,a:'end'},val:{x:bb[0]-6,y:cy+11,a:'end'}};
 if(!has.has('0,-1'))return{ref:{x:cx,y:bb[1]-18,a:'middle'},val:{x:cx,y:bb[1]-6,a:'middle'}};
 return{ref:{x:cx,y:bb[3]+14,a:'middle'},val:{x:cx,y:bb[3]+26,a:'middle'}}}
/* ---------------- MCU: KiCad-style rectangle, VDD on top, VSS at the bottom, ports grouped left/right (or the real package) */
function mcuGeo(){
 const pins=META.device.pins,n=pins.length,g={pins:{}};
 if(PKG){const quad=META.device.layout==='quad';
  if(quad){const s=Math.ceil(n/4),W=s*PP+40;g.w=W;g.h=W;
   pins.forEach((p,i)=>{const side=Math.floor(i/s),k=i%s;let x,y,dx,dy;
    if(side===0){x=0;y=20+k*PP+PP/2;dx=-1;dy=0}else if(side===1){x=20+k*PP+PP/2;y=W;dx=0;dy=1}
    else if(side===2){x=W;y=W-20-k*PP-PP/2;dx=1;dy=0}else{x=W-20-k*PP-PP/2;y=0;dx=0;dy=-1}
    g.pins[p.num]={p,x,y,dx,dy}})}
  else{const L=Math.ceil(n/2),H=L*PP+30;g.w=220;g.h=H;pins.forEach((p,i)=>{const left=i<L,k=left?i:n-1-i;g.pins[p.num]={p,x:left?0:220,y:20+k*PP,dx:left?-1:1,dy:0}})}
  return g}
 const top=[],bot=[],ports={},other=[];
 pins.forEach(p=>{const pw=p.power;if(pw==='vss')bot.push(p);else if(pw)top.push(p);else{const m=/^R([A-K])(\d+)/.exec(p.label);if(m)(ports[m[1]]=ports[m[1]]||[]).push([+m[2],p]);else other.push(p)}});
 const groups=Object.keys(ports).sort().map(k=>ports[k].sort((a,b)=>a[0]-b[0]).map(x=>x[1]));if(other.length)groups.unshift(other);
 const total=groups.reduce((s,g)=>s+g.length+1,0);let left=[],right=[],cnt=0;
 groups.forEach(gr=>{(cnt<total/2?left:right).push(gr);cnt+=gr.length+1});
 const col=gs=>{const out=[];gs.forEach((gr,i)=>{if(i)out.push(null);out.push(...gr)});return out};
 const L=col(left),R=col(right),rows=Math.max(L.length,R.length),W=Math.max(380,(Math.max(top.length,bot.length)+1)*PP*2+40),H=rows*PP+80;
 g.w=W;g.h=H;
 L.forEach((p,i)=>{if(p)g.pins[p.num]={p,x:0,y:60+i*PP,dx:-1,dy:0}});
 R.forEach((p,i)=>{if(p)g.pins[p.num]={p,x:W,y:60+i*PP,dx:1,dy:0}});
 top.sort((a,b)=>(a.power||'').localeCompare(b.power||'')).forEach((p,i)=>g.pins[p.num]={p,x:W/2-(top.length-1)*PP+i*PP*2,y:0,dx:0,dy:-1});
 bot.forEach((p,i)=>g.pins[p.num]={p,x:W/2-(bot.length-1)*PP+i*PP*2,y:H,dx:0,dy:1});
 return g}
const MCU_PIN=30;
let GEO=null,ENDS={};
function partById(id){return D.parts.find(p=>p.id===id)}
function pinPos(ep){
 const i=ep.indexOf('.'),pid=ep.slice(0,i),num=ep.slice(i+1),pt=partById(pid);if(!pt)return null;
 if(pt.type==='mcu'){const q=GEO.mcu.pins[num];if(!q)return null;return{x:pt.x+q.x+q.dx*MCU_PIN,y:pt.y+q.y+q.dy*MCU_PIN,dx:q.dx,dy:q.dy}}
 const g=symGeo(pt),q=g.pin[num];if(!q)return null;const w=xf(pt,q.x,q.y),d=xd(pt,q.dx,q.dy);return{x:w.x,y:w.y,dx:d.dx,dy:d.dy}}

/* ---------------- wires: orthogonal polylines, user bends kept in w.pts (rubber-banded when parts move) */
function wirePath(w){
 const A=pinPos(w.a),B=pinPos(w.b);if(!A||!B)return null;
 if(w.pts){const raw=[A,...w.pts,B],out=[{x:A.x,y:A.y}];let horiz=A.dy===0;
  for(let i=1;i<raw.length;i++){const p=out[out.length-1],q=raw[i];
   if(p.x!==q.x&&p.y!==q.y){out.push(horiz?{x:q.x,y:p.y}:{x:p.x,y:q.y});horiz=!horiz}else horiz=p.y===q.y;
   out.push({x:q.x,y:q.y})}
  return out.filter((p,i)=>!i||p.x!==out[i-1].x||p.y!==out[i-1].y)}
 /* not routed yet (pts undefined): provisional path until /api/circuit/route answers */
 const s=10,a={x:A.x+A.dx*s,y:A.y+A.dy*s},b={x:B.x+B.dx*s,y:B.y+B.dy*s};let mid;
 const hA=A.dx!==0||(A.dy===0&&Math.abs(B.x-A.x)>=Math.abs(B.y-A.y)),hB=B.dx!==0||(B.dy===0&&Math.abs(B.x-A.x)>=Math.abs(B.y-A.y));
 if(hA&&hB){const xm=snap((a.x+b.x)/2);mid=[{x:xm,y:a.y},{x:xm,y:b.y}]}
 else if(!hA&&!hB){const ym=snap((a.y+b.y)/2);mid=[{x:a.x,y:ym},{x:b.x,y:ym}]}
 else if(hA)mid=[{x:b.x,y:a.y}];else mid=[{x:a.x,y:b.y}];
 const pts=[{x:A.x,y:A.y},a,...mid,b,{x:B.x,y:B.y}];return pts.filter((p,i)=>!i||p.x!==pts[i-1].x||p.y!==pts[i-1].y)}

/* ---------------- connectivity (client-side mirror of circuit.py: wires + internal shorts + power/label joins) */
function computeNets(){
 const par={},f=x=>{while(par[x]!==undefined&&par[x]!==x){par[x]=par[par[x]]??par[x];x=par[x]}return x},u=(a,b)=>{par[a]=par[a]??a;par[b]=par[b]??b;const ra=f(a),rb=f(b);if(ra!==rb)par[ra]=rb};
 D.wires.forEach(w=>u(w.a,w.b));const byName={};
 D.parts.forEach(pt=>{if(pt.type==='mcu')return;const l=LIB[pt.type];if(!l)return;
  (l.pins||[]).forEach(p=>{par[pt.id+'.'+p.num]=par[pt.id+'.'+p.num]??pt.id+'.'+p.num});
  const ports={};(l.pins||[]).forEach(p=>(ports[p.port]=ports[p.port]||[]).push(pt.id+'.'+p.num));Object.values(ports).forEach(a=>a.slice(1).forEach(e=>u(a[0],e)));
  const k=l.sym;let key=null;if(k==='pwr'||k==='gnd')key='P:'+(l.power_name||pt.type);else if(k==='label'||k==='glabel')key='L:'+String((pt.params||{}).name||'').toUpperCase();
  if(key){const e=pt.id+'.1';if(byName[key])u(byName[key],e);else byName[key]=e}});
 const groups={};for(const e in par){const r=f(e);(groups[r]=groups[r]||[]).push(e)}
 NET={};let i=0;const names={};
 Object.values(groups).forEach(mem=>{i++;let nm=null;mem.forEach(e=>{const pt=partById(e.split('.')[0]);if(!pt||pt.type==='mcu')return;const l=LIB[pt.type]||{};
   if(l.sym==='pwr'||l.sym==='gnd')nm=l.power_name;else if(!nm&&(l.sym==='label'||l.sym==='glabel'))nm=(pt.params||{}).name});
  mem.forEach(e=>NET[e]={id:i,name:nm,mem})});}
function netDesc(ep){const n=NET[ep];if(!n)return'not connected';const pid=ep.split('.')[0];
 const others=n.mem.filter(e=>e!==ep&&e.split('.')[0]!==pid&&!['junction','label','glabel'].includes(kind(partById(e.split('.')[0])||{type:''})));
 const lab=e=>{const [p,num]=e.split('.'),pt=partById(p);if(!pt)return e;if(pt.type==='mcu'){const q=META.device.pins.find(x=>String(x.num)===num);return`U1 pin ${num} (${q?q.label:'?'})`}
  const l=LIB[pt.type]||{};if(l.sym==='pwr'||l.sym==='gnd')return l.power_name;const pin=(l.pins||[]).find(x=>x.num===num);return`${p}.${num}${pin&&pin.name!=='~'&&pin.name!==num?' ('+pin.name+')':''}`};
 const set=[...new Set(others.map(lab))];return set.length?(n.name?n.name+': ':'')+set.join(', '):(n.name||'not connected')}

/* ================================================================ render */
function fnOf(num){return (CHK&&CHK.assign&&CHK.assign[num])||''}
function drawBody(g,k,pt,geo){
 const L=(x1,y1,x2,y2,c='csl')=>el('line',{x1,y1,x2,y2,class:c},g),PL=(pts,c='csl')=>el('polyline',{points:pts,class:c},g),PG=(pts,c='csb')=>el('polygon',{points:pts,class:c},g);
 const R=(x,y,w,hh,c='csb')=>el('rect',{x,y,width:w,height:hh,class:c},g),C=(cx,cy,r,c='csb')=>el('circle',{cx,cy,r,class:c},g);
 const arrows=(x,y,out)=>{for(const o of [0,8]){const a=out?[x+o,y,x+o+10,y-10]:[x+o+10,y-10,x+o,y];L(a[0],a[1],a[2],a[3]);PL(`${a[2]-4},${a[3]} ${a[2]},${a[3]} ${a[2]},${a[3]+(out?4:-4)}`)}};
 switch(k){
 case 'res':R(-6,-20,12,40);break;
 case 'ldr':R(-6,-20,12,40);arrows(-28,-8,false);break;
 case 'ntc':R(-6,-20,12,40);PL('-14,18 14,-18 20,-18');break;
 case 'cap':L(-12,-4,12,-4,'csl w');L(-12,4,12,4,'csl w');break;
 case 'ecap':L(-12,-4,12,-4,'csl w');el('path',{d:'M-12,8 Q0,2 12,8',class:'csl w'},g);L(-10,-14,-4,-14);L(-7,-17,-7,-11);break;
 case 'led':case 'diode':case 'zener':PG('-10,-15 10,-15 0,15','csb f');
  if(k==='zener')PL('-14,19 -10,15 10,15 14,11','csl w');else L(-10,15,10,15,'csl w');if(k==='led')arrows(12,0,true);break;
 case 'battery':L(-14,-10,14,-10,'csl w');L(-7,-4,7,-4,'csl w');L(-14,4,14,4,'csl w');L(-7,10,7,10,'csl w');break;
 case 'buzzer':R(-12,-20,10,40);PG('-2,-20 14,-34 14,34 -2,20');break;
 case 'motor':C(0,0,20);{const t=el('text',{x:0,y:6,class:'csym-t'},g);t.textContent='M'}break;
 case 'xtal':L(-10,-12,-10,12,'csl w');L(10,-12,10,12,'csl w');R(-5,-14,10,28);break;
 case 'spst':C(-20,0,2.5);C(20,0,2.5);L(-18,-1,18,-12);break;
 case 'spdt':C(-20,0,2.5);C(20,-20,2.5);C(20,20,2.5);L(-18,0,18,-16);break;
 case 'btn4':L(-20,-20,-20,20);L(20,-20,20,20);C(-20,0,2.2,'csd');C(20,0,2.2,'csd');L(-20,0,-10,0);L(10,0,20,0);C(-8,0,2.2);C(8,0,2.2);
  L(-12,-10,12,-10,'csl w');L(0,-10,0,-24);L(-6,-24,6,-24);break;
 case 'pot':R(-6,-20,12,40);L(12,0,10,0);PG('6,0 12,-4 12,4','csb f');break;
 case 'npn':case 'pnp':C(0,0,20);L(-14,0,-6,0);L(-6,-12,-6,12,'csl w');L(-6,-5,10,-18);L(-6,5,10,18);
  if(k==='npn')PG('10,18 2,16 6,11','csb f');else PG('-6,-5 2,-6 -1,-11','csb f');break;
 case 'nmos':case 'pmos':C(0,0,20);L(-14,0,-10,0);L(-10,-10,-10,10,'csl w');L(-5,-12,-5,-6,'csl w');L(-5,-3,-5,3,'csl w');L(-5,6,-5,12,'csl w');
  L(-5,-9,10,-9);L(10,-9,10,-18);L(-5,9,10,9);L(10,9,10,18);L(-5,0,10,0);L(10,0,10,9);
  if(k==='nmos')PG('-5,0 1,-3 1,3','csb f');else PG('5,0 -1,-3 -1,3','csb f');break;
 case 'relay':R(-55,-30,115,60);R(-40,-12,20,24);L(-40,12,-20,-12);L(-30,-30,-30,-12);L(-30,12,-30,30);
  L(30,30,30,12);L(30,12,22,-16);L(20,-30,20,-14,'csl w');L(40,-30,40,-14);L(36,-14,44,-14);el('line',{x1:-20,y1:0,x2:26,y2:0,class:'csl dash'},g);break;
 case 'reg':R(-50,-30,100,60);break;
 case 'pwr':L(0,0,0,-14);PL('-8,-10 0,-20 8,-10','csl w');break;
 case 'gnd':L(0,0,0,10);L(-12,10,12,10,'csl w');L(-7,15,7,15,'csl w');L(-2,20,2,20,'csl w');break;
 case 'label':L(0,0,6,0,'cwl');break;
 case 'glabel':{const w=Math.max(40,String((pt.params||{}).name||'NET').length*7+18);PG(`0,0 8,-8 ${w},-8 ${w},8 8,8`,'csb glab');break}
 case 'junction':{const n=ENDS[pt.id+'.1']||0;if(n>=3)C(0,0,4,'cjn');else if(n<2)R(-4,-4,8,8,'cdang');break}
 case 'nc':L(-6,-6,6,6,'csl nc');L(-6,6,6,-6,'csl nc');break;
 default:{const b=geo.body;R(b[0],b[1],b[2]-b[0],b[3]-b[1],'csb f2');if(k==='dip'){el('path',{d:`M-8,${b[1]} A8,8 0 0 0 8,${b[1]}`,class:'csl'},g)}}}}
function txt(par,x,y,s,cls,anchor='start'){const t=el('text',{x,y,class:cls,'text-anchor':anchor},par);t.textContent=s;return t}
function render(){
 const svg=$('csvg');svg.replaceChildren();GEO={mcu:mcuGeo()};computeNets();
 const defs=el('defs',{},svg);const pat=el('pattern',{id:'cgrid',width:GRID*5,height:GRID*5,patternUnits:'userSpaceOnUse'},defs);
 for(let i=0;i<5;i++)for(let j=0;j<5;j++)el('circle',{cx:i*GRID+.5,cy:j*GRID+.5,r:i||j?.6:1.1,fill:i||j?'#182033':'#26314a'},pat);
 const root=el('g',{id:'croot',transform:`translate(${VIEW.x},${VIEW.y}) scale(${VIEW.k})`},svg);
 el('rect',{x:-8000,y:-8000,width:16000,height:16000,fill:'url(#cgrid)','data-bg':1},root);
 const bad=new Set((CHK&&CHK.bad_pins)||[]),badParts=new Set();
 ((CHK&&CHK.findings)||[]).forEach(f=>f.severity==='error'&&f.parts.forEach(p=>badParts.add(p)));
 const used=new Set();D.wires.forEach(w=>[w.a,w.b].forEach(e=>{const [pid,po]=e.split('.');if(partById(pid)&&partById(pid).type==='mcu')used.add(po)}));
 const pg=el('g',{},root),wg=el('g',{},root),tg=el('g',{},root),mg=el('g',{},root);
 // wires
 const ends={};ENDS=ends;
 D.wires.forEach((w,i)=>{const pts=wirePath(w);if(!pts)return;const s=pts.map(p=>p.x+','+p.y).join(' '),n=(NET[w.a]||{}).id||0;
  el('polyline',{points:s,class:'cw-hit','data-w':i},wg);el('polyline',{points:s,class:'cw'+(SELW.has(i)?' sel':''),'data-wn':n},wg);
  [w.a,w.b].forEach(e=>{ends[e]=(ends[e]||0)+1})});
 for(const e in ends){const pt=partById(e.split('.')[0]);if(!pt)continue;const k=kind(pt);if(k==='junction')continue;
  if(ends[e]>=2||(ends[e]>=1&&k==='label'&&false)){const p=pinPos(e);if(p)el('circle',{cx:p.x,cy:p.y,r:3.5,class:'cjn'},wg)}}
 // parts
 for(const pt of D.parts){
  if(pt.type==='mcu'){drawMcu(pg,pt,bad,used,ends);continue}
  const geo=symGeo(pt),k=geo.kind,lib=LIB[pt.type]||{label:pt.type};
  const g=el('g',{transform:`translate(${pt.x},${pt.y}) rotate(${pt.rot||0}) scale(${pt.mx?-1:1},${pt.my?-1:1})`,class:'cpart k-'+k+(SEL.has(pt.id)?' sel':'')+(badParts.has(pt.id)?' bad':''),'data-id':pt.id},pg);
  const b=geo.box;el('rect',{x:b[0]-4,y:b[1]-4,width:b[2]-b[0]+8,height:b[3]-b[1]+8,class:'chit'},g);
  drawBody(g,k,pt,geo);
  const showNames=['dip','module','conn','reg','relay','led','diode','zener','ecap','battery','npn','pnp','nmos','pmos','spdt'].includes(k),showNums=!ANN.has(k)&&!['res','cap','ldr','ntc'].includes(k);
  geo.pins.forEach(q=>{if(q.len)el('line',{x1:q.x,y1:q.y,x2:q.x-q.dx*q.len,y2:q.y-q.dy*q.len,class:'cspin'},g);
   if(!ANN.has(k)&&!ends[pt.id+'.'+q.num])el('circle',{cx:q.x,cy:q.y,r:2.5,class:'cpo'},g);
   el('circle',{cx:q.x,cy:q.y,r:ANN.has(k)?5:4,class:'cep','data-ep':pt.id+'.'+q.num},g);
   const W=xf(pt,q.x,q.y),dd=xd(pt,q.dx,q.dy),rx=W.x-dd.dx*q.len,ry=W.y-dd.dy*q.len;
   if(showNums&&q.len){const mx=(W.x+rx)/2,my=(W.y+ry)/2;txt(tg,dd.dx?mx:mx-3,dd.dx?my-3:my,q.num,'cpn',dd.dx?'middle':'end')}
   if(showNames&&q.name&&q.name!=='~'&&q.name!==q.num){if(['led','diode','zener','ecap','battery','npn','pnp','nmos','pmos','spdt'].includes(k)){txt(tg,dd.dx?W.x-dd.dx*q.len/2:W.x+5,dd.dx?W.y+11:W.y-dd.dy*q.len/2+4,q.name,'cpl s','start')}
    else{const ins=6;txt(tg,dd.dx?rx-dd.dx*ins:rx,dd.dx?ry+3.5:ry-dd.dy*ins+(dd.dy<0?8:0),q.name.replace(/^~/,'‾'),'cpl',dd.dx>0?'end':dd.dx<0?'start':'middle')}}});
  // fields (always upright, like KiCad)
  const bb=bbox(pt),val=valueOf(pt);
  if(k==='pwr'){const t=xf(pt,0,-26);txt(tg,t.x,t.y,val,'cpwr','middle')}
  else if(k==='gnd'){const t=xf(pt,0,34);txt(tg,t.x,t.y,val,'cpwr','middle')}
  else if(k==='label'){const r=((pt.rot||0)%360+360)%360,anchor=(r===180)!==!!pt.mx?'end':'start',t=xf(pt,8,-3);txt(tg,t.x,t.y,val,'clab',anchor)}
  else if(k==='glabel'){const t=xf(pt,12,4);txt(tg,t.x,t.y,val,'clab g',(((pt.rot||0)%360+360)%360===180)!==!!pt.mx?'end':'start')}
  else if(!ANN.has(k)){
   const f=fieldPlace(pt);txt(tg,f.ref.x,f.ref.y,pt.id,'cref',f.ref.a);txt(tg,f.val.x,f.val.y,val,'cval',f.val.a);
   if(k==='module'){const c=xf(pt,0,0);txt(tg,c.x+5,c.y+4,lib.label,'cmod','middle')}}}
 // ERC markers
 ((CHK&&CHK.findings)||[]).forEach((f,i)=>{if(f.severity==='info')return;let p=null;
  if(f.pins&&f.pins.length){const u=D.parts.find(x=>x.type==='mcu');p=u&&pinPos(u.id+'.'+f.pins[0])}
  if(!p&&f.parts&&f.parts.length){const pt=partById(f.parts[0]);if(pt){const b=bbox(pt);p={x:b[0],y:b[1]}}}
  if(!p)return;const g=el('g',{transform:`translate(${p.x},${p.y})`,class:'cerc '+f.severity,'data-erc':i},mg);
  el('path',{d:'M0,0 L-6,-16 L6,-16 Z'},g);txt(g,0,-6,'!','cerc-t','middle')});
 // in-progress wire
 if(MODE&&MODE.t==='wire'&&MODE.from){const A=pinPos(MODE.from);if(A){const pts=[{x:A.x,y:A.y},...MODE.pts],l=pts[pts.length-1],c={x:snap(MOUSE.x),y:snap(MOUSE.y)};
  if(l.x!==c.x&&l.y!==c.y)pts.push(MODE.vfirst?{x:l.x,y:c.y}:{x:c.x,y:l.y});pts.push(c);el('polyline',{points:pts.map(p=>p.x+','+p.y).join(' '),class:'cw tmp'},mg)}}
 if(MODE&&MODE.t==='wire'){const c={x:snap(MOUSE.x),y:snap(MOUSE.y)};el('path',{d:`M${c.x-6},${c.y}h12M${c.x},${c.y-6}v12`,class:'ccur'},mg)}
 $('czoom').textContent=Math.round(VIEW.k*100)+'%';status();
}
function drawMcu(pg,pt,bad,used,ends){const m=GEO.mcu;
 const g=el('g',{transform:`translate(${pt.x},${pt.y})`,class:'cpart mcu'+(SEL.has(pt.id)?' sel':''),'data-id':pt.id},pg);
 el('rect',{x:0,y:0,width:m.w,height:m.h,class:'csb f2 mcu'},g);
 txt(g,m.w/2,PKG?m.h/2-6:30,META.device.device,'cmcu-t','middle');
 txt(g,m.w/2,PKG?m.h/2+12:46,`${META.device.count} pins · ${META.device.family}${META.device.verified?' · datasheet-verified':''}`,'cmcu-s','middle');
 txt(g,8,16,pt.id,'cref','start');
 for(const num in m.pins){const q=m.pins[num],p=q.p,pw=!!p.power;const s=String(num);
  const cls='cspin'+(pw?' pw':'')+(bad.has(s)?' bad':used.has(s)?' used':'');
  el('line',{x1:q.x,y1:q.y,x2:q.x+q.dx*MCU_PIN,y2:q.y+q.dy*MCU_PIN,class:cls},g);
  if(!(ends||{})[pt.id+'.'+num])el('circle',{cx:q.x+q.dx*MCU_PIN,cy:q.y+q.dy*MCU_PIN,r:2.5,class:'cpo'},g);
  el('circle',{cx:q.x+q.dx*MCU_PIN,cy:q.y+q.dy*MCU_PIN,r:4,class:'cep','data-ep':pt.id+'.'+num},g);
  const fn=fnOf(s);const lab=el('text',{class:'cpl mp'+(fn?' fn':''),'data-pin':num},g);lab.textContent=(fn&&fn!==p.label&&!fn.startsWith('GPIO')?p.label+' · '+fn:p.label);
  if(q.dx){lab.setAttribute('x',q.x-q.dx*5);lab.setAttribute('y',q.y+3.5);lab.setAttribute('text-anchor',q.dx<0?'start':'end')}
  else{lab.setAttribute('transform',`translate(${q.x+3.5},${q.y-q.dy*5}) rotate(-90)`);lab.setAttribute('text-anchor',q.dy<0?'end':'start')}
  const nt=el('text',{class:'cpn'},g);nt.textContent=num;
  if(q.dx){nt.setAttribute('x',q.x+q.dx*MCU_PIN/2);nt.setAttribute('y',q.y-3);nt.setAttribute('text-anchor','middle')}
  else{nt.setAttribute('transform',`translate(${q.x-3},${q.y+q.dy*MCU_PIN/2}) rotate(-90)`);nt.setAttribute('text-anchor','middle')}}}

/* ================================================================ history */
function snapshot(){UNDO.push(JSON.stringify(D));if(UNDO.length>200)UNDO.shift();REDO=[]}
function undo(){if(!UNDO.length)return toast('Nothing to undo');REDO.push(JSON.stringify(D));D=JSON.parse(UNDO.pop());SEL.clear();SELW.clear();changed(false)}
function redo(){if(!REDO.length)return toast('Nothing to redo');UNDO.push(JSON.stringify(D));D=JSON.parse(REDO.pop());SEL.clear();SELW.clear();changed(false)}

/* ================================================================ editing primitives */
function nextRef(type){const l=LIB[type]||{};const pre=PREFIX[l.sym]||(l.prefix||'X').replace(/[^A-Za-z0-9_]/g,'')||'X';let i=1;while(D.parts.some(p=>p.id===pre+i))i++;return pre+i}
function newPart(type,x,y,params){const lib=LIB[type];const id=nextRef(type);const pt={id,type,params:{...(lib.params||{}),...(params||{})},x:snap(x),y:snap(y)};if(pt.type!=='mcu'){pt.rot=0}D.parts.push(pt);return pt}
function addPart(type,x,y,params){snapshot();const pt=newPart(type,x,y,params);SEL=new Set([pt.id]);SELW.clear();changed();return pt}
function targets(){if(SEL.size)return[...SEL].map(partById).filter(Boolean);if(HOVER&&HOVER.part)return[partById(HOVER.part)].filter(Boolean);return[]}
function rotate(){const ts=targets().filter(p=>p.type!=='mcu');if(!ts.length)return;if(!MODE||MODE.t!=='move')snapshot();ts.forEach(p=>p.rot=(((p.rot||0)+90)%360));unroute(ts);changed()}
function mirror(axis){const ts=targets().filter(p=>p.type!=='mcu');if(!ts.length)return;if(!MODE||MODE.t!=='move')snapshot();ts.forEach(p=>{if(axis==='x')p.my=!p.my;else p.mx=!p.mx});unroute(ts);changed()}
function unroute(ts){const ids=new Set(ts.map(p=>p.id));D.wires.forEach(w=>{if(ids.has(w.a.split('.')[0])||ids.has(w.b.split('.')[0]))delete w.pts})}
function delSel(){const ids=new Set(targets().filter(p=>p.type!=='mcu').map(p=>p.id));if(!ids.size&&!SELW.size&&HOVER&&HOVER.wire!=null)SELW.add(HOVER.wire);
 if(!ids.size&&!SELW.size)return;snapshot();D.wires=D.wires.filter((w,i)=>!SELW.has(i)&&!ids.has(w.a.split('.')[0])&&!ids.has(w.b.split('.')[0]));
 D.parts=D.parts.filter(p=>!ids.has(p.id));SEL.clear();SELW.clear();changed()}
function renameRef(old,nid){const pt=partById(old);pt.id=nid;D.wires.forEach(w=>['a','b'].forEach(k=>{const [p,n]=w[k].split('.');if(p===old)w[k]=nid+'.'+n}))}
function clone(ids,dx,dy){const map={},out=[];ids.forEach(id=>{const p=partById(id);if(!p||p.type==='mcu')return;const c=JSON.parse(JSON.stringify(p));c.id=nextRef(p.type);c.x+=dx;c.y+=dy;D.parts.push(c);map[id]=c.id;out.push(c.id)});
 D.wires.slice().forEach(w=>{const a=w.a.split('.'),b=w.b.split('.');if(map[a[0]]&&map[b[0]])D.wires.push({a:map[a[0]]+'.'+a[1],b:map[b[0]]+'.'+b[1],pts:(w.pts||[]).map(p=>({x:p.x+dx,y:p.y+dy}))})});return out}
function duplicate(){const ids=targets().map(p=>p.id);if(!ids.length)return;snapshot();SEL=new Set(clone(ids,40,40));changed()}
function copyPlace(){const ids=targets().map(p=>p.id);if(!ids.length)return;snapshot();const n=clone(ids,0,0);SEL=new Set(n);startMove(n,false,true)}
function startMove(ids,clearPts,fresh){ids=ids.filter(id=>id);if(!ids.length)return;if(!fresh)snapshot();
 MODE={t:'move',ids,clearPts,fresh,start:{x:snap(MOUSE.x),y:snap(MOUSE.y)},orig:ids.map(id=>{const p=partById(id);return[p,p.x,p.y]}),
  wpts:D.wires.map(w=>w.pts?w.pts.map(p=>({...p})):null)};render()}
function moveTo(w){const dx=snap(w.x)-MODE.start.x,dy=snap(w.y)-MODE.start.y;MODE.orig.forEach(([p,x,y])=>{p.x=x+dx;p.y=y+dy});
 const ids=new Set(MODE.ids);D.wires.forEach((wi,i)=>{const a=ids.has(wi.a.split('.')[0]),b=ids.has(wi.b.split('.')[0]);if(!a&&!b)return;
  if(MODE.clearPts){if(!(a&&b))delete wi.pts}else if(a&&b&&MODE.wpts[i])wi.pts=MODE.wpts[i].map(p=>({x:p.x+dx,y:p.y+dy}))});render()}
function endMode(commit){if(!MODE)return;if(MODE.t==='move'&&!commit){if(MODE.fresh){const ids=new Set(MODE.ids);D.parts=D.parts.filter(p=>!ids.has(p.id));D.wires=D.wires.filter(w=>!ids.has(w.a.split('.')[0])&&!ids.has(w.b.split('.')[0]));UNDO.pop()}
  else{MODE.orig.forEach(([p,x,y])=>{p.x=x;p.y=y});D.wires.forEach((w,i)=>{if(MODE.wpts[i])w.pts=MODE.wpts[i];});UNDO.pop()}}
 const was=MODE;MODE=null;if(was.t==='move'&&commit)changed();else render()}
function place(type,params){snapshot();const pt=newPart(type,MOUSE.x,MOUSE.y,params);SEL=new Set([pt.id]);startMove([pt.id],false,true)}

/* wires: click a pin end to start, click empty grid to bend, click a pin / wire to finish (a junction is added on a wire) */
function nearestOnWire(i,w){const pts=wirePath(D.wires[i]);let best=null;
 for(let s=0;s<pts.length-1;s++){const a=pts[s],b=pts[s+1];let p;if(a.x===b.x)p={x:a.x,y:Math.min(Math.max(snap(w.y),Math.min(a.y,b.y)),Math.max(a.y,b.y))};else p={x:Math.min(Math.max(snap(w.x),Math.min(a.x,b.x)),Math.max(a.x,b.x)),y:a.y};
  const d=Math.hypot(p.x-w.x,p.y-w.y);if(!best||d<best.d)best={d,p,s,pts}}return best}
function splitWire(i,w){const n=nearestOnWire(i,w);if(!n)return null;const wi=D.wires[i],J=newPart('junction',n.p.x,n.p.y);J.x=n.p.x;J.y=n.p.y;
 const inner=n.pts.slice(1,-1),before=inner.slice(0,n.s),after=inner.slice(n.s);
 D.wires.splice(i,1,{a:wi.a,b:J.id+'.1',pts:[...before,{x:n.p.x,y:n.p.y}]},{a:J.id+'.1',b:wi.b,pts:after});return J.id+'.1'}
function wireClick(e,w){const t=e.target;
 if(!MODE.from){snapshot();if(t.dataset.ep)MODE.from=t.dataset.ep;else if(t.dataset.w!==undefined)MODE.from=splitWire(+t.dataset.w,w);
  else{const J=newPart('junction',w.x,w.y);MODE.from=J.id+'.1'}MODE.pts=[];MODE.snap=true;render();return}
 let to=null;if(t.dataset.ep&&t.dataset.ep!==MODE.from)to=t.dataset.ep;else if(t.dataset.w!==undefined)to=splitWire(+t.dataset.w,w);
 if(to){const pts=MODE.pts.slice(),l=pts.length?pts[pts.length-1]:pinPos(MODE.from),T=pinPos(to);
  if(l&&T&&l.x!==T.x&&l.y!==T.y)pts.push(MODE.vfirst?{x:l.x,y:T.y}:{x:T.x,y:l.y});
  /* pin-to-pin click with no bends drawn: let the orthogonal router pick the path around the symbols (KiCad-style result, no wire through a body) */
  const nw={a:MODE.from,b:to};if(MODE.pts.length)nw.pts=pts;D.wires.push(nw);MODE.snap=false;const keep=e.detail,vf=MODE.vfirst;MODE={t:'wire',vfirst:vf};changed();if(keep>=2)MODE=null;return}
 const l=MODE.pts.length?MODE.pts[MODE.pts.length-1]:pinPos(MODE.from),c={x:snap(w.x),y:snap(w.y)};
 if(c.x===l.x&&c.y===l.y)return;if(c.x!==l.x&&c.y!==l.y)MODE.pts.push(MODE.vfirst?{x:l.x,y:c.y}:{x:c.x,y:l.y});MODE.pts.push(c);render()}
/* double-click on empty grid ends the wire there (a dangling end - the ERC flags it until something connects) */
function wireEndHere(){if(!MODE||!MODE.from||!MODE.pts.length){MODE=null;render();return}const pts=MODE.pts.slice(),last=pts.pop();
 const J=newPart('junction',last.x,last.y);D.wires.push({a:MODE.from,b:J.id+'.1',pts});MODE=null;changed()}

/* ================================================================ annotate (renumber references top-left -> bottom-right, like KiCad Annotate) */
function annotate(){snapshot();const groups={};
 D.parts.forEach(p=>{if(p.type==='mcu')return;const l=LIB[p.type]||{};const pre=PREFIX[l.sym]||(l.prefix||'X').replace(/[^A-Za-z0-9_]/g,'')||'X';(groups[pre]=groups[pre]||[]).push(p)});
 let tmp=0;const plan=[];
 for(const pre in groups){let n=pre==='U'?2:1;groups[pre].sort((a,b)=>(a.y-b.y)||(a.x-b.x)).forEach(p=>{while(D.parts.some(x=>x.type==='mcu'&&x.id===pre+n))n++;plan.push([p,pre+n++])})}
 plan.forEach(([p])=>renameRef(p.id,'__T'+(tmp++)));plan.forEach(([p,nid])=>renameRef(p.id,nid));changed();toast(`Annotated ${plan.length} symbols`)}

/* ================================================================ interaction */
function toWorld(ev){const r=$('csvg').getBoundingClientRect();return{x:(ev.clientX-r.left-VIEW.x)/VIEW.k,y:(ev.clientY-r.top-VIEW.y)/VIEW.k}}
function setView(){const r=$('croot');if(r)r.setAttribute('transform',`translate(${VIEW.x},${VIEW.y}) scale(${VIEW.k})`);$('czoom').textContent=Math.round(VIEW.k*100)+'%'}
function zoom(f,cx,cy){const r=$('csvg').getBoundingClientRect();cx=cx??r.width/2;cy=cy??r.height/2;const k=Math.min(4,Math.max(.15,VIEW.k*f));VIEW.x=cx-(cx-VIEW.x)*k/VIEW.k;VIEW.y=cy-(cy-VIEW.y)*k/VIEW.k;VIEW.k=k;setView()}
function fit(){const svg=$('csvg').getBoundingClientRect();let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;GEO={mcu:mcuGeo()};
 for(const p of D.parts){const b=bbox(p);x0=Math.min(x0,b[0]-60);y0=Math.min(y0,b[1]-60);x1=Math.max(x1,b[2]+60);y1=Math.max(y1,b[3]+60)}
 if(x0>x1||!svg.width)return render();const k=Math.min(1.6,Math.min(svg.width/(x1-x0),svg.height/(y1-y0)));VIEW={k,x:(svg.width-(x1-x0)*k)/2-x0*k,y:(svg.height-(y1-y0)*k)/2-y0*k};render()}
function tip(ev,html){const t=$('ctip');if(!html){t.style.display='none';return}t.innerHTML=html;t.style.display='block';const r=$('cwrap').getBoundingClientRect();t.style.left=Math.min(ev.clientX-r.left+14,r.width-290)+'px';t.style.top=(ev.clientY-r.top+14)+'px'}
function mcuPinTip(num){const p=META.device.pins.find(x=>String(x.num)===String(num));if(!p)return'';const fn=fnOf(String(num)),u=D.parts.find(x=>x.type==='mcu');
 return `<b>U1 pin ${p.num} · ${esc(p.label)}</b><div class="mut">${esc(p.names.join(' / '))}</div>`+(p.power?`<div>power pin: ${esc(p.power.toUpperCase())}</div>`:'')+
 (fn?`<div>assigned: <b>${esc(fn)}</b></div>`:'')+(p.tol5v!=null?`<div>${p.tol5v?'5 V tolerant':'not 5 V tolerant'}</div>`:'')+
 `<div>→ ${esc(netDesc(u.id+'.'+num))}</div><div class="mut">${p.functions.length} valid functions · click the name to assign · drag/W from the end to wire</div>`}
function partPinTip(ep){const [pid,num]=ep.split('.'),pt=partById(pid);if(!pt)return'';if(pt.type==='mcu')return mcuPinTip(num);const l=LIB[pt.type]||{},p=(l.pins||[]).find(x=>x.num===num)||{};
 const sh=(l.shorts||[]).find(s=>s.includes(num));
 return `<b>${esc(pid)} pin ${esc(num)}${p.name&&p.name!=='~'&&p.name!==num?' · '+esc(p.name):''}</b><div>${esc(p.desc||'')}</div>`+(sh?`<div class="mut">internally shorted to pin ${sh.filter(x=>x!==num).join(', ')}</div>`:'')+
 `<div style="margin-top:4px">now: <b>${esc(netDesc(ep))}</b></div>`}
function hoverAt(e){const t=e.target;HOVER=null;if(!t||!t.dataset||!$('csvg').contains(t))return tip(e,'');
 if(t.dataset.ep){HOVER={ep:t.dataset.ep,part:t.dataset.ep.split('.')[0]};return tip(e,partPinTip(t.dataset.ep))}
 if(t.dataset.pin){const u=D.parts.find(x=>x.type==='mcu');HOVER={part:u.id,ep:u.id+'.'+t.dataset.pin};return tip(e,mcuPinTip(t.dataset.pin))}
 const ec=t.closest&&t.closest('[data-erc]');if(ec){const f=CHK.findings[+ec.dataset.erc];return tip(e,`<b>${esc(f.code)}</b><div>${esc(f.message)}</div>`)}
 if(t.dataset.w!==undefined){HOVER={wire:+t.dataset.w};const w=D.wires[+t.dataset.w],n=NET[w.a];hlNet(n&&n.id);return tip(e,`<b>wire</b> ${esc(w.a)} ↔ ${esc(w.b)}<div class="mut">net ${esc((n&&n.name)||'#'+(n?n.id:'?'))} · Del removes · click a pin with W to branch</div>`)}
 hlNet(null);const pg=t.closest&&t.closest('.cpart');if(pg){HOVER={part:pg.dataset.id};const p=partById(pg.dataset.id),l=p&&LIB[p.type];
  if(p&&p.type==='mcu')return tip(e,'');return tip(e,l?`<b>${esc(p.id)} · ${esc(l.label)}</b> <span class="mut">${esc(valueOf(p))}</span><div class="mut">${esc(l.guide||l.doc||'')}</div><div class="mut">E edit · M move · R rotate · Del delete</div>`:'')}
 tip(e,'')}
function hlNet(n){document.querySelectorAll('#csvg .cw.hl').forEach(x=>x.classList.remove('hl'));if(n)document.querySelectorAll(`#csvg .cw[data-wn="${n}"]`).forEach(x=>x.classList.add('hl'))}
function bind(){
 const svg=$('csvg');
 svg.addEventListener('wheel',e=>{e.preventDefault();const r=svg.getBoundingClientRect();zoom(e.deltaY<0?1.12:1/1.12,e.clientX-r.left,e.clientY-r.top)},{passive:false});
 svg.addEventListener('contextmenu',e=>e.preventDefault());
 svg.addEventListener('mousedown',e=>{closePop();const t=e.target,w=toWorld(e);MOUSE=w;
  if(e.button===1||e.button===2){DRAG={mode:'pan',sx:e.clientX,sy:e.clientY,vx:VIEW.x,vy:VIEW.y};e.preventDefault();return}
  if(MODE&&MODE.t==='move'){endMode(true);return}
  if(MODE&&MODE.t==='wire'){wireClick(e,w);return}
  if(t.dataset.ep){DRAG={mode:'wire',from:t.dataset.ep,sx:e.clientX,sy:e.clientY};return}
  if(t.dataset.pin){e.stopPropagation();pinPop(e,t.dataset.pin);return}
  const ec=t.closest('[data-erc]');if(ec){const f=CHK.findings[+ec.dataset.erc];SEL=new Set(f.parts);render();return}
  const pg=t.closest('.cpart');
  if(pg&&e.detail>=2){propsPop(e,pg.dataset.id);return}
  if(pg){const id=pg.dataset.id;if(e.shiftKey||e.ctrlKey||e.metaKey){SEL.has(id)?SEL.delete(id):SEL.add(id)}else if(!SEL.has(id)){SEL=new Set([id])}SELW.clear();
   DRAG={mode:'move',start:{x:snap(w.x),y:snap(w.y)},snap:JSON.stringify(D),orig:[...SEL].map(i=>{const p=partById(i);return[p,p.x,p.y]}),moved:false};render();guide();return}
  if(t.dataset.w!==undefined){const i=+t.dataset.w;if(!(e.shiftKey||e.ctrlKey))SEL.clear(),SELW.clear();SELW.add(i);render();return}
  if(!(e.shiftKey||e.ctrlKey)){SEL.clear();SELW.clear()}DRAG={mode:'box',start:w,rect:el('rect',{class:'cbox'},$('croot'))};render();DRAG.rect=el('rect',{class:'cbox'},$('croot'));guide()});
 svg.addEventListener('dblclick',e=>{if(MODE&&MODE.t==='wire'){const t=e.target;if(t.dataset&&(t.dataset.ep||t.dataset.w!==undefined)){MODE=null;render()}else wireEndHere()}});
 window.addEventListener('mousemove',e=>{if(!READY||!D)return;const w=toWorld(e);
  if(!DRAG){MOUSE=w;if(MODE&&MODE.t==='move'){moveTo(w);return}if(MODE&&MODE.t==='wire'){render()}hoverAt(e);return}
  if(DRAG.mode==='pan'){VIEW.x=DRAG.vx+e.clientX-DRAG.sx;VIEW.y=DRAG.vy+e.clientY-DRAG.sy;setView()}
  else if(DRAG.mode==='move'){const dx=snap(w.x)-DRAG.start.x,dy=snap(w.y)-DRAG.start.y;if(dx||dy){DRAG.moved=true}DRAG.orig.forEach(([p,x,y])=>{p.x=x+dx;p.y=y+dy});render()}
  else if(DRAG.mode==='wire'){MOUSE=w;if(Math.hypot(e.clientX-DRAG.sx,e.clientY-DRAG.sy)>4){MODE={t:'wire',from:DRAG.from,pts:[]};DRAG.mode='wiredrag';snapshot()}}
  else if(DRAG.mode==='wiredrag'){MOUSE=w;render()}
  else if(DRAG.mode==='box'){const x=Math.min(w.x,DRAG.start.x),y=Math.min(w.y,DRAG.start.y);DRAG.rect.setAttribute('class','cbox'+(w.x<DRAG.start.x?' cross':''));
   Object.entries({x,y,width:Math.abs(w.x-DRAG.start.x),height:Math.abs(w.y-DRAG.start.y)}).forEach(([k,v])=>DRAG.rect.setAttribute(k,v))}});
 window.addEventListener('mouseup',e=>{if(!DRAG)return;const d=DRAG;DRAG=null;
  if(d.mode==='wire'){MODE={t:'wire',from:null,pts:[]};wireClick({target:{dataset:{ep:d.from}},detail:1},MOUSE);return}
  if(d.mode==='wiredrag'){const t=document.elementsFromPoint(e.clientX,e.clientY).find(x=>x.dataset&&(x.dataset.ep&&x.dataset.ep!==MODE.from||x.dataset.w!==undefined));if(t){wireClick({target:t,detail:1},toWorld(e));MODE=null;render()}return}
  if(d.mode==='move'){if(d.moved){UNDO.push(d.snap);REDO=[];changed()}}
  else if(d.mode==='box'){const x=+d.rect.getAttribute('x'),y=+d.rect.getAttribute('y'),W=+d.rect.getAttribute('width'),H=+d.rect.getAttribute('height'),cross=d.rect.getAttribute('class').includes('cross');d.rect.remove();
   if(W<3&&H<3){render();guide();return}
   const inside=b=>cross?!(b[2]<x||b[0]>x+W||b[3]<y||b[1]>y+H):(b[0]>=x&&b[1]>=y&&b[2]<=x+W&&b[3]<=y+H);
   D.parts.forEach(p=>{if(inside(bbox(p)))SEL.add(p.id)});
   D.wires.forEach((wi,i)=>{const pts=wirePath(wi);if(!pts)return;const xs=pts.map(p=>p.x),ys=pts.map(p=>p.y);if(inside([Math.min(...xs),Math.min(...ys),Math.max(...xs),Math.max(...ys)]))SELW.add(i)});render();guide()}});
 window.addEventListener('keydown',e=>{if(!$('p-cir')||$('p-cir').classList.contains('hide')||!D)return;if(/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName))return;
  const k=e.key.toLowerCase(),ctl=e.ctrlKey||e.metaKey;let done=true;
  if(ctl&&k==='z'&&!e.shiftKey)undo();else if(ctl&&(k==='y'||(k==='z'&&e.shiftKey)))redo();else if(ctl&&k==='d')duplicate();
  else if(ctl&&k==='a'){SEL=new Set(D.parts.map(p=>p.id));render()}
  else if(ctl)done=false;
  else if(k==='escape'){if(MODE)endMode(false);else{SEL.clear();SELW.clear();closePop();render()}}
  else if(k==='delete'||k==='backspace')delSel();
  else if(k==='/'&&MODE&&MODE.t==='wire'){MODE.vfirst=!MODE.vfirst;render()}
  else if(k==='w'){if(HOVER&&HOVER.ep){snapshot();MODE={t:'wire',from:HOVER.ep,pts:[]}}else MODE={t:'wire',from:null,pts:[]};render()}
  else if(k==='l'){const n=prompt('Net label name (wires touching labels with the same name are connected):',LASTLABEL);if(n){LASTLABEL=n.toUpperCase().replace(/[^A-Z0-9_+\-]/g,'');place('label',{name:LASTLABEL})}}
  else if(k==='p')powerPop(e);else if(k==='a')addPop(e);
  else if(k==='m'||k==='g'){if(MODE)return;startMove(targets().map(p=>p.id),k==='m',false)}
  else if(k==='r')rotate();else if(k==='x')mirror('x');else if(k==='y')mirror('y');
  else if(k==='e'){const t=targets()[0];if(t)propsPop(null,t.id)}else if(k==='c')copyPlace();else if(k==='home')fit();else if(k==='j'){place('junction')}else if(k==='q'){place('noconnect')}
  else done=false;if(done)e.preventDefault()});
 const wrap=$('cwrap');
 wrap.addEventListener('dragover',e=>{e.preventDefault();e.dataTransfer.dropEffect='copy'});
 wrap.addEventListener('drop',e=>{e.preventDefault();const type=e.dataTransfer.getData('text/x-cl-part');if(!type||!LIB[type])return;const w=toWorld(e);addPart(type,w.x,w.y)});
}
let LASTLABEL='NET1';
function status(){const s=$('cstat');if(!s)return;s.textContent=MODE?(MODE.t==='wire'?(MODE.from?`Wire from ${MODE.from}: click grid to bend, click a pin or wire to finish (junction added) · Esc cancels`:'Wire: click a pin end (or a wire) to start'):MODE.t==='move'?`${MODE.clearPts?'Move':'Drag'} ${MODE.ids.join(', ')}: click to place · R rotate · X/Y mirror · Esc cancels`:''):
 'W wire · L label · P power · A add · M move · G drag · R rotate · X/Y mirror · E edit · C copy · Del · Ctrl+D dup · Ctrl+Z/Y · right-drag pans'}

/* ================================================================ popups / dialogs */
function closePop(){$('cpop').style.display='none'}
document.addEventListener('mousedown',e=>{const p=$('cpop');if(p&&p.style.display==='block'&&!p.contains(e.target)&&!$('csvg').contains(e.target))closePop()});
function popAt(ev,node){const p=$('cpop'),r=$('cwrap').getBoundingClientRect();p.replaceChildren(node);p.style.display='block';
 const cx=ev&&ev.clientX!=null?ev.clientX-r.left:MOUSE.x*VIEW.k+VIEW.x,cy=ev&&ev.clientY!=null?ev.clientY-r.top:MOUSE.y*VIEW.k+VIEW.y;
 p.style.left=Math.max(4,Math.min(cx+8,r.width-290))+'px';p.style.top=Math.max(4,Math.min(cy+8,r.height-340))+'px';const i=p.querySelector('input');if(i)setTimeout(()=>i.focus(),0)}
function pinPop(ev,num){const p=META.device.pins.find(x=>String(x.num)===String(num));if(!p)return;const cur=(D.assign||{})[num]||'';
 const list=h('div',{cls:'cfl'});
 const pick=fn=>{snapshot();D.assign=D.assign||{};if(fn)D.assign[num]=fn;else delete D.assign[num];closePop();changed()};
 list.append(h('button',{cls:'s'+(cur?'':' on'),on:{click:()=>pick('')}},'auto (from the wired part)'));
 p.functions.forEach(fn=>list.append(h('button',{cls:'s'+(cur===fn?' on':''),on:{click:()=>pick(fn)}},fn)));
 popAt(ev,h('div',{},h('div',{cls:'cpop-h'},`U1 pin ${p.num} · ${p.label}`),h('div',{cls:'mut',style:'margin-bottom:6px'},p.functions.length?'Only functions this pin can actually do are listed.':'Power / special pin: nothing assignable.'),list))}
function propsPop(ev,id){const pt=partById(id);if(!pt)return;if(pt.type==='mcu'){SEL=new Set([id]);render();guide();return}const lib=LIB[pt.type]||{};const box=h('div',{cls:'cprops'});
 box.append(h('div',{cls:'cpop-h'},`Symbol properties · ${lib.label}`),h('div',{cls:'mut',style:'margin-bottom:6px'},lib.guide||lib.doc||''));
 const ann=isAnn(pt),idIn=h('input',{value:pt.id}),valIn=h('input',{value:(pt.params||{}).value||'',placeholder:valueOf(pt)}),fpIn=h('input',{value:(pt.params||{}).footprint||'',placeholder:lib.footprint||''});
 box.append(h('label',{},'Reference'),idIn);if(!ann)box.append(h('label',{},'Value'),valIn,h('label',{},'Footprint'),fpIn);
 const ins={};Object.entries({...(lib.params||{}),...(pt.params||{})}).forEach(([k,v])=>{if(['value','footprint'].includes(k))return;let i;if(typeof v==='boolean'){i=h('input',{type:'checkbox'});i.checked=v}else i=h('input',{value:Array.isArray(v)?JSON.stringify(v):v});ins[k]=i;box.append(h('label',{},k),i)});
 const rot=h('div',{cls:'mut f',style:'margin-top:6px'},`rotation ${pt.rot||0}° · mirror ${pt.mx?'Y ':''}${pt.my?'X':''}${!pt.mx&&!pt.my?'none':''} (R, X, Y on the canvas)`);box.append(rot);
 const apply=()=>{snapshot();for(const k in ins){const i=ins[k],v=i.type==='checkbox'?i.checked:i.value;let val=v;if(typeof v==='string'){if(v!==''&&!isNaN(+v)&&!/^0x/i.test(v))val=+v;else if(/^\[.*\]$/.test(v)){try{val=JSON.parse(v)}catch(e){}}}pt.params[k]=val}
  if(!ann){if(valIn.value)pt.params.value=valIn.value;else delete pt.params.value;if(fpIn.value)pt.params.footprint=fpIn.value;else delete pt.params.footprint}
  const nid=idIn.value.replace(/[^A-Za-z0-9_]/g,'');if(nid&&nid!==id&&!D.parts.some(p=>p.id===nid))renameRef(id,nid);closePop();changed()};
 box.addEventListener('keydown',e=>{if(e.key==='Enter')apply();if(e.key==='Escape')closePop()});
 box.append(h('div',{cls:'row',style:'margin-top:8px'},h('button',{cls:'p',on:{click:apply}},'OK'),h('button',{cls:'s',on:{click:closePop}},'Cancel')));popAt(ev,box)}
function powerPop(ev){const box=h('div',{},h('div',{cls:'cpop-h'},'Place power symbol (P)'),h('div',{cls:'mut',style:'margin-bottom:6px'},'Every symbol with the same name is the same net - place one next to each pin instead of long wires.'));
 const list=h('div',{cls:'cfl'});[['gnd','GND'],['vdd33','+3V3'],['v5','+5V'],['vdd','VDD']].forEach(([t,n])=>list.append(h('button',{cls:'s',on:{click:()=>{closePop();place(t)}}},n)));
 list.append(h('button',{cls:'s',on:{click:()=>{closePop();place('noconnect')}}},'No-connect ✕'),h('button',{cls:'s',on:{click:()=>{closePop();place('glabel',{name:LASTLABEL})}}},'Global label'));
 box.append(list);popAt(ev&&ev.clientX!=null?ev:null,box)}
function addPop(ev){const q=h('input',{placeholder:'Search symbols… (resistor, button, lcd, relay)'}),list=h('div',{cls:'cadd'});
 const fill=()=>{const s=q.value.toLowerCase();list.replaceChildren(...META.library.filter(l=>!s||(l.label+' '+l.type+' '+l.cat+' '+(l.footprint||'')).toLowerCase().includes(s)).slice(0,40).map(l=>
  h('div',{cls:'cadd-i',on:{click:()=>{closePop();place(l.type)}}},h('b',{},l.label),h('span',{cls:'mut'},` ${l.cat} · ${(l.pins||[]).length} pins · ${l.footprint||''}`))))};
 q.addEventListener('input',fill);q.addEventListener('keydown',e=>{if(e.key==='Enter'&&list.firstChild)list.firstChild.click();if(e.key==='Escape')closePop()});fill();
 popAt(ev&&ev.clientX!=null?ev:null,h('div',{},h('div',{cls:'cpop-h'},'Add symbol (A)'),q,list))}

/* ================================================================ connection guide / auto-wire / MCU checklist */
function guide(){const box=$('cguide');if(!box||!D)return;const ids=[...SEL];const pt=ids.length===1?partById(ids[0]):null;
 if(!pt){box.replaceChildren(h('div',{cls:'f mut'},'Select a symbol to see what each of its pins must be connected to, then press Auto-wire to place the supporting parts (resistor, transistor, diode, pull-ups, decoupling) and wire it to free MCU pins.'),
  h('div',{cls:'f',style:'margin-top:8px'},h('b',{},'MCU power checklist')),checklistView());return}
 if(pt.type==='mcu'){box.replaceChildren(h('div',{cls:'f'},h('b',{},`${pt.id} · ${D.device}`),h('span',{cls:'mut'},` · ${footprintOf(pt)||''}`)),
  h('div',{cls:'f mut'},'Every VDD/AVDD needs +3V3 and a 100 nF cap, every VSS/AVSS goes to GND, VCAP needs 10 µF to GND, MCLR needs 10 k to +3V3 plus the ICSP header.'),checklistView(),
  h('div',{cls:'row',style:'margin-top:8px;gap:6px'},h('button',{cls:'p',on:{click:()=>autowire(pt.id)}},'Auto-wire MCU power + ICSP')),notesView());return}
 const l=LIB[pt.type]||{};const rows=(l.pins||[]).map(p=>{const ep=pt.id+'.'+p.num,now=netDesc(ep),ok=now!=='not connected';
  return h('tr',{cls:ok?'':'nc',on:{mouseenter:()=>{const q=pinPos(ep);if(q){const c=el('circle',{cx:q.x,cy:q.y,r:9,class:'cpinhl'},$('croot'));setTimeout(()=>c.remove(),900)}}}},
   h('td',{},p.num),h('td',{},p.name),h('td',{},p.desc||''),h('td',{cls:ok?'ok':'mut'},ok?now:'—'))});
 box.replaceChildren(h('div',{cls:'f'},h('b',{},`${pt.id} · ${l.label}`),h('span',{cls:'mut'},` · ${valueOf(pt)} · ${footprintOf(pt)}`)),
  h('div',{cls:'f',style:'margin:6px 0'},l.guide||l.doc||''),
  ...(l.shorts&&l.shorts.length?[h('div',{cls:'f mut'},'Internally connected: '+l.shorts.map(s=>s.join('-')).join(', '))]:[]),
  h('table',{cls:'ctab cgt'},h('thead',{},h('tr',{},h('th',{},'Pin'),h('th',{},'Name'),h('th',{},'Connect to'),h('th',{},'Now'))),h('tbody',{},...rows)),
  ...(isAnn(pt)?[]:[h('div',{cls:'row',style:'margin-top:8px;gap:6px'},h('button',{cls:'p',on:{click:()=>autowire(pt.id)}},'Auto-wire'),h('button',{cls:'s',on:{click:()=>propsPop(null,pt.id)}},'Properties (E)'))]),notesView())}
function notesView(){return h('div',{},...NOTES.map(n=>h('div',{cls:'f'},h('span',{cls:'pill info'},'auto'),' '+n)))}
function checklistView(){if(!CL)return h('div',{cls:'mut f'},'…');return h('div',{cls:'ccl'},...CL.map(c=>h('div',{cls:'f '+(c.done?'ok':'todo')},(c.done?'✓ ':'✗ ')+c.text+(c.pins&&c.pins.length?` - pins ${c.pins.join(', ')}`:''))))}
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());return r}
async function autowire(pid){try{const r=await (await post('/api/circuit/autowire',{circuit:D,part:pid})).json();
 if(!r.ok){toast(r.reason||'auto-wire failed');return}snapshot();D=r.circuit;NOTES=r.notes||[];const added=r.added||[];SEL=new Set([pid,...added]);changed();toast(`Auto-wired ${pid}: +${added.length} parts`)}catch(e){toast('auto-wire: '+e.message)}}
async function checklist(){try{CL=await (await post('/api/circuit/checklist',{circuit:D})).json()}catch(e){CL=null}guide()}
async function netlist(fmt){const r=await post('/api/circuit/netlist',{circuit:D,fmt});const nm=(D.name||'circuit').replace(/[^A-Za-z0-9_]+/g,'_');
 if(fmt==='json')dl(nm+'.netlist.json',new Blob([JSON.stringify(await r.json(),null,1)],{type:'application/json'}));else dl(nm+'.net',await r.blob())}

/* ================================================================ ERC / BOM */
let RT=null;
function routeSoon(){clearTimeout(RT);if(!D.wires.some(w=>w.pts===undefined))return;RT=setTimeout(async()=>{const snap=JSON.stringify(D);
 try{const r=await (await post('/api/circuit/route',{circuit:D,part:''})).json();if(JSON.stringify(D)!==snap||MODE)return routeSoon();D=r.circuit;save();render()}catch(e){console.warn(e)}},150)}
async function reroute(mode){snapshot();try{const r=await (await post('/api/circuit/route',{circuit:D,part:mode})).json();D=r.circuit;changed();
 const a=r.audit;toast(a.problems.length?`${a.problems.length} drawing problems: ${a.problems[0]}`:`Wires routed - ${D.wires.length} wires, none through a symbol`)}catch(e){toast('route failed: '+e.message)}}
function changed(){save();render();routeSoon();clearTimeout(T);T=setTimeout(check,250);PRJ=null;$('cprj')&&$('cprj').classList.add('stale');guide()}
function save(){try{localStorage.setItem('cl_circuit',JSON.stringify(D))}catch(e){}}
async function check(){
 try{CHK=await (await post('/api/circuit/check',{circuit:D})).json()}catch(e){return}
 render();checklist();const f=CHK.findings||[],ne=f.filter(x=>x.severity==='error').length,nw=f.filter(x=>x.severity==='warning').length;
 const b=$('cerc');b.className='pill '+(ne?'bad':nw?'warn':'ok');b.textContent=ne?`${ne} ERROR${ne>1?'S':''}`:nw?`${nw} WARNING${nw>1?'S':''}`:'ERC CLEAN';
 $('cfind').replaceChildren(...f.map(x=>{const d=h('div',{cls:'f cf-'+x.severity},h('span',{cls:'pill '+(x.severity==='error'?'bad':x.severity==='warning'?'warn':'info')},x.severity),' ',x.message+(x.page?` (p.${x.page})`:''));
  d.addEventListener('mouseenter',()=>{SEL=new Set(x.parts);render()});return d}));
 $('cbom').replaceChildren(...bom().map(r=>h('tr',{},h('td',{},r.refs.join(', ')),h('td',{},r.qty),h('td',{},r.value),h('td',{cls:'mut'},r.fp))));
}
function bom(){const m={};for(const p of D.parts){if(isAnn(p))continue;const lib=LIB[p.type]||{label:p.type};const value=valueOf(p),fp=p.type==='mcu'?'':footprintOf(p);
 const key=(p.type==='mcu'?'mcu':lib.label)+'|'+value+'|'+fp;(m[key]=m[key]||{desc:lib.label||p.type,value,fp,refs:[],qty:0}).refs.push(p.id);m[key].qty++}
 return Object.values(m).sort((a,b)=>a.refs[0].localeCompare(b.refs[0],undefined,{numeric:true}))}
function dl(name,blob){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),4000)}
function bomCsv(){const q=s=>'"'+String(s).replace(/"/g,'""')+'"';dl((D.name||'circuit')+'_bom.csv',new Blob(['References,Qty,Value,Footprint,Description\n'+bom().map(r=>[q(r.refs.join(' ')),r.qty,q(r.value),q(r.fp),q(r.desc)].join(',')).join('\n')+'\n'],{type:'text/csv'}))}
function bomMd(){dl((D.name||'circuit')+'_bom.md',new Blob([`# BOM - ${D.name||'circuit'} (${D.device})\n\n| References | Qty | Value | Footprint | Description |\n|---|---|---|---|---|\n`+bom().map(r=>`| ${r.refs.join(', ')} | ${r.qty} | ${r.value} | ${r.fp} | ${r.desc} |`).join('\n')+'\n'],{type:'text/markdown'}))}

/* ================================================================ MPLAB X project */
async function generate(){
 const b=$('cpbadge');b.className='pill info';b.textContent='…';$('cprj').classList.remove('stale');
 const r=await fetch('/api/circuit/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({circuit:D,compile:$('ccomp').checked})});PRJ=await r.json();
 if(!PRJ.files){b.className='pill bad';b.textContent='FIX ERC';$('ctree').replaceChildren(h('div',{cls:'mut f'},'The circuit has ERC errors; fix them first.'));return}
 const comp=PRJ.compile;b.className='pill '+(PRJ.verified&&(!comp||comp.status!=='error')?'ok':'bad');
 b.textContent=PRJ.verified?(comp&&comp.status==='ok'?'VERIFIED · .hex built':'VERIFIED'):'CHECK FAILED';
 $('cpinfo').replaceChildren(h('div',{cls:'f'},`${PRJ.device} · ${PRJ.compiler.toUpperCase()} · backend ${PRJ.backend} · ${PRJ.clock}`),
  h('div',{cls:'f mut'},`checker: ${PRJ.check.scope||''}`),
  h('div',{cls:'f mut'},`host syntax check (gcc, stand-in <xc.h> from EDC): ${PRJ.syntax.status}${PRJ.syntax.files?` · ${PRJ.syntax.files} files`:''}`),
  ...(comp?[h('div',{cls:'f'},`XC compile: ${comp.status}${comp.reason?' - '+comp.reason:''}`)]:[]),
  ...PRJ.notes.map(n=>h('div',{cls:'f'},h('span',{cls:'pill warn'},'note'),' '+n)),
  ...PRJ.check.findings.filter(x=>x.severity!=='info').map(x=>h('div',{cls:'f'},h('span',{cls:'pill '+(x.severity==='error'?'bad':'warn')},x.severity),' '+x.message)));
 renderTree();showFile('app/main.c')}
function renderTree(){const dirs={};PRJ.tree.forEach(f=>{const i=f.path.lastIndexOf('/');const d=i<0?'':f.path.slice(0,i);(dirs[d]=dirs[d]||[]).push(f)});
 const out=[h('div',{cls:'ct-d'},'▾ '+PRJ.name+'.X/')];
 Object.keys(dirs).sort((a,b)=>(a===''?'~':a).localeCompare(b===''?'~':b)).forEach(d=>{if(d)out.push(h('div',{cls:'ct-d',style:'padding-left:14px'},'▾ '+d+'/'));
  dirs[d].forEach(f=>out.push(h('div',{cls:'ct-f'+(f.path===FILE?' on':''),style:`padding-left:${d?30:14}px`,on:{click:()=>showFile(f.path)}},f.path.split('/').pop(),h('span',{cls:'mut'},` ${f.lines} lines`))))});
 $('ctree').replaceChildren(...out)}
function showFile(p){FILE=p;if(!PRJ||!PRJ.files[p])return;$('cfname').textContent=p;$('cfile').textContent=PRJ.files[p];renderTree()}
async function zip(){const r=await fetch('/api/circuit/project.zip',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({circuit:D})});
 if(!r.ok){window.toast&&toast('Fix the ERC errors first');return}dl((D.name||'chipllama_circuit').replace(/[^A-Za-z0-9_]+/g,'_')+'.X.zip',await r.blob())}
function hex(){if(PRJ&&PRJ.hex)dl((D.name||'app')+'.hex',new Blob([PRJ.hex],{type:'text/plain'}))}
async function toBlocks(){const r=await (await fetch('/api/circuit/to_blocks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({circuit:D})})).json();
 if(!r.ok){window.toast&&toast(r.reason);return}tab('blk');await Blocks.init();Blocks.loadWs(r.workspace)}

/* ================================================================ export */
const EXPORT_CSS=`.csl{stroke:#e06c75;stroke-width:1.6;fill:none}.csl.w{stroke-width:2.4}.csb{stroke:#e06c75;stroke-width:1.6;fill:none}.csb.f,.csd{fill:#e06c75}.csb.f2{fill:#1b1a12}.csb.mcu{fill:#15140f}
 .cspin{stroke:#c47b7b;stroke-width:1.4}.cspin.pw{stroke:#f5b84b}.cspin.used{stroke:#3ddc97}.cspin.bad{stroke:#ff4d5e}.cep,.chit,.cw-hit{fill:none;stroke:none}.cjn{fill:#3ddc97}
 .cw{fill:none;stroke:#3ddc97;stroke-width:1.8}text{font-family:Inter,Segoe UI,sans-serif}.cpl{font-size:9px;fill:#5fd7d7}.cpl.fn{fill:#f5b84b}.cpn{font-size:7.5px;fill:#a46a6a}
 .cref{font-size:11px;font-weight:700;fill:#5fd7d7}.cval{font-size:10px;fill:#9fb7b7}.cpwr{font-size:10px;font-weight:700;fill:#e06c75}.clab{font-size:11px;font-weight:600;fill:#e8e8e8}.cmod{font-size:10px;fill:#9aa1b2}
 .cmcu-t{font-size:15px;font-weight:700;fill:#ececf1}.cmcu-s{font-size:10px;fill:#7d8394}.csym-t{font-size:16px;font-weight:700;fill:#e06c75;text-anchor:middle}.cerc path{fill:#ff4d5e}.cerc.warning path{fill:#f5b84b}.cerc-t{font-size:10px;font-weight:800;fill:#000}`;
function svgText(){GEO={mcu:mcuGeo()};const keep=VIEW,ks=SEL,kw=SELW;SEL=new Set();SELW=new Set();fit();const svg=$('csvg').cloneNode(true);VIEW=keep;SEL=ks;SELW=kw;render();
 const r=$('csvg').getBoundingClientRect();svg.setAttribute('xmlns',NS);svg.setAttribute('width',r.width);svg.setAttribute('height',r.height);
 const st=document.createElementNS(NS,'style');st.textContent=EXPORT_CSS;svg.insertBefore(st,svg.firstChild);
 const bg=document.createElementNS(NS,'rect');bg.setAttribute('width','100%');bg.setAttribute('height','100%');bg.setAttribute('fill','#0b0e15');svg.insertBefore(bg,st.nextSibling);
 return {s:new XMLSerializer().serializeToString(svg),w:r.width,h:r.height}}
function exportSvg(){dl((D.name||'circuit')+'.svg',new Blob([svgText().s],{type:'image/svg+xml'}))}
function exportPng(){const {s,w,h:H}=svgText();const img=new Image();img.onload=()=>{const c=document.createElement('canvas');c.width=w*2;c.height=H*2;const x=c.getContext('2d');x.scale(2,2);x.drawImage(img,0,0);c.toBlob(b=>dl((D.name||'circuit')+'.png',b))};img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(s)}
function saveFile(){dl((D.name||'circuit')+'.circuit.json',new Blob([JSON.stringify(D,null,1)],{type:'application/json'}))}
function loadFile(f){const r=new FileReader();r.onload=async()=>{try{const d=JSON.parse(r.result);if(!d.parts||!d.wires)throw Error('missing parts/wires');snapshot();await setCircuit(d)}catch(e){alert('Not a ChipLlama circuit file: '+e.message)}};r.readAsText(f)}

/* ================================================================ device / palette / init */
async function loadMeta(dev){const r=await fetch('/api/circuit/meta?device='+encodeURIComponent(dev));if(!r.ok)throw Error('no pinout for '+dev);META=await r.json();LIB={};for(const k in SYMCACHE)delete SYMCACHE[k];META.library.forEach(l=>LIB[l.type]=l)}
async function setCircuit(d){
 if(+(d.schema||1)<2){try{d=await (await post('/api/circuit/migrate',{circuit:d})).json()}catch(e){}}
 D=d;D.schema=2;D.assign=D.assign||{};D.wires=D.wires||[];if(!D.parts.some(p=>p.type==='mcu'))D.parts.unshift({id:'U1',type:'mcu',x:420,y:60});
 await loadMeta(D.device||'PIC24FJ256GB110');$('cdev').value=META.device.device;$('cname').value=D.name||'';CHK=null;PRJ=null;CL=null;NOTES=[];SEL.clear();SELW.clear();MODE=null;
 {const b=$('cpbadge');b.className='pill info';b.textContent='not generated';$('cprj').classList.remove('stale');$('cpinfo').replaceChildren();$('ctree').replaceChildren(h('div',{cls:'mut f'},'Generate to see config/ hal/ drivers/ board/ app/.'));$('cfname').textContent='';$('cfile').textContent=''}
 fit();save();check();guide()}
async function setDevice(dev){if(D.wires.length&&!confirm(`Switch to ${dev}? Wires to MCU pins are kept by pin number; check the ERC afterwards.`)){$('cdev').value=D.device;return}snapshot();D.device=dev;D.assign={};await setCircuit(D)}
function palette(q=''){const cats={};META.library.filter(l=>!q||(l.label+' '+l.type+' '+l.cat).toLowerCase().includes(q.toLowerCase())).forEach(l=>(cats[l.cat]=cats[l.cat]||[]).push(l));
 $('cpal').replaceChildren(...Object.keys(cats).sort().map(c=>h('div',{},h('div',{cls:'cpal-c',style:`color:${CATC[c]||'#ccc'}`},c),...cats[c].map(l=>{const b=h('div',{cls:'cpal-i',draggable:'true',title:(l.guide||l.doc||'')+` (${(l.pins||[]).length} pins)`},h('span',{cls:'dot',style:`background:${CATC[c]||'#7c8cff'}`}),l.label,h('span',{cls:'cpal-n'},(l.pins||[]).length));
  b.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/x-cl-part',l.type);e.dataTransfer.effectAllowed='copy'});
  b.addEventListener('dblclick',()=>{const r=$('csvg').getBoundingClientRect();addPart(l.type,(r.width/2-VIEW.x)/VIEW.k,(r.height/2-VIEW.y)/VIEW.k)});return b}))))}
async function loadEx(n){if(!n)return;const d=await (await fetch('/api/circuit/example/'+n)).json();if(D)snapshot();await setCircuit(d);palette($('cq').value)}
async function init(){if(READY){render();return}READY=true;
 const ex=await (await fetch('/api/circuit/examples')).json();$('cex').replaceChildren(new Option('Examples…',''),...ex.map(e=>new Option(e.title,e.name)));
 let d=null;try{d=JSON.parse(localStorage.getItem('cl_circuit')||'null')}catch(e){}
 bind();
 if(d&&d.parts){try{await setCircuit(d)}catch(e){await loadEx('led_button')}}else await loadEx('led_button');
 $('cdev').replaceChildren(...META.bundled.map(b=>new Option(b,b)));$('cdev').value=META.device.device;palette()}
window.Circuit={init,fit,zoom,generate,zip,hex,toBlocks,exportSvg,exportPng,saveFile,loadFile,bomCsv,bomMd,loadEx,setDevice,palette,
 undo,redo,annotate,netlist,autowire,reroute,place,addPart,rotate,mirror,duplicate,
 tool:t=>{if(t==='wire'){MODE={t:'wire',from:null,pts:[]};render()}else if(t==='label'){const n=prompt('Net label name:',LASTLABEL);if(n){LASTLABEL=n.toUpperCase();place('label',{name:LASTLABEL})}}else if(t==='power')powerPop(null);else if(t==='add')addPop(null)},
 pkg:()=>{PKG=!PKG;$('cpkg')&&$('cpkg').classList.toggle('on',PKG);fit()},
 select:ids=>{SEL=new Set(ids);render();guide()},
 rename:v=>{D.name=v.replace(/[^A-Za-z0-9_ -]/g,'');save()},resize:()=>D&&render(),
 clear:()=>{if(confirm('Remove every part except the MCU?')){snapshot();D.parts=D.parts.filter(p=>p.type==='mcu');D.wires=[];D.assign={};changed()}},
 state:()=>D,audit:async()=>(await (await post('/api/circuit/route',{circuit:D,part:'check'})).json()).audit,nets:()=>{computeNets();return NET},pinPos:ep=>pinPos(ep),view:()=>VIEW,checkNow:check};
})();
