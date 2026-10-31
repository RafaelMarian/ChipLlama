/* Theme: Light / Dark / System, persisted in localStorage('cl_theme'). The no-flash bootstrap is inline in <head>. */
(function(){
const MQ=window.matchMedia?matchMedia('(prefers-color-scheme: light)'):null;
function pref(){return localStorage.getItem('cl_theme')||'dark'}
function eff(p){p=p||pref();return p==='system'?(MQ&&MQ.matches?'light':'dark'):p}
function apply(){const t=eff();document.documentElement.dataset.theme=t;document.dispatchEvent(new CustomEvent('cl-theme',{detail:t}))}
function set(p){localStorage.setItem('cl_theme',p);apply();const s=document.getElementById('s-theme');if(s)s.value=p}
function toggle(){set(eff()==='light'?'dark':'light')}
if(MQ)(MQ.addEventListener?MQ.addEventListener('change',()=>pref()==='system'&&apply()):MQ.addListener(()=>pref()==='system'&&apply()));
window.Theme={pref,eff,set,toggle,apply,current:()=>document.documentElement.dataset.theme||'dark'};
/* tiny C highlighter for code previews (tokens styled per theme in theme.css) */
const KW=/^(if|else|for|while|do|return|switch|case|default|break|continue|goto|sizeof|typedef|struct|union|enum|static|const|volatile|extern|inline|register)$/;
const TY=/^(void|char|short|int|long|float|double|signed|unsigned|bool|u?int(8|16|32|64)_t|size_t|uint_fast\d+_t)$/;
const RE=/(\/\*[\s\S]*?\*\/|\/\/[^\n]*)|("(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')|(^[ \t]*#[^\n]*)|(\b0[xX][0-9a-fA-F]+[uUlL]*\b|\b\d+(?:\.\d+)?[uUlLfF]*\b)|([A-Za-z_]\w*)/gm;
window.hlC=function(pre,text){text=text==null?'':String(text);pre.replaceChildren();let last=0,m;const add=(s,c)=>{if(!s)return;if(!c){pre.append(s);return}const e=document.createElement('span');e.className=c;e.textContent=s;pre.append(e)};
 if(text.length>200000){pre.textContent=text;return}
 while((m=RE.exec(text))){add(text.slice(last,m.index));last=RE.lastIndex;
  if(m[1])add(m[0],'tk-c');else if(m[2])add(m[0],'tk-s');else if(m[3])add(m[0],'tk-p');else if(m[4])add(m[0],'tk-n');
  else add(m[0],KW.test(m[0])?'tk-k':TY.test(m[0])?'tk-t':'')}
 add(text.slice(last))};
document.addEventListener('DOMContentLoaded',()=>{const s=document.getElementById('s-theme');if(s){s.value=pref();s.onchange=()=>set(s.value)}const b=document.getElementById('s-expbg');if(b){b.value=localStorage.getItem('cl_expbg')||'theme';b.onchange=()=>localStorage.setItem('cl_expbg',b.value)}});
})();
