/* Audit tab: static rules (server) + optional Muse review, fix diff and patched-zip download. */
(function () {
  let FILES = [], IMGS = [], RID = null, EX = null, EXMETA = null;
  const sevc = { error: 'bad', warning: 'warn', info: 'info' };
  const E = (t, c, x) => { const e = document.createElement(t); if (c) e.className = c; if (x != null) e.textContent = x; return e; };
  const g = id => document.getElementById(id);

  function chips() {
    const box = g('afiles'); box.replaceChildren();
    for (const f of FILES) box.append(E('span', 'pill info', f.name));
    for (const f of IMGS) box.append(E('span', 'pill warn', '🖼 ' + (f.name || 'screenshot')));
    if (FILES.length || IMGS.length) { const c = E('a', 'ex', 'clear'); c.href = '#'; c.onclick = e => { e.preventDefault(); FILES = []; IMGS = []; chips(); }; box.append(c); }
  }
  function take(list) {
    for (const f of list) {
      if (/^image\//.test(f.type)) IMGS.push(f);
      else if (/\.(c|h|zip|xml)$/i.test(f.name)) FILES.push(f);
    }
    chips();
  }
  function diffPre(txt) {
    const pre = E('pre', 'diff');
    for (const l of txt.split('\n')) {
      const s = E('span', l.startsWith('+') && !l.startsWith('+++') ? 'a' : l.startsWith('-') && !l.startsWith('---') ? 'r' : l.startsWith('@@') ? 'h' : '', l + '\n');
      pre.append(s);
    }
    return pre;
  }
  function render(j) {
    const out = g('aout'); out.replaceChildren();
    const head = E('div', 'card ascore');
    const sc = E('div', 'score ' + (j.score >= 85 ? 'ok' : j.score >= 50 ? 'warn' : 'bad'));
    sc.append(E('b', '', String(j.score)), E('span', '', '/100'));
    const meta = E('div', 'ameta');
    meta.append(E('div', 'ttl', `${j.counts.error} errors · ${j.counts.warning} warnings · ${j.counts.info} info`),
      E('div', 'mut f', `${j.device} · ${j.project} · ${j.files.length} file(s)` + (j.fcy ? ` · Fcy ${j.fcy / 1e6} MHz` : '')));
    const acts = E('div', 'row'); acts.style.flex = '0 0 auto';
    const fixable = j.findings.filter(f => f.fix).length;
    if (fixable) { const b = E('a', 'btn p', `Apply ${fixable} fixes ↓`); b.href = `/api/audit/${j.id}/fixes.zip`; b.title = 'Download a patched COPY of your project + report (nothing is edited in place)'; acts.append(b); }
    const md = E('button', 's', 'Copy report'); md.onclick = () => { navigator.clipboard.writeText(j.markdown); window.toast && toast('Report copied'); }; acts.append(md);
    head.append(sc, meta, acts); out.append(head);
    const fc = E('div', 'card'); fc.append(E('h3', '', 'Findings'));
    if (!j.findings.length) fc.append(E('div', 'empty', '✓ No rule fired. Clean bill of health from the static checker.'));
    for (const f of j.findings) {
      const r = E('div', 'afind');
      const top = E('div', 'row');
      top.append(E('span', 'pill ' + sevc[f.severity], f.severity), E('code', 'rule', f.rule), E('span', 'loc', `${f.file}:${f.line}`));
      if (f.pages && f.pages.length) { const p = E('span', 'pages'); for (const n of f.pages) { const a = E('a', 'ex', 'p.' + n); a.href = `/api/page/${n}.png`; a.target = '_blank'; p.append(a); } top.append(p); }
      if (f.fix) top.append(E('span', 'pill ok', 'auto-fix'));
      r.append(top, E('div', 'f', f.message));
      if (f.hint) r.append(E('div', 'f mut', '→ ' + f.hint));
      fc.append(r);
    }
    out.append(fc);
    if (j.diff) { const d = E('div', 'card'); d.append(E('h3', '', 'Proposed fix diff'), diffPre(j.diff)); out.append(d); }
    if (j.compile) {
      const c = E('div', 'card'); c.append(E('h3', '', 'Compile (patched files)'));
      c.append(E('div', 'f', 'status: ' + j.compile.status));
      for (const r of j.compile.files) { c.append(E('div', 'f mut', `${r.file}: ${r.status} ${r.reason || ''}`)); if (r.output) c.append(E('pre', '', r.output)); }
      out.append(c);
    }
    if (j.explanation) { const m = E('div', 'card'); m.append(E('h3', '', 'Muse Spark review'), E('div', 'prose', j.explanation)); out.append(m); }
    for (const n of j.notes || []) out.append(E('div', 'f mut', 'note: ' + n));
  }
  async function run() {
    const b = g('arun'); b.disabled = true; g('ameta').textContent = 'auditing…';
    const fd = new FormData();
    fd.append('code', g('acode').value); fd.append('explain', g('aexplain').checked); fd.append('compile', g('acompile').checked);
    for (const f of FILES) fd.append('files', f, f.name);
    for (const f of IMGS) fd.append('images', f, f.name || 'shot.png');
    if (EX) { for (const [n, t] of Object.entries(EX)) fd.append('files', new File([t], n)); fd.append('device', EXMETA.device); fd.append('project', EXMETA.project); }
    try {
      const r = await fetch('/api/audit', { method: 'POST', body: fd }); const j = await r.json();
      if (!r.ok) throw new Error(j.detail || r.statusText);
      RID = j.id; render(j); g('ameta').textContent = '';
    } catch (e) { g('ameta').textContent = '⚠ ' + e.message; }
    b.disabled = false;
  }
  async function examples() {
    const sel = g('aex'); const r = await fetch('/api/audit/examples'); const list = await r.json();
    sel.replaceChildren(E('option', '', 'Load a buggy example…')); sel.firstChild.value = '';
    window.AUDIT_EX = list;
    for (const x of list) { const o = E('option', '', `${x.name} (${x.kind}, ${Object.keys(x.files).length} files)`); o.value = x.name; sel.append(o); }
  }
  function loadEx(name) {
    const x = (window.AUDIT_EX || []).find(e => e.name === name); if (!x) return;
    FILES = []; IMGS = []; g('acode').value = '';
    EX = {}; for (const [n, t] of Object.entries(x.files)) EX[n.split('/').pop()] = t;
    EXMETA = { device: x.device, project: x.kind };
    g('afiles').replaceChildren(...Object.keys(EX).map(n => E('span', 'pill info', n)));
    run();
  }

  async function explain() {
    let code = document.getElementById('acode').value;
    if (!code.trim()) { const f = FILES.find(x => /\.c$/i.test(x.name)); if (f) code = await f.text(); else { const k = Object.keys(EX || {}).find(n => /\.c$/i.test(n)); if (k) code = EX[k]; } }
    if (!code.trim()) { window.toast && toast('paste or drop a .c file first', 'warn'); return; }
    const muse = document.getElementById('aexplain').checked;
    const out = document.getElementById('aout'); out.innerHTML = '<div class="card"><div class="f mut">Explaining…</div></div>';
    let j;
    try { j = await (await fetch('/api/explain', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code, muse }) })).json(); }
    catch (e) { out.innerHTML = ''; window.toast && toast('explain failed: ' + e.message, 'bad'); return; }
    out.innerHTML = '';
    const c = E('div', 'card'); const h = E('h3', '', 'Explain code '); h.append(E('span', 'pill info', j.lines.length + ' annotated lines')); c.append(h);
    const f = E('div', 'f');
    if (j.registers.length) f.append(E('div', 'mut', 'Registers: ' + j.registers.map(r => r.name + ' p.' + r.page).join(' · ')));
    j.lines.forEach(l => { const d = E('div', 'xl'); d.append(E('span', 'ln', 'L' + l.line), E('code', '', l.code)); const u = E('ul');
      l.notes.forEach(n => u.append(E('li', '', n.what + '  [p.' + n.page + ']'))); d.append(u); f.append(d); });
    if (!j.lines.length) f.append(E('div', 'mut', 'No register accesses recognised.'));
    c.append(f); out.append(c);
    if (j.explanation || j.explanation_error) { const m = E('div', 'card'); m.append(E('h3', '', 'Muse walkthrough'));
      const p = E('div', 'f'); p.style.whiteSpace = 'pre-wrap'; p.textContent = j.explanation || j.explanation_error; m.append(p); out.append(m); }
  }
  function init() {
    const d = g('adrop'), inp = g('ainput');
    d.onclick = () => inp.click(); inp.onchange = () => { EX = null; take(inp.files); inp.value = ''; };
    d.ondragover = e => { e.preventDefault(); d.classList.add('over'); }; d.ondragleave = () => d.classList.remove('over');
    d.ondrop = e => { e.preventDefault(); d.classList.remove('over'); EX = null; take(e.dataTransfer.files); };
    g('acode').oninput = () => { EX = null; };
    document.addEventListener('paste', e => {
      if (g('p-aud').classList.contains('hide')) return;
      const im = [...e.clipboardData.items].filter(i => i.type.startsWith('image/')).map(i => i.getAsFile());
      if (im.length) { e.preventDefault(); IMGS.push(...im); chips(); }
    });
    examples().catch(() => {});
  }
  window.Audit = { explain, init, run, loadEx };
})();
