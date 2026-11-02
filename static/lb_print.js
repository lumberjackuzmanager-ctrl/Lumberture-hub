/* Lumberture — печать онлайн-заказа: отчёт по раскрою + ведомость на закупку плит + карты раскроя (как Базис-Раскрой, ч/б, A4 книжная).
   Работает без интернета, только из JSON заказа, который сайт присылает в POST /api/online/order (поля material, layout, parts, number, client).
   Использование на платформе:  <script src="/static/lb_print.js" charset="utf-8"></script>
     LBPrint.open(order)            — один заказ (один материал)
     LBPrint.open([order1, order2]) — несколько материалов одного заказа (group) в одной печати
   Ничего не отправляет и ничего не меняет — только открывает окно печати. */
(function () {
  const KERF0 = 4.4, TRIM0 = 5, EDGE_ADD = 40;          // те же правила, что на сайте (calc.js)
  const esc = s => String(s == null ? '' : s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const fmt = (n, d) => Number(n).toLocaleString('ru-RU', { minimumFractionDigits: d || 0, maximumFractionDigits: d == null ? 2 : d });

  // гильотина — копия логики сайта: полосы → колонки → ряды; нужна для числа и длины резов
  function guillotine(pieces, sheet, preferTr) {
    const eps = 0.05;
    const tryOri = tr => {
      const X = tr ? sheet.W : sheet.L, Y = tr ? sheet.L : sheet.W;
      const it = pieces.map((p, i) => tr ? { x: p.y, y: p.x, w: p.h, h: p.w, i } : { x: p.x, y: p.y, w: p.w, h: p.h, i });
      const S = { x: ['x', 'w', 'X', 'Px'], y: ['y', 'h', 'Y', 'Py'] };
      const same = (a, b) => Math.abs(a.X - b.X) < eps && Math.abs(a.Y - b.Y) < eps;
      function bands(items, d) { const [k, s] = S[d], arr = items.slice().sort((a, b) => a[k] - b[k]), out = [];
        arr.forEach(q => { const g = out[out.length - 1]; if (g && q[k] < g.end - eps) { g.items.push(q); g.end = Math.max(g.end, q[k] + q[s]); } else out.push({ start: q[k], end: q[k] + q[s], items: [q] }); }); return out; }
      function split(R, items, d, k) {
        if (items.length === 1) { const q = items[0], L = { X: q.w, Y: q.h, Px: q.x, Py: q.y }; if (same(R, L)) return L; }
        const gs = bands(items, d), DX = S[d][2], DP = S[d][3], reg = g => Object.assign({}, R, { [DX]: g.end - g.start, [DP]: g.start });
        if (gs.length === 1 && same(reg(gs[0]), R)) { if (k) return null; return split(R, items, d === 'y' ? 'x' : 'y', 1); }
        const kids = gs.map(g => split(reg(g), g.items, d === 'y' ? 'x' : 'y', 0)); if (kids.some(x => !x)) return null;
        return Object.assign({}, R, { kids, d }); }
      return split({ X, Y, Px: 0, Py: 0 }, it, 'y', 0);
    };
    return preferTr ? (tryOri(true) || tryOri(false)) : (tryOri(false) || tryOri(true));
  }
  function cutsOf(s) { let n = 0, len = 0; const G = guillotine(s.pieces, s.sheet, false); if (!G) return { n: 0, len: 0 };
    const walk = r => { if (!r.kids) return; const along = r.d === 'y' ? 'Py' : 'Px', size = r.d === 'y' ? 'Y' : 'X', other = r.d === 'y' ? 'X' : 'Y', st = r[along], en = r[along] + r[size];
      r.kids.forEach((c, i) => { const a = c[along], b = c[along] + c[size]; if (i === 0 && a > st + .5) { n++; len += r[other]; } if (b < en - .5) { n++; len += r[other]; } walk(c); }); };
    walk(G.kids ? G : { X: G.X, Y: G.Y, Px: 0, Py: 0, kids: [G], d: 'y' }); return { n, len }; }

  function report(o) {
    const m = o.material || {}, lay = o.layout || {}, parts = o.parts || [], T = lay.trim != null ? lay.trim : TRIM0, K = lay.kerf != null ? lay.kerf : KERF0;
    const S0 = m.sheet || { L: 2800, W: 2070 };
    const sheets = (lay.sheets || []).map(s => ({ off: !!s.offcut, sheet: s.sheet || S0, pieces: (s.pieces || []).map(q => ({ pos: q.pos, x: +q.x, y: +q.y, w: +q.w, h: +q.h, rot: !!q.rot })) })).filter(s => s.pieces.length);
    const sheetA = sheets.reduce((a, s) => a + s.sheet.L * s.sheet.W, 0) / 1e6, panA = sheets.reduce((a, s) => a + s.pieces.reduce((t, q) => t + q.w * q.h, 0), 0) / 1e6;
    const trimA = sheets.reduce((a, s) => a + (s.sheet.L * s.sheet.W - (s.sheet.L - 2 * T) * (s.sheet.W - 2 * T)), 0) / 1e6;
    let nCut = 0, lenCut = 0, nRot = 0; sheets.forEach(s => { const c = cutsOf(s); nCut += c.n; lenCut += c.len; nRot += s.pieces.filter(q => q.rot).length; });
    const perim = parts.reduce((a, p) => { const c = p.cut || p; return a + 2 * ((+c.L || 0) + (+c.W || 0)) * (+p.qty || 0); }, 0) / 1000;
    const em = {}, segs = {};
    parts.forEach(p => { const e = p.edges || {}, nm = p.edge_names || {}; ['L1', 'L2', 'W1', 'W2'].forEach(k => { if (!e[k]) return; const name = nm[k] || String(e[k]), len = (k[0] === 'L' ? +p.L : +p.W) || 0;
      em[name] = (em[name] || 0) + (len + EDGE_ADD) * (+p.qty || 0) / 1000; segs[name] = (segs[name] || 0) + (+p.qty || 0); }); });
    return { m, S0, K, T, sheets, sheetA, panA, trimA, nCut, lenCut: lenCut / 1000, nRot, perim, em, segs, nPanels: parts.reduce((a, p) => a + (+p.qty || 0), 0), names: Object.fromEntries(parts.map(p => [p.pos, p.name])) };
  }

  function pageFor(o, date) {
    const R = report(o), m = R.m, full = R.sheets.filter(s => !s.off), f3 = n => fmt(n, 3);
    const ord = esc([o.number ? '№' + o.number : '', o.client && o.client.name, m.name].filter(Boolean).join(' '));
    const row = (a, b) => `<tr><td>${a}</td><td>${b}</td></tr>`, gap = '<tr><td colspan="2">&nbsp;</td></tr>';
    const edges = Object.keys(R.em).map(n => row('Облицовка', esc(n)) + row('Длина облицовки', fmt(Math.round(R.em[n] * 100) / 100) + ' м.') + row('Количество отрезов облицовки', R.segs[n])).join('');
    const sum = `<section class="pg sum"><table class="kv">${row('Заказ', ord)}${row('Материал', esc(m.name))}${row('Дата', date)}${row('Размер плиты', R.S0.L + ',00x' + R.S0.W + ',00')}${row('Количество плит материала', full.length)}${R.sheets.length > full.length ? row('Количество кусков клиента', R.sheets.length - full.length) : ''}${gap}
      ${row('КИМ', R.sheetA ? fmt(Math.round(R.panA / R.sheetA * 10000) / 100) + ' %' : '—')}${row('Площадь использованных плит', f3(R.sheetA) + ' кв.м.')}${row('Площадь панелей', f3(R.panA) + ' кв.м.')}${row('Площадь всех отходов', f3(R.sheetA - R.panA) + ' кв.м.')}${row('Площадь обрезки плит', f3(R.trimA) + ' кв.м.')}${row('Площадь резов', f3(R.lenCut * R.K / 1000) + ' кв.м.')}${gap}
      ${row('Длина резов', fmt(Math.round(R.lenCut * 1000) / 1000) + ' м.')}${row('Периметр панелей', fmt(Math.round(R.perim * 1000) / 1000) + ' м.')}${gap}${edges}${edges ? gap : ''}
      ${row('Количество карт раскроя', R.sheets.length)}${row('Количество панелей', R.nPanels)}${row('Количество поворотов панелей', R.nRot)}${row('Количество резов', R.nCut)}${gap}${row('Пропил / отступ от края', R.K + ' мм / ' + R.T + ' мм')}
      ${o.urgent ? gap + row('Срочно', 'да') : ''}${o.note ? row('Примечание', esc(o.note)) : ''}</table></section>`;
    const head = `<div class="mh">Заказ: ${ord}<br>Материал: ${esc(m.name)}<br>Дата: ${date}</div>`;
    let fullNo = 0;
    const maps = R.sheets.map((s, si) => {
      const L = s.sheet.L, W = s.sheet.W, lw = Math.max(L, W) / 400, grp = {};
      s.pieces.forEach(q => { const k = q.pos + '|' + q.w + '|' + q.h + '|' + (q.rot ? 1 : 0); grp[k] = (grp[k] || 0) + 1; });
      const rows = Object.keys(grp).sort((a, b) => +a.split('|')[0] - +b.split('|')[0]).map(k => { const [n, w, h, r] = k.split('|'); return `<tr><td>${n}</td><td>${w}</td><td>${h}</td><td>${grp[k]}${r === '1' ? ' (90°)' : ''}</td></tr>`; }).join('');
      const pcs = s.pieces.map(q => { const vert = q.h > q.w * 1.6 && q.w < 260, fs = vert ? Math.max(16, Math.min(60, q.w / 3.2)) : Math.max(16, Math.min(70, q.h / 4, q.w / (String(q.w).length * 1.6 + 3))), lab = (q.rot ? '<R> ' : '') + '(' + q.pos + ') ' + q.w + 'x' + q.h;
        return `<rect x="${q.x}" y="${q.y}" width="${q.w}" height="${q.h}" fill="#fff" stroke="#000" stroke-width="${lw}"/>` + (Math.min(q.w, q.h) > 60 ? `<text x="${q.x + q.w / 2}" y="${q.y + q.h / 2}" font-size="${fs}" text-anchor="middle" dominant-baseline="middle"${vert ? ` transform="rotate(-90 ${q.x + q.w / 2} ${q.y + q.h / 2})"` : ''}>${esc(lab)}</text>` : ''); }).join('');
      if (!s.off) fullNo++;
      return `<div class="map">${head}<svg viewBox="-30 -30 ${L + 60} ${W + 60}"><defs><pattern id="h${o.number || ''}_${si}" width="40" height="40" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><line x1="0" y1="0" x2="0" y2="40" stroke="#000" stroke-width="3"/></pattern></defs><rect width="${L}" height="${W}" fill="url(#h${o.number || ''}_${si})" stroke="#000" stroke-width="${lw * 2}"/>${pcs}</svg>
        <div class="mt">${s.off ? 'Кусок клиента ' + L + 'x' + W : 'Карта ' + fullNo + ' из ' + full.length + '. Количество плит материала 1<br>Размер плиты ' + L + 'x' + W}</div><table class="pt"><tr><th>Поз.</th><th>Длина</th><th>Ширина</th><th>Кол.</th></tr>${rows}<tr><th>ИТОГО</th><th></th><th></th><th>${s.pieces.length}</th></tr></table></div>`; }).join('');
    const names = Object.keys(R.names).sort((a, b) => a - b).map(n => `${n} — ${esc(R.names[n])}`).join('; ');
    const buy = `<section class="pg"><h2>Ведомость на закупку плит</h2>${head}<table class="pt buy"><tr><th>Кол.</th><th>Длина</th><th>Ширина</th><th>Толщина</th><th>Материал</th></tr><tr><td>${full.length}</td><td>${R.S0.L}</td><td>${R.S0.W}</td><td>${m.thick || 16}</td><td>${esc(m.name)}</td></tr></table>
      <p class="sg">Сдал ____________ &nbsp;&nbsp; Принял ____________</p><p class="nm">Позиции: ${names}</p><div class="maps">${maps}</div></section>`;
    return (R.sheets.length ? '' : '<p style="color:#a00">В заказе нет карты раскроя (layout) — проверьте заказ.</p>') + sum + buy;
  }

  function html(orders) {
    const d = new Date(), dd = x => String(x).padStart(2, '0'), date = dd(d.getDate()) + '.' + dd(d.getMonth() + 1) + '.' + d.getFullYear();
    const t = orders.map(o => o.number).filter(Boolean).join(', ');
    return `<!doctype html><html lang="ru"><meta charset="utf-8"><title>Раскрой ${esc(t)}</title><style>
@page{size:A4 portrait;margin:8mm}body{font:12px/1.35 Arial,sans-serif;color:#000;background:#fff;margin:0}
.pg{page-break-after:always;padding:6mm}.pg:last-child{page-break-after:auto}h2{font-size:16px;margin:0 0 6px;text-align:center}
.sum .kv{font:14px/1.5 Consolas,"Courier New",monospace;border-collapse:collapse;margin:20px 0 0 30px}.kv td{padding:0 18px 0 0;vertical-align:top}
.mh{font-size:10px;margin-bottom:2px}.maps{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5mm 6mm;margin-top:6px}
.map{break-inside:avoid}.map svg{width:100%;height:auto;display:block}.map text{font-family:Arial,sans-serif;fill:#000}.mt{font-size:10px;margin:2px 0 3px}
.pt{border-collapse:collapse;font-size:10px}.pt td,.pt th{border:1px solid #000;padding:1px 6px;text-align:right}.pt th{font-weight:600;text-align:center}.buy{font-size:12px;margin:4px 0}.sg{margin:6px 0 0}.nm{font-size:10px;margin:4px 0 0}
.np{position:fixed;top:8px;right:8px}.np button{font:600 14px Arial;padding:8px 14px;cursor:pointer}@media print{.np{display:none}}
</style><div class="np"><button onclick="print()">Печать</button></div>${orders.map(o => pageFor(o, date)).join('')}</html>`;
  }

  function open(order) {
    let list = Array.isArray(order) ? order : (order && order.orders) ? order.orders : [order && order.order ? order.order : order];
    list = list.filter(Boolean);
    const w = window.open('', '_blank'); if (!w) { alert('Разрешите всплывающие окна для печати'); return null; }
    w.document.write(html(list)); w.document.close(); setTimeout(() => { try { w.focus(); w.print(); } catch (e) {} }, 400); return w;
  }
  window.LBPrint = { open, html, report };
})();
