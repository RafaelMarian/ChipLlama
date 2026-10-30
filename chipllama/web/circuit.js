/* ChipLlama - Circuit tab: Simulink-style SVG schematic editor (no third-party library, works offline).
   Model: {name, device, parts:[{id,type,params,x,y}], wires:[{a:"PID.PORT", b:"PID.PORT"}], assign:{pin:fn}}.
   ERC, codegen and project generation run server-side (/api/circuit/*). */
(function(){
const NS='http://www.w3.org/2000/svg',P=18,STUB=22;
let D=null,META=null,LIB={},CHK=null,PRJ=null,SEL=new Set(),SELW=-1,VIEW={x:40,y:20,k:.8},T=null,READY=false,DRAG=null,FILE='';
const $=id=>document.getElementById(id);
const el=(t,a={},p)=>{const e=document.createElementNS(NS,t);for(const k in a)e.setAttribute(k,a[k]);if(p)p.appendChild(e);return e};
const h=(t,a={},...c)=>{const e=document.createElement(t);for(const k in a){if(k==='on')for(const ev in a.on)e.addEventListener(ev,a.on[ev]);else if(k==='cls')e.className=a[k];else e.setAttribute(k,a[k])}e.append(...c);return e};
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const CATC={Power:'#f5b84b',Passive:'#94a3b8',Output:'#3ddc97',Input:'#8ea2ff',Analog:'#c2457c',Sensor:'#2dd4bf',Comms:'#5a8dee',Memory:'#a78bfa',Motion:'#fb923c',Switching:'#f472b6'};

/* ---------------------------------------------------------------- geometry */
function mcuGeo(){
 const pins=META.device.pins,n=pins.length,quad=META.device.layout==='quad';
 const g={pins:{},quad};
 if(quad){const s=Math.ceil(n/4),W=s*P+40;g.w=W;g.h=W;
  pins.forEach((p,i)=>{const side=Math.floor(i/s),k=i%s;let x,y,dx,dy;
   if(side===0){x=0;y=20+k*P+P/2;dx=-1;dy=0}else if(side===1){x=20+k*P+P/2;y=W;dx=0;dy=1}
   else if(side===2){x=W;y=W-20-k*P-P/2;dx=1;dy=0}else{x=W-20-k*P-P/2;y=0;dx=0;dy=-1}
   g.pins[p.num]={p,x,y,dx,dy}})}
 else{const L=Math.ceil(n/2),H=L*P+30;g.w=200;g.h=H;
  pins.forEach((p,i)=>{const left=i<L,k=left?i:n-1-i;g.pins[p.num]={p,x:left?0:200,y:15+k*P+P/2,dx:left?-1:1,dy:0}})}
 return g}
function partGeo(pt){
 const lib=LIB[pt.type]||{ports:[],label:pt.type};const ports=lib.ports,g={w:118,ports:{}};
 if(['gnd','vdd33','v5','battery'].includes(pt.type)&&ports.length===1){g.w=70;g.h=40;
  const up=pt.type!=='gnd';g.ports[ports[0].name]={x:35,y:up?40:0,dx:0,dy:up?1:-1};return g}
 if(ports.length===2&&ports.every(p=>p.role==='p')){g.h=44;g.ports[ports[0].name]={x:0,y:22,dx:-1,dy:0};g.ports[ports[1].name]={x:g.w,y:22,dx:1,dy:0};return g}
 const mg=GEO&&GEO.mcu,mcu=D.parts.find(p=>p.type==='mcu');
 const right=mcu&&mg?(pt.x+g.w/2<mcu.x+mg.w/2):true;
 g.h=Math.max(44,ports.length*P+28);
 ports.forEach((p,i)=>{g.ports[p.name]={x:right?g.w:0,y:26+i*P,dx:right?1:-1,dy:0}});
 g.right=right;return g}
let GEO=null;
function portPos(ep){
 const [pid,port]=ep.split('.');const pt=D.parts.find(p=>p.id===pid);if(!pt)return null;
 if(pt.type==='mcu'){const q=GEO.mcu.pins[port];if(!q)return null;return{x:pt.x+q.x+q.dx*STUB,y:pt.y+q.y+q.dy*STUB,dx:q.dx,dy:q.dy}}
 const g=partGeo(pt),q=g.ports[port];if(!q)return null;return{x:pt.x+q.x+q.dx*10,y:pt.y+q.y+q.dy*10,dx:q.dx,dy:q.dy}}
function route(A,B){
 const a={x:A.x+A.dx*12,y:A.y+A.dy*12},b={x:B.x+B.dx*12,y:B.y+B.dy*12};let pts;
 if(A.dx){const xm=Math.round((a.x+b.x)/2);pts=B.dx?[a,{x:xm,y:a.y},{x:xm,y:b.y},b]:[a,{x:b.x,y:a.y},b]}
 else{const ym=Math.round((a.y+b.y)/2);pts=B.dy?[a,{x:a.x,y:ym},{x:b.x,y:ym},b]:[a,{x:a.x,y:b.y},b]}
 return [A,...pts,B].map(p=>p.x+','+p.y).join(' ')}

/* ---------------------------------------------------------------- render */
function fnOf(num){return (CHK&&CHK.assign&&CHK.assign[num])||''}
function render(){
 const svg=$('csvg');svg.replaceChildren();GEO={mcu:mcuGeo()};
 const defs=el('defs',{},svg);const pat=el('pattern',{id:'cgrid',width:20,height:20,patternUnits:'userSpaceOnUse'},defs);el('circle',{cx:1,cy:1,r:1,fill:'#1e2638'},pat);
 const root=el('g',{id:'croot',transform:`translate(${VIEW.x},${VIEW.y}) scale(${VIEW.k})`},svg);
 el('rect',{x:-5000,y:-5000,width:10000,height:10000,fill:'url(#cgrid)','data-bg':1},root);
 const bad=new Set((CHK&&CHK.bad_pins)||[]),badParts=new Set();
 ((CHK&&CHK.findings)||[]).forEach(f=>f.severity==='error'&&f.parts.forEach(p=>badParts.add(p)));
 const used=new Set();D.wires.forEach(w=>[w.a,w.b].forEach(e=>{const [pid,po]=e.split('.');if(D.parts.find(p=>p.id===pid&&p.type==='mcu'))used.add(po)}));
 const wg=el('g',{},root);
 D.wires.forEach((w,i)=>{const A=portPos(w.a),B=portPos(w.b);if(!A||!B)return;
  const pts=route(A,B);el('polyline',{points:pts,class:'cw-hit','data-w':i},wg);
  el('polyline',{points:pts,class:'cw'+(i===SELW?' sel':''),'data-w':i},wg)});
 for(const pt of D.parts){
  const g=el('g',{transform:`translate(${pt.x},${pt.y})`,class:'cpart'+(SEL.has(pt.id)?' sel':'')+(badParts.has(pt.id)?' bad':''),'data-id':pt.id},root);
  if(pt.type==='mcu'){const m=GEO.mcu;
   el('rect',{x:0,y:0,width:m.w,height:m.h,rx:10,class:'cbody mcu'},g);
   const t=el('text',{x:m.w/2,y:m.h/2-6,class:'cmcu-t'},g);t.textContent=META.device.device;
   const t2=el('text',{x:m.w/2,y:m.h/2+12,class:'cmcu-s'},g);t2.textContent=`${META.device.count} pins · ${META.device.family}${META.device.verified?' · datasheet-verified':''}`;
   for(const num in m.pins){const q=m.pins[num],p=q.p;const pw=!!p.power;
    const cls='cpin'+(pw?' pw':'')+(bad.has(String(num))?' bad':used.has(String(num))?' used':'');
    el('line',{x1:q.x,y1:q.y,x2:q.x+q.dx*STUB,y2:q.y+q.dy*STUB,class:cls},g);
    el('circle',{cx:q.x+q.dx*STUB,cy:q.y+q.dy*STUB,r:3.6,class:cls+' cport','data-ep':pt.id+'.'+num,'data-pin':num},g);
    const fn=fnOf(String(num));
    const lab=el('text',{class:'cpl'+(fn?' fn':''),'data-pin':num},g);lab.textContent=(fn&&fn!==p.label&&!fn.startsWith('GPIO')?fn:p.label);
    if(q.dx){lab.setAttribute('x',q.x-q.dx*6);lab.setAttribute('y',q.y+3.5);lab.setAttribute('text-anchor',q.dx<0?'start':'end')}
    else{lab.setAttribute('transform',`translate(${q.x+3.5},${q.y-q.dy*6}) rotate(-90)`);lab.setAttribute('text-anchor',q.dy<0?'end':'start')}
    const nt=el('text',{class:'cpn'},g);nt.textContent=num;
    if(q.dx){nt.setAttribute('x',q.x+q.dx*12);nt.setAttribute('y',q.y-3);nt.setAttribute('text-anchor','middle')}
    else{nt.setAttribute('transform',`translate(${q.x-3},${q.y+q.dy*12}) rotate(-90)`);nt.setAttribute('text-anchor','middle')}}
   continue}
  const lib=LIB[pt.type]||{label:pt.type,cat:'',ports:[]},geo=partGeo(pt),col=CATC[lib.cat]||'#7c8cff';
  el('rect',{x:0,y:0,width:geo.w,height:geo.h,rx:8,class:'cbody',style:`--pc:${col}`},g);
  el('rect',{x:0,y:0,width:4,height:geo.h,rx:2,fill:col},g);
  const t=el('text',{x:10,y:15,class:'cid'},g);t.textContent=pt.id;
  const t2=el('text',{x:10,y:geo.h<=44?32:geo.h-8,class:'cty'},g);t2.textContent=lib.label+(pt.params&&pt.params.ohms?` ${fmtOhm(pt.params.ohms)}`:'');
  for(const name in geo.ports){const q=geo.ports[name];
   el('line',{x1:q.x,y1:q.y,x2:q.x+q.dx*10,y2:q.y+q.dy*10,class:'cpin'},g);
   el('circle',{cx:q.x+q.dx*10,cy:q.y+q.dy*10,r:3.6,class:'cpin cport','data-ep':pt.id+'.'+name},g);
   if(geo.h>44||geo.w===70){const l=el('text',{class:'cpl',x:q.dx?q.x-q.dx*5:q.x+5,y:q.dy?(q.dy>0?q.y-5:q.y+12):q.y+3.5,'text-anchor':q.dx>0?'end':'start'},g);l.textContent=name}}}
 $('czoom').textContent=Math.round(VIEW.k*100)+'%';
}
const fmtOhm=o=>o>=1e6?o/1e6+'M':o>=1e3?o/1e3+'k':o+'Ω';

/* ---------------------------------------------------------------- interaction */
function toWorld(ev){const r=$('csvg').getBoundingClientRect();return{x:(ev.clientX-r.left-VIEW.x)/VIEW.k,y:(ev.clientY-r.top-VIEW.y)/VIEW.k}}
function setView(){const r=$('croot');if(r)r.setAttribute('transform',`translate(${VIEW.x},${VIEW.y}) scale(${VIEW.k})`);$('czoom').textContent=Math.round(VIEW.k*100)+'%'}
function zoom(f,cx,cy){const r=$('csvg').getBoundingClientRect();cx=cx??r.width/2;cy=cy??r.height/2;const k=Math.min(3,Math.max(.2,VIEW.k*f));VIEW.x=cx-(cx-VIEW.x)*k/VIEW.k;VIEW.y=cy-(cy-VIEW.y)*k/VIEW.k;VIEW.k=k;setView()}
function fit(){const svg=$('csvg').getBoundingClientRect();let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9;GEO={mcu:mcuGeo()};
 for(const p of D.parts){const g=p.type==='mcu'?GEO.mcu:partGeo(p);x0=Math.min(x0,p.x-40);y0=Math.min(y0,p.y-40);x1=Math.max(x1,p.x+g.w+40);y1=Math.max(y1,p.y+g.h+40)}
 if(x0>x1)return;const k=Math.min(1.4,Math.min(svg.width/(x1-x0),svg.height/(y1-y0)));VIEW={k,x:(svg.width-(x1-x0)*k)/2-x0*k,y:(svg.height-(y1-y0)*k)/2-y0*k};render()}
function tip(ev,html){const t=$('ctip');if(!html){t.style.display='none';return}t.innerHTML=html;t.style.display='block';const r=$('cwrap').getBoundingClientRect();t.style.left=(ev.clientX-r.left+14)+'px';t.style.top=(ev.clientY-r.top+14)+'px'}
function pinTip(num){const p=META.device.pins.find(x=>String(x.num)===String(num));if(!p)return'';const fn=fnOf(String(num));
 return `<b>pin ${p.num} · ${esc(p.label)}</b><div class="mut">${esc(p.names.join(' / '))}</div>`+(p.power?`<div>power: ${esc(p.power)}</div>`:'')+
 (fn?`<div>assigned: <b>${esc(fn)}</b></div>`:'')+(p.tol5v!=null?`<div>${p.tol5v?'5 V tolerant':'not 5 V tolerant'}</div>`:'')+
 `<div class="mut">${p.functions.length} valid functions · click to choose</div>`}
function bind(){
 const svg=$('csvg');
 svg.addEventListener('wheel',e=>{e.preventDefault();const r=svg.getBoundingClientRect();zoom(e.deltaY<0?1.12:1/1.12,e.clientX-r.left,e.clientY-r.top)},{passive:false});
 svg.addEventListener('mousedown',e=>{closePop();const t=e.target,w=toWorld(e);
  if(t.dataset.ep){DRAG={mode:'wire',from:t.dataset.ep,line:el('polyline',{class:'cw tmp'},$('croot'))};return}
  if(t.dataset.pin){e.stopPropagation();pinPop(e,t.dataset.pin);return}
  const pg=t.closest('.cpart');
  if(pg&&e.detail>=2){paramPop(e,pg.dataset.id);return}
  if(pg){const id=pg.dataset.id;if(e.shiftKey||e.ctrlKey||e.metaKey){SEL.has(id)?SEL.delete(id):SEL.add(id)}else if(!SEL.has(id)){SEL=new Set([id])}SELW=-1;
   DRAG={mode:'move',start:w,orig:[...SEL].map(i=>{const p=D.parts.find(x=>x.id===i);return[p,p.x,p.y]}),moved:false};render();return}
  if(t.dataset.w!==undefined){SELW=+t.dataset.w;SEL.clear();render();return}
  if(e.shiftKey){DRAG={mode:'box',start:w,rect:el('rect',{class:'cbox'},$('croot'))};return}
  SEL.clear();SELW=-1;DRAG={mode:'pan',sx:e.clientX,sy:e.clientY,vx:VIEW.x,vy:VIEW.y};render()});
 window.addEventListener('mousemove',e=>{
  if(!DRAG){const t=e.target;if(t&&t.dataset&&t.dataset.pin&&$('csvg').contains(t))tip(e,pinTip(t.dataset.pin));
   else if(t&&t.dataset&&t.dataset.ep&&$('csvg').contains(t))tip(e,`<b>${esc(t.dataset.ep)}</b><div class="mut">drag to another port to wire</div>`);
   else if(t&&t.closest&&t.closest('.cpart')&&$('csvg').contains(t)){const p=D.parts.find(x=>x.id===t.closest('.cpart').dataset.id);const l=p&&LIB[p.type];tip(e,l?`<b>${esc(p.id)} · ${esc(l.label)}</b><div class="mut">${esc(l.doc||'')}</div><div class="mut">double-click to edit parameters</div>`:'')}
   else tip(e,'');return}
  const w=toWorld(e);
  if(DRAG.mode==='pan'){VIEW.x=DRAG.vx+e.clientX-DRAG.sx;VIEW.y=DRAG.vy+e.clientY-DRAG.sy;setView()}
  else if(DRAG.mode==='move'){const dx=Math.round((w.x-DRAG.start.x)/10)*10,dy=Math.round((w.y-DRAG.start.y)/10)*10;DRAG.moved=DRAG.moved||dx||dy;DRAG.orig.forEach(([p,x,y])=>{p.x=x+dx;p.y=y+dy});render()}
  else if(DRAG.mode==='wire'){const A=portPos(DRAG.from);if(A)DRAG.line.setAttribute('points',`${A.x},${A.y} ${w.x},${A.y} ${w.x},${w.y}`)}
  else if(DRAG.mode==='box'){const x=Math.min(w.x,DRAG.start.x),y=Math.min(w.y,DRAG.start.y);Object.entries({x,y,width:Math.abs(w.x-DRAG.start.x),height:Math.abs(w.y-DRAG.start.y)}).forEach(([k,v])=>DRAG.rect.setAttribute(k,v))}});
 window.addEventListener('mouseup',e=>{if(!DRAG)return;const d=DRAG;DRAG=null;
  if(d.mode==='wire'){d.line.remove();const t=e.target;
   if(t&&t.dataset&&t.dataset.ep&&t.dataset.ep!==d.from){const a=d.from,b=t.dataset.ep;if(!D.wires.some(w=>(w.a===a&&w.b===b)||(w.a===b&&w.b===a))){D.wires.push({a,b});changed()}}
   else if(t&&t.dataset&&t.dataset.ep===d.from&&t.dataset.pin)pinPop(e,t.dataset.pin)}
  else if(d.mode==='move'){if(d.moved)changed(false)}
  else if(d.mode==='box'){const x=+d.rect.getAttribute('x'),y=+d.rect.getAttribute('y'),W=+d.rect.getAttribute('width'),H=+d.rect.getAttribute('height');d.rect.remove();
   D.parts.forEach(p=>{const g=p.type==='mcu'?GEO.mcu:partGeo(p);if(p.x>=x&&p.y>=y&&p.x+g.w<=x+W&&p.y+g.h<=y+H)SEL.add(p.id)});render()}});
 window.addEventListener('keydown',e=>{if(!$('p-cir')||$('p-cir').classList.contains('hide'))return;if(/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName))return;
  if(e.key==='Delete'||e.key==='Backspace'){if(SELW>=0){D.wires.splice(SELW,1);SELW=-1;changed();e.preventDefault()}
   else if(SEL.size){const ids=[...SEL].filter(i=>D.parts.find(p=>p.id===i).type!=='mcu');D.parts=D.parts.filter(p=>!ids.includes(p.id));D.wires=D.wires.filter(w=>!ids.includes(w.a.split('.')[0])&&!ids.includes(w.b.split('.')[0]));SEL.clear();changed();e.preventDefault()}}
  if(e.key==='Escape'){SEL.clear();SELW=-1;closePop();render()}
  if((e.ctrlKey||e.metaKey)&&e.key==='a'){SEL=new Set(D.parts.map(p=>p.id));render();e.preventDefault()}});
 const wrap=$('cwrap');
 wrap.addEventListener('dragover',e=>{e.preventDefault();e.dataTransfer.dropEffect='copy'});
 wrap.addEventListener('drop',e=>{e.preventDefault();const type=e.dataTransfer.getData('text/x-cl-part');if(!type||!LIB[type])return;const w=toWorld(e);addPart(type,Math.round(w.x/10)*10-50,Math.round(w.y/10)*10-20)});
}
function addPart(type,x,y){const lib=LIB[type];let i=1;const pre=(lib.prefix||'X').replace(/[^A-Za-z0-9_]/g,'')||'X';while(D.parts.some(p=>p.id===pre+i))i++;
 const id=pre+i;D.parts.push({id,type,params:{...(lib.params||{})},x,y});SEL=new Set([id]);changed()}

/* ---------------------------------------------------------------- popups */
function closePop(){$('cpop').style.display='none'}
document.addEventListener('mousedown',e=>{const p=$('cpop');if(p&&p.style.display==='block'&&!p.contains(e.target)&&!$('csvg').contains(e.target))closePop()});
function popAt(ev,node){const p=$('cpop'),r=$('cwrap').getBoundingClientRect();p.replaceChildren(node);p.style.display='block';p.style.left=Math.min(ev.clientX-r.left+8,r.width-270)+'px';p.style.top=Math.min(ev.clientY-r.top+8,r.height-320)+'px'}
function pinPop(ev,num){const p=META.device.pins.find(x=>String(x.num)===String(num));if(!p)return;const cur=(D.assign||{})[num]||'';
 const list=h('div',{cls:'cfl'});
 const pick=fn=>{D.assign=D.assign||{};if(fn)D.assign[num]=fn;else delete D.assign[num];closePop();changed()};
 list.append(h('button',{cls:'s'+(cur?'':' on'),on:{click:()=>pick('')}},'auto (from the wired part)'));
 p.functions.forEach(fn=>list.append(h('button',{cls:'s'+(cur===fn?' on':''),on:{click:()=>pick(fn)}},fn)));
 popAt(ev,h('div',{},h('div',{cls:'cpop-h'},`pin ${p.num} · ${p.label}`),h('div',{cls:'mut',style:'margin-bottom:6px'},p.functions.length?'Only functions this pin can actually do are listed.':'Power / special pin: nothing assignable.'),list))}
function paramPop(ev,id){const pt=D.parts.find(p=>p.id===id);if(!pt||pt.type==='mcu')return;const lib=LIB[pt.type];const box=h('div',{});
 box.append(h('div',{cls:'cpop-h'},`${pt.id} · ${lib.label}`),h('div',{cls:'mut',style:'margin-bottom:8px'},lib.doc||''));
 const idIn=h('input',{value:pt.id});box.append(h('label',{},'Reference'),idIn);const ins={};
 Object.entries({...(lib.params||{}),...(pt.params||{})}).forEach(([k,v])=>{let i;if(typeof v==='boolean'){i=h('input',{type:'checkbox'});i.checked=v}else i=h('input',{value:v});ins[k]=i;box.append(h('label',{},k),i)});
 box.append(h('div',{cls:'row',style:'margin-top:8px'},h('button',{cls:'p',on:{click:()=>{
  for(const k in ins){const i=ins[k],v=i.type==='checkbox'?i.checked:i.value;pt.params[k]=(typeof v==='string'&&v!==''&&!isNaN(+v)&&!/^0x/i.test(v))?+v:v}
  const nid=idIn.value.replace(/[^A-Za-z0-9_]/g,'');if(nid&&nid!==id&&!D.parts.some(p=>p.id===nid)){pt.id=nid;D.wires.forEach(w=>{['a','b'].forEach(k=>{if(w[k].split('.')[0]===id)w[k]=nid+'.'+w[k].split('.')[1]})})}
  closePop();changed()}}},'Apply'),h('button',{cls:'s',on:{click:closePop}},'Cancel')));popAt(ev,box)}

/* ---------------------------------------------------------------- ERC / project */
function changed(rerender=true){save();if(rerender)render();else render();clearTimeout(T);T=setTimeout(check,250);PRJ=null;$('cprj').classList.add('stale')}
function save(){localStorage.setItem('cl_circuit',JSON.stringify(D))}
async function check(){
 try{const r=await fetch('/api/circuit/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({circuit:D})});CHK=await r.json()}catch(e){return}
 render();const f=CHK.findings||[],ne=f.filter(x=>x.severity==='error').length,nw=f.filter(x=>x.severity==='warning').length;
 const b=$('cerc');b.className='pill '+(ne?'bad':nw?'warn':'ok');b.textContent=ne?`${ne} ERROR${ne>1?'S':''}`:nw?`${nw} WARNING${nw>1?'S':''}`:'ERC CLEAN';
 $('cfind').replaceChildren(...f.map(x=>{const d=h('div',{cls:'f cf-'+x.severity},h('span',{cls:'pill '+(x.severity==='error'?'bad':x.severity==='warning'?'warn':'info')},x.severity),' ',x.message+(x.page?` (p.${x.page})`:''));
  d.addEventListener('mouseenter',()=>{SEL=new Set(x.parts);render()});return d}));
 $('cbom').replaceChildren(...bom().map(r=>h('tr',{},h('td',{},r.refs.join(', ')),h('td',{},r.qty),h('td',{},r.desc))));
}
function bom(){const m={};for(const p of D.parts){if(['gnd','vdd33','v5'].includes(p.type))continue;const lib=LIB[p.type]||{label:p.type};
 const desc=p.type==='mcu'?D.device:lib.label+(p.params&&Object.keys(p.params).length?' ('+Object.entries(p.params).map(([k,v])=>k+'='+v).join(', ')+')':'');
 (m[desc]=m[desc]||{desc,refs:[],qty:0}).refs.push(p.id);m[desc].qty++}return Object.values(m).sort((a,b)=>a.refs[0].localeCompare(b.refs[0],undefined,{numeric:true}))}
function dl(name,blob){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),4000)}
function bomCsv(){const q=s=>'"'+String(s).replace(/"/g,'""')+'"';dl((D.name||'circuit')+'_bom.csv',new Blob(['References,Qty,Description\n'+bom().map(r=>[q(r.refs.join(' ')),r.qty,q(r.desc)].join(',')).join('\n')+'\n'],{type:'text/csv'}))}
function bomMd(){dl((D.name||'circuit')+'_bom.md',new Blob([`# BOM - ${D.name||'circuit'} (${D.device})\n\n| References | Qty | Description |\n|---|---|---|\n`+bom().map(r=>`| ${r.refs.join(', ')} | ${r.qty} | ${r.desc} |`).join('\n')+'\n'],{type:'text/markdown'}))}
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

/* ---------------------------------------------------------------- export */
function svgText(){GEO={mcu:mcuGeo()};const keep=VIEW;SEL.clear();SELW=-1;fit();const svg=$('csvg').cloneNode(true);VIEW=keep;render();
 const css=`.cbody{fill:#151a26;stroke:#3a4560;stroke-width:1.2}.cbody.mcu{fill:#121725;stroke:#7c8cff}.cpin{stroke:#5b6782;stroke-width:1.4;fill:#0c111c}.cpin.used{stroke:#3ddc97;fill:#3ddc97}.cpin.bad{stroke:#ff6b7a;fill:#ff6b7a}.cpin.pw{stroke:#f5b84b}
 .cw{fill:none;stroke:#8ea2ff;stroke-width:2;stroke-linejoin:round}.cw-hit{display:none}text{font-family:Inter,Segoe UI,sans-serif}.cpl{font-size:9px;fill:#b4b8c5}.cpl.fn{fill:#3ddc97}.cpn{font-size:7px;fill:#5b6782}
 .cid{font-size:11px;font-weight:700;fill:#ececf1}.cty{font-size:10px;fill:#9aa1b2}.cmcu-t{font-size:15px;font-weight:700;fill:#ececf1;text-anchor:middle}.cmcu-s{font-size:10px;fill:#7d8394;text-anchor:middle}`;
 const r=$('csvg').getBoundingClientRect();svg.setAttribute('xmlns',NS);svg.setAttribute('width',r.width);svg.setAttribute('height',r.height);
 const st=document.createElementNS(NS,'style');st.textContent=css;svg.insertBefore(st,svg.firstChild);
 const bg=document.createElementNS(NS,'rect');bg.setAttribute('width','100%');bg.setAttribute('height','100%');bg.setAttribute('fill','#0b0e15');svg.insertBefore(bg,st.nextSibling);
 return {s:new XMLSerializer().serializeToString(svg),w:r.width,h:r.height}}
function exportSvg(){dl((D.name||'circuit')+'.svg',new Blob([svgText().s],{type:'image/svg+xml'}))}
function exportPng(){const {s,w,h:H}=svgText();const img=new Image();img.onload=()=>{const c=document.createElement('canvas');c.width=w*2;c.height=H*2;const x=c.getContext('2d');x.scale(2,2);x.drawImage(img,0,0);c.toBlob(b=>dl((D.name||'circuit')+'.png',b))};img.src='data:image/svg+xml;charset=utf-8,'+encodeURIComponent(s)}
function saveFile(){dl((D.name||'circuit')+'.circuit.json',new Blob([JSON.stringify(D,null,1)],{type:'application/json'}))}
function loadFile(f){const r=new FileReader();r.onload=async()=>{try{const d=JSON.parse(r.result);if(!d.parts||!d.wires)throw Error('missing parts/wires');await setCircuit(d)}catch(e){alert('Not a ChipLlama circuit file: '+e.message)}};r.readAsText(f)}

/* ---------------------------------------------------------------- device / palette / init */
async function loadMeta(dev){const r=await fetch('/api/circuit/meta?device='+encodeURIComponent(dev));if(!r.ok)throw Error('no pinout for '+dev);META=await r.json();LIB={};META.library.forEach(l=>LIB[l.type]=l)}
async function setCircuit(d){D=d;D.assign=D.assign||{};if(!D.parts.some(p=>p.type==='mcu'))D.parts.unshift({id:'U1',type:'mcu',x:420,y:60});
 await loadMeta(D.device||'PIC24FJ256GB110');$('cdev').value=META.device.device;$('cname').value=D.name||'';CHK=null;PRJ=null;SEL.clear();SELW=-1;fit();save();check()}
async function setDevice(dev){if(D.wires.length&&!confirm(`Switch to ${dev}? Wires to MCU pins are kept by pin number; check the ERC afterwards.`)){$('cdev').value=D.device;return}D.device=dev;D.assign={};await setCircuit(D)}
function palette(q=''){const cats={};META.library.filter(l=>!q||(l.label+' '+l.type+' '+l.cat).toLowerCase().includes(q.toLowerCase())).forEach(l=>(cats[l.cat]=cats[l.cat]||[]).push(l));
 $('cpal').replaceChildren(...Object.keys(cats).sort().map(c=>h('div',{},h('div',{cls:'cpal-c',style:`color:${CATC[c]||'#ccc'}`},c),...cats[c].map(l=>{const b=h('div',{cls:'cpal-i',draggable:'true',title:l.doc||''},h('span',{cls:'dot',style:`background:${CATC[c]||'#7c8cff'}`}),l.label);
  b.addEventListener('dragstart',e=>{e.dataTransfer.setData('text/x-cl-part',l.type);e.dataTransfer.effectAllowed='copy'});
  b.addEventListener('dblclick',()=>{const r=$('csvg').getBoundingClientRect();addPart(l.type,Math.round(((r.width/2-VIEW.x)/VIEW.k)/10)*10,Math.round(((r.height/2-VIEW.y)/VIEW.k)/10)*10)});return b}))))}
async function loadEx(n){if(!n)return;const d=await (await fetch('/api/circuit/example/'+n)).json();await setCircuit(d);palette($('cq').value)}
async function init(){if(READY){render();return}READY=true;
 const ex=await (await fetch('/api/circuit/examples')).json();$('cex').replaceChildren(new Option('Examples…',''),...ex.map(e=>new Option(e.title,e.name)));
 let d=null;try{d=JSON.parse(localStorage.getItem('cl_circuit')||'null')}catch(e){}
 bind();
 if(d&&d.parts){try{await setCircuit(d)}catch(e){await loadEx('led_button')}}else await loadEx('led_button');
 $('cdev').replaceChildren(...META.bundled.map(b=>new Option(b,b)));$('cdev').value=META.device.device;palette()}
window.Circuit={init,fit,zoom,generate,zip,hex,toBlocks,exportSvg,exportPng,saveFile,loadFile,bomCsv,bomMd,loadEx,setDevice,palette,
 rename:v=>{D.name=v.replace(/[^A-Za-z0-9_ -]/g,'');save()},resize:()=>D&&render(),clear:()=>{if(confirm('Remove every part except the MCU?')){D.parts=D.parts.filter(p=>p.type==='mcu');D.wires=[];D.assign={};changed()}},state:()=>D};
})();
