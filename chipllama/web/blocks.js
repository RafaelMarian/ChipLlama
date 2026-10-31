/* ChipLlama - Blocks tab (Blockly 11, vendored). C is generated server-side (deterministic, verified). */
(function(){
const C={ev:'#7c5cff',pin:'#2f9e7a',time:'#c48a1d',ser:'#2d7fd3',an:'#c2457c',com:'#5a6bd6'};
let PINS=['RA0'],CN=['RD6'],WS=null,T=null,LAST=null,READY=false,EX=[],CTX=null,PARTS={};
/* M8: family boards - blocks drive the parts of a Circuit (CTX={device,circuit}) through the HAL/driver API */
const partDD=k=>({type:'field_dropdown',name:'PART',options:()=>{const a=PARTS[k]||[];return a.length?a.map(p=>[p,p]):[['('+k+')','']]}});
const pinDD=n=>({type:'field_dropdown',name:n,options:()=>PINS.map(p=>[p,p])});
const cnDD=n=>({type:'field_dropdown',name:n,options:()=>CN.map(p=>[p,p])});
const rp=(n,v)=>({type:'field_number',name:n,value:v,min:0,max:46,precision:1});
const DEFS=[
 {type:'cl_on_start',message0:'on start %1 %2',args0:[{type:'input_dummy'},{type:'input_statement',name:'DO'}],colour:C.ev,tooltip:'Runs once in main() after the clock and peripherals are initialised.'},
 {type:'cl_forever',message0:'forever %1 %2',args0:[{type:'input_dummy'},{type:'input_statement',name:'DO'}],colour:C.ev,tooltip:'Body of the main while(1) loop.'},
 {type:'cl_every',message0:'every %1 ms (Timer1 interrupt) %2 %3',args0:[{type:'field_number',name:'MS',value:500,min:0.01,max:1000},{type:'input_dummy'},{type:'input_statement',name:'DO'}],colour:C.ev,tooltip:'Timer1 period interrupt. Prescaler and PR1 are solved exactly for Fcy = 16 MHz.'},
 {type:'cl_on_pin_change',message0:'when pin %1 changes, pull-up %2 %3 %4',args0:[cnDD('PIN'),{type:'field_checkbox',name:'PULLUP',checked:true},{type:'input_dummy'},{type:'input_statement',name:'DO'}],colour:C.ev,tooltip:'Change-notification interrupt (_CNInterrupt). Only pins with a CNx line are listed.'},
 {type:'cl_on_uart_rx',message0:'when a UART byte arrives %1 %2',args0:[{type:'input_dummy'},{type:'input_statement',name:'DO'}],colour:C.ev,tooltip:'_U1RXInterrupt. Use "UART read byte" inside.'},
 {type:'cl_pin_mode',message0:'set pin %1 as %2',args0:[pinDD('PIN'),{type:'field_dropdown',name:'MODE',options:[['output','OUTPUT'],['input','INPUT'],['input + pull-up','INPUT_PULLUP']]}],previousStatement:null,nextStatement:null,colour:C.pin,tooltip:'Writes TRISx (and AD1PCFG / CNPU when needed).'},
 {type:'cl_pin_write',message0:'write pin %1 %2',args0:[pinDD('PIN'),{type:'field_dropdown',name:'VALUE',options:[['HIGH','HIGH'],['LOW','LOW']]}],previousStatement:null,nextStatement:null,colour:C.pin,tooltip:'Writes LATx (never PORTx) to avoid read-modify-write problems.'},
 {type:'cl_pin_toggle',message0:'toggle pin %1',args0:[pinDD('PIN')],previousStatement:null,nextStatement:null,colour:C.pin,tooltip:'LATxbits.LATxn ^= 1'},
 {type:'cl_pin_read',message0:'read pin %1',args0:[pinDD('PIN')],output:null,colour:C.pin,tooltip:'Reads PORTx (0 or 1).'},
 {type:'cl_delay_ms',message0:'wait %1 ms',args0:[{type:'field_number',name:'MS',value:100,min:0,max:60000,precision:1}],previousStatement:null,nextStatement:null,colour:C.time,tooltip:'__delay_ms() from libpic30 (needs FCY, already defined).'},
 {type:'cl_delay_us',message0:'wait %1 µs',args0:[{type:'field_number',name:'US',value:10,min:0,max:60000,precision:1}],previousStatement:null,nextStatement:null,colour:C.time,tooltip:'__delay_us() from libpic30.'},
 {type:'cl_uart_begin',message0:'UART1 begin %1 baud TX RP %2 RX RP %3',args0:[{type:'field_dropdown',name:'BAUD',options:[['115200','115200'],['57600','57600'],['38400','38400'],['19200','19200'],['9600','9600']]},rp('TX',10),rp('RX',17)],previousStatement:null,nextStatement:null,colour:C.ser,tooltip:'Maps U1TX/U1RX with PPS and solves U1BRG / BRGH for the lowest error.'},
 {type:'cl_uart_print',message0:'UART print %1 newline %2',args0:[{type:'field_input',name:'TEXT',text:'hello'},{type:'field_checkbox',name:'NL',checked:false}],previousStatement:null,nextStatement:null,colour:C.ser,tooltip:'Blocking transmit of a text string.'},
 {type:'cl_uart_println',message0:'UART print line %1',args0:[{type:'field_input',name:'TEXT',text:'hello'}],previousStatement:null,nextStatement:null,colour:C.ser,tooltip:'Text followed by \\r\\n.'},
 {type:'cl_uart_print_num',message0:'UART print number %1 newline %2',args0:[{type:'input_value',name:'VALUE'},{type:'field_checkbox',name:'NL',checked:true}],previousStatement:null,nextStatement:null,colour:C.ser,tooltip:'Signed decimal print of an integer.'},
 {type:'cl_uart_available',message0:'UART byte available?',output:null,colour:C.ser,tooltip:'U1STAbits.URXDA'},
 {type:'cl_uart_read',message0:'UART read byte',output:null,colour:C.ser,tooltip:'Reads U1RXREG.'},
 {type:'cl_adc_read',message0:'analog read AN %1',args0:[{type:'field_number',name:'CH',value:5,min:0,max:15,precision:1}],output:null,colour:C.an,tooltip:'10-bit ADC sample (0..1023). Clears the AD1PCFG bit and sets TRIS for the pin.'},
 {type:'cl_spi_begin',message0:'SPI1 begin %1 kHz mode %2 SCK RP %3 SDO RP %4 SDI RP %5',args0:[{type:'field_number',name:'KHZ',value:1000,min:1,max:8000},{type:'field_dropdown',name:'MODE',options:[['0','0'],['1','1'],['2','2'],['3','3']]},rp('SCK',10),rp('SDO',17),rp('SDI',16)],previousStatement:null,nextStatement:null,colour:C.com,tooltip:'Master mode; prescalers solved for the closest frequency not above the request.'},
 {type:'cl_spi_transfer',message0:'SPI transfer %1',args0:[{type:'input_value',name:'VALUE'}],output:null,colour:C.com,tooltip:'Sends a byte and returns the byte received.'},
 {type:'cl_spi_send',message0:'SPI send %1',args0:[{type:'input_value',name:'VALUE'}],previousStatement:null,nextStatement:null,colour:C.com,tooltip:'Sends a byte over SPI1 and discards the byte received.'},
 {type:'cl_i2c_begin',message0:'I2C1 begin %1 kHz',args0:[{type:'field_dropdown',name:'KHZ',options:[['100','100'],['400','400'],['1000','1000']]}],previousStatement:null,nextStatement:null,colour:C.com,tooltip:'I2C1 master on SCL1/SDA1 (RG2/RG3); I2C1BRG from the datasheet formula.'},
 {type:'cl_i2c_write',message0:'I2C write to %1 value %2',args0:[{type:'field_input',name:'ADDR',text:'0x48'},{type:'input_value',name:'VALUE'}],previousStatement:null,nextStatement:null,colour:C.com,tooltip:'START, 7-bit address + W, one data byte, STOP.'},

 {type:'cl_led_set',message0:'LED %1 %2',args0:[partDD('led'),{type:'field_dropdown',name:'VALUE',options:[['toggle','TOGGLE'],['on','ON'],['off','OFF']]}],previousStatement:null,nextStatement:null,colour:C.pin,tooltip:'Family boards: led_on/led_off/led_toggle on a Circuit LED.'},
 {type:'cl_button_read',message0:'button %1 is down',args0:[partDD('button')],output:null,colour:C.pin,tooltip:'button_read() - polarity handled by the driver.'},
 {type:'cl_button_pressed',message0:'button %1 was pressed',args0:[partDD('button')],output:null,colour:C.pin,tooltip:'button_pressed() - debounced edge.'},
 {type:'cl_servo_write',message0:'servo %1 to %2 °',args0:[partDD('servo'),{type:'input_value',name:'DEG'}],previousStatement:null,nextStatement:null,colour:C.an,tooltip:'servo_set() 0..180°.'},
 {type:'cl_pot_read',message0:'pot %1 (0..1000)',args0:[partDD('pot')],output:null,colour:C.an,tooltip:'pot_read_permille().'},
 {type:'cl_temp_read',message0:'temperature °C from %1',args0:[partDD('temp')],output:null,colour:C.an,tooltip:'tmp102_read() / 16.'},
 {type:'cl_millis',message0:'milliseconds since start',output:null,colour:C.time,tooltip:'hal_millis().'},
];
const cat=(name,colour,types)=>({kind:'category',name,colour,contents:types.map(t=>typeof t==='string'?{kind:'block',type:t}:t)});
const N=v=>({kind:'block',type:'math_number',fields:{NUM:v}});
const TOOLBOX={kind:'categoryToolbox',contents:[
 cat('Events',C.ev,['cl_on_start','cl_forever','cl_every','cl_on_pin_change','cl_on_uart_rx']),
 cat('Pins',C.pin,['cl_pin_mode','cl_pin_write','cl_pin_toggle','cl_pin_read']),
 cat('Timing',C.time,['cl_delay_ms','cl_delay_us']),
 cat('Serial',C.ser,['cl_uart_begin','cl_uart_println','cl_uart_print',{kind:'block',type:'cl_uart_print_num',inputs:{VALUE:{shadow:N(0)}}},'cl_uart_available','cl_uart_read']),
 cat('Analog',C.an,['cl_adc_read']),
 cat('Control','#5b80a5',['controls_if','controls_repeat_ext','controls_whileUntil','controls_flow_statements','logic_compare','logic_operation','logic_negate','logic_boolean']),
 {kind:'category',name:'Variables & Math',colour:'#a55b80',custom:'VARIABLE',},
 cat('Math','#5b67a5',['math_number','math_arithmetic','math_modulo']),
 cat('Board parts (Arduino/STM32/ESP32)',C.pin,['cl_led_set','cl_button_read','cl_button_pressed',{kind:'block',type:'cl_servo_write',inputs:{DEG:{shadow:N(90)}}},'cl_pot_read','cl_temp_read','cl_millis']),
 cat('Comms',C.com,['cl_spi_begin',{kind:'block',type:'cl_spi_send',inputs:{VALUE:{shadow:N(0)}}},{kind:'block',type:'cl_spi_transfer',inputs:{VALUE:{shadow:N(0)}}},'cl_i2c_begin',{kind:'block',type:'cl_i2c_write',inputs:{VALUE:{shadow:N(0)}}}]),
]};
let INITP=null;
function init(){if(!INITP)INITP=init0();return INITP}
async function init0(){
 if(READY)return;READY=true;
 try{const m=await (await fetch('/api/blocks/meta')).json();PINS=m.pins.map(p=>p.pin);CN=m.pins.filter(p=>p.cn).map(p=>p.pin);
  EX=m.examples;const s=document.getElementById('bex');s.replaceChildren(new Option('Examples…',''),...m.examples.map(e=>new Option((e.family?'⬡ ':'')+e.title,e.name)))}catch(e){}
 try{CTX=JSON.parse(localStorage.getItem('cl_blocks_ctx')||'null')}catch(e){CTX=null}
 await setCtx(CTX);
 Blockly.defineBlocksWithJsonArray(DEFS);
 const theme=Blockly.Theme.defineTheme('cl',{base:Blockly.Themes.Classic,componentStyles:{workspaceBackgroundColour:'#0c111c',toolboxBackgroundColour:'#0f1522',toolboxForegroundColour:'#cbd5e1',flyoutBackgroundColour:'#141b2b',flyoutForegroundColour:'#cbd5e1',flyoutOpacity:.97,scrollbarColour:'#2a3550',insertionMarkerColour:'#fff',insertionMarkerOpacity:.3,cursorColour:'#d0d0d0'},fontStyle:{family:'Inter, system-ui, sans-serif',size:11}});
 WS=Blockly.inject('bws',{toolbox:TOOLBOX,media:'/vendor/',sounds:false,theme,renderer:'zelos',trashcan:true,zoom:{controls:true,wheel:true,startScale:.8},grid:{spacing:24,length:2,colour:'#1a2234',snap:true}});
 WS.addChangeListener(e=>{if(e.isUiEvent)return;clearTimeout(T);T=setTimeout(gen,300)});
 const saved=localStorage.getItem('cl_blocks');
 if(saved){try{Blockly.serialization.workspaces.load(JSON.parse(saved),WS)}catch(e){await loadEx('blink')}}else await loadEx('blink');
}
async function gen(){
 const ws=Blockly.serialization.workspaces.save(WS);localStorage.setItem('cl_blocks',JSON.stringify(ws));
 const r=await fetch('/api/blocks/generate',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.assign({workspace:ws},CTX?{device:CTX.device,circuit:CTX.circuit,gen_mode:(document.getElementById('cmode')||{}).value||null}:{}))});
 const j=await r.json();LAST=j;const b=document.getElementById('bbadge'),c=document.getElementById('bcode'),f=document.getElementById('bfind');
 c.textContent=j.c||('/* '+(j.error||'add an event block (on start / forever / every …) */')+' */');
 b.className='pill '+(j.error?'bad':j.verified?'ok':'warn');b.textContent=j.error?'ERROR':j.verified?'VERIFIED':'CHECK FAILED';
 document.getElementById('bintent').textContent=j.intent||'';
 f.replaceChildren(...[...(j.warnings||[]).map(w=>({severity:'warning',message:w})),...(j.findings||[]).filter(x=>x.severity!=='info')].map(x=>{const d=document.createElement('div');d.className='f';d.innerHTML='<span class="pill '+(x.severity==='error'?'bad':'warn')+'">'+x.severity+'</span> ';d.append(x.message);return d}));
}
async function setCtx(c){CTX=c&&c.circuit?{device:c.device,circuit:c.circuit}:null;localStorage.setItem('cl_blocks_ctx',JSON.stringify(CTX));PARTS={};
 if(CTX){try{PARTS=(await (await fetch('/api/blocks/parts?circuit='+encodeURIComponent(CTX.circuit))).json()).parts||{}}catch(e){}}
 const l=document.getElementById('bctx');if(l)l.textContent=CTX?('Board: '+CTX.device+' · circuit '+CTX.circuit):'Board: PIC24 (Settings device)'}
async function loadEx(n){if(!n)return;const e=EX.find(x=>x.name===n);await setCtx(e&&e.family?e:null);const j=await (await fetch('/api/blocks/example/'+n)).json();WS.clear();Blockly.serialization.workspaces.load(j,WS);WS.cleanUp();WS.scrollCenter();gen()}
function save(){const ws=Object.assign(Blockly.serialization.workspaces.save(WS),CTX||{});const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(ws,null,1)],{type:'application/json'}));a.download='chipllama_blocks.json';a.click()}
function load(file){const r=new FileReader();r.onload=async()=>{try{const j=JSON.parse(r.result);await setCtx(j.circuit?j:null);WS.clear();Blockly.serialization.workspaces.load(j,WS);gen()}catch(e){alert('Not a ChipLlama blocks file: '+e.message)}};r.readAsText(file)}
function copy(){if(LAST&&LAST.c)navigator.clipboard.writeText(LAST.c).then(()=>window.toast&&toast('C copied'))}
function polish(){if(!LAST||!LAST.c){return}if(typeof tab==='function')tab('gen');document.getElementById('req').value=LAST.intent;window.BLOCKS_CONTEXT=LAST.c;window.toast&&toast('Muse is polishing the Blocks code…');generate()}
function loadWs(j){WS.clear();Blockly.serialization.workspaces.load(j,WS);WS.cleanUp();WS.scrollCenter();gen()}
window.Blocks={init,loadWs,gen,loadEx,save,load,copy,polish,resize:()=>WS&&Blockly.svgResize(WS)};
})();
