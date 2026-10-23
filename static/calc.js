/* 1С'дагидек калькулятор: сон катагида F4 — калькулятор чиқади, катакдаги сон билан ҳисоблаб, OK (Enter) — натижа катакка.
   Катакка «45000/12», «120*3,5», «1500+10%» каби ёзса ҳам бўлади — катакдан чиққанда ҳисобланади. */
(function () {
  function norm(s) { return String(s || '').replace(/[\s  ]/g, '').replace(/,/g, '.').replace(/−/g, '-').replace(/^=/, '').replace(/[×х]/g, '*').replace(/[÷:]/g, '/'); }
  // хавфсиз ҳисоблаш (eval йўқ): + - * / ( ) ва % (a+10% = a*1.1, a*10% = a*0.1)
  function calc(src) {
    var s = norm(src), i = 0;
    if (!s || /[^0-9.+\-*/()%]/.test(s)) return NaN;
    function num() { var m = /^\d*\.?\d+(?:e[+-]?\d+)?|^\d+\./.exec(s.slice(i)); if (!m) throw 0; i += m[0].length; return parseFloat(m[0]); }
    function atom() {
      var c = s[i];
      if (c === '-') { i++; var a = atom(); return { v: -a.v, p: a.p }; }
      if (c === '+') { i++; return atom(); }
      if (c === '(') { i++; var v = add(); if (s[i] !== ')') throw 0; i++; return pc({ v: v, p: false }); }
      return pc({ v: num(), p: false });
    }
    function pc(a) { if (s[i] === '%') { i++; return { v: a.v, p: true }; } return a; }
    function mul() {
      var a = atom(), v = a.p ? a.v / 100 : a.v, first = a;
      while (s[i] === '*' || s[i] === '/') {
        var op = s[i++], b = atom(), bv = b.p ? b.v / 100 : b.v;
        v = op === '*' ? v * bv : v / bv; first = null;
      }
      return { v: v, p: !!(first && first.p), raw: first ? first.v : v };
    }
    function add() {
      var a = mul(), v = a.p ? a.raw / 100 : a.v;
      while (s[i] === '+' || s[i] === '-') {
        var op = s[i++], b = mul();
        var bv = b.p ? v * b.raw / 100 : b.v;
        v = op === '+' ? v + bv : v - bv;
      }
      return v;
    }
    try { var r = add(); if (i !== s.length || !isFinite(r)) return NaN; return Math.round(r * 1e6) / 1e6; } catch (e) { return NaN; }
  }
  window.lbCalc = calc;
  function isExpr(v) { var s = norm(v); return /\d/.test(s) && /[+*/%()]|\d-/.test(s); }
  function numInput(el) { return el && el.tagName === 'INPUT' && el.getAttribute('inputmode') === 'decimal' && !el.readOnly && !el.disabled; }
  function fire(el) { el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true })); }
  function fmt(v) { return String(v); }

  // катакдан чиққанда ифодани ҳисоблаш (кассада формула сақланади — у ерда тегилмайди: data-cur / data-keepfx)
  document.addEventListener('blur', function (e) {
    var el = e.target;
    if (!numInput(el) || el.hasAttribute('data-cur') || el.closest('[data-keepfx]') || !isExpr(el.value)) return;
    var v = calc(el.value);
    if (!isNaN(v)) { el.value = fmt(v); fire(el); }
  }, true);

  var box = null, tgt = null, disp = null, expr = '';
  function show() { disp.textContent = expr || '0'; }
  function close(ok) {
    if (!box) return;
    if (ok && tgt) { var v = calc(expr); if (!isNaN(v)) { tgt.value = fmt(v); fire(tgt); } }
    box.remove(); box = null; if (tgt) tgt.focus(); tgt = null;
  }
  function press(k) {
    if (k === 'C') expr = '';
    else if (k === '<-') expr = expr.slice(0, -1);
    else if (k === '=') { var v = calc(expr); expr = isNaN(v) ? expr : fmt(v); }
    else if (k === '+/-') { var v2 = calc(expr); if (!isNaN(v2)) expr = fmt(-v2); }
    else if (k === 'OK') { close(true); return; }
    else expr += k;
    show();
  }
  function open(el) {
    close(false);
    tgt = el; var v = calc(el.value); expr = isNaN(v) ? '' : fmt(v);
    box = document.createElement('div');
    box.className = 'lbcalc';
    box.innerHTML = '<div class="lbd"></div><div class="lbk">' +
      ['7', '8', '9', '/', 'C', '4', '5', '6', '*', '<-', '1', '2', '3', '-', '%', '0', '00', '.', '+', '+/-', '(', ')', '=', 'OK'].map(function (k) {
        return '<button type="button" data-k="' + k + '"' + (k === 'OK' ? ' class="ok" style="grid-column:span 2"' : '') + '>' + (k === '<-' ? '←' : k) + '</button>';
      }).join('') + '</div>';
    document.body.appendChild(box);
    disp = box.querySelector('.lbd'); show();
    var r = el.getBoundingClientRect(), w = 230;
    box.style.left = Math.max(4, Math.min(window.innerWidth - w - 4, r.left + window.scrollX)) + 'px';
    box.style.top = (r.bottom + window.scrollY + 2) + 'px';
    box.addEventListener('mousedown', function (e) { e.preventDefault(); });
    box.addEventListener('click', function (e) { var b = e.target.closest('button'); if (b) press(b.dataset.k); });
  }
  document.addEventListener('keydown', function (e) {
    if (box) {
      var k = e.key;
      e.stopPropagation();
      if (k === 'Escape') { e.preventDefault(); close(false); return; }
      if (k === 'Enter') { e.preventDefault(); press('OK'); return; }
      if (k === 'Backspace') { e.preventDefault(); press('<-'); return; }
      if (k === 'Delete') { e.preventDefault(); press('C'); return; }
      if (k === ',') { e.preventDefault(); press('.'); return; }
      if (/^[0-9.+\-*/%()=]$/.test(k)) { e.preventDefault(); press(k); return; }
      if (k === 'F4') { e.preventDefault(); close(false); return; }
      return;
    }
    if (e.key === 'F4' && numInput(e.target)) { e.preventDefault(); e.stopPropagation(); open(e.target); }
  }, true);
  document.addEventListener('mousedown', function (e) { if (box && !box.contains(e.target)) close(false); });
  var st = document.createElement('style');
  st.textContent = '.lbcalc{position:absolute;z-index:200;width:230px;background:var(--card,#fff);border:1px solid var(--line,#bbb);border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.25);padding:8px}' +
    '.lbcalc .lbd{background:#111;color:#7CFC00;font:600 18px monospace;text-align:right;padding:6px 8px;border-radius:4px;margin-bottom:6px;overflow:hidden;white-space:nowrap}' +
    '.lbcalc .lbk{display:grid;grid-template-columns:repeat(5,1fr);gap:4px}.lbcalc button{padding:7px 0;font:600 14px system-ui;border:1px solid var(--line,#ccc);border-radius:4px;background:var(--bg,#f7f7f7);color:inherit;cursor:pointer}' +
    '.lbcalc button.ok,.lbcalc button[data-k=C]{color:#b22}.lbcalc button:hover{filter:brightness(.95)}';
  document.head.appendChild(st);
})();
/* Сумма катаклари (Excel'дагидек): хоналар бўш жой билан ажратилади «100 000», доллар — вергулдан кейин 2 хона «700,00».
   Белги: data-cur="uzs|usd" (касса) ёки data-money="uzs|usd|" (бўш — валюта номаълум, каср ихтиёрий). Формула («=», +, *, /) — тегилмайди. */
(function () {
  var SP = /[\s  ]/g;
  function cur(el) { if (!el || el.tagName !== 'INPUT') return null; var c = el.getAttribute('data-money'); if (c === null) c = el.getAttribute('data-cur'); return c === null ? null : c; }
  function parts(v) {
    var s = String(v || '').replace(SP, '').replace(/−/g, '-');
    var m = /^(-?)(\d*)(?:[.,](\d*))?$/.exec(s);
    if (!m || (!m[2] && m[3] === undefined)) return null;
    return { neg: m[1], i: m[2], d: m[3] };
  }
  function grp(i) { i = i.replace(/^0+(?=\d)/, ''); return i.replace(/\B(?=(\d{3})+(?!\d))/g, ' '); }
  function fmt(v, c, fin) {
    var p = parts(v); if (!p) return null;
    var i = grp(p.i || (p.d !== undefined ? '0' : '')), d = p.d;
    if (fin) { if (c === 'usd') d = (d || '').length >= 2 ? d : ((d || '') + '00').slice(0, 2); else if (d === '') d = undefined; }
    return p.neg + i + (d !== undefined ? ',' + d : '');
  }
  window.lbMoney = function (v, c) { var r = fmt(v, c || '', true); return r === null ? String(v == null ? '' : v) : r; };
  function apply(el, fin) {
    var c = cur(el); if (c === null) return;
    var v = el.value, f = fmt(v, c, fin); if (f === null || f === v) return;
    var act = document.activeElement === el && !fin, k = 0;
    if (act) { var pos = el.selectionStart || 0; k = v.slice(0, pos).replace(SP, '').length; }
    el.value = f;
    if (act) { var j = 0, n = 0; while (j < f.length && n < k) { if (f[j] !== ' ') n++; j++; } try { el.setSelectionRange(j, j); } catch (e) {} }
  }
  function all(root) { (root || document).querySelectorAll('input[data-money],input[data-cur]').forEach(function (el) { if (el !== document.activeElement) apply(el, true); }); }
  window.lbMoneyAll = all;
  document.addEventListener('input', function (e) { apply(e.target, false); });
  document.addEventListener('blur', function (e) { apply(e.target, true); }, true);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { all(); }); else all();
  if (window.MutationObserver) new MutationObserver(function (ms) { ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.nodeType === 1) { if (cur(n) !== null) apply(n, true); else if (n.querySelectorAll) all(n); } }); }); })
    .observe(document.documentElement, { childList: true, subtree: true });
  var st = document.createElement('style'); st.textContent = 'input[data-money],input[data-cur]{text-align:right;font-variant-numeric:tabular-nums}'; document.head.appendChild(st);
})();
