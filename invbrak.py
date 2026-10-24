# -*- coding: utf-8 -*-
"""Инвентаризация ёрдамчиси ва брак (эгаси, 24.10).

1) «📋 Сотилган листлар» — остатка куни (платформада ёқилади): сотилган (накладнойи ёзилган), лекин ҳали кесилмаган
   ёки бутун сотилиб олиб кетилмаган листлар ва ҳали ёпиштирилмаган кромка омборда турибди (эгаси, 24.10). Ишчи уларнинг устидаги накладной расмини ботга ташлайди (ёки рақамини ёзади) — бот рақамни ўқийди,
   1С'дан товарларини олади (каталог товарига stock_map / таклиф орқали). Платформада рўйхат ва каталог товари бўйича жами;
   офис сонини тузатади (қисман олиб кетилган бўлса) ва битта тугма билан инвентаризация «Факт»идан айиради.
2) «🗑 Брак» — ишчи синган/хато кесилган листни ёзади (товар, сони, сабаби, расм). Шериклар ва менежерларга тасдиқлаш учун
   хабар; тасдиқлангач — «Списание (брак)» инвентаризацияси (тест режимида черновик) омбордан айиради.
   Қолган қисми ишласа — бот кусок омборига киритишни таклиф қилади."""
import os, re, json
from datetime import date, datetime

from flask import request, render_template, redirect, url_for, flash, abort, send_file

A = None
B_INVU = "📋 Сотилган листлар"                 # эски (эгаси, 24.10: ботда керак эмас) — платформа маълумоти қолади
B_INVC = "📦 Инвентаризация"                   # остатка куни: товарларни санаш (эгаси, 24.10; «остатка» — кусок деб тушунилмасин)
B_ICF, B_ICS, B_ICM = "📁 Папкалар", "🔍 Товар қидириш", "📋 Санаганларим"
PAGE = 14
B_BRAK = "🗑 Брак"
REASONS = {"sin": "💥 Синди (ташиш, юклаш)", "cut": "✂️ Кесишда хато", "zav": "🏭 Заводдан брак", "oth": "Бошқа сабаб"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS inv_unpicked(id INTEGER PRIMARY KEY, day TEXT NOT NULL, iddoc TEXT NOT NULL, doc_no TEXT, doc_date TEXT,
  client TEXT DEFAULT '', rw_id INTEGER, at TEXT, photo TEXT DEFAULT '', lines TEXT DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(day, iddoc));
CREATE TABLE IF NOT EXISTS inv_count(id INTEGER PRIMARY KEY, day TEXT NOT NULL, good TEXT, new_name TEXT DEFAULT '', gname TEXT DEFAULT '',
  unit TEXT DEFAULT '', fact REAL NOT NULL DEFAULT 0, rw_id INTEGER, at TEXT, hist TEXT DEFAULT '[]', UNIQUE(day, good));
CREATE TABLE IF NOT EXISTS raspil_brak(id INTEGER PRIMARY KEY, rw_id INTEGER, good TEXT, gname TEXT, unit TEXT, qty REAL, reason TEXT,
  photo TEXT DEFAULT '', at TEXT, status TEXT NOT NULL DEFAULT 'new', by_name TEXT DEFAULT '', by_at TEXT, doc_id INTEGER, note TEXT DEFAULT '',
  msgs TEXT DEFAULT '[]');
"""


def init_tables(con):
    con.executescript(SCHEMA)


def _rb():
    import raspil_bot
    return raspil_bot


def _pk():
    import pickup
    return pickup


def inv_day():
    """Остатка куни (платформада ёқилган бўлса). «📋 Сотилган листлар» тугмаси шу кунгача ва шу куни кўринади
    (эгаси, 24.10: остатка эртага, ёзишни бугундан бошлаш керак); ёзувлар остатка кунига тушади."""
    d = (A.get_setting("inv_unpicked_day", "") or "").strip()
    return d if d and d >= date.today().isoformat() else ""


def can_brak(rw):
    rb = _rb()
    return bool(rw) and not rb.owner_only(rw) and not dict(rw).get("driver")


def buttons(rw):
    """Пастки клавиатурага: [Брак] ва остатка куни [📦 Инвентаризация]."""
    out = []
    if rw is None or can_brak(rw):
        out.append(B_BRAK)
    if inv_day() and (rw is None or can_brak(rw)):
        out.append(B_INVC)
    return out


# ------------------------------------------------------------------ олиб кетилмаган накладнойлар
def _map_good(con, gid, name):
    """1С товари -> каталог товари: аввал сақланган мослик (Ввод остатков), йўқса таклиф."""
    r = con.execute("SELECT good FROM stock_map WHERE c1=?", (gid,)).fetchone()
    if r:
        return r[0]
    try:
        import stockimp
        return stockimp.suggest(con, name) or ""
    except Exception:
        return ""


def _edge_good(con, gid, name):
    """1С «2 Кромка» хизмати (кромкалаш, пог.м) -> каталог кромка товари (метр): «Услуга ПВХ/Овал …» нормативидаги кромка."""
    r = con.execute("SELECT good FROM stock_map WHERE c1=?", (gid,)).fetchone()
    if r:
        return r[0]
    try:
        import basis, erp
        svc = basis.cat_best(con, "edgefig" if "овал" in (name or "").casefold() else "edge", name)
        for comp, k in erp.norms().get(svc, ()):
            if k >= 0.5:                                 # кромка ×1 (клей — кичик норма, санамаймиз)
                return comp
    except Exception:
        A.errlog.exception("invbrak edge map")
    return ""


def doc_lines(con, iddoc):
    """Накладнойдаги листлар ва кромка (кромкалаш хизмати метри → кромка товари); бошқа хизматлар, кусок ташқари.
    -> [dict(gid, name, unit, qty, good, gname)]"""
    import c1, stockimp
    sd = c1.sale_doc(iddoc) or {}
    g = c1.goods()
    out = []
    for l in sd.get("lines") or []:
        it = g.get(l["gid"]) or {}
        if not it or (l["qty"] or 0) <= 0:
            continue
        path = c1.folder_path(g, it.get("parent"))
        if re.match(r"2\s", path or "") and "кромк" in (path or "").casefold():      # «2 Кромка» — сотилган, ҳали ёпиштирилмаган кромка
            cg = _edge_good(con, l["gid"], l["name"])
            gn = con.execute("SELECT name FROM erp_goods WHERE ext=?", (cg,)).fetchone() if cg else None
            out.append(dict(gid=l["gid"], name=l["name"], unit="м", qty=round(l["qty"], 3), good=cg, gname=gn[0] if gn else "", edge=1))
            continue
        if stockimp._skip(path):
            continue
        cg = _map_good(con, l["gid"], l["name"])
        gn = con.execute("SELECT name FROM erp_goods WHERE ext=?", (cg,)).fetchone() if cg else None
        out.append(dict(gid=l["gid"], name=l["name"], unit=l["unit"], qty=round(l["qty"], 3), good=cg, gname=gn[0] if gn else ""))
    return out, sd


def record_unpicked(con, rw, doc, photo=""):
    """-> (матн ишчига)"""
    day = inv_day() or date.today().isoformat()
    old = con.execute("SELECT u.*, w.name wname FROM inv_unpicked u LEFT JOIN ready_workers w ON w.id=u.rw_id WHERE u.day=? AND u.iddoc=? AND u.deleted=0",
                      (day, doc["iddoc"])).fetchone()
    if old:
        return f"ℹ️ №{doc['no']} аввал ёзилган ({old['wname'] or ''} {old['at'][11:16]}). Қайта ёзилмади."
    lines, sd = doc_lines(con, doc["iddoc"])
    client = (sd.get("client") or {}).get("name", "") if sd else ""
    con.execute("DELETE FROM inv_unpicked WHERE day=? AND iddoc=?", (day, doc["iddoc"]))
    con.execute("INSERT INTO inv_unpicked(day, iddoc, doc_no, doc_date, client, rw_id, at, photo, lines) VALUES(?,?,?,?,?,?,?,?,?)",
                (day, doc["iddoc"], doc["no"], str(doc.get("date") or ""), client, rw["id"], A.now(), photo or "", json.dumps(lines, ensure_ascii=False)))
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM inv_unpicked WHERE day=? AND deleted=0", (day,)).fetchone()[0]
    if not lines:
        return f"✅ №{doc['no']} · {client}\nЛист йўқ (фақат хизмат). Жами ёзилган: {n} та накладной."
    body = "\n".join(f"• {l['name'][:48]} — {_rb().P._n(l['qty'])} {l['unit']}" for l in lines[:15])
    return f"✅ №{doc['no']} · {client}\n{body}\n\nБу листлар омбордан айрилади. Жами ёзилган: {n} та накладной."


def _invu_start(rw, chat):
    con = A.db()
    _pk().st_set(con, rw["id"], "invu")
    _rb().send(chat, "📋 Сотилган, лекин ҳали кесилмаган ёки олиб кетилмаган листлар (ва кромка) накладнойини расмга олиб ташланг "
                     "(ёки рақамини ёзинг).\nҲар бир накладной — алоҳида.", _rb().ikb([[("✅ Тугатдим", "ib:ue")]]))


def _invu_doc(rw, chat, text="", photo=""):
    import ready as R
    ocr = ""
    if photo and not R.NUM_RE.search(text or ""):
        ocr = R.ocr_image(photo) or ""
        if ocr.startswith("#error"):
            ocr = ""
    doc, st, info = R.pick_doc(text, ocr)
    if not doc:
        _rb().send(chat, {"nodoc": f"❓ №{info} 1С'да топилмади. Рақамини текшириб ёзинг.", "many": f"❓ Бир нечта рақам: {info}. Биттасини ёзинг."}
                   .get(st, "❓ Накладной рақамини ўқий олмадим. Рақамини ёзинг (масалан 6490)."))
        return
    _rb().send(chat, record_unpicked(A.db(), rw, doc, photo), _rb().ikb([[("✅ Тугатдим", "ib:ue")]]))


# ------------------------------------------------------------------ брак
def _brak_start(rw, chat):
    con = A.db()
    _pk().st_set(con, rw["id"], "brak_q")
    _rb().send(chat, "🗑 Брак. Қайси лист? Номини ёзинг (масалан: ХДФ оқ ёки Мадера 16).")


def _search(con, text):
    import stockimp
    return stockimp.search(con, text, limit=8)


def approvers():
    return A.q("SELECT * FROM ready_workers WHERE active=1 AND bot_uid IS NOT NULL AND (owner=1 OR mgr=1)")


def brak_card(b):
    w = A.q("SELECT name FROM ready_workers WHERE id=?", (b["rw_id"],), one=True)
    P = _rb().P
    return (f"🗑 Брак: {b['gname']} — {P._n(b['qty'])} {b['unit'] or ''}\nСабаби: {REASONS.get(b['reason'], b['reason'])}\n"
            f"Ким: {w['name'] if w else '?'} · {b['at'][8:10]}.{b['at'][5:7]} {b['at'][11:16]}")


def _brak_save(rw, chat, d, photo=""):
    con = A.db()
    bid = con.execute("INSERT INTO raspil_brak(rw_id, good, gname, unit, qty, reason, photo, at) VALUES(?,?,?,?,?,?,?,?)",
                      (rw["id"], d["good"], d["gname"], d.get("unit", ""), d["qty"], d["reason"], photo or "", A.now())).lastrowid
    con.commit()
    _pk().st_set(con, rw["id"])
    rb = _rb()
    rb.send(chat, "✅ Брак ёзилди. Тасдиқлангач омбордан айрилади.\nЛистнинг қолган қисми ишласа — кусок омборига киритинг.",
            rb.ikb([[("🧩 Қолган қисмини кусокка киритиш", "sw:kin")]]))
    b = con.execute("SELECT * FROM raspil_brak WHERE id=?", (bid,)).fetchone()
    msgs = []
    for a in approvers():
        if a["id"] == rw["id"] and not a["owner"]:
            continue
        if photo:
            rb.send_photo(a["bot_uid"], photo, "🗑 Брак")
        m = rb.send(a["bot_uid"], brak_card(b), rb.ikb([[("✅ Тасдиқлаш", f"ib:ok:{bid}"), ("✖️ Рад", f"ib:no:{bid}")]], home=False))
        if m and m.get("message_id"):
            msgs.append([a["bot_uid"], m["message_id"]])
    con.execute("UPDATE raspil_brak SET msgs=? WHERE id=?", (json.dumps(msgs), bid))
    con.commit()
    return bid


def decide(con, bid, ok, who):
    """Тасдиқлаш / рад. -> (натижа матни, хато)"""
    b = con.execute("SELECT * FROM raspil_brak WHERE id=?", (bid,)).fetchone()
    if not b:
        return None, "Топилмади"
    if b["status"] != "new":
        return None, f"Аввал {'тасдиқланган' if b['status'] == 'ok' else 'рад этилган'} ({b['by_name']})"
    doc_id = None
    if ok:
        import erp
        book = round(erp.stock_all(con).get(b["good"]) or 0, 3)
        form = dict(date=date.today().isoformat(), note=f"Списание (брак) — {REASONS.get(b['reason'], b['reason'])}; цех-бот #{bid}",
                    lines=[dict(good=b["good"], fact=str(max(book, 0)), note=f"брак #{bid}")])
        with A.app.test_request_context():
            doc_id = erp.save_doc2(con, "inv", None, form, post=not erp.test_mode())
        # списание: фарқ — доим «−сони» (қолдиқ 0 ёки минус бўлса ҳам); проведение пайтида қайта ҳисобланмайди (erp.inv_rebook — «брак #»)
        con.execute("UPDATE erp_lines SET qty=?, price=?, price2=? WHERE doc_id=?", (-(b["qty"] or 0), round(book - (b["qty"] or 0), 3), book, doc_id))
        con.execute("UPDATE erp_docs SET created_by=?, updated_by=? WHERE id=?", (who, who, doc_id))
    con.execute("UPDATE raspil_brak SET status=?, by_name=?, by_at=?, doc_id=? WHERE id=?", ("ok" if ok else "no", who, A.now(), doc_id, bid))
    con.commit()
    b = con.execute("SELECT * FROM raspil_brak WHERE id=?", (bid,)).fetchone()
    rb = _rb()
    w = A.q("SELECT bot_uid FROM ready_workers WHERE id=?", (b["rw_id"],), one=True)
    if w and w["bot_uid"]:
        rb.send(w["bot_uid"], (f"✅ Брак тасдиқланди: {b['gname']} — {rb.P._n(b['qty'])}" if ok else
                               f"✖️ Брак рад этилди: {b['gname']} — {rb.P._n(b['qty'])}. Офисга учранг."))
    txt = brak_card(b) + (f"\n\n✅ Тасдиқланди ({who})" if ok else f"\n\n✖️ Рад этилди ({who})")
    for chat, mid in json.loads(b["msgs"] or "[]"):
        try:
            rb.edit(chat, mid, txt)
        except Exception:
            pass
    return txt, None


# ------------------------------------------------------------------ инвентаризация: ботда санаш (эгаси, 24.10)
# Ишчи «📦 Инвентаризация» → пастки меню алмашади (папкалар / қидириш / санаганларим / 🏠 Асосий меню).
# Папка → товарлар (платформадаги сони билан) → товар → ҳақиқий сонини ёзади. Йўқ товар — қидириб ёки номини ёзиб қўшади.
# Платформада: ҳамма саналганлар → инвентаризация «Факт»ига битта тугма; саналмаганлар рўйхати алоҳида.
def ic_kb():
    return {"keyboard": [[{"text": B_ICF}, {"text": B_ICS}], [{"text": B_ICM}], [{"text": _rb().B_HOME}]],
            "resize_keyboard": True, "is_persistent": True}


def _num(x):
    return _rb().P._n(round(x or 0, 3))


def counts(con, day):
    """{good: row} (каталог товарлари) ва янги (каталогда йўқ) қаторлар."""
    by, new = {}, []
    for r in con.execute("SELECT c.*, w.name wname FROM inv_count c LEFT JOIN ready_workers w ON w.id=c.rw_id WHERE c.day=? ORDER BY c.id", (day,)):
        (by.__setitem__(r["good"], dict(r)) if r["good"] else new.append(dict(r)))
    return by, new


def books(con):
    """Каталог товарининг «учёт» сони (санашда кўрсатиш учун). Платформада ҳали каталог қолдиғи йўқ (тест режими, приход/инвентаризация
    проведен эмас), шунинг учун 1С қолдиғидан: 1С'дан кўчирилган товар (skey «c1g:<1С id>») ёки «Ввод остатков из 1С» мослиги (stock_map).
    Платформада шу товарнинг ҳаракати бўлса — платформаники. -> {good: сони}"""
    import erp
    st = erp.stock_all(con)
    s1 = erp.stock_now()
    out = {}
    for r in con.execute("SELECT id, skey FROM cat_items WHERE skey LIKE 'c1g:%' AND deleted=0 AND COALESCE(svc,0)=0"):
        q = s1.get(r["skey"][4:])
        if q:
            out[f"ci{r['id']}"] = q
    c1g = set(out)
    try:
        for c1id, good in con.execute("SELECT c1, good FROM stock_map"):
            q = s1.get(c1id)
            if q and good not in c1g:
                out[good] = out.get(good, 0) + q
    except Exception:
        pass
    for g, q in st.items():
        if g.startswith("ci"):
            out[g] = q
    return {g: round(q, 3) for g, q in out.items()}


def stocked(con, bk=None):
    """Учёти бор каталог товарлари: [dict(good, gname, unit, stock)] (хизматлар, ўчирилганлар йўқ)."""
    import erp
    bk = books(con) if bk is None else bk
    out = []
    for r in con.execute("SELECT ext, name, unit FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 ORDER BY name"):
        q = bk.get(r["ext"])
        if not q or abs(q) < 0.0005 or erp.is_service(con, r["ext"]):
            continue
        out.append(dict(good=r["ext"], gname=r["name"], unit=r["unit"] or "", stock=q))
    return out


def _tree(con, day):
    """-> (folders {ext: (name, parent)}, goods {parent: [dict(good, gname, unit, book)]}, total {folder: (жами, саналган)}, cnt)"""
    import erp
    folders = {r["ext"]: (r["name"], r["parent"] or "") for r in
               con.execute("SELECT ext, name, parent FROM erp_goods WHERE src='cat' AND folder=1 AND deleted=0")}
    cnt, _ = counts(con, day)
    bk = books(con)
    items = {g["good"]: dict(g, book=g["stock"]) for g in stocked(con, bk)}
    for gd, c in cnt.items():
        if gd not in items:
            items[gd] = dict(good=gd, gname=c["gname"], unit=c["unit"], book=bk.get(gd) or 0)
    par = {r["ext"]: r["parent"] or "" for r in con.execute(
        f"SELECT ext, parent FROM erp_goods WHERE ext IN ({','.join('?' * len(items))})", tuple(items))} if items else {}
    goods, total = {}, {}
    for g in items.values():
        p = par.get(g["good"], "")
        p = p if p in folders else ""
        goods.setdefault(p, []).append(g)
        seen = set()
        while True:
            a, b = total.get(p, (0, 0))
            total[p] = (a + 1, b + (1 if g["good"] in cnt else 0))
            if not p or p in seen:
                break
            seen.add(p)
            p = folders.get(p, ("", ""))[1]
    return folders, goods, total, cnt


def view(con, day, folder="", page=0):
    """Папка кўриниши: ички папкалар ва товарлар (платформадаги сони; ✅ — саналган). -> (матн, инлайн тугмалар)"""
    folders, goods, total, cnt = _tree(con, day)
    if folder and folder not in folders:
        folder = ""
    subs = sorted((e for e, (n, p) in folders.items() if p == folder and total.get(e, (0, 0))[0]), key=lambda e: folders[e][0].casefold())
    items = [("f", e) for e in subs] + [("g", g) for g in sorted(goods.get(folder, []), key=lambda g: g["gname"].casefold())]
    pages = max(1, -(-len(items) // PAGE))
    page = min(max(page, 0), pages - 1)
    rows = []
    for kind, x in items[page * PAGE:(page + 1) * PAGE]:
        if kind == "f":
            a, b = total.get(x, (0, 0))
            rows.append([(f"{'✅' if a and a == b else '📁'} {folders[x][0][:40]} · {b}/{a}", f"ib:cf:{x}:0")])
        else:
            c = cnt.get(x["good"])
            t = (f"✅ {x['gname'][:38]} · {_num(x['book'])}→{_num(c['fact'])}" if c else f"{x['gname'][:44]} · {_num(x['book'])}")
            rows.append([(t, f"ib:cg:{x['good']}:{folder}:{page}")])
    nav = []
    if page > 0:
        nav.append(("◀️", f"ib:cf:{folder}:{page - 1}"))
    if folder:
        nav.append(("⬆️ Орқага", f"ib:cf:{folders[folder][1]}:0"))
    if page < pages - 1:
        nav.append(("▶️", f"ib:cf:{folder}:{page + 1}"))
    if nav:
        rows.append(nav)
    path, p, seen = [], folder, set()
    while p and p in folders and p not in seen:
        seen.add(p); path.insert(0, folders[p][0]); p = folders[p][1]
    a, b = total.get(folder, (0, 0))
    txt = (f"📦 Инвентаризация · {day[8:10]}.{day[5:7]}\n📁 {' / '.join(path) or 'Ҳамма папкалар'}\nСаналди: {b} / {a}"
           + (f" · {page + 1}/{pages}-бет" if pages > 1 else "")
           + ("\n\nТоварни босинг ва ҳақиқий сонини ёзинг. Рўйхатда йўқ товарни «🔍 Товар қидириш» билан топинг." if not folder else ""))
    if not items:
        txt += "\n\nБу ерда товар йўқ."
    return txt, _rb().ikb(rows)


def _ic_start(rw, chat):
    con = A.db()
    _pk().st_set(con, rw["id"], "ic")
    rb = _rb()
    rb.send(chat, f"📦 Инвентаризация ({inv_day()[8:10]}.{inv_day()[5:7]}). Омбордаги ҳар бир товарнинг ҳақиқий сонини ёзинг.\n"
                  "Пастда: папкалар, қидириш, санаганларингиз ва «🏠 Асосий меню».", ic_kb())
    t, kb = view(con, inv_day())
    rb.send(chat, t, kb)


def _ask_qty(con, day, good, gname, unit, f="", p=0):
    import erp
    book = (books(con).get(good) or 0) if good else 0
    c = con.execute("SELECT c.*, w.name wname FROM inv_count c LEFT JOIN ready_workers w ON w.id=c.rw_id WHERE c.day=? AND c.good=?",
                    (day, good)).fetchone() if good else None
    txt = (f"📦 {gname}\nПлатформада: {_num(book)} {unit or ''}"
           + (f"\nАввал саналган: {_num(c['fact'])} ({c['wname'] or ''} {c['at'][11:16]})" if c else "")
           + "\n\nНечта бор? Сонини ёзинг (масалан 10).\n«+3» — аввалгисига қўшади, 0 — йўқ.")
    return txt, book


def save_count(con, day, rw, good, gname, unit, val, add=False, new_name=""):
    """-> янги факт"""
    who = rw["name"] if rw and "name" in dict(rw) else ""
    old = (con.execute("SELECT * FROM inv_count WHERE day=? AND good=?", (day, good)).fetchone() if good else None)
    fact = round(((old["fact"] if old else 0) + val) if add else val, 3)
    if fact < 0:
        raise ValueError("манфий")
    h = json.loads(old["hist"] or "[]") if old else []
    h.append([A.now(), who, "+" if add else "=", val])
    if old:
        con.execute("UPDATE inv_count SET fact=?, rw_id=?, at=?, hist=? WHERE id=?", (fact, rw["id"] if rw else None, A.now(), json.dumps(h, ensure_ascii=False), old["id"]))
    else:
        con.execute("INSERT INTO inv_count(day, good, new_name, gname, unit, fact, rw_id, at, hist) VALUES(?,?,?,?,?,?,?,?,?)",
                    (day, good or None, new_name, gname, unit or "", fact, rw["id"] if rw else None, A.now(), json.dumps(h, ensure_ascii=False)))
    con.commit()
    return fact


def _my_counts(rw, day):
    con = A.db()
    rows = con.execute("SELECT * FROM inv_count WHERE day=? AND rw_id=? ORDER BY at DESC", (day, rw["id"])).fetchall()
    n = con.execute("SELECT COUNT(*) FROM inv_count WHERE day=?", (day,)).fetchone()[0]
    if not rows:
        return f"📋 Сиз ҳали ҳеч нарса санамадингиз. Ҳаммаси бўлиб саналган: {n} та товар."
    body = "\n".join(f"• {(r['gname'] or r['new_name'])[:44]} — {_num(r['fact'])} {r['unit'] or ''}" + (" (янги)" if not r["good"] else "")
                     for r in rows[:40])
    return f"📋 Сиз санаганлар: {len(rows)} та\n{body}" + ("\n…" if len(rows) > 40 else "") + f"\n\nҲаммаси бўлиб саналган: {n} та товар."


_QTY_RE = re.compile(r"\s*(\+)?\s*(\d+(?:[.,]\d+)?)\s*(?:та|шт|лист|м|м2|кг)?\s*")


def _ic_message(rw, chat, msg, text, mode, d):
    rb = _rb()
    con = A.db()
    day = inv_day()
    if not day:
        _pk().st_set(con, rw["id"])
        rb.send(chat, "ℹ️ Инвентаризация тугади.", rb.kb_for(rw))
        return True
    if text == B_ICF:
        _pk().st_set(con, rw["id"], "ic")
        t, kb = view(con, day)
        rb.send(chat, t, kb)
        return True
    if text == B_ICS:
        _pk().st_set(con, rw["id"], "ic", f=d.get("f", ""), p=d.get("p", 0))
        rb.send(chat, "🔍 Товар номини ёзинг (масалан: Мадера 16 ёки ХДФ оқ).", ic_kb())
        return True
    if text == B_ICM:
        rb.send(chat, _my_counts(rw, day), ic_kb())
        return True
    if mode in ("icq", "icn"):
        m = _QTY_RE.fullmatch(text or "")
        if not m or msg.get("photo"):
            rb.send(chat, "🔢 Фақат сонини ёзинг, масалан 10 ёки 2,5. «+3» — қўшади.")
            return True
        add, val = bool(m.group(1)), float(m.group(2).replace(",", "."))
        if val > 100000:
            rb.send(chat, "❓ Жуда катта сон. Қайта ёзинг.")
            return True
        if mode == "icq":
            fact = save_count(con, day, rw, d["good"], d["gname"], d.get("unit", ""), val, add)
            diff = round(fact - (d.get("book") or 0), 3)
            rb.send(chat, f"✅ {d['gname'][:60]} — {_num(fact)} {d.get('unit') or ''}"
                          f" (платформада {_num(d.get('book'))}, фарқ {'+' if diff > 0 else ''}{_num(diff)})", ic_kb())
        else:
            save_count(con, day, rw, None, "", "", val, False, new_name=d["name"])
            rb.send(chat, f"✅ Янги товар ёзилди: {d['name'][:60]} — {_num(val)}\nОфис уни каталогдаги товарга боғлайди.", ic_kb())
        _pk().st_set(con, rw["id"], "ic", f=d.get("f", ""), p=d.get("p", 0))
        t, kb = view(con, day, d.get("f", ""), d.get("p", 0))
        rb.send(chat, t, kb)
        return True
    # mode == "ic": матн — қидириш
    if msg.get("photo") or not text:
        rb.send(chat, "🔍 Товар номини ёзинг ёки папкадан танланг.", ic_kb())
        return True
    if len(text.strip()) < 2:
        rb.send(chat, "🔍 Камида 2 ҳарф ёзинг.")
        return True
    import erp
    cands = _search(con, text)[:8]
    st = books(con) if cands else {}
    _pk().st_set(con, rw["id"], "ic", f=d.get("f", ""), p=d.get("p", 0), q=text.strip()[:80],
                 cands=[[c["ext"], c["name"], c["unit"]] for c in cands])
    rows = [[(f"{c['name'][:44]} · {_num(st.get(c['ext']) or 0)}", f"ib:cs:{i}")] for i, c in enumerate(cands)]
    rows.append([(f"📝 «{text.strip()[:30]}» — каталогда йўқ, янги ёзиш", "ib:cn")])
    rb.send(chat, ("🔍 Топилди — қайси бири?" if cands else "❓ Каталогда топилмади. Бошқача ёзиб кўринг ёки янги товар сифатида ёзинг."),
            rb.ikb(rows))
    return True


def _ic_cb(rw, cq, act, parts):
    rb = _rb()
    con = A.db()
    chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    day = inv_day()
    if not day:
        return "Инвентаризация ёқилмаган"
    mode, d = _pk().st_get(con, rw["id"])
    if act == "cf":
        f = parts[2] if len(parts) > 2 else ""
        p = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        _pk().st_set(con, rw["id"], "ic", f=f, p=p)
        t, kb = view(con, day, f, p)
        rb.edit(chat, mid, t, kb)
        return ""
    if act in ("cg", "cs"):
        if act == "cg":
            good = parts[2] if len(parts) > 2 else ""
            f = parts[3] if len(parts) > 3 else ""
            p = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
            g = con.execute("SELECT ext, name, unit FROM erp_goods WHERE ext=? AND folder=0", (good,)).fetchone()
            if not g:
                return "Товар топилмади"
            good, gname, unit = g["ext"], g["name"], g["unit"] or ""
        else:
            cands = d.get("cands") or []
            i = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
            if not 0 <= i < len(cands):
                return "Қайта қидиринг"
            good, gname, unit = cands[i]
            f, p = d.get("f", ""), d.get("p", 0)
        txt, book = _ask_qty(con, day, good, gname, unit)
        _pk().st_set(con, rw["id"], "icq", good=good, gname=gname, unit=unit, book=book, f=f, p=p)
        rb.edit(chat, mid, txt, rb.ikb([[("⬅️ Рўйхатга", f"ib:cf:{f}:{p}")]]))
        return ""
    if act == "cn":
        q = (d.get("q") or "").strip()
        if mode != "ic" or not q:
            return "Аввал номини ёзинг"
        _pk().st_set(con, rw["id"], "icn", name=q, f=d.get("f", ""), p=d.get("p", 0))
        rb.edit(chat, mid, f"📝 Янги товар: {q}\n\nНечта бор? Сонини ёзинг.")
        return ""
    return "Эскирган тугма"


# ------------------------------------------------------------------ бот: матн, расм, тугмалар
def on_message(rw, chat, msg, text):
    """-> True агар шу модул қабул қилса."""
    rb = _rb()
    if text == B_BRAK and can_brak(rw):
        _brak_start(rw, chat)
        return True
    if text in (B_INVC, B_INVU):
        if inv_day() and can_brak(rw):
            _ic_start(rw, chat)
        else:
            rb.send(chat, "ℹ️ Инвентаризация ёқилмаган.", rb.kb_for(rw))
        return True
    con = A.db()
    mode, d = _pk().st_get(con, rw["id"])
    if mode in ("ic", "icq", "icn"):
        if text in _main_buttons():                       # асосий менюдаги тугма — санашдан чиқади
            _pk().st_set(con, rw["id"])
            return False
        return _ic_message(rw, chat, msg, text, mode, d)
    if text in (B_ICF, B_ICS, B_ICM) and inv_day():       # эски клавиатура қолган бўлса
        _pk().st_set(con, rw["id"], "ic")
        return _ic_message(rw, chat, msg, text, "ic", {})
    if mode not in ("invu", "brak_q", "brak_n", "brak_p", "brak_r"):
        return False
    ph = (msg.get("photo") or [])[-1] if msg.get("photo") else None
    if mode == "invu":
        if not inv_day():
            _pk().st_set(con, rw["id"])
            return False
        path = ""
        if ph:
            path = _save_photo(rw, ph, "inv")
            if not path:
                rb.send(chat, "⚠️ Расм юкланмади (интернет). Қайта ташланг.")
                return True
            rb.send_typing(chat)
        elif not re.search(r"\d{3,6}", text or ""):
            rb.send(chat, "📋 Накладной расмини ташланг ёки рақамини ёзинг. Тугатган бўлсангиз — «✅ Тугатдим».",
                    rb.ikb([[("✅ Тугатдим", "ib:ue")]]))
            return True
        _invu_doc(rw, chat, text if not ph else (msg.get("caption") or ""), path)
        return True
    if mode == "brak_q":
        if ph or not text:
            rb.send(chat, "🗑 Аввал листнинг номини ёзинг (масалан: ХДФ оқ).")
            return True
        cands = _search(con, text)
        if not cands:
            rb.send(chat, "❓ Каталогда топилмади. Бошқача ёзиб кўринг (масалан: «Мадера 16» ёки «ХДФ белый»).")
            return True
        _pk().st_set(con, rw["id"], "brak_q", cands=[[c["ext"], c["name"], c["unit"]] for c in cands])
        rb.send(chat, "🗑 Қайси бири?", rb.ikb([[(c["name"][:60], f"ib:bg:{i}")] for i, c in enumerate(cands)]))
        return True
    if mode == "brak_n":
        m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:та|шт|лист)?\s*", text or "")
        q = float(m.group(1).replace(",", ".")) if m else 0
        if not (0 < q <= 500):
            rb.send(chat, "🔢 Нечта? Фақат рақам ёзинг, масалан 1 ёки 0.5")
            return True
        d["qty"] = q
        _pk().st_set(con, rw["id"], "brak_r", **d)
        rb.send(chat, f"🗑 {d['gname']} — {rb.P._n(q)} {d.get('unit') or ''}\nСабаби?",
                rb.ikb([[(v, f"ib:br:{k}")] for k, v in REASONS.items()]))
        return True
    if mode == "brak_r":
        rb.send(chat, "👆 Сабабини тугмадан танланг.")
        return True
    if mode == "brak_p":
        if not ph:
            rb.send(chat, "📷 Синган листнинг расмини ташланг ёки «⏭ Расмсиз» босинг.", rb.ikb([[("⏭ Расмсиз", "ib:bp")]]))
            return True
        path = _save_photo(rw, ph, "brak")
        if not path:
            rb.send(chat, "⚠️ Расм юкланмади (интернет). Қайта ташланг ёки «⏭ Расмсиз».", rb.ikb([[("⏭ Расмсиз", "ib:bp")]]))
            return True
        _brak_save(rw, chat, d, path)
        return True
    return False


def _main_buttons():
    rb = _rb()
    import botlang
    return {rb.B_IN, rb.B_SWITCH, rb.B_WAIT, rb.B_OPEN, rb.B_KUS, rb.B_END, rb.B_TODAY, rb.B_SAL, rb.B_PAY, B_BRAK, botlang.B_LANG,
            getattr(rb, "B_PICK", ""), getattr(rb, "B_OWN", ""), getattr(rb, "B_KROYS", ""), getattr(rb, "B_KPI", "")} - {""}


def _save_photo(rw, ph, kind):
    folder = os.path.join(A.DATA_DIR, kind, datetime.now().strftime("%Y-%m"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{datetime.now():%d_%H%M%S}_{rw['id']}.jpg")
    return path if _rb().download_try(ph["file_id"], path) else ""


def on_cb(rw, cq):
    """ib:… тугмалари. -> қисқа жавоб."""
    rb = _rb()
    con = A.db()
    chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    parts = (cq.get("data") or "").split(":")
    act = parts[1] if len(parts) > 1 else ""
    if act == "ue":                                       # олиб кетилмаганлар: тугатдим
        _pk().st_set(con, rw["id"])
        n = con.execute("SELECT COUNT(*) FROM inv_unpicked WHERE day=? AND deleted=0", (inv_day() or date.today().isoformat(),)).fetchone()[0]
        rb.edit(chat, mid, f"✅ Тайёр. Жами {n} та сотилган листлар накладнойи ёзилди.")
        rb.send(chat, f"{rb.B_HOME} 👇", rb.kb_for(rw))
        return "Тайёр"
    if act in ("cf", "cg", "cs", "cn"):
        return _ic_cb(rw, cq, act, parts)
    if act in ("ok", "no"):
        if not (rb.is_owner(rw) or rb.is_mgr(rw) or dict(rw).get("mgr")):
            return "Рухсат йўқ"
        try:
            txt, err = decide(con, int(parts[2]), act == "ok", rw["name"])
        except ValueError as e:
            return str(e)[:150]
        return err or ("Тасдиқланди" if act == "ok" else "Рад этилди")
    mode, d = _pk().st_get(con, rw["id"])
    if act == "bg" and mode == "brak_q":
        cands = d.get("cands") or []
        i = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
        if not 0 <= i < len(cands):
            return "Қайта қидиринг"
        ext, name, unit = cands[i]
        _pk().st_set(con, rw["id"], "brak_n", good=ext, gname=name, unit=unit)
        rb.edit(chat, mid, f"🗑 {name}\n\nНечта {unit or 'лист'}? Рақам ёзинг (масалан 1).")
        return ""
    if act == "br" and mode == "brak_r":
        d["reason"] = parts[2] if len(parts) > 2 and parts[2] in REASONS else "oth"
        _pk().st_set(con, rw["id"], "brak_p", **d)
        rb.edit(chat, mid, f"🗑 {d['gname']} — {rb.P._n(d['qty'])} · {REASONS[d['reason']]}\n\n📷 Синган листнинг расмини ташланг.",
                rb.ikb([[("⏭ Расмсиз", "ib:bp")]]))
        return ""
    if act == "bp" and mode == "brak_p":
        rb.edit(chat, mid, f"🗑 {d['gname']} — {rb.P._n(d['qty'])} · {REASONS.get(d.get('reason'), '')}")
        _brak_save(rw, chat, d)
        return "Ёзилди"
    return "Эскирган тугма"


# ------------------------------------------------------------------ платформа
def summary(con, day):
    """Каталог товари бўйича жами олиб кетилмаган. -> (rows, unmapped)"""
    agg, unmapped = {}, []
    for u in con.execute("SELECT * FROM inv_unpicked WHERE day=? AND deleted=0", (day,)):
        for l in json.loads(u["lines"] or "[]"):
            if l.get("good"):
                a = agg.setdefault(l["good"], dict(good=l["good"], name=l.get("gname") or l["name"], unit=l.get("unit", ""), qty=0.0, docs=[]))
                a["qty"] = round(a["qty"] + (l.get("qty") or 0), 3)
                a["docs"].append(u["doc_no"])
            elif (l.get("qty") or 0) > 0:
                unmapped.append(dict(doc=u["doc_no"], uid=u["id"], **l))
    return sorted(agg.values(), key=lambda x: x["name"]), unmapped


def apply_to_inv(con, doc_id, day):
    """Инвентаризация черновигидаги «Факт»дан олиб кетилмаганларни айириш (бир марта). -> (n, хато)"""
    d = con.execute("SELECT * FROM erp_docs WHERE id=? AND kind='inv' AND src='pf'", (doc_id,)).fetchone()
    if not d or d["status"] != "draft":
        return 0, "Черновик инвентаризации не найден"
    mark = f"[сотилган листлар {day} айирилди]"
    if mark in (d["note"] or ""):
        return 0, "Из этой инвентаризации уже вычтено"
    rows, _ = summary(con, day)
    if not rows:
        return 0, "Список проданных листов пуст"
    by = {r["good"]: r for r in rows}
    lines = [dict(x) for x in con.execute("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (doc_id,))]
    have = {l["good"] for l in lines}
    form_lines, n = [], 0
    for l in lines:
        fact = l["price"] or 0
        note = l["note"] or ""
        if l["good"] in by:
            q = by[l["good"]]["qty"]
            fact = max(round(fact - q, 3), 0)
            note = (note + f" −{q:g} сотилган, омборда").strip()[:300]
            n += 1
        form_lines.append(dict(good=l["good"], fact=str(fact), note=note))
    missing = [r for r in rows if r["good"] not in have]
    import erp
    form = dict(date=d["date"], note=((d["note"] or "") + " " + mark).strip(), version=d["updated_at"] or "", lines=form_lines)
    erp.save_doc2(con, "inv", doc_id, form, post=False)
    con.commit()
    return n, ("Нет в инвентаризации (не вычтено): " + ", ".join(f"{r['name']} {r['qty']:g}" for r in missing[:10])) if missing else None


def count_rows(con, day):
    """Платформа: саналганлар (ҳозирги учёт ва фарқ билан), янгилар, саналмаганлар."""
    import erp
    st = books(con)
    by, new = counts(con, day)
    rows = []
    for g, c in by.items():
        bk = round(st.get(g) or 0, 3)
        rows.append(dict(c, book=bk, diff=round(c["fact"] - bk, 3)))
    rows.sort(key=lambda r: (r["gname"] or "").casefold())
    left = [g for g in stocked(con, st) if g["good"] not in by]
    return rows, new, left


def apply_counts(con, doc_id, day):
    """Саналганларни инвентаризация «Факт»ига ёзиш (бор қатор — алмаштиради, йўқ — қўшади). doc_id=None — янги черновик.
    -> (doc_id, n, янгилар сони)"""
    import erp
    by, new = counts(con, day)
    if not by:
        raise ValueError("Пересчёт пуст")
    lines, note, ver, ddate = [], f"Пересчёт из цех-бота {day[8:10]}.{day[5:7]}", "", day
    if doc_id:
        d = con.execute("SELECT * FROM erp_docs WHERE id=? AND kind='inv' AND src='pf'", (doc_id,)).fetchone()
        if not d or d["status"] != "draft":
            raise ValueError("Черновик инвентаризации не найден")
        note, ver, ddate = d["note"] or "", d["updated_at"] or "", d["date"]
        for l in con.execute("SELECT * FROM erp_lines WHERE doc_id=? ORDER BY lineno", (doc_id,)):
            if l["good"] in by:
                continue
            lines.append(dict(good=l["good"], fact=str(l["price"] or 0), note=l["note"] or ""))
    for g, c in sorted(by.items(), key=lambda x: (x[1]["gname"] or "").casefold()):
        if erp.is_service(con, g):
            continue
        lines.append(dict(good=g, fact=str(c["fact"]), note=f"цех-бот: {c.get('wname') or ''}"[:300]))
    form = dict(date=ddate, note=note, version=ver, lines=lines)
    with A.app.test_request_context():
        doc_id = erp.save_doc2(con, "inv", doc_id, form, post=False)
    con.commit()
    return doc_id, len(by), len(new)


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con); con.commit()
    finally:
        con.close()

    @app.route("/erp/inv-help", methods=["GET", "POST"])
    @A.admin_required
    def erp_inv_help():
        con = A.db()
        init_tables(con)
        day = (request.values.get("day") or "").strip() or (A.get_setting("inv_unpicked_day", "") or date.today().isoformat())
        if request.method == "POST":
            f, act = request.form, request.form.get("act")
            try:
                if act == "on":
                    A.set_settings(inv_unpicked_day=date.today().isoformat())
                    flash("Режим включён: в цех-боте появилась кнопка «📦 Инвентаризация»", "ok")
                    day = date.today().isoformat()
                elif act == "off":
                    A.set_settings(inv_unpicked_day="")
                    flash("Режим выключен", "ok")
                elif act == "qty":
                    u = con.execute("SELECT * FROM inv_unpicked WHERE id=?", (A.parse_float(f.get("uid"), 0),)).fetchone()
                    if u:
                        ls = json.loads(u["lines"] or "[]")
                        for i, l in enumerate(ls):
                            if f"q_{i}" in f:
                                l["qty"] = max(A.parse_float(f.get(f"q_{i}"), 0), 0)
                            g = (f.get(f"g_{i}") or "").strip()
                            if g and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND src='cat' AND folder=0", (g,)).fetchone():
                                l["good"] = g
                                l["gname"] = con.execute("SELECT name FROM erp_goods WHERE ext=?", (g,)).fetchone()[0]
                                con.execute("INSERT INTO stock_map(c1, good, at) VALUES(?,?,?) ON CONFLICT(c1) DO UPDATE SET good=excluded.good, at=excluded.at",
                                            (l["gid"], g, A.now()))
                        con.execute("UPDATE inv_unpicked SET lines=? WHERE id=?", (json.dumps(ls, ensure_ascii=False), u["id"]))
                        con.commit()
                        flash(f"№{u['doc_no']} сохранено", "ok")
                elif act == "del":
                    con.execute("UPDATE inv_unpicked SET deleted=1 WHERE id=?", (A.parse_float(f.get("uid"), 0),)); con.commit()
                    flash("Удалено из списка", "ok")
                elif act == "add":
                    import ready as R
                    doc, st, info = R.pick_doc(f.get("no") or "", "")
                    if not doc:
                        flash(f"Накладная №{f.get('no')} не найдена в 1С", "error")
                    else:
                        rw = dict(id=None)
                        msg = record_unpicked(con, rw, doc)
                        flash(msg.split("\n")[0], "ok")
                elif act == "apply":
                    n, warn = apply_to_inv(con, int(A.parse_float(f.get("doc"), 0)), day)
                    flash(f"Вычтено из «Факт»: {n} поз." if n else (warn or "Нечего вычитать"), "ok" if n else "error")
                    if warn and n:
                        flash(warn, "warn")
                elif act == "cfact":
                    c = con.execute("SELECT * FROM inv_count WHERE id=?", (int(A.parse_float(f.get("cid"), 0)),)).fetchone()
                    v = A.parse_float(f.get("fact"), -1)
                    if c and v >= 0:
                        con.execute("UPDATE inv_count SET fact=? WHERE id=?", (round(v, 3), c["id"])); con.commit()
                        flash("Сохранено", "ok")
                elif act == "cdel":
                    con.execute("DELETE FROM inv_count WHERE id=?", (int(A.parse_float(f.get("cid"), 0)),)); con.commit()
                    flash("Удалено из пересчёта", "ok")
                elif act == "cmap":
                    c = con.execute("SELECT * FROM inv_count WHERE id=? AND good IS NULL", (int(A.parse_float(f.get("cid"), 0)),)).fetchone()
                    g = con.execute("SELECT ext, name, unit FROM erp_goods WHERE ext=? AND src='cat' AND folder=0", ((f.get("g") or "").strip(),)).fetchone()
                    if not c or not g:
                        flash("Выберите товар каталога", "error")
                    else:
                        old = con.execute("SELECT * FROM inv_count WHERE day=? AND good=?", (c["day"], g["ext"])).fetchone()
                        if old:
                            con.execute("UPDATE inv_count SET fact=? WHERE id=?", (round(old["fact"] + c["fact"], 3), old["id"]))
                            con.execute("DELETE FROM inv_count WHERE id=?", (c["id"],))
                        else:
                            con.execute("UPDATE inv_count SET good=?, gname=?, unit=? WHERE id=?", (g["ext"], g["name"], g["unit"] or "", c["id"]))
                        con.commit()
                        flash("Товар привязан", "ok")
                elif act == "capply":
                    dv = (f.get("doc") or "").strip()
                    did, n, nn = apply_counts(con, int(dv) if dv.isdigit() else None, day)
                    flash(f"Перенесено в инвентаризацию: {n} поз.", "ok")
                    if nn:
                        flash(f"Не перенесено (нет товара каталога): {nn} поз.", "warn")
                    return redirect(url_for("erp_doc2_edit", doc_id=did))
                elif act in ("bok", "bno"):
                    u = A.current_user()
                    txt, err = decide(con, int(A.parse_float(f.get("bid"), 0)), act == "bok", (u["username"] if u else "") or "admin")
                    flash("Уже обработано" if err else ("Брак подтверждён — списано" if act == "bok" else "Брак отклонён"), "error" if err else "ok")
            except ValueError as e:
                flash(str(e), "error")
            return redirect(url_for("erp_inv_help", day=day))
        ups = []
        for u in con.execute("SELECT u.*, w.name wname FROM inv_unpicked u LEFT JOIN ready_workers w ON w.id=u.rw_id WHERE u.day=? AND u.deleted=0 "
                             "ORDER BY u.id DESC", (day,)):
            ups.append(dict(u, lines=json.loads(u["lines"] or "[]")))
        rows, unmapped = summary(con, day)
        invs = [dict(r, done="[сотилган листлар" in (r["note"] or "")) for r in
                con.execute("SELECT id, no, date, note FROM erp_docs WHERE kind='inv' AND src='pf' AND status='draft' ORDER BY id DESC LIMIT 10")]
        braks = con.execute("SELECT b.*, w.name wname FROM raspil_brak b LEFT JOIN ready_workers w ON w.id=b.rw_id ORDER BY b.id DESC LIMIT 60").fetchall()
        crows, cnew, cleft = count_rows(con, day)
        return render_template("erp_inv_help.html", day=day, on=bool(inv_day()), inv_d=inv_day(),
                               ups=ups, rows=rows, unmapped=unmapped, invs=invs, braks=braks, REASONS=REASONS,
                               crows=crows, cnew=cnew, cleft=cleft)

    @app.route("/erp/inv-help/photo/<kind>/<int:rid>")
    @A.admin_required
    def erp_inv_help_photo(kind, rid):
        con = A.db()
        r = con.execute("SELECT photo FROM inv_unpicked WHERE id=?" if kind == "u" else "SELECT photo FROM raspil_brak WHERE id=?", (rid,)).fetchone()
        p = r["photo"] if r else ""
        root = os.path.realpath(A.DATA_DIR)
        if not p or not os.path.realpath(p).startswith(root + os.sep) or not os.path.isfile(p):
            abort(404)
        return send_file(p, mimetype="image/jpeg")
