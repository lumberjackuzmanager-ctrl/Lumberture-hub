/* Платформанинг «Печать» ойнаси — браузернинг чоп этиш ойнаси ўрнига (window.print, Ctrl+P, «Печать» тугмалари).
   LB.print()        — ҳозирги саҳифани (экрандаги ҳолати билан) чоп этиш
   LB.print(url)     — бошқа саҳифа ёки PDF'ни (масалан, бланкдаги ҳужжат) чоп этиш */
(function () {
  if (window.__LB_HEADLESS) { window.print = function () {}; return; }
  var LB = window.LB = window.LB || {};
  var lang = (document.documentElement.lang || 'ru').slice(0, 2);
  var T = {
    ru: {title: 'Печать', printer: 'Принтер', pages: 'Страницы', all: 'Все', range: 'Выбрать', copies: 'Копии', print: 'Печать', pdf: 'Скачать PDF',
         cancel: 'Отмена', prep: 'Готовим документ…', sending: 'Отправляем на принтер…', done: 'Отправлено на принтер', of: 'Листов:', noprn: 'Принтеры не найдены — можно скачать PDF', err: 'Не получилось', browser: 'Печать через браузер', hint: 'например: 1-3, 5'},
    uz: {title: 'Chop etish', printer: 'Printer', pages: 'Sahifalar', all: 'Hammasi', range: 'Tanlash', copies: 'Nusxa', print: 'Chop etish', pdf: 'PDF yuklab olish',
         cancel: 'Bekor qilish', prep: 'Hujjat tayyorlanmoqda…', sending: 'Printerga yuborilmoqda…', done: 'Printerga yuborildi', of: 'Varaq:', noprn: 'Printer topilmadi — PDF yuklab olish mumkin', err: 'Bo‘lmadi', browser: 'Brauzer orqali chop etish', hint: 'masalan: 1-3, 5'},
    en: {title: 'Print', printer: 'Printer', pages: 'Pages', all: 'All', range: 'Custom', copies: 'Copies', print: 'Print', pdf: 'Download PDF',
         cancel: 'Cancel', prep: 'Preparing document…', sending: 'Sending to printer…', done: 'Sent to printer', of: 'Sheets:', noprn: 'No printers found — you can download the PDF', err: 'Failed', browser: 'Print via browser', hint: 'e.g. 1-3, 5'}
  };
  T = T[lang] || T.ru;
  var nativePrint = window.print.bind(window);
  var css = '#lbpr{position:fixed;inset:0;z-index:99999;background:rgba(15,20,18,.55);display:flex;align-items:center;justify-content:center;font:14px/1.4 Inter,"Segoe UI",system-ui,sans-serif;color:#1f2a27}' +
    '#lbpr .w{background:#fff;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.35);width:min(1100px,96vw);height:min(760px,92vh);display:flex;overflow:hidden}' +
    '#lbpr .pv{flex:1;background:#eef0ee;overflow:auto;padding:22px;display:flex;flex-direction:column;align-items:center;gap:16px}' +
    '#lbpr .pv img{width:min(560px,100%);background:#fff;box-shadow:0 2px 10px rgba(0,0,0,.18);border-radius:2px}' +
    '#lbpr .pv .ld{margin:auto;color:#5d6b67;display:flex;align-items:center;gap:10px}' +
    '#lbpr .sp{width:18px;height:18px;border:2.5px solid #cfd8d5;border-top-color:#3E643C;border-radius:50%;animation:lbsp .8s linear infinite}@keyframes lbsp{to{transform:rotate(360deg)}}' +
    '#lbpr .sd{width:330px;padding:22px 22px 18px;display:flex;flex-direction:column;gap:14px;border-left:1px solid #e3e7e5}' +
    '#lbpr h3{margin:0;font-size:19px;font-weight:650;display:flex;justify-content:space-between;align-items:baseline}#lbpr h3 small{font-size:12.5px;color:#6b7b76;font-weight:500}' +
    '#lbpr label{display:flex;flex-direction:column;gap:5px;font-size:12.5px;color:#5d6b67;font-weight:500}' +
    '#lbpr select,#lbpr input{font:inherit;font-size:14px;padding:8px 10px;border:1px solid #cfd8d5;border-radius:8px;background:#fff;color:#1f2a27;outline:none}' +
    '#lbpr select:focus,#lbpr input:focus{border-color:#3E643C;box-shadow:0 0 0 3px rgba(62,100,60,.15)}' +
    '#lbpr .seg{display:flex;background:#eef0ee;border-radius:8px;padding:3px;gap:3px}#lbpr .seg button{flex:1;border:0;background:transparent;padding:6px;border-radius:6px;font:inherit;font-size:13px;cursor:pointer;color:#5d6b67}' +
    '#lbpr .seg button.on{background:#fff;color:#1f2a27;font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.12)}' +
    '#lbpr .bt{margin-top:auto;display:flex;flex-direction:column;gap:8px}#lbpr .b{border:1px solid #cfd8d5;background:#fff;border-radius:8px;padding:9px 14px;font:inherit;font-weight:550;cursor:pointer;color:#1f2a27;text-align:center;text-decoration:none}' +
    '#lbpr .b.p{background:#3E643C;border-color:#3E643C;color:#fff}#lbpr .b:disabled{opacity:.5;cursor:default}#lbpr .row{display:flex;gap:8px}#lbpr .row .b{flex:1}' +
    '#lbpr .st{font-size:12.5px;min-height:18px;color:#5d6b67}#lbpr .st.ok{color:#2e7d32}#lbpr .st.er{color:#b3261e}#lbpr .lnk{font-size:12px;color:#6b7b76;background:none;border:0;cursor:pointer;text-decoration:underline;padding:0;align-self:center}' +
    '@media print{#lbpr{display:none!important}}';

  function csrf() {
    if (window.LBCSRF) return window.LBCSRF;
    var i = document.querySelector('input[name=csrf]'); return i ? i.value : '';
  }
  function post(u, d) {
    return fetch(u, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf()}, body: JSON.stringify(d)})
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); });
  }
  function snapshot() {
    var d = document.documentElement.cloneNode(true);
    d.querySelectorAll('#lbpr,#lbdlg,script').forEach(function (x) { x.remove(); });
    // киритилган қийматлар (input/textarea/select) клонга ўтмайди — қўлда кўчирамиз
    var src = document.querySelectorAll('input,textarea,select'), dst = d.querySelectorAll('input,textarea,select');
    for (var i = 0; i < src.length && i < dst.length; i++) {
      var s = src[i], t = dst[i];
      if (s.tagName === 'TEXTAREA') t.textContent = s.value;
      else if (s.tagName === 'SELECT') { [].forEach.call(t.options, function (o, k) { o.selected = s.options[k] && s.options[k].selected; if (o.selected) o.setAttribute('selected', ''); }); }
      else if (s.type === 'checkbox' || s.type === 'radio') { if (s.checked) t.setAttribute('checked', ''); else t.removeAttribute('checked'); }
      else t.setAttribute('value', s.value);
    }
    return '<!doctype html>' + d.outerHTML;
  }

  var open = false;
  LB.print = function (url) {
    if (open) return; open = true;
    if (!document.getElementById('lbprcss')) { var st = document.createElement('style'); st.id = 'lbprcss'; st.textContent = css; document.head.appendChild(st); }
    var o = document.createElement('div'); o.id = 'lbpr';
    o.innerHTML = '<div class="w" role="dialog" aria-modal="true"><div class="pv"><div class="ld"><span class="sp"></span>' + T.prep + '</div></div>' +
      '<div class="sd"><h3>' + T.title + ' <small class="cnt"></small></h3>' +
      '<label>' + T.printer + '<select class="prn" disabled></select></label>' +
      '<label>' + T.pages + '<div class="seg"><button type="button" class="on" data-m="all">' + T.all + '</button><button type="button" data-m="r">' + T.range + '</button></div>' +
      '<input class="rng" placeholder="' + T.hint + '" style="display:none"></label>' +
      '<label>' + T.copies + '<input class="cp" type="number" min="1" max="50" value="1" style="width:90px"></label>' +
      '<div class="st"></div>' +
      '<div class="bt"><button type="button" class="b p go" disabled>' + T.print + '</button><div class="row"><a class="b dl" style="pointer-events:none;opacity:.5">' + T.pdf + '</a><button type="button" class="b cx">' + T.cancel + '</button></div>' +
      '<button type="button" class="lnk nat">' + T.browser + '</button></div></div></div>';
    document.body.appendChild(o);
    var $ = function (s) { return o.querySelector(s); }, job = null, mode = 'all';
    function close() { o.remove(); open = false; document.removeEventListener('keydown', key, true); }
    function key(e) {
      if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); }
      else if (e.key === 'Enter' && !$('.go').disabled && e.target.tagName !== 'BUTTON') { e.preventDefault(); $('.go').click(); }
      else if ((e.ctrlKey || e.metaKey) && e.code === 'KeyP') { e.preventDefault(); e.stopImmediatePropagation(); if (!$('.go').disabled) $('.go').click(); }
    }
    document.addEventListener('keydown', key, true);
    o.addEventListener('mousedown', function (e) { if (e.target === o) close(); });
    $('.cx').onclick = close;
    $('.nat').onclick = function () { close(); if (url) window.open(url, '_blank'); else nativePrint(); };
    [].forEach.call(o.querySelectorAll('.seg button'), function (b) {
      b.onclick = function () { mode = b.dataset.m; [].forEach.call(o.querySelectorAll('.seg button'), function (x) { x.classList.toggle('on', x === b); }); $('.rng').style.display = mode === 'r' ? '' : 'none'; if (mode === 'r') $('.rng').focus(); };
    });
    function status(t, c) { var s = $('.st'); s.textContent = t || ''; s.className = 'st' + (c ? ' ' + c : ''); }
    var req = url ? {url: url, title: document.title} : {html: snapshot(), base: location.pathname.replace(/[^/]*$/, ''), title: document.title};
    post('/api/print/prepare', req).then(function (j) {
      job = j;
      $('.cnt').textContent = T.of + ' ' + j.pages;
      var pv = $('.pv'); pv.innerHTML = '';
      for (var i = 1; i <= j.pages; i++) { var im = new Image(); im.loading = 'lazy'; im.src = '/api/print/' + j.id + '/p/' + i + '.png'; im.alt = ''; pv.appendChild(im); }
      var dl = $('.dl'); dl.href = '/api/print/' + j.id + '.pdf?dl=1'; dl.style.pointerEvents = ''; dl.style.opacity = '';
      var sel = $('.prn');
      if (j.can_print) {
        var last = ''; try { last = localStorage.getItem('lb_printer') || ''; } catch (e) {}
        j.printers.forEach(function (p) { var op = document.createElement('option'); op.value = op.textContent = p; if (p === (j.printers.indexOf(last) >= 0 ? last : j.default)) op.selected = true; sel.appendChild(op); });
        sel.disabled = false; $('.go').disabled = false; $('.go').focus();
      } else { var op = document.createElement('option'); op.textContent = '—'; sel.appendChild(op); status(T.noprn); }
    }).catch(function (e) { $('.pv').innerHTML = '<div class="ld">' + T.err + '</div>'; status(T.err + ': ' + e.message, 'er'); });
    $('.go').onclick = function () {
      if (!job) return;
      var b = this; b.disabled = true; status(T.sending);
      var pr = $('.prn').value; try { localStorage.setItem('lb_printer', pr); } catch (e) {}
      post('/api/print/send', {id: job.id, printer: pr, copies: $('.cp').value, pages: mode === 'r' ? $('.rng').value : ''})
        .then(function () { status(T.done, 'ok'); setTimeout(close, 900); })
        .catch(function (e) { status(T.err + ': ' + e.message, 'er'); b.disabled = false; });
    };
  };
  window.print = function () { LB.print(); };
  window.addEventListener('keydown', function (e) {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.code !== 'KeyP' || document.getElementById('lbdlg') || document.getElementById('lbpr')) return;
    e.preventDefault(); e.stopImmediatePropagation();
    var pb = document.querySelector('button[name=act][value=post_print],button[name=act][value=save_print]');
    if (pb) { if (!pb.disabled) pb.click(); return; }
    var cp = document.querySelector('a[data-ctrlp]');                 // ҳужжат саҳифаси: Ctrl+P — печать созламаси саҳифаси (масштаб, ширина…)
    if (cp) { location.href = cp.getAttribute('href'); return; }
    var b = document.querySelector('[data-print]');
    LB.print(b && b.getAttribute('href') ? b.getAttribute('href') : undefined);
  }, true);
  document.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a[data-print]');
    if (!a || e.ctrlKey || e.metaKey || e.shiftKey || e.button) return;
    e.preventDefault(); LB.print(a.getAttribute('href'));
  });
})();
