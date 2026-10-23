// Lumberture Hub — отправить проект в платформу и создать накладную (черновик).
// Пользователь: {{ user }}. Запуск: Базис-Мебельщик → Скрипты → Выполнить скрипт → этот файл.
// Собирает из открытой модели: панели (материал, толщина, площадь, кол-во деталей), кромку (материал, длина), фурнитуру.
var SERVER = {{ server }};
var TOKEN = {{ token }};

var panels = {}, butts = {}, furn = {}, furnH = {}, parts = 0;
var PM = {}, PL = {}, ERR = [], FE = [];   // PM: milling features per part (cutouts, grooves); PL: parts list
function sv(f) { try { var v = f(); if (v === undefined || v === null) return null; var t = typeof v; return (t == 'number' || t == 'string' || t == 'boolean') ? v : String(v); } catch (e) { return null; } }
function r1(x) { return Math.round(Number(x || 0) * 10) / 10; }
function elems(c) {
  var out = [];
  try {
    var cnt = Number(c.Count || 0);
    for (var i = 0; i < cnt; i++) {
      var el = null; try { el = c.Objects[i]; } catch (e1) { } if (!el) { try { el = c[i]; } catch (e2) { } }
      if (el) out.push(el);
    }
  } catch (e) { }
  return out;
}
function isArc(el) { try { return !!(el.IsArc() || el.IsCircle()); } catch (e) { return false; } }
function elLen(el) { try { return Number(el.ObjLength()); } catch (e) { return 0; } }
function pathInfo(c) {
  var es = elems(c), L = 0, arcs = 0;
  for (var i = 0; i < es.length; i++) { L += elLen(es[i]); if (isArc(es[i])) arcs++; }
  return { l: r1(L), n: es.length, arcs: arcs };
}
// Cutouts in the contour: elements that do not lie on the outer bounding box; consecutive ones form one cutout.
function contourCuts(o) {
  var es = elems(o.Contour), n = es.length, res = { cut: [], fb: 0, arcs: 0 };
  for (var a = 0; a < n; a++) if (isArc(es[a])) res.arcs++;
  if (n == 4 && !res.arcs) return res;
  var P = [], ok = true;
  for (var i = 0; i < n; i++) {
    try { var p1 = es[i].Pos1, p2 = es[i].Pos2; P.push([Number(p1.x), Number(p1.y), Number(p2.x), Number(p2.y)]); if (isNaN(P[i][0]) || isNaN(P[i][3])) ok = false; } catch (e) { ok = false; }
    if (!ok) break;
  }
  if (!ok || !n) { res.fb = Math.max(0, n - 4); return res; }
  var x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (var j = 0; j < n; j++) { x0 = Math.min(x0, P[j][0], P[j][2]); x1 = Math.max(x1, P[j][0], P[j][2]); y0 = Math.min(y0, P[j][1], P[j][3]); y1 = Math.max(y1, P[j][1], P[j][3]); }
  function eq(u, v) { return Math.abs(u - v) < 0.5; }
  function onBox(j) {
    if (isArc(es[j])) return false;
    var q = P[j];
    return (eq(q[0], x0) && eq(q[2], x0)) || (eq(q[0], x1) && eq(q[2], x1)) || (eq(q[1], y0) && eq(q[3], y0)) || (eq(q[1], y1) && eq(q[3], y1));
  }
  res.box = []; for (var bx = 0; bx < n; bx++) res.box.push(onBox(bx));   // for edging: side on the outer box or inside a cutout
  var st = -1;
  for (var k = 0; k < n; k++) if (onBox(k)) { st = k; break; }
  if (st < 0) { var t = 0; for (var m = 0; m < n; m++) t += elLen(es[m]); res.cut.push(r1(t)); return res; }   // whole contour is shaped (round, etc.)
  var run = 0;
  for (var s = 1; s <= n; s++) {
    var idx = (st + s) % n;
    if (onBox(idx)) { if (run > 0) res.cut.push(r1(run)); run = 0; } else run += elLen(es[idx]);
  }
  if (run > 0) res.cut.push(r1(run));
  return res;
}
function partInfo(o, mat, th, w, h) {
  var nm = String(sv(function () { return o.Name; }) || '').replace(/^\s+|\s+$/g, '');
  var cc = { cut: [], fb: 0, arcs: 0 }; try { cc = contourCuts(o); } catch (e) { ERR.push('contour ' + e); }
  var pz = [];
  try {
    var cs = elems(o.Cuts);
    for (var k = 0; k < cs.length; k++) {
      var cu = cs[k], tr = { l: 0, arcs: 0 };
      try { tr = pathInfo(cu.Trajectory); } catch (e3) { }
      // groove section from the cut profile: width (smaller side) and depth (larger side), mm
      var pw = 0, pd = 0;
      try {
        var ps = elems(cu.Contour), x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
        for (var q = 0; q < ps.length; q++) { var a1 = ps[q].Pos1, a2 = ps[q].Pos2; x0 = Math.min(x0, a1.x, a2.x); x1 = Math.max(x1, a1.x, a2.x); y0 = Math.min(y0, a1.y, a2.y); y1 = Math.max(y1, a1.y, a2.y); }
        if (ps.length && x1 > x0 && y1 > y0) { pw = r1(Math.min(x1 - x0, y1 - y0)); pd = r1(Math.max(x1 - x0, y1 - y0)); }
      } catch (e4) { }
      if (tr.l > 0) pz.push({ n: String(sv(function () { return cu.Name; }) || '') + ' ' + String(sv(function () { return cu.Sign; }) || ''), l: tr.l, a: tr.arcs, pw: pw, pd: pd });
    }
  } catch (e5) { ERR.push('cuts ' + e5); }
  // edging per side: ew = edged sides along the width (x), eh = along the length (y), ef = shaped sides (arc / inside a cutout)
  var eb = 0, ew = 0, eh = 0, ef = 0;
  try {
    for (var j = 0; j < o.Butts.Count; j++) {
      var bt = o.Butts.Butts[j]; if (bt.Profile) continue;
      eb++;
      var el2 = null, ei2 = -1; try { ei2 = Number(bt.ElemIndex); el2 = o.Contour.Objects[ei2]; } catch (e7) { el2 = null; }
      if (!el2 || isArc(el2) || (cc.box && ei2 >= 0 && cc.box[ei2] === false)) { ef++; continue; }
      try { var q1 = el2.Pos1, q2 = el2.Pos2; if (Math.abs(Number(q2.x) - Number(q1.x)) >= Math.abs(Number(q2.y) - Number(q1.y))) ew++; else eh++; } catch (e8) { ef++; }
    }
  } catch (e6) { }
  var pk = [nm, mat, th, r1(w), r1(h), eb, ew, eh, ef, cc.cut.join(','), cc.fb].join('|');
  if (!PL[pk]) PL[pk] = { nm: nm, m: mat, t: th, w: r1(w), h: r1(h), e: eb ? (eb + ' ст.') : '', ew: ew, eh: eh, ef: ef, c: cc.cut.length || (cc.fb ? 1 : 0), q: 0 };
  PL[pk].q += 1;
  if (cc.cut.length || cc.fb || cc.arcs || pz.length || Math.min(w, h) > 1000) {
    var mk = pk + '|' + JSON.stringify(pz);
    if (!PM[mk]) PM[mk] = { nm: nm, m: mat, w: r1(w), h: r1(h), cut: cc.cut, fb: cc.fb, arcs: cc.arcs, pz: pz, q: 0 };
    PM[mk].q += 1;
  }
  var pza = false; for (var q2 = 0; q2 < pz.length; q2++) if (pz[q2].a) pza = true;
  var ut = /^\s*ут(\s|$)|утолщ/i.test(String(nm || ''));   // «УТ» (утолщение) — станокда тешилади
  if ((Math.min(w, h) > 1190 && !ut) || cc.arcs || pza) { try { ncHoles(o); } catch (en) { ERR.push('nc ' + en); } }
  return cc;
}
// Parts that cannot be drilled on CNC (wider than 1190 mm except «УТ», radius milling) go to the rover: their holes are not charged as drilling.
var NCH = {}, NCX = 0;
function holeKey(hh) { return r1(sv(function () { return hh.Diameter; })) + '|' + r1(sv(function () { return hh.Depth; })) + '|' + sv(function () { return hh.DrillMode; }); }
function ncHoles(o) {
  var fs = null; try { fs = o.FindConnectedFasteners(); } catch (e0) { return; }
  var cnt = 0; try { cnt = Number(fs.Count || fs.length || 0); } catch (e1) { }
  var mn = null, mx = null; try { mn = o.GMin; mx = o.GMax; if (mn.x === undefined) { mn = null; } } catch (e2) { mn = null; }
  for (var i = 0; i < cnt; i++) {
    var f = null; try { f = fs[i]; } catch (e3) { } if (!f) { try { f = fs.Objects[i]; } catch (e4) { } } if (!f) continue;
    var nm = String(f.Name || '') + (f.ArtPos ? ' ' + f.ArtPos : ''); nm = nm.replace(/^\s+|\s+$/g, '');
    var hs = null; try { hs = f.Holes; } catch (e5) { } if (!hs) continue;
    for (var j = 0; j < Number(hs.Count || 0); j++) {
      var hh = null; try { hh = hs[j]; } catch (e6) { } if (!hh) { try { hh = hs.Objects[j]; } catch (e7) { } } if (!hh) continue;
      var w = 0.5;                                       // position unknown: count half (a fastener usually joins two parts)
      if (mn) {
        try {
          var g = f.ToGlobal(hh.Position), t = 1.5;
          w = (g.x >= mn.x - t && g.x <= mx.x + t && g.y >= mn.y - t && g.y <= mx.y + t && g.z >= mn.z - t && g.z <= mx.z + t) ? 1 : 0;
        } catch (e8) { w = 0.5; NCX++; }
      } else NCX++;
      if (!w) continue;
      if (!NCH[nm]) NCH[nm] = {};
      var k = holeKey(hh); NCH[nm][k] = (NCH[nm][k] || 0) + w;
    }
  }
}
function fastenerHoles(o, nm, depth, seen) {
  // own holes + holes of the parts inside the assembly (guides may keep marking holes in inner parts).
  // Holes inside ONE list are all real (a dowel has two equal holes starting at the same point), so the same list is never
  // de-duplicated; a child hole is skipped only when an outer level already listed a hole at the same global point.
  depth = depth || 0; seen = seen || {};
  var hs = null; try { hs = o.Holes; } catch (e0) { }
  var n = 0; try { n = hs ? Number(hs.Count || 0) : 0; } catch (e3) { }
  var mine = [];
  for (var i = 0; i < n; i++) {
    var hh = null; try { hh = hs[i]; } catch (e1) { } if (!hh) { try { hh = hs.Objects[i]; } catch (e2) { } } if (!hh) continue;
    var key = holeKey(hh), gk = null;
    try { var g = o.ToGlobal(hh.Position); gk = key + '@' + r1(g.x) + ',' + r1(g.y) + ',' + r1(g.z); } catch (e4) { gk = null; }
    if (gk && depth > 0 && seen[gk]) continue;
    if (gk) mine.push(gk);
    if (!furnH[nm]) furnH[nm] = {};
    furnH[nm][key] = (furnH[nm][key] || 0) + 1;
  }
  for (var m = 0; m < mine.length; m++) seen[mine[m]] = 1;
  if (depth < 4) {
    var ks = kidsOf(o);
    for (var q = 0; q < ks.length; q++) {
      var kn = String(sv(function () { return ks[q].Name; }) || '');
      if (/шуруп|саморез|screw/i.test(kn)) continue;     // screws are counted separately (furnS): one screw = one nasechka
      fastenerHoles(ks[q], nm, depth + 1, seen);
    }
  }
}
// Probe: structure of one sample of each furniture item that has no holes (to find where guides keep theirs).
var DBG = {}, DBGN = 0;
function probe(o, depth) {
  var r = { n: String(sv(function () { return o.Name; }) || ''), t: String(sv(function () { return String(o); }) || '').slice(0, 40) };
  try { r.h = o.Holes ? Number(o.Holes.Count || 0) : -1; } catch (e) { r.h = -2; }
  try { if (r.h > 0) { var hh = o.Holes[0] || o.Holes.Objects[0]; r.k = holeKey(hh); } } catch (e1) { }
  var ks = kidsOf(o); r.c = ks.length;
  if (depth < 3 && ks.length) { r.s = []; for (var i = 0; i < ks.length && i < 8; i++) r.s.push(probe(ks[i], depth + 1)); }
  return r;
}
function addPanel(o) {
  var mat = String(o.MaterialName || '');
  var th = Number(o.Thickness || 0);
  var w = Number(o.ContourWidth || 0), h = Number(o.ContourHeight || 0);
  if ((!w || !h) && o.GSize) { w = Number(o.GSize.x || 0); h = Number(o.GSize.y || 0); }
  var k = mat + '|' + th;
  if (!panels[k]) panels[k] = { m: mat, t: th, a: 0, n: 0, ap: 0 };
  panels[k].a += w * h;
  // processed part (non-rectangular contour, cuts/grooves) - HDF counts in the project area only when processed
  var proc = false;
  try { var cc = Number(o.Contour.Count || 0); if (cc && cc != 4) proc = true; } catch (e3) { }
  try { if (o.Cuts && Number(o.Cuts.Count) > 0) proc = true; } catch (e4) { }
  if (proc) panels[k].ap += w * h;
  panels[k].n += 1;
  parts += 1;
  var pc = null; try { pc = partInfo(o, mat, th, w, h); } catch (ep) { ERR.push('part ' + ep); }
  try {
    for (var j = 0; j < o.Butts.Count; j++) {
      var b = o.Butts.Butts[j];
      if (b.Profile) continue;
      var len = 0, ei = -1, el = null;
      try { ei = Number(b.ElemIndex); el = o.Contour.Objects[ei]; len = el.ObjLength(); } catch (e1) { len = 0; }
      if (!len) len = Math.max(w, h);
      len += 2 * Number(b.Overhung || 0);
      // edge on a shaped side (arc, or a side inside a cutout): Bazis labels it "see drawing" and leaves it out of the edge count
      var fig = false;
      try { fig = (el && isArc(el)) || (pc && pc.box && ei >= 0 && pc.box[ei] === false); } catch (e5) { }
      var bk = String(b.Material || '') + '|' + Number(b.Thickness || 0) + '|' + Number(b.Width || 0) + (fig ? '|f' : '');
      if (!butts[bk]) butts[bk] = { m: String(b.Material || ''), t: Number(b.Thickness || 0), w: Number(b.Width || 0), l: 0, f: fig ? 1 : 0 };
      butts[bk].l += len;
      if (fig && FE.length < 200) FE.push({ nm: String(o.Name || ''), w: r1(w), h: r1(h), e: ei, l: r1(len) });
    }
  } catch (e2) { }
}

function isPanel(o) {
  return o && o.Butts !== undefined && o.MaterialName !== undefined && o.Contour !== undefined;
}

function isFurniture(o) {
  var t = String(o);
  return /TFurnAsm|TFastener|TFurniture/.test(t);
}

// Screws inside furniture assemblies (hinges, guides): Bazis counts them in its report, each screw = one hole (nasechka).
var furnS = {}, KIDS = {};
function kidsOf(o) {
  var out = [];
  try { var c = Number(o.Count || 0); for (var i = 0; i < c; i++) { var k = null; try { k = o[i]; } catch (e1) { } if (!k) { try { k = o.Objects[i]; } catch (e2) { } } if (k) out.push(k); } } catch (e) { }
  return out;
}
function countScrews(o, depth) {
  var n = 0, ks = kidsOf(o);
  for (var i = 0; i < ks.length; i++) {
    var nm = String(sv(function () { return ks[i].Name; }) || '').split('\r')[0];
    if (depth == 0 && nm) KIDS[nm] = (KIDS[nm] || 0) + 1;
    if (/шуруп|саморез|screw/i.test(nm)) n++;
    else if (depth < 4) n += countScrews(ks[i], depth + 1);
  }
  return n;
}
function walk(list) {
  for (var i = 0; i < list.Count; i++) {
    var o = list[i];
    if (!o) continue;
    if (isPanel(o)) { addPanel(o); continue; }
    if (isFurniture(o)) {
      var n = String(o.Name || '') + (o.ArtPos ? ' ' + o.ArtPos : '');
      n = n.replace(/^\s+|\s+$/g, '');
      if (n) {
        furn[n] = (furn[n] || 0) + 1;
        try { fastenerHoles(o, n); } catch (eh) { ERR.push('holes ' + eh); }
        try { if (!furnH[n] && !DBG[n] && DBGN < 12) { DBG[n] = probe(o, 0); DBGN++; } } catch (ed) { ERR.push('probe ' + ed); }
        try { var sc = countScrews(o, 0); if (sc) furnS[n] = (furnS[n] || 0) + sc; } catch (es) { ERR.push('screws ' + es); }
      }
      continue;
    }
    if (o.Count !== undefined && o.Count > 0) walk(o);
  }
}

function values(obj) {
  var out = [];
  for (var k in obj) if (obj.hasOwnProperty(k)) out.push(obj[k]);
  return out;
}

// Bazis crashes when a JS string longer than ~16-30K is passed to a COM call: build the UTF-8 body in small pieces.
function utf8Bytes(str) {
  var st = NewCOMObject('ADODB.Stream');
  st.Type = 2; st.Charset = 'utf-8'; st.Open();
  for (var p = 0; p < str.length; p += 4000) st.WriteText(str.substr(p, 4000));
  st.Position = 0; st.Type = 1; st.Position = 3;   // skip BOM
  var out = NewCOMObject('ADODB.Stream');
  out.Type = 1; out.Open();
  st.CopyTo(out); st.Close();
  out.Position = 0;
  return out;                                       // IStream: XMLHTTP.send reads it directly
}
function http(url, body) {
  var data = body; try { data = utf8Bytes(body); } catch (eb) { if (body.length > 15000) return { status: 0, text: 'ADODB.Stream: ' + eb }; }
  var progs = ['MSXML2.ServerXMLHTTP.6.0', 'MSXML2.XMLHTTP.6.0', 'WinHttp.WinHttpRequest.5.1'];
  var last = '';
  for (var i = 0; i < progs.length; i++) {
    try {
      var x = NewCOMObject(progs[i]);
      x.open('POST', url, false);
      x.setRequestHeader('Content-Type', 'application/json; charset=utf-8');
      x.setRequestHeader('X-LB-Token', TOKEN);
      x.send(data);
      return { status: Number(x.status), text: String(x.responseText) };
    } catch (e) { last = String(e); }
  }
  return { status: 0, text: last };
}

function hubOpen() {
  // the platform window (own Chrome profile) is already running?
  var q = "SELECT CommandLine FROM Win32_Process WHERE Name='chrome.exe' OR Name='msedge.exe'";
  var set = null;
  try { set = NewCOMObject('WbemScripting.SWbemLocator').ConnectServer('.', 'root\\cimv2').ExecQuery(q); } catch (e1) { }
  if (!set) { try { set = GetObject('winmgmts:\\\\.\\root\\cimv2').ExecQuery(q); } catch (e2) { } }
  if (!set) return false;
  function hit(it) { return String(it.CommandLine || '').toLowerCase().indexOf('\\lumberture\\chrome') >= 0; }
  try {
    var n = Number(set.Count);
    for (var i = 0; i < n; i++) if (hit(set.ItemIndex(i))) return true;
    return false;
  } catch (e3) { }
  try {
    for (var en = new Enumerator(set); !en.atEnd(); en.moveNext()) if (hit(en.item())) return true;
  } catch (e4) { }
  return false;
}

function openUrl(url) {
  var sh = null;
  try { sh = NewCOMObject('WScript.Shell'); } catch (e0) { }
  if (sh && hubOpen()) {
    // already open: the window picks the project up by itself, just bring it to front
    try { sh.AppActivate('Lumberture Hub'); } catch (e1) { }
    return;
  }
  try {
    var pd = sh.ExpandEnvironmentStrings('%LOCALAPPDATA%') + '\\Lumberture\\chrome2';
    sh.Run('chrome --app="' + url + '" --start-maximized --user-data-dir="' + pd + '" --no-first-run --no-default-browser-check', 1, false);
    return;
  } catch (e) { }
  try { system.exec('cmd /c start "" "' + url + '"'); } catch (e2) { }
}

walk(Model);

var fl = [];
for (var fk in furn) if (furn.hasOwnProperty(fk)) fl.push({ m: fk, n: furn[fk], h: furnH[fk] || null, sc: furnS[fk] || 0 });
var project = '';
try { project = String(Model.Name || Model.ArtPos || ''); } catch (e) { }

if (!parts && !fl.length) {
  alert('В открытой модели нет панелей — сначала откройте проект.');
} else {
  var payload = { v: 3, project: project, parts: parts, panels: values(panels), butts: values(butts), furn: fl, pm: values(PM), pl: values(PL), err: ERR.slice(0, 20), kids: KIDS, nch: NCH, ncx: NCX, fe: FE, dbg: DBG };
  var r = http(SERVER + 'api/basis/import', JSON.stringify(payload));
  if (r.status == 200) {
    var j = JSON.parse(r.text);
    if (j.open) {
      // the platform window is already open: it picks the project up by itself, just bring it to front
      try { NewCOMObject('WScript.Shell').AppActivate('Lumberture Hub'); } catch (e5) { }
      alert('Проект отправлен в Lumberture Hub: деталей ' + parts + '. Он откроется в уже открытом окне платформы.');
    } else {
      openUrl(j.url);
      alert('Проект отправлен в Lumberture Hub: деталей ' + parts + '. Откроется окно — выберите клиента и нажмите «Создать накладную».');
    }
  } else if (r.status == 403) {
    alert('Ключ скрипта устарел. Скачайте скрипт заново в платформе: Документы → Из Базиса.');
  } else {
    alert('Не удалось отправить в Lumberture Hub (' + r.status + '). Проверьте, что сервер ' + SERVER + ' доступен.\n' + r.text);
  }
}
