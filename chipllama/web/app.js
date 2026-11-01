/* ChipLlama dashboard - main script (Generate, Reverse debug, Settings, shell). */
const $=id=>document.getElementById(id);let SET=null,LAST=null,FILES=[],FIX_CONTEXT='';
function el(t,c,x){const e=document.createElement(t);if(c)e.className=c;if(x!=null)e.textContent=x;return e}
function pill(t,c){return el('span','pill '+c,t)}const sevc={error:'bad',warning:'warn',info:'info'};
function toast(m,kind){const box=$('toasts');const t=el('div','toast '+(kind||(/^(not|error|⚠)/i.test(m)?'bad':'ok')));t.append(el('span','tdot'),el('span','',m));box.append(t);requestAnimationFrame(()=>t.classList.add('on'));setTimeout(()=>{t.classList.remove('on');setTimeout(()=>t.remove(),300)},m.length>70?6000:2800)}
const TABS={gen:['Generate','Plain English → register-level C, verified against the datasheet and XC16'],blk:['Blocks','Drag blocks → deterministic, verified C · polish with Muse'],cir:['Circuit','Wire an MCU to parts · live ERC · HAL-style MPLAB project, verified'],aud:['Audit','Static rules for existing code · file:line · datasheet pages · patched copy'],dbg:['Reverse debug','Screenshot or dump → decoded registers → root cause → fix'],pin:['Pin planner','Assign peripherals to pins · PPS conflicts · power estimate'],set:['Settings','Board, model, toolchain · stored locally in settings.json']};
let CUR='gen';function tab(n){CUR=n;for(const k of Object.keys(TABS)){const p=$('p-'+k);if(p)p.classList.toggle('hide',k!=n);const t=$('t-'+k);if(t)t.classList.toggle('on',k==n)}$('ptitle').textContent=TABS[n][0];$('psub').textContent=TABS[n][1];history.replaceState(null,'','#'+n);if(n==='pin'&&window.Pins&&!window.PINS_INIT){window.PINS_INIT=1;Pins.init()}if(n==='aud'&&window.Audit&&!window.AUDIT_INIT){window.AUDIT_INIT=1;Audit.init()}if(n==='blk'&&window.Blocks){Blocks.init().then(()=>Blocks.resize())}if(n==='cir'&&window.Circuit){Circuit.init()}}
function ex(e){$('req').value=e.textContent}
async function api(u,o){const r=await fetch(u,o);const ct=r.headers.get('content-type')||'';const j=ct.includes('json')?await r.json():null;if(!r.ok)throw new Error((j&&j.detail)||r.statusText);return j}
const post=(u,b)=>api(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
async function chips(){const s=await api('/api/status');const c=$('chips');c.replaceChildren(pill(s.device,'info'),pill('meta · '+s.meta_model+(s.meta_key_set?'':' (no key)'),s.meta_key_set?'ok':'warn'),pill((s.compiler||'xc16')+' '+(s.xc16_gcc?'ok':'missing'),s.xc16_gcc?'ok':'warn'),pill('ICS '+s.ics,'info'),pill('flash '+(s.flash_enabled?'ON':'off'),s.flash_enabled?'warn':'info'))}
const ICON={ok:'✓',warn:'!',info:'i',error:'✕'};
function tail(p){const t=String(p).split(/[\\/]/).filter(Boolean);return t.length>2?'…/'+t.slice(-2).join('/'):p}
async function loadDoctor(){const d=$('doctor');try{const j=await api('/api/doctor');const n={ok:0,warn:0,info:0,error:0};
d.replaceChildren(...j.rows.map(r=>{const lv=r.level||(r.ok?'ok':'warn');n[lv]=(n[lv]||0)+1;const x=el('div','r');const dot=el('span','dot '+lv,ICON[lv]);dot.title=lv;const v=el('div','v');
if(r.path&&r.value.includes(r.path)){const [pre,post]=r.value.split(r.path);if(pre)v.append(pre);const c=el('code','',tail(r.path));c.title=r.path;v.append(c);if(post)v.append(post)}else v.textContent=r.value;
x.append(dot,el('span','k',r.label),v);return x}));$('docsum').textContent=n.error?`· ${n.error} to fix`:n.warn?`· ${n.warn} warning${n.warn>1?'s':''}`:'· all good'}catch(e){d.textContent=e.message}
 try{const p=await api('/api/pragmas');$('pmeta').textContent=p.title;hlC($('pragmas'),p.text+(p.clock_code.length?'\n\n/* first in main() */\n'+p.clock_code.join('\n'):''))}catch(e){$('pmeta').textContent=e.message}}
// ---------- generate (streamed)
function step(cls,title){const s=el('div','step '+cls);s.append(el('b','',title));$('steps').append(s);s.scrollIntoView({block:'nearest',behavior:'smooth'});return s}
function diffPre(t){const p=el('pre','diff');t.split('\n').forEach(l=>{p.append(el('span',l.startsWith('+')&&!l.startsWith('+++')?'a':l.startsWith('-')&&!l.startsWith('---')?'r':l.startsWith('@@')?'h':'',l+'\n'))});return p}
async function generate(){const bctx=window.BLOCKS_CONTEXT||'';window.BLOCKS_CONTEXT='';const ctx=bctx||FIX_CONTEXT;const ckind=bctx?'blocks':'debug';FIX_CONTEXT='';const b=$('gen');b.disabled=true;b.textContent='Working…';$('prog').classList.remove('hide');$('final').classList.add('hide');$('gempty').classList.add('hide');$('steps').replaceChildren();let running=null;const t0=Date.now();
 try{const r=await fetch('/api/generate/stream',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request:$('req').value,use_images:$('imgs').checked,inject_fault:$('fault').checked,context:ctx,context_kind:ckind})});
  if(!r.ok){const j=await r.json().catch(()=>({}));throw new Error(j.detail||r.statusText)}
  const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
  for(;;){const{value,done}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+1);if(line.trim())running=onEvent(JSON.parse(line),running)}}
 }catch(e){step('bad','Error').append(el('div','f',e.message))}
 b.disabled=false;b.textContent='Generate & verify';$('genmeta').textContent=((Date.now()-t0)/1000).toFixed(1)+' s'}
function onEvent(ev,running){const d=ev.data;
 if(ev.event=='spec'){const s=d.spec,r=d.reference,u=d.uart;const x=step('ok','Parsed request');
  if(s){x.append(el('div','f',`Timer${s.bits32?'2/3 (32-bit)':s.timer} · ${s.period_s?(s.period_s*1e3).toPrecision(6)+' ms':'period ?'} · Fosc ${s.fosc_hz/1e6} MHz → Fcy ${s.fosc_hz/2e6} MHz · ${s.interrupt?'interrupt':'polling'}`),el('div','f mut',`Reference: TCKPS=${r.tckps} (1:${r.prescale}), PR=${r.period_reg}, ${r.error_ppm.toFixed(0)} ppm`));(s.notes||[]).forEach(n=>x.append(el('div','f mut','· '+n)))}
  if(u){const b=u.solution;x.append(el('div','f',`UART${u.uart} · ${u.baud} baud · TX ${u.tx_rp!=null?'RP'+u.tx_rp+' ('+u.tx_pin+')':'any'} · RX ${u.rx_rp!=null?'RP'+u.rx_rp+' ('+u.rx_pin+')':'any'} · Fcy ${u.fcy/1e6} MHz`),el('div','f mut',`Reference: BRGH=${b.brgh}, U1BRG=${b.brg} → ${Math.round(b.actual)} baud (${b.error_pct.toFixed(2)}%)`));(u.notes||[]).forEach(n=>x.append(el('div','f mut','· '+n)))}
  if(d.demo_fault_injection){const q=el('div','f');q.append(pill('DEMO','warn'),el('span','',' Fault injection ON: attempt 1 will be corrupted on purpose to show self-correction.'));x.append(q)}
  return null}
 if(ev.event=='fault'){if(running)running.remove();const x=step('bad',`DEMO fault injection · attempt ${d.n}`);const q=el('div','f');q.append(pill('DEMO','warn'),el('span','',' '+d.description+' (done by ChipLlama, not the model)'));x.append(q);
  if(d.diff){const dd=el('details');dd.append(el('summary','','what was corrupted'),diffPre(d.diff));dd.open=true;x.append(dd)}return null}
 if(ev.event=='reflect'){const x=step('run2',`Self-correction · reading feedback for attempt ${d.n}`);x.append(el('div','f',`Errors fed back: ${(d.errors||[]).join(', ')||'compile errors'}`));
  if(d.pages&&d.pages.length){const q=el('div','f mut');q.append(document.createTextNode('Re-reading datasheet '+d.pages.map(p=>'p.'+p).join(', ')+(d.images_attached&&d.images_attached.length?' (page images sent to the model)':'')+' '));d.pages.forEach(p=>{const a=el('a','','p.'+p+' ');a.href='/api/page/'+p+'.png';a.target='_blank';q.append(a)});x.append(q)}return null}
 if(ev.event=='model_call'){return step('run',`Attempt ${d.n}: asking ${d.backend}…`)}
 if(ev.event=='attempt'){if(running)running.remove();if(d.error){step('bad','Attempt '+d.n+': '+d.error);return null}
  const ok=d.check.ok&&d.compile.status!=='error';const x=step(ok?'ok':'bad',`Attempt ${d.n} · ${d.seconds}s `);
  x.append(pill('checker '+(d.check.ok?'pass':'fail'),d.check.ok?'ok':'bad'),document.createTextNode(' '),pill('compile '+d.compile.status,d.compile.status=='ok'?'ok':d.compile.status=='skipped'?'warn':'bad'));
  d.check.findings.filter(f=>f.severity!='info').forEach(f=>{const q=el('div','f');q.append(pill(f.code,sevc[f.severity]),el('span','',' '+f.message));x.append(q)});
  const inf=d.check.findings.filter(f=>f.severity=='info');if(inf.length){const de=el('details');de.append(el('summary','',inf.length+' info notes'));inf.forEach(f=>de.append(el('div','f mut',f.code+': '+f.message)));x.append(de)}
  if(d.compile.output){const de=el('details');de.append(el('summary','','compiler output'),el('pre','',d.compile.output));if(d.compile.status=='error')de.open=true;x.append(de)}
  const de=el('details');de.append(el('summary','','registers: code vs reference'));const t=el('table');const h=el('tr');['register','code gives','reference'].forEach(k=>h.append(el('th','',k)));t.append(h);
  Object.keys(d.check.registers).forEach(k=>{const tr=el('tr');tr.append(el('td','',k),el('td','',d.check.registers[k]),el('td','',d.check.expected[k]||''));t.append(tr)});de.append(t);x.append(de);
  if(d.diff){const dd=el('details');dd.append(el('summary','',d.n>1?'fix applied by the agent (diff vs previous attempt)':'diff vs previous attempt'),diffPre(d.diff));dd.open=true;x.append(dd)}
  if(d.injected_fault){const q=el('div','f');q.append(pill('DEMO','warn'),el('span','mut',' this attempt contains an injected fault'));x.insertBefore(q,x.children[1]||null)}
  return null}
 if(ev.event=='done'){if(running)running.remove();step(d.status=='verified'?'ok':'bad',d.status=='verified'?'VERIFIED':'FAILED'+(d.error?': '+d.error:''));showFinal(d);return null}return running}
function showFinal(j){LAST=j;const f=$('final');f.classList.remove('hide');f.replaceChildren();
 const h=el('div','row');h.append(el('h3','',''),);f.append(pill(j.status.toUpperCase(),j.status=='verified'?'ok':'bad'),el('span','mut',' '+(j.verification_level||'')+(j.error?' · '+j.error:'')));
 if(!j.code)return;{const pc=el('pre');hlC(pc,j.code);f.append(pc)}
 const r=el('div','row');const bt=(t,c,fn)=>{const b=el('button',c,t);b.style.flex='0 0 auto';b.onclick=fn;r.append(b)};
 bt('Copy','s',()=>{navigator.clipboard.writeText(j.code);toast('Copied')});
 bt('Download main.c','s',()=>dl(new Blob([j.code],{type:'text/plain'}),'main.c'));
 bt('Export MPLAB X project','p',async()=>{const x=await fetch('/api/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:j.code,request:j.request})});dl(await x.blob(),'ChipLlamaProject.zip')});
 bt('Flash to board','d',flashIt);f.append(r);r.append(el('span','mut',''));}
function dl(b,n){const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=n;a.click()}
async function flashIt(){const s=SET&&SET.settings;if(!s||!s.enable_flash){toast('Flashing is off - enable it in Settings first');return}
 $('dlgtxt').textContent=`This builds main.c and programs ${s.device} with ${s.ipecmd_tool}${s.ipecmd_power_target?' (PICkit powers the board)':''}. ICS is ${s.ics}. The chip's current firmware will be erased.`;
 $('dlg').returnValue='';$('dlg').showModal();$('dlg').onclose=async()=>{if($('dlg').returnValue!=='yes')return;toast('Flashing…');
  try{const j=await post('/api/flash',{code:LAST.code,confirm:true});const x=step(j.status=='ok'?'ok':'bad','Flash: '+j.status);if(j.reason)x.append(el('div','f',j.reason));if(j.output)x.append(el('pre','',j.output));if(j.compile&&j.compile.output)x.append(el('pre','',j.compile.output))}catch(e){toast(e.message)}}}
// ---------- debug
const drop=$('drop');drop.onclick=()=>$('shots').click();$('shots').onchange=e=>addFiles(e.target.files);
drop.ondragover=e=>{e.preventDefault();drop.classList.add('over')};drop.ondragleave=()=>drop.classList.remove('over');drop.ondrop=e=>{e.preventDefault();drop.classList.remove('over');addFiles(e.dataTransfer.files)};
document.addEventListener('paste',e=>{if($('p-dbg').classList.contains('hide'))return;const fs=[...e.clipboardData.items].filter(i=>i.type.startsWith('image/')).map(i=>i.getAsFile());if(fs.length){addFiles(fs);e.preventDefault();toast('image pasted')}});
let EXTRACTION='';
function addFiles(fs){for(const f of fs){if(!f.type.startsWith('image/'))continue;FILES.push(f);const i=el('img');i.src=URL.createObjectURL(f);i.title=f.name||'pasted';i.onclick=()=>window.open(i.src);$('thumbs').append(i)}EXTRACTION='';$('dbgmeta').textContent=FILES.length+' image(s)'}
function clearShots(){FILES=[];EXTRACTION='';$('thumbs').replaceChildren();$('dbgmeta').textContent=''}
async function loadEx(n){clearShots();const b=await (await fetch('/api/examples/debug/'+n+'.png')).blob();addFiles([new File([b],n+'.png',{type:'image/png'})]);
 const m=await (await fetch('/api/examples/debug/'+n+'.json')).json();$('intent').value=m.intent||'';$('dcode').value=m.code||'';EXTRACTION='';$('dbgmeta').textContent='demo image loaded – Muse will read it'}
function dstep(cls,title){const x=el('div','step '+cls);x.append(el('b','',title));$('dsteplist').append(x);return x}
function renderDiff(t){const p=el('pre','diff');t.split('\n').forEach(l=>{p.append(el('span',l.startsWith('+')&&!l.startsWith('+++')?'a':l.startsWith('-')&&!l.startsWith('---')?'r':l.startsWith('@@')?'h':'',l+'\n'))});return p}
const stc={ok:'ok',bad:'bad',warn:'warn',info:'info'};
async function dbg(){const o=$('dout'),b=$('dbgb');
 if(!FILES.length){return legacyDbg()}
 const fd=new FormData();fd.append('intent',$('intent').value);fd.append('code',$('dcode').value);fd.append('explain',$('usem').checked);if($('fosc').value)fd.append('fosc_mhz',$('fosc').value);if($('ddev').value)fd.append('device',$('ddev').value.trim());if($('dpasses').value)fd.append('passes',$('dpasses').value);
 FILES.forEach(f=>fd.append('images',f,f.name||'paste.png'));
 b.disabled=true;$('dsteps').classList.remove('hide');$('dsteplist').replaceChildren();o.replaceChildren();const t0=Date.now();
 const s1=dstep('run','1 · Muse Spark reads the image → strict JSON'+($('dpasses').value!=='1'?' + independent verification pass':''));
 try{const j=await api('/api/debug/vision',{method:'POST',body:fd});s1.className='step ok';
  const e=j.extraction;s1.append(el('div','f mut',`kind: ${e.kind} · confidence ${(e.confidence||0).toFixed(2)}`+(e.repaired&&e.repaired.length?' · repaired: '+e.repaired.join('; '):'')));
  const ex=el('details');ex.append(el('summary','','extracted JSON'),el('pre','',JSON.stringify({registers:e.registers,scope:e.scope,serial:e.serial},null,1)));s1.append(ex);
  (e.passes||[]).slice(1).forEach(p=>{const d=p.disagree&&Object.keys(p.disagree).length,x=dstep(p.error?'warn':d||p.reads?'warn':'ok',`1.${p.pass} · pass ${p.pass}: ${p.what}`);x.append(el('div','f mut',p.error||(p.reads?'reads: '+Object.entries(p.reads).map(([k,v])=>k+' '+v.join(' / ')).join(', '):d?'disagree: '+Object.entries(p.disagree).map(([k,v])=>k+' '+v.join(' vs ')).join(', '):'all '+(p.agree||[]).length+' values agree')+(p.voted?' · vote: '+Object.entries(p.voted).map(([k,v])=>k+'='+v).join(', '):'')))});
  Object.entries(e.disputed||{}).forEach(([k,v])=>{const x=dstep('warn','⚠ '+k+' read as '+v.join(' or '));x.append(el('div','f mut','low confidence - not used as sole evidence for a bug'))});
  const s2=dstep(j.findings.some(r=>r.status=='bad')?'bad':j.findings.some(r=>r.status=='warn')?'warn':'ok','2 · Deterministic decode (chipdb + timer/baud math)');s2.append(el('div','f mut',`${j.findings.length} rows · clock: ${j.clock.source||''}`));
  const s3=dstep(j.explanation?'ok':'run2','3 · Grounded explanation'+(j.explanation?' (Muse Spark)':' (rules only)'));
  const bad=j.verdict.startsWith('BUG'),unc=j.verdict.startsWith('UNCERTAIN'),okv=j.verdict.startsWith('OK');const v=el('div','card');v.append(el('h3','',bad?'Bug found':unc?'Uncertain - needs more evidence':'Verdict'));const vv=el('div','f');vv.append(pill(bad?'BUG':j.verdict.split(':')[0],bad?'bad':unc?'warn':okv?'ok':'info'),el('span','',' '+j.root_cause));v.append(vv);
  [...j.causes,...(j.uncertain||[]),...(j.explained||[])].filter(c=>c!==j.root_cause).forEach(c=>v.append(el('div','f mut','also: '+c)));
  if(j.pages.length){const pg=el('div','f mut','datasheet: ');j.pages.forEach(p=>{const a=el('a','',' p.'+p+' ');a.href='/api/page/'+p+'.png';a.target='_blank';a.style.color='var(--acc)';pg.append(a)});v.append(pg)}
  o.append(v);
  const tc=el('div','card');tc.append(el('h3','','Findings'));const tb=el('table');const hr=el('tr');['Register','Value','Decoded','Expected',''].forEach(h=>hr.append(el('th','',h)));tb.append(hr);
  j.findings.forEach(r=>{const tr=el('tr');tr.append(el('td','',r.register),el('td','',r.value),el('td','',r.decoded),el('td','',r.expected));const td=el('td');td.append(pill(r.status.toUpperCase(),stc[r.status]||'info'));tr.append(td);if(r.note)tr.title=r.note;tb.append(tr)});tc.append(tb);o.append(tc);
  if(j.fix_diff){const fc=el('div','card');fc.append(el('h3','','Fix'),renderDiff(j.fix_diff));
   if(j.regenerate_context){const bt=el('button','p','Apply fix → regenerate & verify');bt.onclick=()=>applyFix(j);fc.append(bt)}o.append(fc)}
  else if(j.regenerate_context){const fc=el('div','card');fc.append(el('h3','','Fix'));const bt=el('button','p','Apply fix → regenerate & verify');bt.onclick=()=>applyFix(j);fc.append(bt);o.append(fc)}
  if(j.explanation){const ec=el('div','card');ec.append(el('h3','','Muse Spark explanation'),el('pre','',j.explanation));o.append(ec)}
  (j.notes||[]).forEach(n=>o.append(el('div','f mut','note: '+n)));
  $('dbgmeta').textContent=`done in ${((Date.now()-t0)/1000).toFixed(1)} s`}
 catch(e){s1.className='step bad';s1.append(el('div','f',e.message));o.replaceChildren(el('div','card','Error: '+e.message))}b.disabled=false}
function applyFix(j){tab('gen');$('req').value=j.regenerate_request||$('intent').value;FIX_CONTEXT=j.regenerate_context;$('fault').checked=false;toast('Regenerating with the reverse-debug fix…');generate()}
async function legacyDbg(){const fd=new FormData();fd.append('dump',$('dump').value);fd.append('symptom',$('sym').value||$('intent').value);fd.append('code',$('dcode').value);
 if($('fosc').value)fd.append('fosc_mhz',$('fosc').value);if($('exp').value)fd.append('expected_period_ms',$('exp').value);fd.append('use_model',$('usem').checked);
 const o=$('dout'),b=$('dbgb');b.disabled=true;o.replaceChildren(el('div','card','Analysing…'));
 try{const j=await api('/api/debug',{method:'POST',body:fd});o.replaceChildren();
  const f=el('div','card');f.append(el('h3','','Findings'));j.findings.forEach(x=>{const d=el('div','f');d.append(pill(x.severity,sevc[x.severity]||'info'),el('span','',' '+x.message));f.append(d)});if(!j.findings.length)f.append(el('div','f mut','no rule findings'));o.append(f);
  if(j.explanation){const e=el('div','card');e.append(el('h3','','Muse explanation'),el('pre','',j.explanation));o.append(e)}
  const c=el('div','card');c.append(el('h3','','Decoded registers'));j.decoded.forEach(d=>c.append(el('div','f',`${d.register} = ${d.value}  `+Object.entries(d.fields).map(([k,v])=>k+'='+v).join(' '))));o.append(c);
  (j.notes||[]).forEach(n=>o.append(el('div','f mut','note: '+n)))}catch(e){o.replaceChildren(el('div','card','Error: '+e.message))}b.disabled=false}
// ---------- settings
const FIELDS=['device','ics','clock_profile','crystal_hz','max_iters','max_tokens','vision_passes','meta_model','meta_reasoning_effort','xc16_gcc','xc8_cc','xc32_gcc','xc_dsc_gcc','dspic_compiler','mplab_packs_dir','ipecmd_path','ipecmd_tool','ipecmd_serial','ipecmd_power_target','enable_flash','datasheet_pdf','family_mode'];
function opts(id,list){const s=$('s-'+id);s.replaceChildren(...list.map(o=>{const x=el('option','',o.title||o);x.value=o.key||o;return x}))}
function keyPill(id,on){const p=$(id);p.className='pill '+(on?'ok':'warn');p.textContent=on?'set':'not set'}
async function loadSettings(){SET=await api('/api/settings');const o=SET.options;opts('ics',o.ics);opts('clock_profile',o.clock_profile);opts('ipecmd_tool',o.ipecmd_tool);opts('meta_reasoning_effort',o.reasoning_effort);opts('family_mode',(o.family_mode||['baremetal','arduino']).map(k=>({key:k,title:k==='arduino'?'Arduino sketch (.ino, arduino-cli)':'Bare-metal C (avr-gcc / arm-gcc)'})));
 const s=SET.settings;FIELDS.forEach(k=>{$('s-'+k).value=String(s[k])});keyPill('k-meta',s.meta_api_key_set);$('s-meta_api_key').value='';$('sfile').textContent='File: '+SET.file;devInfo()}
let DEVT=null;function devSearch(){clearTimeout(DEVT);DEVT=setTimeout(async()=>{const q=$('s-device').value.trim();const j=await api('/api/devices?limit=60&q='+encodeURIComponent(q));$('devlist').replaceChildren(...j.devices.map(d=>{const o=document.createElement('option');o.value=d.name;o.label=d.family+' · '+d.compiler.toUpperCase();return o}));if(!j.roots.length)$('devinfo').textContent='No MPLAB X packs found - set the packs folder below.'},250)}
async function devInfo(){const n=$('s-device').value.trim();if(!n)return;try{const r=await fetch('/api/device?name='+encodeURIComponent(n));if(!r.ok){$('devinfo').textContent='No device data for '+n+' (check packs folder)';return}const d=await r.json();$('devinfo').textContent=d.family+' · '+d.compiler.toUpperCase()+' · '+d.settings+' config settings · '+d.sfrs+' SFRs · '+(d.source=='built-in'?'built-in data':d.source)}catch(e){}}
async function saveSettings(){const u={};FIELDS.forEach(k=>{let v=$('s-'+k).value;if(v==='true')v=true;else if(v==='false')v=false;u[k]=v});
 for(const k of['meta_api_key']){if($('s-'+k).value)u[k]=$('s-'+k).value}
 try{await post('/api/settings',u);toast('Settings saved');await loadSettings();chips();loadDoctor()}catch(e){toast('Not saved: '+e.message)}}
async function clearKey(k){if(!confirm('Remove the saved '+k+' from settings.json?'))return;await post('/api/settings',{[k]:'__clear__'});toast('Key cleared');loadSettings();chips()}
async function detect(){const j=await post('/api/detect',{});const fnd=[];[['xc16_gcc','xc16'],['xc8_cc','xc8'],['xc32_gcc','xc32'],['xc_dsc_gcc','xc-dsc']].forEach(([k,n])=>{if(j[k]&&!$('s-'+k).value){$('s-'+k).value=j[k]}if(j[k])fnd.push(n)});toast(fnd.length?'Found '+fnd.join(', '):'No XC compiler found - enter the path');if(j.ipecmd&&!$('s-ipecmd_path').value)$('s-ipecmd_path').value=j.ipecmd}
document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();const b={gen:'gen',aud:'arun',dbg:'dbgb'}[CUR];if(b&&!$(b).disabled)$(b).click();else if(CUR==='blk'&&window.Blocks)Blocks.polish()}});
chips();loadDoctor();loadSettings();{const h=location.hash.slice(1);tab(TABS[h]?h:'gen')}
