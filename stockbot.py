# -*- coding: utf-8 -*-
"""Цех-ботда омбор қолдиғи ва кромка сўрови (эгаси, 26.10).

«📊 Остатка» — каталог товарлари қолдиғи (папкалар бўйича, фақат қолдиғи борлари; қидириш ҳам). Рухсат платформада
(«Олиб келиш» → «Права в цех-боте»): p_stock = 1 — ҳамма каталог (цех бошлиғи), 2 — фақат кромка (кромкачи).
«📝 Кромка заказ» (p_kreq) — кромкачи керакли кромкаларни ёзади (номи ва метри), «Кассирга юбориш» — рўйхат «Олиб келиш»
саҳифасига тушади (кассир заказга қўшиб, шофёрга юборади) ва хабар кетади («Кому сообщать» рўйхатидагиларга)."""
import re
from datetime import datetime

A = None
B_STOCK = "📊 Остатка"
B_KREQ = "📝 Кромка заказ"
PAGE = 14
KROMKA_TOP = "Кромка"

SCHEMA = """
CREATE TABLE IF NOT EXISTS kreq_items(id INTEGER PRIMARY KEY, rw_id INTEGER NOT NULL, good TEXT NOT NULL, name TEXT DEFAULT '',
  unit TEXT DEFAULT '', qty REAL NOT NULL DEFAULT 0, at TEXT, sent_at TEXT, pickup_id INTEGER);
"""


def init_tables(con):
    con.executescript(SCHEMA)
    wc = {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}
    if wc and "p_stock" not in wc:
        con.execute("ALTER TABLE ready_workers ADD COLUMN p_stock INTEGER NOT NULL DEFAULT 0")     # 0 — йўқ, 1 — ҳамма, 2 — кромка
    if wc and "p_kreq" not in wc:
        con.execute("ALTER TABLE ready_workers ADD COLUMN p_kreq INTEGER NOT NULL DEFAULT 0")


def _rb():
    import raspil_bot
    return raspil_bot


def _pk():
    import pickup
    return pickup


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _n(v):
    v = round(float(v or 0), 3)
    return f"{v:g}".replace(".", ",")


def buttons(rw):
    d = dict(rw or {})
    out = []
    if d.get("p_stock"):
        out.append(B_STOCK)
    if d.get("p_kreq"):
        out.append(B_KREQ)
    return out


# ------------------------------------------------------------------ каталог дарахти ва қолдиқ
def _kromka_root(con):
    r = con.execute("SELECT ext FROM erp_goods WHERE src='cat' AND folder=1 AND deleted=0 AND COALESCE(parent,'')='' AND name=? "
                    "AND COALESCE(cat,'')<>'svc'", (KROMKA_TOP,)).fetchone()
    return r["ext"] if r else None


def _data(con):
    """(папкалар {ext: (номи, ота, тартиб)}, товарлар {ота: [dict(good, gname, unit, book)]}, қолдиқ {good: сон})."""
    import invbrak
    folders, goods, _, _ = invbrak._tree(con, "")
    return folders, goods


def _under(folders, e, root):
    seen = set()
    while e and e not in seen:
        if e == root:
            return True
        seen.add(e)
        e = folders.get(e, ("", "", 0))[1]
    return False


def view(con, rw, folder="", page=0):
    """Папка: ички папкалар (қолдиғи бор товарлар сони) ва қолдиғи бор товарлар. -> (матн, тугмалар)"""
    folders, goods = _data(con)
    root = _kromka_root(con) if dict(rw).get("p_stock") == 2 else ""
    if root is None:
        return "Каталогда «Кромка» папкаси топилмади.", None
    if folder and (folder not in folders or (root and not _under(folders, folder, root))):
        folder = root
    folder = folder or root
    kids = {}
    for e, (n, p, so) in folders.items():
        kids.setdefault(p, []).append(e)
    cnt = {}

    def count(e, depth=0):
        if e in cnt:
            return cnt[e]
        c = sum(1 for g in goods.get(e, []) if abs(g["book"]) > 0.0005)
        if depth < 40:
            c += sum(count(k, depth + 1) for k in kids.get(e, []))
        cnt[e] = c
        return c
    subs = sorted((e for e in kids.get(folder, []) if count(e)), key=lambda e: (folders[e][2], folders[e][0].casefold()))
    gl = sorted((g for g in goods.get(folder, []) if abs(g["book"]) > 0.0005), key=lambda g: g["gname"].casefold())
    pages = max(1, -(-len(gl) // PAGE))
    page = min(max(page, 0), pages - 1)
    rows = [[(f"📁 {folders[e][0][:40]} · {count(e)}", f"sk:f:{e}:0")] for e in (subs if page == 0 else [])]
    lines = [f"• {g['gname'][:60]} — {_n(g['book'])} {g['unit']}".rstrip() for g in gl[page * PAGE:(page + 1) * PAGE]]
    nav = []
    if page > 0:
        nav.append(("◀️", f"sk:f:{folder}:{page - 1}"))
    if folder and folder != root:
        nav.append(("⬆️ Орқага", f"sk:f:{folders[folder][1]}:0"))
    if page < pages - 1:
        nav.append(("▶️", f"sk:f:{folder}:{page + 1}"))
    if nav:
        rows.append(nav)
    path, p, seen = [], folder, set()
    while p and p in folders and p not in seen:
        seen.add(p); path.insert(0, folders[p][0]); p = folders[p][1]
    txt = (f"📊 Остатка · {datetime.now():%d.%m %H:%M}\n📁 {' / '.join(path) or 'Каталог'}"
           + (f" · {page + 1}/{pages}" if pages > 1 else ""))
    if lines:
        txt += "\n\n" + "\n".join(lines)
    elif not subs:
        txt += "\n\nБу ерда қолдиғи бор товар йўқ."
    if not folder or folder == root:
        txt += "\n\nТовар номини ёзсангиз — қидиради."
    return txt, _rb().ikb(rows) if rows else None


def search(con, rw, text, kromka=False, limit=15):
    """Каталог товарлари (кромкачига — фақат кромка) номи бўйича. -> [dict(good, gname, unit, book)]"""
    import kb as K
    folders, goods = _data(con)
    root = _kromka_root(con) if (kromka or dict(rw).get("p_stock") == 2) else ""
    allg = {g["good"]: dict(g, parent=p) for p, gs in goods.items() for g in gs}
    out, seen = [], set()
    for v in K.variants(text or ""):
        ws = [w for w in v.casefold().split() if w][:5]
        if not ws:
            continue
        sql = ("SELECT ext FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 AND COALESCE(cat,'')<>'svc' AND "
               + " AND ".join("lc LIKE ?" for _ in ws) + " ORDER BY length(name) LIMIT 200")
        for r in con.execute(sql, tuple(f"%{w}%" for w in ws)):
            g = allg.get(r["ext"])
            if not g or g["good"] in seen or (root and not _under(folders, g["parent"], root)):
                continue
            seen.add(g["good"]); out.append(g)
            if len(out) >= limit:
                return out
    return out


def _stock_text(res, q):
    if not res:
        return "Топилмади. Бошқача ёзиб кўринг (масалан: «дуб вотан», «19/1 кашемир»)."
    return "\n".join([f"🔍 «{q}»:"] + [f"• {g['gname'][:60]} — {_n(g['book'])} {g['unit']}".rstrip() for g in res])


# ------------------------------------------------------------------ кромка сўрови
def _draft(con, rw):
    return con.execute("SELECT * FROM kreq_items WHERE rw_id=? AND sent_at IS NULL ORDER BY id", (rw["id"],)).fetchall()


def kreq_view(con, rw):
    items = _draft(con, rw)
    out = ["📝 Кромка заказ", ""]
    if items:
        out += [f"{i}. {r['name']} — {_n(r['qty'])} {r['unit'] or 'м'}" for i, r in enumerate(items, 1)]
    else:
        out.append("Рўйхат бўш.")
    out += ["", "Керакли кромка номини ёзинг (масалан: «19/1 кашемир»)."]
    kb = [[(f"✖️ {i}", f"kq:d:{r['id']}") for i, r in enumerate(items, 1)][k:k + 5] for k in range(0, len(items), 5)]
    if items:
        kb.append([("📤 Кассирга юбориш", "kq:s")])
    return "\n".join(out), _rb().ikb(kb) if kb else None


def kreq_send(con, rw):
    """Рўйхат → «Олиб келиш» (pickup_items, қўлда қўшилган) ва хабар. -> сони"""
    pk = _pk()
    items = _draft(con, rw)
    if not items:
        return 0
    import erp
    fp = erp.folder_paths(con)
    t = now()
    lines = []
    for r in items:
        g = con.execute("SELECT ext, name, unit, parent FROM erp_goods WHERE ext=?", (r["good"],)).fetchone()
        path = fp.get(g["parent"], "") if g else ""
        pid = con.execute("INSERT INTO pickup_items(ext, good, name, unit, path, sup, need, status, first_at, upd_at, manual, note) "
                          "VALUES(?,?,?,?,?,?,?,'open',?,?,1,?)",
                          (f"kq{r['id']}:{r['good']}", r["good"], r["name"], r["unit"] or "м", path, pk.sup_of(con, r["good"], path, r["name"]),
                           r["qty"], t, t, f"{rw['name']} сўради (цех-бот)")).lastrowid
        con.execute("UPDATE kreq_items SET sent_at=?, pickup_id=? WHERE id=?", (t, pid, r["id"]))
        lines.append(f"• {r['name']} — {_n(r['qty'])} {r['unit'] or 'м'}")
    con.commit()
    pk.tell(con, f"📝 Кромка керак — {rw['name']}:\n" + "\n".join(lines)
            + "\n\nПлатформада «Олиб келиш» саҳифасида турибди — заказга қўшиб, шофёрга юборинг.")
    return len(items)


# ------------------------------------------------------------------ бот
def on_message(rw, chat, text):
    """-> True агар шу модул жавоб берса."""
    if not rw or not text:
        return False
    d0 = dict(rw)
    rb, pk = _rb(), _pk()
    con = A.db()
    if text == B_STOCK and d0.get("p_stock"):
        pk.st_set(con, rw["id"], "sk")
        t, kb = view(con, rw)
        rb.send(chat, t, kb)
        return True
    if text == B_KREQ and d0.get("p_kreq"):
        pk.st_set(con, rw["id"], "kq")
        t, kb = kreq_view(con, rw)
        rb.send(chat, t, kb)
        return True
    mode, d = pk.st_get(con, rw["id"])
    if mode == "sk" and d0.get("p_stock"):
        q = text.strip()
        rb.send(chat, _stock_text(search(con, rw, q), q))
        return True
    if mode == "kq" and d0.get("p_kreq"):
        q = text.strip()
        res = search(con, rw, q, kromka=True, limit=8)
        if not res:
            rb.send(chat, "Топилмади. Бошқача ёзиб кўринг (масалан: «дуб вотан», «19/1 кашемир»).")
            return True
        pk.st_set(con, rw["id"], "kq", cands=[g["good"] for g in res])
        rb.send(chat, "Қайси бири?", rb.ikb([[(f"{g['gname'][:48]} · {_n(g['book'])}", f"kq:g:{i}")] for i, g in enumerate(res)]
                                             + [[("⬅️ Рўйхатга", "kq:l")]]))
        return True
    if mode == "kqq" and d0.get("p_kreq"):
        m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(?:м|метр)?\s*", text)
        if not m or float(m.group(1).replace(",", ".")) <= 0:
            rb.send(chat, "Фақат сон ёзинг, масалан: 5", rb.ikb([[("⬅️ Рўйхатга", "kq:l")]]))
            return True
        g = con.execute("SELECT ext, name, unit FROM erp_goods WHERE ext=?", (d.get("good"),)).fetchone()
        if g:
            con.execute("INSERT INTO kreq_items(rw_id, good, name, unit, qty, at) VALUES(?,?,?,?,?,?)",
                        (rw["id"], g["ext"], g["name"], g["unit"] or "м", float(m.group(1).replace(",", ".")), now()))
            con.commit()
        pk.st_set(con, rw["id"], "kq")
        t, kb = kreq_view(con, rw)
        rb.send(chat, t, kb)
        return True
    return False


def on_cb(rw, cq):
    rb, pk = _rb(), _pk()
    con = A.db()
    data = cq.get("data") or ""
    parts = data.split(":")
    chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    d0 = dict(rw)
    if parts[0] == "sk":
        if not d0.get("p_stock"):
            return "Рухсат йўқ"
        f = parts[2] if len(parts) > 2 else ""
        p = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        pk.st_set(con, rw["id"], "sk")
        t, kb = view(con, rw, f, p)
        rb.edit(chat, mid, t, kb)
        return ""
    if not d0.get("p_kreq"):
        return "Рухсат йўқ"
    act = parts[1] if len(parts) > 1 else ""
    mode, d = pk.st_get(con, rw["id"])
    if act == "g":
        cands = d.get("cands") or []
        i = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else -1
        if not 0 <= i < len(cands):
            return "Қайта қидиринг"
        g = con.execute("SELECT ext, name, unit FROM erp_goods WHERE ext=?", (cands[i],)).fetchone()
        if not g:
            return "Топилмади"
        pk.st_set(con, rw["id"], "kqq", good=g["ext"])
        rb.edit(chat, mid, f"{g['name']}\n\nНеча метр керак? Сонини ёзинг.", rb.ikb([[("⬅️ Рўйхатга", "kq:l")]]))
        return ""
    if act == "d":
        con.execute("DELETE FROM kreq_items WHERE id=? AND rw_id=? AND sent_at IS NULL", (int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0, rw["id"]))
        con.commit()
    if act == "s":
        n = kreq_send(con, rw)
        pk.st_set(con, rw["id"])
        rb.edit(chat, mid, f"✅ Кассирга юборилди: {n} та кромка." if n else "Рўйхат бўш.")
        return ""
    pk.st_set(con, rw["id"], "kq")
    t, kb = kreq_view(con, rw)
    rb.edit(chat, mid, t, kb)
    return ""


def register(app_module):
    global A
    A = app_module
    con = A.connect()
    try:
        init_tables(con)
        con.commit()
    finally:
        con.close()
