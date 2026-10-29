/* Pin planner (M4): PPS / fixed pins / JTAG + ICS conflicts, init code and a rough power estimate. */
(function () {
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
  let FNS = [], RP = [], ready = false;
  const DEF = [['U1TX', 'RP10'], ['U1RX', 'RP17'], ['OC1', 'RD0'], ['AN3', 'RB3'], ['GPIO_OUT', 'RA0']];
  const st = { rows: DEF.map(([fn, pin]) => ({ fn, pin })) };

  function row(r, i) {
    const d = E('div', 'pinrow');
    const s = E('select'); FNS.forEach(f => { const o = E('option', '', f.fn); o.value = f.fn; if (f.fn === r.fn) o.selected = true; s.append(o); });
    s.onchange = () => { r.fn = s.value; const f = FNS.find(x => x.fn === r.fn); if (f && f.pin) { r.pin = f.pin; draw(); } run(); };
    const p = E('input'); p.value = r.pin; p.placeholder = 'RF4 or RP10'; p.setAttribute('list', 'rplist');
    p.oninput = () => { r.pin = p.value; clearTimeout(st.t); st.t = setTimeout(run, 250); };
    const x = E('button', 's', '✕'); x.title = 'remove'; x.onclick = () => { st.rows.splice(i, 1); draw(); run(); };
    d.append(s, p, x); return d;
  }
  function draw() {
    const box = document.getElementById('pinrows'); box.innerHTML = '';
    st.rows.forEach((r, i) => box.append(row(r, i)));
  }
  async function run() {
    const body = { items: st.rows.filter(r => r.fn), ics: document.getElementById('pics').value,
      jtag: document.getElementById('pjtag').checked,
      power: { fcy: +document.getElementById('pfcy').value * 1e6, mode: document.getElementById('pmode').value,
        duty: +document.getElementById('pduty').value / 100, wdt: document.getElementById('pwdt').checked,
        battery_mah: +document.getElementById('pbat').value || null } };
    let j;
    try { j = await (await fetch('/api/pins/plan', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })).json(); }
    catch (e) { window.toast && toast('pin plan failed: ' + e.message, 'bad'); return; }
    const out = document.getElementById('pinout'); out.innerHTML = '';
    const c1 = E('div', 'card'); const h = E('h3'); h.append('Assignment ', E('span', 'pill ' + (j.ok ? 'ok' : 'bad'), j.ok ? 'no conflicts' : j.issues.filter(i => i.severity === 'error').length + ' conflict(s)')); c1.append(h);
    const t = E('table', 'ptbl'); t.innerHTML = '<tr><th>Function</th><th>Pin</th><th>RP</th><th>Kind</th></tr>';
    j.rows.forEach(r => { const tr = E('tr'); [r.fn, r.pin, r.rp || '—', r.kind.replace('_', ' ')].forEach(v => tr.append(E('td', '', v))); t.append(tr); });
    const f = E('div', 'f'); f.append(t);
    j.issues.forEach(i => { const d = E('div', 'pissue'); d.append(E('span', 'pill ' + (i.severity === 'error' ? 'bad' : 'warn'), i.severity), ' ', i.msg); f.append(d); });
    c1.append(f);
    const c2 = E('div', 'card'); const h2 = E('h3', '', 'pins_init()'); const cp = E('button', 's', 'Copy');
    cp.onclick = () => { navigator.clipboard.writeText(j.code); window.toast && toast('copied', 'ok'); }; h2.append(cp); c2.append(h2);
    const pre = E('pre', 'code', j.code); c2.append(E('div', 'f')); c2.lastChild.append(pre);
    const pw = j.power, c3 = E('div', 'card'); c3.append(E('h3', '', 'Power estimate'));
    const g = E('div', 'f pgrid');
    [['run', pw.run_ma + ' mA'], ['awake', pw.awake_ma + ' mA'], ['sleep', (pw.sleep_ma * 1000).toFixed(0) + ' µA'], ['average', pw.avg_ma + ' mA']]
      .concat(pw.battery_hours ? [['battery', pw.battery_hours > 48 ? (pw.battery_hours / 24).toFixed(1) + ' days' : pw.battery_hours + ' h']] : [])
      .forEach(([k, v]) => { const d = E('div', 'pstat'); d.append(E('div', 'mut', k), E('b', '', v)); g.append(d); });
    g.append(E('div', 'mut pnote', 'peripherals: ' + (pw.peripherals.join(', ') || 'none') + ' · ' + pw.note));
    c3.append(g);
    out.append(c1, c2, c3);
  }
  async function init() {
    if (ready) return; ready = true;
    const j = await (await fetch('/api/pins/functions')).json(); FNS = j.functions; RP = j.rp;
    const root = document.getElementById('pinroot');
    root.innerHTML = `<div class="grid"><div><div class="card"><h3>Pin assignments <span class="mut" style="font-weight:400">PPS · fixed pins · JTAG/ICS checks</span></h3><div class="f">
      <div id="pinrows"></div><datalist id="rplist"></datalist>
      <div class="row" style="margin-top:10px"><button class="s" id="padd" style="flex:0 0 auto">+ Add function</button>
      <label style="flex:0 0 auto;margin:0">ICS <select id="pics" style="width:auto"><option>PGx1</option><option selected>PGx2</option><option>PGx3</option></select></label>
      <label style="flex:0 0 auto;margin:0"><input type="checkbox" id="pjtag" style="width:auto"> JTAGEN=ON</label></div></div></div>
      <div class="card"><h3>Power inputs <span class="mut" style="font-weight:400">rough typical figures</span></h3><div class="f"><div class="row">
      <div><label>Fcy, MHz</label><input id="pfcy" type="number" value="16"></div>
      <div><label>Mode when awake</label><select id="pmode"><option value="run">Run</option><option value="idle">Idle</option></select></div>
      <div><label>Awake %</label><input id="pduty" type="number" value="100" min="0" max="100"></div>
      <div><label>Battery mAh</label><input id="pbat" type="number" placeholder="e.g. 2000"></div></div>
      <label style="margin-top:8px"><input type="checkbox" id="pwdt" style="width:auto"> watchdog running in sleep</label></div></div>
      </div><aside id="pinout"></aside></div>`;
    const dl = document.getElementById('rplist');
    RP.forEach(r => { const o = E('option'); o.value = (r.input_only ? 'RPI' : 'RP') + r.rp; o.label = r.pin + (r.input_only ? ' (input only)' : ''); dl.append(o); });
    document.getElementById('padd').onclick = () => { st.rows.push({ fn: 'GPIO_OUT', pin: '' }); draw(); };
    ['pics', 'pjtag', 'pfcy', 'pmode', 'pduty', 'pbat', 'pwdt'].forEach(id => document.getElementById(id).addEventListener('change', run));
    if (window.ics) document.getElementById('pics').value = window.ics;
    draw(); run();
  }
  window.Pins = { init, run };
})();
