/* Платформанинг ўз тасдиқ/хабар ойнаси — браузернинг «Подтвердите действие на …» ойнаси ўрнига.
   LB.confirm(msg) -> Promise<bool>, LB.prompt(msg, def) -> Promise<string|null>, LB.alert(msg) -> Promise.
   window.alert/confirm/prompt ҳам алмаштирилган:
   - alert — ойна очади (кутмайди);
   - confirm — onsubmit/onclick="return confirm(...)" каби жойлар учун: ҳозир false қайтаради,
     «OK» босилса шу ҳаракат (submit, click, тугма) қайта ишга туширилади ва бу сафар confirm true қайтаради. */
(function () {
  var T = window.LBT || {ok: 'OK', cancel: 'Отмена'};
  var css = '#lbdlg{position:fixed;inset:0;z-index:10000;display:flex;align-items:center;justify-content:center;background:rgba(15,23,20,.38);padding:16px}' +
    '#lbdlg .lbd{background:var(--card,#fff);color:var(--txt,#1d2320);border:1px solid var(--line,#e3e3e3);border-radius:12px;box-shadow:0 18px 48px rgba(0,0,0,.22);width:100%;max-width:440px;padding:20px 22px 16px}' +
    '#lbdlg .lbm{white-space:pre-line;line-height:1.45;font-size:14.5px;max-height:60vh;overflow:auto;word-break:break-word}' +
    '#lbdlg input{width:100%;margin-top:12px;box-sizing:border-box}' +
    '#lbdlg .lbb{display:flex;justify-content:flex-end;gap:8px;margin-top:18px}' +
    '#lbdlg .lbb .btn{min-width:84px}';
  var st = document.createElement('style'); st.textContent = css; document.head.appendChild(st);

  var Q = [], open = false;
  function dlg(o) {
    return new Promise(function (res) { Q.push({o: o, res: res}); next(); });
  }
  function next() {
    if (open || !Q.length || !document.body) return;
    var it = Q.shift(), o = it.o; open = true;
    var prev = document.activeElement;
    var w = document.createElement('div'); w.id = 'lbdlg';
    w.setAttribute('role', 'dialog'); w.setAttribute('aria-modal', 'true');
    var box = document.createElement('div'); box.className = 'lbd';
    var m = document.createElement('div'); m.className = 'lbm'; m.textContent = o.msg == null ? '' : String(o.msg);
    box.appendChild(m);
    var inp = null;
    if (o.kind === 'prompt') { inp = document.createElement('input'); inp.value = o.def == null ? '' : String(o.def); box.appendChild(inp); }
    var bb = document.createElement('div'); bb.className = 'lbb';
    var bc = null;
    if (o.kind !== 'alert') { bc = document.createElement('button'); bc.type = 'button'; bc.className = 'btn'; bc.textContent = T.cancel; bb.appendChild(bc); }
    var bo = document.createElement('button'); bo.type = 'button'; bo.className = 'btn primary'; bo.textContent = T.ok; bb.appendChild(bo);
    box.appendChild(bb); w.appendChild(box); document.body.appendChild(w);
    function done(v) {
      document.removeEventListener('keydown', key, true); w.remove(); open = false;
      try { if (prev && prev.focus) prev.focus(); } catch (e) {}
      it.res(v); setTimeout(next, 0);
    }
    function ok() { done(o.kind === 'prompt' ? inp.value : (o.kind === 'confirm' ? true : undefined)); }
    function no() { done(o.kind === 'prompt' ? null : (o.kind === 'confirm' ? false : undefined)); }
    function key(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); no(); }
      else if (e.key === 'Enter' && !(document.activeElement === bc)) { e.preventDefault(); e.stopPropagation(); ok(); }
      else if (e.key === 'Tab') { // фокус ойнадан чиқмасин
        var f = [inp, bc, bo].filter(Boolean), i = f.indexOf(document.activeElement);
        e.preventDefault(); f[(i + (e.shiftKey ? f.length - 1 : 1)) % f.length].focus();
      } else { e.stopPropagation(); }
    }
    document.addEventListener('keydown', key, true);
    bo.onclick = ok; if (bc) bc.onclick = no;
    w.addEventListener('mousedown', function (e) { if (e.target === w && o.kind === 'alert') no(); });
    (inp || bo).focus(); if (inp) inp.select();
  }
  window.LB = window.LB || {};
  LB.confirm = function (msg) { return dlg({kind: 'confirm', msg: msg}); };
  LB.prompt = function (msg, def) { return dlg({kind: 'prompt', msg: msg, def: def}); };
  LB.alert = function (msg) { return dlg({kind: 'alert', msg: msg}); };
  document.addEventListener('DOMContentLoaded', next);

  window.alert = function (msg) { LB.alert(msg); };

  var PASS = 0;
  function replay(e) {
    var t = e && e.target;
    if (!t) return;
    PASS = 1;
    if (e.type === 'submit' && t.requestSubmit) {
      var sb = e.submitter;
      if (sb && sb.form === t) t.requestSubmit(sb); else t.requestSubmit();
    } else if (e.type === 'click' && t.click) {
      t.click();
    } else {
      var ev; try { ev = new e.constructor(e.type, e); } catch (x) { ev = new Event(e.type, {bubbles: true, cancelable: true}); }
      t.dispatchEvent(ev);
    }
    PASS = 0;
  }
  window.confirm = function (msg) {
    if (PASS) { PASS = 0; return true; }
    var e = window.event;
    LB.confirm(msg).then(function (ok) { if (ok) replay(e); });
    return false;
  };
})();
