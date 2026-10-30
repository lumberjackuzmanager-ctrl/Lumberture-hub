/* 31.10 (эгаси): автосохранение — ток ўчса, браузер ёпилса ёки платформа чиқариб юборса, ёзилган иш йўқолмасин.
   Форма ҳар ўзгаришдан кейин шу компьютер браузерига (localStorage) ёзилади; қайта очилганда «тиклайми?» деб сўралади.
   Сервердаги ҳужжатга ҳеч нарса ёзилмайди — сақлаш олдингидек фақат «Сохранить» / «Провести» билан.
   LBAS(form, {key, user, idle, t:{found, restore, drop}, prep(), restore(pairs) | server:true}) */
(function () {
  var DAYS = 7, PING = 4 * 60 * 1000;
  function ls(k, v) {
    try { if (v === undefined) return localStorage.getItem(k); if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, v) } catch (e) { return null }
  }
  // сессия: ёзаётган одамни 30 дақиқа ўтса ҳам чиқариб юбормаслик (фақат фаол бўлса)
  var lastSrv = Date.now(), lastPing = Date.now();
  function ping() {
    if (Date.now() - lastPing < PING) return;
    lastPing = Date.now();
    fetch('/api/ping', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) { if (r.ok) lastSrv = Date.now() }).catch(function () { });
  }
  function fmt(t) { var d = new Date(t), p = function (n) { return (n < 10 ? '0' : '') + n }; return p(d.getDate()) + '.' + p(d.getMonth() + 1) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) }
  window.LBAS = function (form, o) {
    if (!form || !o || !o.key) return;
    var KEY = 'lbas:' + (o.user || '') + ':' + o.key, timer = null, dirty = false, T = o.t || {};
    function ser() {
      if (o.prep) try { o.prep() } catch (e) { }
      var a = [];
      new FormData(form).forEach(function (v, k) { if (k !== 'csrf' && k !== 'act' && typeof v === 'string') a.push([k, v]) });
      return a;
    }
    var base = JSON.stringify(ser());
    // 31.10: кўриниб турсин — «✓ Автосохранено 00:35 (на этом компьютере)»
    var ind = document.createElement('div');
    ind.className = 'muted small lbas-ind';
    ind.style.cssText = 'position:fixed;right:14px;bottom:10px;z-index:60;background:var(--card,#fff);border:1px solid var(--line,#ddd);border-radius:8px;padding:4px 10px;box-shadow:0 2px 8px rgba(0,0,0,.08);display:none';
    document.body.appendChild(ind);
    function save() {
      dirty = false;
      var a = ser(), s = JSON.stringify(a);
      if (s === base) { ls(KEY, null); ind.style.display = 'none'; return }
      var ok = ls(KEY, JSON.stringify({ t: Date.now(), u: location.pathname + location.search, d: a })) !== null || ls(KEY) !== null;
      ind.textContent = ok ? ('✓ ' + (T.saved || 'Автосохранено') + ' ' + fmt(Date.now()).slice(6)) : (T.fail || 'Автосохранение недоступно');
      ind.style.display = '';
    }
    function touch() { dirty = true; clearTimeout(timer); timer = setTimeout(save, 700); ping() }
    form.addEventListener('input', touch);
    form.addEventListener('change', touch);
    document.addEventListener('keydown', ping, true);
    setInterval(function () { if (dirty) save() }, 10000);
    window.addEventListener('pagehide', function () { if (dirty) save() });
    form.addEventListener('submit', function () {
      clearTimeout(timer);
      // сессия тугаган бўлиши мумкин (узоқ турди) — нусха қолади, қайта киргандан кейин тикланади
      if (Date.now() - lastSrv < (o.idle || 1800) * 1000 - 60000) ls(KEY, null); else save();
    });
    var raw = ls(KEY), rec = null;
    try { rec = raw ? JSON.parse(raw) : null } catch (e) { rec = null }
    if (!rec || !rec.d || Date.now() - rec.t > DAYS * 864e5) { if (rec) ls(KEY, null); return }
    if (JSON.stringify(rec.d) === base || o.restored) return;      // тикланган саҳифа — қайта сўралмайди
    var b = document.createElement('div');
    b.className = 'flash warn lbas';
    b.style.cssText = 'display:flex;gap:10px;align-items:center;flex-wrap:wrap';
    b.innerHTML = '<span>' + (T.found || 'Найдена несохранённая работа от') + ' <b>' + fmt(rec.t) + '</b></span>' +
      '<button type="button" class="btn sm primary" data-a="r">' + (T.restore || 'Восстановить') + '</button>' +
      '<button type="button" class="btn sm" data-a="x">' + (T.drop || 'Удалить') + '</button>';
    form.parentNode.insertBefore(b, form);
    b.querySelector('[data-a=x]').onclick = function () { ls(KEY, null); b.remove() };
    b.querySelector('[data-a=r]').onclick = function () {
      if (o.server) {                         // сервер шу маълумот билан саҳифани қайта чизади (ҳеч нарса сақланмайди)
        var f = document.createElement('form'); f.method = 'post'; f.action = rec.u || location.href; f.style.display = 'none';
        function add(k, v) { var i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i) }
        rec.d.forEach(function (p) { add(p[0], p[1]) });
        var c = form.querySelector('[name=csrf]'); if (c) add('csrf', c.value);
        add('act', 'restore');
        document.body.appendChild(f); f.submit(); return;
      }
      if (o.restore) o.restore(rec.d); else {
        var by = {}; rec.d.forEach(function (p) { (by[p[0]] = by[p[0]] || []).push(p[1]) });
        Object.keys(by).forEach(function (k) {
          form.querySelectorAll('[name="' + k.replace(/"/g, '\\"') + '"]').forEach(function (el, i) {
            if (i >= by[k].length) return;
            if (el.type === 'checkbox' || el.type === 'radio') el.checked = by[k].indexOf(el.value) >= 0; else el.value = by[k][i];
          });
        });
      }
      form.dispatchEvent(new Event('input', { bubbles: true }));
      b.remove();
    };
  };
})();
