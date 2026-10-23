/* Lumberture Hub: файл юклаб олишдан олдин платформа ойнаси.
   Ҳавола файл берса (Content-Disposition: attachment) — ойна: номи, ҳажми, «Выбрать папку и сохранить» / «В Загрузки» / «Отмена».
   «Выбрать папку» — браузер showSaveFilePicker'и (ярлык янгиланган бўлса LAN'да ҳам ишлайди). Бўлмаса — «Загрузки»га. */
(function () {
  var LB = window.LB = window.LB || {};
  var T = window.LBDL || {};
  function t(k, d) { return T[k] || d; }
  var CAND = /\.(xlsx|xls|pdf|bat|js|db|csv|zip|docx|txt)(\?|$)|[?&](dl|download|xlsx|export)=|\/(backup|export|download)(\/|\?|$)|\/month\/pdf|\/raspil\/report|\/setup\//i;

  function isCand(a) {
    if (!a || !a.href || a.hasAttribute('data-nodl')) return false;
    if (a.hasAttribute('download') || a.hasAttribute('data-dl')) return true;
    if (a.target && a.target !== '_self') return false;
    var u; try { u = new URL(a.href, location.href); } catch (e) { return false; }
    if (u.origin !== location.origin) return false;
    if (u.pathname.indexOf('/static/') === 0) return false;
    return CAND.test(u.pathname + u.search);
  }

  function fname(resp, a, u) {
    var cd = resp.headers.get('Content-Disposition') || '', m;
    if ((m = /filename\*=UTF-8''([^;]+)/i.exec(cd))) { try { return decodeURIComponent(m[1].replace(/"/g, '')); } catch (e) {} }
    if ((m = /filename="?([^";]+)"?/i.exec(cd))) return m[1];
    if (a && a.getAttribute('download')) return a.getAttribute('download');
    var p = u.pathname.split('/').pop();
    return p || 'file';
  }
  function size(n) {
    if (n < 1024) return n + ' Б';
    if (n < 1048576) return Math.round(n / 1024) + ' КБ';
    return (n / 1048576).toFixed(1).replace('.', ',') + ' МБ';
  }
  function plain(blob, name) {
    var u = URL.createObjectURL(blob), x = document.createElement('a');
    x.href = u; x.download = name; x.setAttribute('data-nodl', '1'); x.style.display = 'none';
    document.body.appendChild(x); x.click();
    setTimeout(function () { x.remove(); URL.revokeObjectURL(u); }, 4000);
  }
  function toast(msg) {
    var d = document.createElement('div');
    d.style.cssText = 'position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:10001;background:#1d2320;color:#fff;padding:10px 16px;border-radius:10px;font-size:14px;box-shadow:0 8px 24px rgba(0,0,0,.25);max-width:90vw';
    d.textContent = msg; document.body.appendChild(d); setTimeout(function () { d.remove(); }, 3500);
  }

  function ask(name, blob) {
    return new Promise(function (res) {
      var picker = typeof window.showSaveFilePicker === 'function';
      var w = document.createElement('div'); w.id = 'lbdlg'; w.style.zIndex = '100000'; w.setAttribute('role', 'dialog'); w.setAttribute('aria-modal', 'true');
      var box = document.createElement('div'); box.className = 'lbd'; box.style.maxWidth = '480px';
      var h = document.createElement('div'); h.style.cssText = 'font-weight:700;font-size:16px;margin-bottom:10px'; h.textContent = t('title', 'Сохранить файл');
      var f = document.createElement('div'); f.style.cssText = 'display:flex;gap:10px;align-items:center;border:1px solid var(--line,#e3e3e3);border-radius:10px;padding:10px 12px';
      var ic = document.createElement('div'); ic.style.cssText = 'flex:none;width:34px;height:34px;border-radius:8px;background:var(--g,#3E643C);color:#fff;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700';
      ic.textContent = (name.split('.').pop() || '').slice(0, 4).toUpperCase();
      var nm = document.createElement('div'); nm.style.cssText = 'min-width:0;word-break:break-all';
      nm.innerHTML = '<b></b><div class="muted small"></div>';
      nm.firstChild.textContent = name; nm.lastChild.textContent = size(blob.size);
      f.appendChild(ic); f.appendChild(nm); box.appendChild(h); box.appendChild(f);
      var bb = document.createElement('div'); bb.className = 'lbb'; bb.style.flexWrap = 'wrap';
      function btn(txt, cls, v) { var b = document.createElement('button'); b.type = 'button'; b.className = 'btn' + (cls ? ' ' + cls : ''); b.textContent = txt; b.onclick = function () { done(v); }; bb.appendChild(b); return b; }
      btn(t('cancel', 'Отмена'), '', null);
      var bDl = btn(picker ? t('dl', 'В «Загрузки»') : t('save', 'Сохранить'), picker ? '' : 'primary', 'dl');
      var bPk = picker ? btn(t('pick', 'Выбрать папку и сохранить'), 'primary', 'pick') : null;
      box.appendChild(bb); w.appendChild(box); document.body.appendChild(w);
      function key(e) {
        if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); done(null); }
        else if (e.key === 'Enter') { e.preventDefault(); e.stopImmediatePropagation(); var ae = document.activeElement; if (ae && ae.parentNode === bb) ae.click(); else done(picker ? 'pick' : 'dl'); }
        else if (e.key !== 'Tab') e.stopImmediatePropagation();
      }
      function done(v) { window.removeEventListener('keydown', key, true); w.remove(); res(v); }
      window.addEventListener('keydown', key, true);
      w.addEventListener('mousedown', function (e) { if (e.target === w) done(null); });
      (bPk || bDl).focus();
    });
  }

  function save(name, blob) {
    return ask(name, blob).then(function (v) {
      if (v === 'dl') { plain(blob, name); return; }
      if (v !== 'pick') return;
      var ext = (name.match(/\.[^.]+$/) || [''])[0];
      var opts = {suggestedName: name, startIn: 'downloads'};
      if (ext) { var acc = {}; acc[blob.type || 'application/octet-stream'] = [ext]; opts.types = [{description: ext.slice(1).toUpperCase(), accept: acc}]; }
      return window.showSaveFilePicker(opts).then(function (fh) {
        return fh.createWritable().then(function (wr) { return wr.write(blob).then(function () { return wr.close(); }); })
          .then(function () { toast(t('done', 'Сохранено: ') + (fh.name || name)); });
      }).catch(function (e) {
        if (e && e.name === 'AbortError') return;
        // тип ёки рухсат муаммоси — типсиз қайта уриниб кўрамиз, бўлмаса «Загрузки»
        return window.showSaveFilePicker({suggestedName: name}).then(function (fh) {
          return fh.createWritable().then(function (wr) { return wr.write(blob).then(function () { return wr.close(); }); });
        }).catch(function (e2) { if (!e2 || e2.name !== 'AbortError') { plain(blob, name); } });
      });
    });
  }
  LB.saveBlob = save;

  var busy = false;
  document.addEventListener('click', function (e) {
    if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    var a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!isCand(a)) return;
    var u = new URL(a.href, location.href);
    e.preventDefault(); e.stopPropagation();
    if (busy) return; busy = true;
    document.body.classList.add('lb-wait');
    fetch(u.href, {credentials: 'same-origin'}).then(function (r) {
      var cd = r.headers.get('Content-Disposition') || '', ct = r.headers.get('Content-Type') || '';
      var isFile = /attachment/i.test(cd) || a.hasAttribute('download') || u.protocol === 'blob:';
      if (!r.ok || !isFile) { busy = false; document.body.classList.remove('lb-wait'); location.href = u.href; return; }
      var name = fname(r, a, u);
      return r.blob().then(function (b) {
        busy = false; document.body.classList.remove('lb-wait');
        return save(name, b);
      });
    }).catch(function () { busy = false; document.body.classList.remove('lb-wait'); location.href = u.href; });
  }, true);
})();
