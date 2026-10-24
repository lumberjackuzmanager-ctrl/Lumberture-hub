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
B_INVU = "📋 Сотилган листлар"
B_BRAK = "🗑 Брак"
REASONS = {"sin": "💥 Синди (ташиш, юклаш)", "cut": "✂️ Кесишда хато", "zav": "🏭 Заводдан брак", "oth": "Бошқа сабаб"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS inv_unpicked(id INTEGER PRIMARY KEY, day TEXT NOT NULL, iddoc TEXT NOT NULL, doc_no TEXT, doc_date TEXT,
  client TEXT DEFAULT '', rw_id INTEGER, at TEXT, photo TEXT DEFAULT '', lines TEXT DEFAULT '[]', deleted INTEGER NOT NULL DEFAULT 0,
  UNIQUE(day, iddoc));
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
    """Пастки клавиатурага: [Брак] ва остатка куни [Сотилган листлар]."""
    out = []
    if inv_day() and rw is not None and not _rb().owner_only(rw):
        out.append(B_INVU)
    if rw is None or can_brak(rw):
        out.append(B_BRAK)
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


# ------------------------------------------------------------------ бот: матн, расм, тугмалар
def on_message(rw, chat, msg, text):
    """-> True агар шу модул қабул қилса."""
    rb = _rb()
    if text == B_BRAK and can_brak(rw):
        _brak_start(rw, chat)
        return True
    if text == B_INVU:
        if inv_day():
            _invu_start(rw, chat)
        else:
            rb.send(chat, "ℹ️ Остатка режими ёқилмаган.", rb.kb_for(rw))
        return True
    con = A.db()
    mode, d = _pk().st_get(con, rw["id"])
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
                    flash("Режим включён: в цех-боте появилась кнопка «📋 Сотилган листлар» (только сегодня)", "ok")
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
        return render_template("erp_inv_help.html", day=day, on=bool(inv_day()), inv_d=inv_day(),
                               ups=ups, rows=rows, unmapped=unmapped, invs=invs, braks=braks, REASONS=REASONS)

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
