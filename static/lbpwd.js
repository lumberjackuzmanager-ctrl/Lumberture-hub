/* Пароль майдонлари: «кўз» тугмаси (кўрсатиш/яшириш), ёзилаётган тил (Кир/Lat) ва Caps Lock огоҳлантириши. Ҳамма саҳифада. */
(function () {
  var L = (document.documentElement.lang || 'ru').slice(0, 2);
  var T = {ru: {show: 'Показать пароль', hide: 'Скрыть пароль', caps: 'Caps Lock'}, uz: {show: 'Parolni ko‘rsatish', hide: 'Parolni yashirish', caps: 'Caps Lock'},
           en: {show: 'Show password', hide: 'Hide password', caps: 'Caps Lock'}}[L] || null;
  T = T || {show: 'Показать пароль', hide: 'Скрыть пароль', caps: 'Caps Lock'};
  var EYE = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>';
  var EYEX = '<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.5 10.5 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.1M6.6 6.6C3.8 8.5 2 12 2 12s3.6 7 10 7c1.6 0 3-.4 4.3-1"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>';
  var css = '.pwbox{position:relative;display:block}.pwbox>input{padding-right:84px!important;width:100%}' +
    '.pwbox .pweye{position:absolute;right:6px;top:50%;transform:translateY(-50%);border:0;background:transparent;color:var(--muted,#6b7b76);cursor:pointer;padding:5px;border-radius:6px;line-height:0}' +
    '.pwbox .pweye:hover{color:var(--g,#3E643C);background:rgba(62,100,60,.08)}' +
    '.pwbox .pwkb{position:absolute;right:38px;top:50%;transform:translateY(-50%);font-size:11px;font-weight:600;letter-spacing:.03em;color:var(--muted,#6b7b76);background:var(--bg2,#eef0ee);border-radius:5px;padding:2px 6px;pointer-events:none}' +
    '.pwbox .pwkb.caps{color:#fff;background:#b3261e}.pwbox .pwkb[hidden]{display:none}';
  function ensureCss() { if (document.getElementById('lbpwcss')) return; var s = document.createElement('style'); s.id = 'lbpwcss'; s.textContent = css; document.head.appendChild(s); }
  function hidden(i) { return i.type === 'password' || i.classList.contains('pwd'); }
  function setHidden(i, h) {
    if (i.dataset.pwmode === 'class') i.classList.toggle('pwd', h); else i.type = h ? 'password' : 'text';
  }
  function kind(ch) { return /[А-Яа-яЁёЎўҚқҒғҲҳ]/.test(ch) ? 'Кир' : (/[A-Za-z]/.test(ch) ? 'Lat' : ''); }
  function wire(i) {
    if (i.dataset.pwwired) return; i.dataset.pwwired = '1';
    i.dataset.pwmode = i.type === 'password' ? 'type' : 'class';
    ensureCss();
    var box = document.createElement('span'); box.className = 'pwbox';
    i.parentNode.insertBefore(box, i); box.appendChild(i);
    var b = document.createElement('button'); b.type = 'button'; b.className = 'pweye'; b.tabIndex = -1; b.innerHTML = EYE; b.title = T.show; b.setAttribute('aria-label', T.show);
    var k = document.createElement('span'); k.className = 'pwkb'; k.hidden = true;
    box.appendChild(k); box.appendChild(b);
    b.addEventListener('mousedown', function (e) { e.preventDefault(); });
    b.addEventListener('click', function () {
      var h = !hidden(i); setHidden(i, h);
      b.innerHTML = h ? EYE : EYEX; b.title = h ? T.show : T.hide; b.setAttribute('aria-label', b.title); i.focus();
    });
    var last = '';
    function upd(e) {
      var caps = e && e.getModifierState && e.getModifierState('CapsLock');
      if (e && e.key && e.key.length === 1 && kind(e.key)) last = kind(e.key);
      if (!last && i.value) { for (var p = i.value.length - 1; p >= 0 && !last; p--) last = kind(i.value[p]); }
      var kk = i.value ? last : '';
      if (caps) { k.textContent = T.caps; k.className = 'pwkb caps'; k.hidden = false; }
      else if (kk && i.value) { k.textContent = kk; k.className = 'pwkb'; k.hidden = false; }
      else k.hidden = true;
    }
    i.addEventListener('keydown', upd); i.addEventListener('keyup', upd); i.addEventListener('input', function () { upd(); });
    i.addEventListener('blur', function () { k.hidden = true; });
    var f = i.form; if (f) f.addEventListener('submit', function () { setHidden(i, true); });
  }
  function scan(root) { (root || document).querySelectorAll('input[type=password],input.pwd').forEach(wire); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { scan(); }); else scan();
  new MutationObserver(function (ms) { ms.forEach(function (m) { m.addedNodes.forEach(function (n) { if (n.querySelectorAll) scan(n.parentNode || n); }); }); })
    .observe(document.documentElement, {childList: true, subtree: true});
})();
