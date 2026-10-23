/* Товар танлаш ойнаси (erp_goods?pick=1) — алоҳида браузер ойнаси эмас, шу саҳифа ичида модал (iframe).
   window.open(url, 'gpick') чақирувлари ўзгармайди: шу ерда ушлаб олинади. Танланганда iframe {type:'pickGood'} юборади
   (саҳифадаги эски тингловчилар ишлайверади), ёпилганда — {type:'pickClose'}. */
(function () {
  var wrap = null, prev = null;
  var css = '#lbpick{position:fixed;inset:0;z-index:10050;background:rgba(15,23,20,.42);display:flex;align-items:center;justify-content:center;padding:2vh 2vw}' +
    '#lbpick .bx{background:var(--card,#fff);border-radius:12px;box-shadow:0 18px 48px rgba(0,0,0,.28);width:min(1200px,96vw);height:92vh;display:flex;flex-direction:column;overflow:hidden}' +
    '#lbpick .hd{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid var(--line,#e3e3e3);font-weight:600;font-size:14px}' +
    '#lbpick .hd button{border:0;background:none;font-size:20px;line-height:1;cursor:pointer;color:var(--muted,#777);padding:2px 6px}' +
    '#lbpick iframe{flex:1;border:0;width:100%;background:var(--bg,#f6f6f4)}';
  function close() {
    if (!wrap) return;
    wrap.remove(); wrap = null;
    document.removeEventListener('keydown', key, true);
    try { if (prev && prev.focus) prev.focus(); } catch (e) {}
  }
  function key(e) { if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); close(); } }
  function open(url) {
    close();
    prev = document.activeElement;
    if (!document.getElementById('lbpickcss')) { var st = document.createElement('style'); st.id = 'lbpickcss'; st.textContent = css; document.head.appendChild(st); }
    wrap = document.createElement('div'); wrap.id = 'lbpick';
    wrap.innerHTML = '<div class="bx"><div class="hd"><span></span><button type="button" title="Esc">×</button></div><iframe></iframe></div>';
    wrap.querySelector('.hd span').textContent = (/\/erp\/clients/.test(url) ? 'Выбор клиента' : (/\/erp\/suppliers/.test(url) ? 'Выбор поставщика' : 'Выбор товара')) + ' (Enter или двойной щелчок — выбрать, Esc — закрыть)';
    wrap.querySelector('.hd button').onclick = close;
    wrap.addEventListener('mousedown', function (e) { if (e.target === wrap) close(); });
    var fr = wrap.querySelector('iframe');
    fr.src = url;
    fr.addEventListener('load', function () { try { fr.contentWindow.focus(); } catch (e) {} });
    document.body.appendChild(wrap);
    document.addEventListener('keydown', key, true);
    return { focus: function () { try { fr.contentWindow.focus(); } catch (e) {} }, close: close, closed: false };
  }
  var nativeOpen = window.open;
  window.open = function (url, name, feat) {
    if (name === 'gpick' && url) return open(url);
    return nativeOpen.apply(window, arguments);
  };
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data || !wrap) return;
    if (e.data.type === 'pickGood' || e.data.type === 'pickClient' || e.data.type === 'pickClose') setTimeout(close, 0);
  });
  window.LB = window.LB || {}; LB.pickOpen = open; LB.pickClose = close;

  /* Мижоз/поставщик майдони: <input data-cpick="/erp/clients?pick=1" data-cpick-hid="cid">
     F4, икки марта босиш ёки «…» — справочник шу ойна ичида; танланганда майдон ва яширин ext тўлади, 'lbpicked' ҳодисаси. */
  var CT = null;
  function copen(inp) { CT = inp; open(inp.getAttribute('data-cpick')); }
  function wire(inp) {
    if (inp.dataset.cpw) return; inp.dataset.cpw = '1';
    var b = document.createElement('button'); b.type = 'button'; b.className = 'gpick'; b.tabIndex = -1; b.textContent = '…'; b.title = 'Справочник (F4)';
    b.style.cssText = 'position:absolute;right:2px;top:50%;transform:translateY(-50%);border:0;background:none;cursor:pointer;color:var(--muted,#888);padding:0 6px;font-size:15px;line-height:1';
    var par = inp.parentNode; if (getComputedStyle(par).position === 'static') par.style.position = 'relative';
    inp.style.paddingRight = '28px';
    par.insertBefore(b, inp.nextSibling);
    b.addEventListener('mousedown', function (e) { e.preventDefault(); });
    b.addEventListener('click', function () { inp.focus(); copen(inp); });
    inp.addEventListener('dblclick', function () { copen(inp); });
  }
  function wireAll() { document.querySelectorAll('input[data-cpick]').forEach(wire); }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', wireAll); else wireAll();
  window.addEventListener('keydown', function (e) {
    var a = document.activeElement;
    if (e.key === 'F4' && a && a.hasAttribute && a.hasAttribute('data-cpick') && !wrap) { e.preventDefault(); e.stopImmediatePropagation(); copen(a); }
  }, true);
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data || e.data.type !== 'pickClient' || !CT) return;
    var x = e.data.x || {}, inp = CT; CT = null;
    inp.value = x.name || ''; inp.title = x.name || '';
    var h = document.getElementById(inp.getAttribute('data-cpick-hid') || ''); if (h) { h.value = x.ext || ''; h.dispatchEvent(new Event('change', {bubbles: true})); }
    var ul = inp.parentNode.querySelector('ul'); if (ul) ul.hidden = true;
    inp.dispatchEvent(new CustomEvent('lbpicked', {detail: x, bubbles: true}));
    try { inp.focus(); } catch (e2) {}
  });
})();
