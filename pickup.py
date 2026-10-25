# -*- coding: utf-8 -*-
"""Олиб келиш рўйхати (шофёрлар учун).

1 ноябргача манба — 1С: кунлик проведен накладнойлардаги товар складда минусга кетган бўлса («Возврат» белгиси билан минусга
сотилади) — олиб келиш керак (эски минуслар эмас). 1С'да приход проведен бўлиб қолдиқ 0 дан ошса — қатор ўзи ёпилади.

Кассир платформада товарларни белгилаб, шофёрни танлаб юборади. Шофёр цех-ботда ҳар бир товар учун «Олдим» ёки «Йўқ экан»
босади. «Йўқ экан» ва белгиланмаганлар ҳақида хабар — танланган одамларга (админ, кассир, крой менежери).

Ҳолатлар: open — янги; sent — шофёрда; taken — олди (1С приходи кутилмоқда); missing — йўқ экан; skip — олиб келинмайди
(қўлда); done — ёпилди (қолдиқ минусдан чиқди)."""
import json, re, time
from datetime import datetime, timedelta

from flask import request, render_template, redirect, url_for, flash, abort

A = None
ACTIVE = ("open", "sent", "taken", "missing", "skip")
SYNC_SEC = 180
REMIND_SEC = 2 * 3600          # белгиланмаган товарлар бўйича шофёрга эслатма оралиғи
EOD_HOUR = 18                  # кун охиридаги ҳисобот (белгиланмаган ва йўқ экан)
WORK_HOURS = (9, 19)
CHUNK = 20                     # битта бот хабарида энг кўп товар

SCHEMA = """
CREATE TABLE IF NOT EXISTS pickup_items(id INTEGER PRIMARY KEY, ext TEXT NOT NULL, name TEXT DEFAULT '', unit TEXT DEFAULT '',
  path TEXT DEFAULT '', sup TEXT DEFAULT '', need REAL NOT NULL DEFAULT 0, sent_qty REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open', first_at TEXT, upd_at TEXT, rw_id INTEGER, sent_at TEXT, sent_by TEXT DEFAULT '',
  msg INTEGER, marked_at TEXT, closed_at TEXT, note TEXT DEFAULT '');
CREATE INDEX IF NOT EXISTS ix_pickup_items_st ON pickup_items(status);
CREATE INDEX IF NOT EXISTS ix_pickup_items_ext ON pickup_items(ext);
CREATE TABLE IF NOT EXISTS pickup_sup(ext TEXT PRIMARY KEY, sup TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pickup_msgs(id INTEGER PRIMARY KEY, rw_id INTEGER, chat INTEGER, msg_id INTEGER, sup TEXT DEFAULT '',
  at TEXT, items TEXT DEFAULT '[]', remind_at TEXT, by_user TEXT DEFAULT '');
"""

STATUS = {"open": "Новый", "sent": "У водителя", "taken": "Забрал — ждём приход в 1С", "missing": "Нет у поставщика", "skip": "Не везём",
          "done": "Закрыт"}


def init_tables(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}
    if "driver" not in cols:
        con.execute("ALTER TABLE ready_workers ADD COLUMN driver INTEGER NOT NULL DEFAULT 0")
    if "refs" not in {r[1] for r in con.execute("PRAGMA table_info(pickup_items)")}:
        con.execute("ALTER TABLE pickup_items ADD COLUMN refs TEXT DEFAULT '[]'")
        # аввалги усулда (ҳамма минус) тушган, ҳали юборилмаган қаторлар — олиб ташланади
        con.execute("DELETE FROM pickup_items WHERE status='open' AND sent_at IS NULL")
    ic = {r[1] for r in con.execute("PRAGMA table_info(pickup_items)")}
    for col, ddl in (("recv_qty", "REAL"), ("good", "TEXT DEFAULT ''"), ("manual", "INTEGER NOT NULL DEFAULT 0")):
        if col not in ic:
            con.execute(f"ALTER TABLE pickup_items ADD COLUMN {col} {ddl}")
    mc = {r[1] for r in con.execute("PRAGMA table_info(pickup_msgs)")}
    for col, ddl in (("state", "TEXT NOT NULL DEFAULT 'sent'"), ("sup_ext", "TEXT DEFAULT ''"), ("ext_driver", "TEXT DEFAULT ''"),
                     ("by_rw", "INTEGER"), ("sel", "TEXT DEFAULT '[]'"), ("recv_at", "TEXT"), ("recv_by", "TEXT DEFAULT ''"),
                     ("doc_id", "INTEGER"), ("err", "TEXT DEFAULT ''")):
        if col not in mc:
            con.execute(f"ALTER TABLE pickup_msgs ADD COLUMN {col} {ddl}")
    wc = {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}
    if "p_order" not in wc:                     # кимга рухсат — платформада белгиланади (эгаси айтди); бошланғичи: менежер/кройчи, цех бошлиғи
        con.execute("ALTER TABLE ready_workers ADD COLUMN p_order INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE ready_workers SET p_order=1 WHERE active=1 AND (mgr=1 OR kroy=1)")
    if "p_recv" not in wc:
        con.execute("ALTER TABLE ready_workers ADD COLUMN p_recv INTEGER NOT NULL DEFAULT 0")
        con.execute("UPDATE ready_workers SET p_recv=1 WHERE active=1 AND boss=1")
    con.execute("CREATE TABLE IF NOT EXISTS pickup_state(rw_id INTEGER PRIMARY KEY, mode TEXT, data TEXT, at TEXT)")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_q(v):
    v = float(v or 0)
    return str(int(round(v))) if abs(v - round(v)) < 0.005 else f"{v:.2f}".rstrip("0").rstrip(".")


# ------------------------------------------------------------------ 1С'дан етишмовчилик
START = "2026-10-22"            # шу санадан олдинги накладнойлар ҳисобга олинмайди (эски минуслар — ҳисоб хатолари)


def _skip_good(path, name):
    top = (path or "").split(" / ")[0].strip()
    if re.match(r"\d+\s", top) and not re.match(r"6\s", top):         # 1–9, 99 — хизматлар («6 Кромка склад новый» — товар)
        return True
    return bool(re.search(r"б\s*/\s*у", f"{path} {name}", re.I))       # ишлатилган плита — сотиб олинмайди


def build(st, g, orders, folder_path):
    """Кунлик буюртмадан етишмовчилик (эгаси айтди): янги накладнойдаги товар складда минусда бўлса — олиб келиш керак.
    Миқдор = min(минус, шу накладнойлардаги сон). Эски минус (янги накладнойсиз) — рўйхатга тушмайди.
    orders: [dict(gid, qty, no, date, client)] — START дан бери накладнойлар (проведен қилинмаганлари ҳам: st уларсиз қолдиқ)."""
    dem = {}
    for o in orders:
        if (o.get("qty") or 0) > 0:
            dem.setdefault(o["gid"], []).append(o)
    out = {}
    for gid, lines in dem.items():
        q = st.get(gid, 0.0)
        if q >= -0.0005:
            continue
        it = g.get(gid)
        if not it or it["folder"] or it["deleted"]:
            continue
        path = folder_path(g, it["parent"])
        if _skip_good(path, it["name"]):
            continue
        tot = sum(o["qty"] for o in lines)
        need = round(min(-q, tot), 3)
        if need <= 0.0005:
            continue
        lines.sort(key=lambda o: (o["date"], o.get("no") or ""), reverse=True)
        refs, acc = [], 0.0
        for o in lines:                                    # энг янги накладнойлардан — етишмаган миқдоргача
            refs.append(dict(no=o.get("no") or "", client=o.get("client") or "", qty=round(o["qty"], 3), date=str(o["date"])[:10]))
            acc += o["qty"]
            if acc >= need - 0.0005:
                break
        out[gid] = dict(name=it["name"], unit=it["unit"], path=path, need=need, refs=refs)
    return out


CUTOVER = "2026-10-26"          # 1С ўчирилган кун: шундан бошлаб буюртмалар — платформа накладнойлари (каталог товарлари)


def shortages_pf(con=None):
    """Платформа накладнойлари (CUTOVER дан, черновик ва проведен) бўйича етишмовчилик — каталог товарлари («ci…»).
    Қолдиқ — erp.stock_all (1С регистри + платформа ҳужжатлари); проведен қилинмаганлари қолдиқдан айрилади.
    Хизматлар (кромкалаш…) норматив бўйича товарга (кромка, клей) айланади. -> {good: dict(...)}"""
    import erp
    con = con or A.db()
    since = max((A.get_setting("pickup_from", "") or START) if A else START, CUTOVER)
    st = dict(erp.stock_all(con))
    g = {}
    for r in con.execute("SELECT ext, name, unit, parent, folder, deleted, COALESCE(cat,'') cat FROM erp_goods WHERE src='cat'"):
        g[r["ext"]] = dict(name=r["name"] or "", unit=r["unit"] or "", parent=r["parent"] or "", folder=bool(r["folder"]),
                           deleted=bool(r["deleted"]) or r["cat"] == "svc")
    orders = []
    for d in con.execute("SELECT d.id, d.no, d.date, d.status, COALESCE(c.name,'') cname FROM erp_docs d LEFT JOIN erp_clients c ON c.ext=d.client "
                         "WHERE d.src='pf' AND d.kind='sale' AND d.status IN ('draft','posted') AND d.date>=?", (since,)).fetchall():
        pairs = [(l["good"], float(l["qty"] or 0)) for l in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (d["id"],))]
        for gid, q in erp.expand_lines(pairs):
            if gid not in g:
                continue
            if d["status"] != "posted":
                st[gid] = st.get(gid, 0.0) - q
            orders.append(dict(gid=gid, qty=q, no=(d["no"] or "") + ("" if d["status"] == "posted" else " (не проведен)"), date=d["date"],
                               client=d["cname"]))

    def fpath(items, fid):
        names, seen = [], set()
        while fid and fid in items and fid not in seen:
            seen.add(fid); names.append(items[fid]["name"]); fid = items[fid]["parent"]
        return " / ".join(reversed(names))
    return build(st, g, orders, fpath)


def shortages():
    """{товар id: dict(name, unit, path, need, refs)}: 1С архиви (1С товарлари) + платформа накладнойлари (каталог товарлари).
    None — иккаласи ҳам ўқилмади."""
    pf = None
    try:
        pf = shortages_pf()
    except Exception:
        if A:
            A.errlog.exception("pickup pf shortages")
    if datetime.now().strftime("%Y-%m-%d") >= CUTOVER:
        return pf                                  # 1С ўчди: 1С товарлари бўйича эски қаторлар ёпилади (1С'да приход бўлмайди)
    old = _shortages_1c()
    if old is None:
        return pf
    return {**old, **(pf or {})}


def _shortages_1c():
    """{1С товар id: dict(name, unit, path, need, refs)}. None — 1С очилмади."""
    import c1
    try:
        if not c1.base_path():
            return None
        _, st = c1.stock()
        g = c1.goods()
        since = (A.get_setting("pickup_from", "") or START) if A else START
        from datetime import date as _d
        d1 = _d.fromisoformat(since)
        jd = {k: v for k, v in c1.journal().items() if v["kind"] == 210 and v["date"] and v["date"] >= d1 and not v["deleted"]}
        st = dict(st)

        cl = c1.clients()
        heads = {h["IDDOC"]: h for h in c1.table("DH210") if h["IDDOC"] in jd}
        orders = []
        for x in c1.table("DT210"):
            d = jd.get(x["IDDOC"])
            if not d:
                continue
            h = heads.get(x["IDDOC"])
            q = float(x["SP217"] or 0)
            if not d["posted"]:
                st[x["SP216"]] = st.get(x["SP216"], 0.0) - q
            orders.append(dict(gid=x["SP216"], qty=q, no=d["no"] + ("" if d["posted"] else " (не проведен)"), date=d["date"],
                               client=cl.get(h["SP232"], {}).get("name", "") if h else ""))
    except Exception:
        return None
    return build(st, g, orders, c1.folder_path)


def sup_of(con, ext, path, name):
    r = con.execute("SELECT sup FROM pickup_sup WHERE ext=?", (ext,)).fetchone()
    if r:
        return r[0]
    import catalog
    return catalog.sup_for([p for p in (path or "").split(" / ") if p.strip()] + [name or ""])


def sync(con, data=None):
    """1С минуслари -> pickup_items. -> (янги, ёпилган) ёки None (1С очилмади)."""
    data = shortages() if data is None else data
    if data is None:
        return None
    t = now()
    added = closed = 0
    act = {r["ext"]: r for r in con.execute(f"SELECT * FROM pickup_items WHERE COALESCE(manual,0)=0 AND status IN ({','.join('?' * len(ACTIVE))})", ACTIVE)}
    for ext, d in data.items():
        r = act.get(ext)
        if r:
            refs = json.dumps(d.get("refs") or [], ensure_ascii=False)
            if abs((r["need"] or 0) - d["need"]) > 0.0005 or r["name"] != d["name"] or (r["refs"] or "[]") != refs:
                con.execute("UPDATE pickup_items SET need=?, name=?, unit=?, path=?, refs=?, upd_at=? WHERE id=?",
                            (d["need"], d["name"], d["unit"], d["path"], refs, t, r["id"]))
        else:
            con.execute("INSERT INTO pickup_items(ext, name, unit, path, sup, need, refs, status, first_at, upd_at) VALUES(?,?,?,?,?,?,?,'open',?,?)",
                        (ext, d["name"], d["unit"], d["path"], sup_of(con, ext, d["path"], d["name"]), d["need"],
                         json.dumps(d.get("refs") or [], ensure_ascii=False), t, t))
            added += 1
    for ext, r in act.items():
        if ext not in data:                                   # 1С'да қолдиқ минусдан чиқди (келди) ёки накладной бекор қилинди
            con.execute("UPDATE pickup_items SET status='done', closed_at=?, upd_at=? WHERE id=?", (t, t, r["id"]))
            closed += 1
    con.commit()
    return added, closed


# ------------------------------------------------------------------ одамлар
def drivers(con):
    return con.execute("SELECT * FROM ready_workers WHERE active=1 AND driver=1 ORDER BY name").fetchall()


def notify_people(con):
    """Хабар оладиганлар (ready_workers id). Созланмаган бўлса — менежер, кройчи ва шериклар."""
    try:
        ids = json.loads(A.get_setting("pickup_notify", "") or "null")
    except ValueError:
        ids = None
    if ids is None:
        return [r for r in con.execute("SELECT * FROM ready_workers WHERE active=1 AND bot_uid IS NOT NULL AND (mgr=1 OR kroy=1 OR owner=1)")]
    if not ids:
        return []
    return con.execute(f"SELECT * FROM ready_workers WHERE active=1 AND bot_uid IS NOT NULL AND id IN ({','.join('?' * len(ids))})",
                       tuple(int(i) for i in ids)).fetchall()


def bot_people(con):
    return con.execute("SELECT * FROM ready_workers WHERE active=1 AND bot_uid IS NOT NULL ORDER BY name").fetchall()


def _rb():
    import raspil_bot
    return raspil_bot


def tell(con, text):
    rb = _rb()
    for p in notify_people(con):
        rb.send(p["bot_uid"], text)


# ------------------------------------------------------------------ шофёрга хабар
def msg_view(con, m):
    """Хабар матни ва тугмалари (pickup_msgs қатори бўйича)."""
    ids = json.loads(m["items"] or "[]")
    rows = {r["id"]: r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    lines, kb = [f"🚚 Олиб келиш — {m['sup']}", f"📅 {m['at'][8:10]}.{m['at'][5:7]} {m['at'][11:16]}", ""], []
    left = 0
    for i, iid in enumerate(ids, 1):
        r = rows.get(iid)
        if not r:
            continue
        mark = {"taken": "✅", "missing": "❌", "done": "✅"}.get(r["status"], "▫️")
        if r["status"] == "sent" and r["msg"] == m["id"]:
            left += 1
            kb.append([(f"✅ {i} Олдим", f"pk:ok:{iid}"), (f"❌ {i} Йўқ экан", f"pk:no:{iid}")])
        elif r["status"] not in ("taken", "missing", "done"):
            mark = "↪️"
        lines.append(f"{mark} {i}. {r['name']} — {fmt_q(r['sent_qty'] or r['need'])} {r['unit'] or ''}".rstrip())
    lines += ["", "Ҳар бирини белгиланг 👇" if left else "✔️ Ҳаммаси белгиланди. Раҳмат!"]
    return "\n".join(lines), (_rb().ikb(kb) if kb else None), left


def _dispatch(con, mid, d):
    """pickup_msgs[mid] (танланган қаторлари items'да) — шофёрга юбориш. -> хато матни ёки ''."""
    rb = _rb()
    t = now()
    m = con.execute("SELECT * FROM pickup_msgs WHERE id=?", (mid,)).fetchone()
    ids = json.loads(m["items"] or "[]")
    con.execute("UPDATE pickup_msgs SET state='sent', rw_id=?, chat=?, at=? WHERE id=?", (d["id"], d["bot_uid"], t, mid))
    for iid in ids:
        con.execute("UPDATE pickup_items SET status='sent', rw_id=?, sent_at=?, sent_by=?, sent_qty=need, msg=?, marked_at=NULL, upd_at=? WHERE id=?",
                    (d["id"], t, m["by_user"] or "", mid, t, iid))
    con.commit()
    m = con.execute("SELECT * FROM pickup_msgs WHERE id=?", (mid,)).fetchone()
    text, kb, _ = msg_view(con, m)
    res = rb.send(d["bot_uid"], text, kb)
    if not res:
        con.execute("UPDATE pickup_items SET status='open', rw_id=NULL, sent_at=NULL, msg=NULL WHERE msg=?", (mid,))
        con.execute("UPDATE pickup_msgs SET state='draft' WHERE id=?", (mid,))
        con.commit()
        return "Telegram не принял сообщение — попробуйте ещё раз позже"
    con.execute("UPDATE pickup_msgs SET msg_id=? WHERE id=?", (res.get("message_id"), mid))
    con.commit()
    return ""


def send_to_driver(con, ids, rw_id, user=""):
    """Танланган қаторларни шофёрга (етказиб берувчи бўйича алоҳида хабар). -> (юборилган товар сони, хато матни)."""
    rb = _rb()
    d = con.execute("SELECT * FROM ready_workers WHERE id=? AND active=1", (rw_id,)).fetchone()
    if not d or not d["bot_uid"]:
        return 0, "Водитель не подключён к цех-боту (не отправил свой номер)"
    if not rb.token():
        return 0, "Цех-бот не подключён"
    rows = con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))}) AND status IN ('open','missing','sent','taken')",
                       tuple(ids)).fetchall() if ids else []
    by = {}
    for r in rows:
        by.setdefault(r["sup"] or "Эман", []).append(r)
    t, n = now(), 0
    for sup, items in sorted(by.items()):
        for k in range(0, len(items), CHUNK):
            part = items[k:k + CHUNK]
            mid = con.execute("INSERT INTO pickup_msgs(rw_id, chat, sup, sup_ext, at, items, by_user, state) VALUES(?,?,?,?,?,?,?,'draft')",
                              (rw_id, d["bot_uid"], sup, default_sup_ext(con, sup), t, json.dumps([r["id"] for r in part]), user)).lastrowid
            err = _dispatch(con, mid, d)
            if err:
                con.execute("DELETE FROM pickup_msgs WHERE id=?", (mid,))
                con.commit()
                return n, err
            n += len(part)
    return n, ""


def on_cb(rw, cq):
    """Цех-бот тугмаси: pk:ok:<id> / pk:no:<id>. -> қисқа жавоб."""
    con = A.db()
    parts = (cq.get("data") or "").split(":")
    if len(parts) != 3 or not parts[2].isdigit():
        return "?"
    r = con.execute("SELECT * FROM pickup_items WHERE id=?", (int(parts[2]),)).fetchone()
    if not r or not r["msg"]:
        return "Бу рўйхат эскирган"
    m = con.execute("SELECT * FROM pickup_msgs WHERE id=?", (r["msg"],)).fetchone()
    if not m or (m["rw_id"] != rw["id"] and not dict(rw).get("driver")):
        return "Бу рўйхат сизга эмас"
    if r["status"] != "sent":
        note = "Аввал белгиланган"
    else:
        st = "taken" if parts[1] == "ok" else "missing"
        con.execute("UPDATE pickup_items SET status=?, marked_at=?, upd_at=? WHERE id=?", (st, now(), now(), r["id"]))
        con.commit()
        note = "✅ Олдим" if st == "taken" else "❌ Йўқ экан"
        if st == "missing":
            tell(con, f"❌ Йўқ экан — {m['sup']}\n{r['name']} — {fmt_q(r['sent_qty'] or r['need'])} {r['unit'] or ''}\n"
                      f"Шофёр: {rw['name']}\nПлатформа → Олиб келиш: бошқа жойдан олиш ёки мижозга айтиш керак.")
    text, kb, _ = msg_view(con, m)
    msg = cq.get("message") or {}
    _rb().edit(msg.get("chat", {}).get("id") or m["chat"], msg.get("message_id") or m["msg_id"], text, kb)
    return note


def driver_list(rw, chat):
    """Шофёр «🚚 Олиб келиш» тугмасини босса — белгиланмаган рўйхатлари қайта келади."""
    con = A.db()
    rb = _rb()
    ms = con.execute("SELECT DISTINCT m.* FROM pickup_msgs m JOIN pickup_items i ON i.msg=m.id AND i.status='sent' WHERE m.rw_id=? ORDER BY m.id",
                     (rw["id"],)).fetchall()
    if not ms:
        rb.send(chat, "✔️ Ҳозир олиб келадиган товар йўқ.", rb.kb_for(rw))
        return
    for m in ms:
        text, kb, _ = msg_view(con, m)
        res = rb.send(chat, text, kb)
        if res:
            con.execute("UPDATE pickup_msgs SET msg_id=?, chat=? WHERE id=?", (res.get("message_id"), chat, m["id"]))
    con.commit()


# ------------------------------------------------------------------ эслатмалар
def remind(con, dt=None):
    dt = dt or datetime.now()
    if not (WORK_HOURS[0] <= dt.hour < WORK_HOURS[1]):
        return 0
    rb = _rb()
    lim = (dt - timedelta(seconds=REMIND_SEC)).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for m in con.execute("SELECT DISTINCT m.* FROM pickup_msgs m JOIN pickup_items i ON i.msg=m.id AND i.status='sent' "
                         "WHERE m.rw_id IS NOT NULL AND m.chat IS NOT NULL AND m.state='sent' AND m.at<=? AND COALESCE(m.remind_at,'')<=?", (lim, lim)).fetchall():
        left = con.execute("SELECT COUNT(*) FROM pickup_items WHERE msg=? AND status='sent'", (m["id"],)).fetchone()[0]
        if left:
            rb.send(m["chat"], f"⏰ {m['sup']}: {left} та товар белгиланмаган. Олдингизми?", reply_to=m["msg_id"])
            n += 1
        con.execute("UPDATE pickup_msgs SET remind_at=? WHERE id=?", (dt.strftime("%Y-%m-%d %H:%M:%S"), m["id"]))
    con.commit()
    return n


def eod(con, dt=None):
    """Кун охири (18:00): белгиланмаган ва «йўқ экан» товарлар рўйхати — масъулларга (бир марта)."""
    dt = dt or datetime.now()
    day = dt.strftime("%Y-%m-%d")
    if dt.hour < EOD_HOUR or (A.get_setting("pickup_eod", "") or "") >= day:
        return False
    A.set_settings(pickup_eod=day)
    rows = con.execute("SELECT i.*, w.name dname FROM pickup_items i LEFT JOIN ready_workers w ON w.id=i.rw_id "
                       "WHERE i.status IN ('sent','missing') ORDER BY i.status, i.sup, i.name").fetchall()
    if not rows:
        return False
    out = ["📋 Олиб келиш — кун охири"]
    for st, title in (("sent", "⏳ Белгиланмаган"), ("missing", "❌ Йўқ экан")):
        part = [r for r in rows if r["status"] == st]
        if part:
            out += ["", title + f" ({len(part)}):"]
            out += [f"• {r['sup']}: {r['name']} — {fmt_q(r['sent_qty'] or r['need'])} {r['unit'] or ''} ({r['dname'] or '—'})".rstrip() for r in part[:40]]
    tell(con, "\n".join(out))
    return True


def tick(dt=None):
    con = A.connect()
    try:
        init_tables(con)
        last = float(A.get_setting("pickup_sync_at", "0") or 0)
        if time.time() - last >= SYNC_SEC:
            A.set_settings(pickup_sync_at=str(time.time()))
            sync(con)
        remind(con, dt)
        eod(con, dt)
    finally:
        con.close()


# ------------------------------------------------------------------ саҳифа
def page_rows(con):
    rows = [dict(r) for r in con.execute("SELECT i.*, w.name dname FROM pickup_items i LEFT JOIN ready_workers w ON w.id=i.rw_id "
                                         f"WHERE i.status IN ({','.join('?' * len(ACTIVE))}) ORDER BY i.sup, i.path, i.name", ACTIVE)]
    for r in rows:
        try:
            r["refs_l"] = json.loads(r.get("refs") or "[]")
        except ValueError:
            r["refs_l"] = []
        r["more"] = r["status"] in ("sent", "taken") and (r["need"] or 0) > (r["sent_qty"] or 0) + 0.0005
        r["pick"] = r["status"] in ("open", "missing") or r["more"]
    groups = {}
    for r in rows:
        groups.setdefault(r["sup"] or "Эман", []).append(r)
    order = {"open": 0, "missing": 1, "sent": 2, "taken": 3, "skip": 4}
    for v in groups.values():
        v.sort(key=lambda r: (order.get(r["status"], 9), r["path"], r["name"]))
    return sorted(groups.items(), key=lambda kv: (-sum(1 for r in kv[1] if r["pick"]), kv[0]))


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con)
        if _seed_roles(con):
            con.commit()
            try:
                import roles
                roles.reload(con)
            except Exception:
                A.errlog.exception("pickup roles reload")
        con.commit()
    finally:
        con.close()

    _orig = A.scheduler_tick

    def sched(dt=None):
        r = _orig(dt)
        try:
            tick(dt)
        except Exception:
            A.errlog.exception("pickup tick")
        return r
    A.scheduler_tick = sched

    @app.route("/erp/pickup", methods=["GET", "POST"])
    @A.login_required
    def erp_pickup():
        con = A.db()
        init_tables(con)
        u = A.current_user()
        if request.method == "POST":
            f = request.form
            act = f.get("act")
            ids = [int(x) for x in f.getlist("id") if x.isdigit()]
            if act == "send":
                rw = int(f.get("driver") or 0)
                if not ids:
                    flash("Отметьте товары", "error")
                elif not rw:
                    flash("Выберите водителя", "error")
                else:
                    n, err = send_to_driver(con, ids, rw, u["username"])
                    if err:
                        flash(err, "error")
                    if n:
                        flash(f"Отправлено водителю: {n} поз.", "ok")
            elif act == "skip" and ids:
                con.execute(f"UPDATE pickup_items SET status='skip', upd_at=? WHERE id IN ({','.join('?' * len(ids))})", (now(), *ids))
                con.commit()
                flash(f"Отмечено «не везём»: {len(ids)} поз. Когда остаток в 1С выправится, строка закроется сама", "ok")
            elif act == "unskip" and ids:
                con.execute(f"UPDATE pickup_items SET status='open', upd_at=? WHERE status='skip' AND id IN ({','.join('?' * len(ids))})", (now(), *ids))
                con.commit()
                flash("Возвращено в список", "ok")
            elif act == "sup":
                iid, sup = int(f.get("item") or 0), (f.get("sup") or "").strip()[:80]
                r = con.execute("SELECT * FROM pickup_items WHERE id=?", (iid,)).fetchone()
                if r and sup:
                    con.execute("INSERT INTO pickup_sup(ext, sup) VALUES(?,?) ON CONFLICT(ext) DO UPDATE SET sup=excluded.sup", (r["ext"], sup))
                    con.execute("UPDATE pickup_items SET sup=? WHERE ext=? AND status IN ('open','missing','skip')", (sup, r["ext"]))
                    con.commit()
                    flash("Поставщик сохранён (запомнится для этого товара)", "ok")
            elif act == "perm" and u["role"] == "admin":
                po, pr, dv, pe, kq = ({int(x) for x in f.getlist(k) if x.isdigit()} for k in ("p_order", "p_recv", "driver_w", "p_edge", "p_kreq"))
                for p_ in con.execute("SELECT id FROM ready_workers WHERE active=1").fetchall():
                    ps = f.get(f"p_stock_{p_['id']}", "0")
                    con.execute("UPDATE ready_workers SET p_order=?, p_recv=?, driver=?, p_edge=?, p_kreq=?, p_stock=? WHERE id=?",
                                (int(p_["id"] in po), int(p_["id"] in pr), int(p_["id"] in dv), int(p_["id"] in pe), int(p_["id"] in kq),
                                 int(ps) if ps in ("0", "1", "2") else 0, p_["id"]))
                con.commit()
                flash("Права сохранены. В цех-боте кнопки появятся после следующего сообщения сотрудника (или /start)", "ok")
            elif act == "notify" and u["role"] == "admin":
                A.set_settings(pickup_notify=json.dumps([int(x) for x in f.getlist("who") if x.isdigit()]))
                flash("Сохранено", "ok")
            elif act == "refresh":
                res = sync(con)
                A.set_settings(pickup_sync_at=str(time.time()))
                flash("База 1С не открылась" if res is None else f"Обновлено: новых {res[0]}, закрыто {res[1]}", "error" if res is None else "ok")
            return redirect(url_for("erp_pickup"))
        import catalog
        groups = page_rows(con)
        done = con.execute("SELECT i.*, w.name dname FROM pickup_items i LEFT JOIN ready_workers w ON w.id=i.rw_id "
                           "WHERE i.status='done' AND i.closed_at>=? ORDER BY i.closed_at DESC LIMIT 60",
                           ((datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d"),)).fetchall()
        npl = {r["id"] for r in notify_people(con)}
        orders = [dict(r) for r in con.execute("SELECT m.*, w.name dname FROM pickup_msgs m LEFT JOIN ready_workers w ON w.id=m.rw_id "
                                               "WHERE m.state IN ('sent','received') AND m.at>=? ORDER BY m.at DESC LIMIT 60",
                                               ((datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),))]
        for o in orders:
            o["n"] = len(json.loads(o["items"] or "[]"))
            o["doc_no"] = (con.execute("SELECT no FROM erp_docs WHERE id=?", (o["doc_id"],)).fetchone() or [None])[0] if o["doc_id"] else None
        workers = con.execute("SELECT * FROM ready_workers WHERE active=1 ORDER BY name").fetchall()
        return render_template("erp_pickup.html", groups=groups, done=done, drivers=drivers(con), STATUS=STATUS, fmt_q=fmt_q,
                               sups=catalog.SUPPLIERS, people=bot_people(con), npl=npl, orders=orders, workers=workers,
                               sync_at=datetime.fromtimestamp(float(A.get_setting("pickup_sync_at", "0") or 0)) if float(A.get_setting("pickup_sync_at", "0") or 0) else None,
                               is_admin=u["role"] == "admin")


def _seed_roles(con):
    """Бир марта: «Кассир» ва «Менеджер (крой)» ролларига «Олиб келиш» бўлими қўшилади (эгаси айтди)."""
    if con.execute("SELECT 1 FROM settings WHERE key='pickup_roles'").fetchone():
        return False
    try:
        for key in ("kassa", "manager"):
            r = con.execute("SELECT sections FROM roles WHERE key=?", (key,)).fetchone()
            if r:
                s = json.loads(r[0] or "[]")
                if "pickup" not in s:
                    s.append("pickup")
                    con.execute("UPDATE roles SET sections=? WHERE key=?", (json.dumps(s), key))
        con.execute("INSERT INTO settings(key, value) VALUES('pickup_roles', '1')")
        return True
    except Exception:
        A.errlog.exception("pickup roles seed")
        return False


# ================================================================== ботда буюртма ва қабул (эгаси айтган тартиб)
#  «🚚 Заказ» (рухсати бор одам): поставщик → бугунги накладнойлардан етишмаётганлар (белгилаб/олиб ташлаб) → «➕ Товар» (қидириб,
#  сонини ёзиб) → шофёр: бизнинг шофёр (ботда «Олдим/Йўқ экан») ёки «Бошқа шофёр» (матн — ўзингиз юборасиз).
#  «📥 Қабул» (рухсати бор одам, одатда цех бошлиғи): келган буюртма → ҳар товар келган сони → платформада приход қоралама.
GROUP_RX = [(r"eman|эман", 0), (r"гилам|ибн|назиф|сергели|панельн|абдурахим", 1), (r"каштан|фаёз", 2), (r"кастамону", 3),
            (r"бозор", 4), (r"timber|тимбер", 5), (r"крафт|craft|окажон", 6)]


def group_of(name):
    import catalog
    t = (name or "").casefold()
    for rx, i in GROUP_RX:
        if re.search(rx, t):
            return catalog.SUPPLIERS[i]
    return name or ""


def suppliers(con):
    return con.execute("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0 AND ckind='supplier' ORDER BY name").fetchall()


def default_sup_ext(con, group):
    """Гуруҳ номи («Эман», «Панельный …») -> поставщик (erp_clients.ext): созламада танланган ёки номи мос биринчиси."""
    try:
        mp = json.loads(A.get_setting("pickup_sup_map", "") or "{}")
    except ValueError:
        mp = {}
    if mp.get(group):
        return mp[group]
    for r in suppliers(con):
        if group_of(r["name"]) == group:
            return r["ext"]
    return ""


def st_get(con, rw_id):
    r = con.execute("SELECT mode, data FROM pickup_state WHERE rw_id=?", (rw_id,)).fetchone()
    if not r:
        return None, {}
    try:
        return r[0], json.loads(r[1] or "{}")
    except ValueError:
        return r[0], {}


def st_set(con, rw_id, mode=None, **data):
    if mode is None:
        con.execute("DELETE FROM pickup_state WHERE rw_id=?", (rw_id,))
    else:
        con.execute("INSERT INTO pickup_state(rw_id, mode, data, at) VALUES(?,?,?,?) ON CONFLICT(rw_id) DO UPDATE SET mode=excluded.mode, "
                    "data=excluded.data, at=excluded.at", (rw_id, mode, json.dumps(data, ensure_ascii=False), now()))
    con.commit()


def _short(s, n=34):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def order_start(rw, chat):
    con = A.db()
    rb = _rb()
    st_set(con, rw["id"])
    need = {}
    for r in con.execute("SELECT sup, COUNT(*) n FROM pickup_items WHERE status IN ('open','missing') GROUP BY sup"):
        need[r["sup"]] = r["n"]
    rows = []
    sl = sorted(suppliers(con), key=lambda r: (-need.get(group_of(r["name"]), 0), r["name"]))
    for i in range(0, len(sl), 2):
        rows.append([(_short(f"{x['name']}" + (f" ({need[group_of(x['name'])]})" if need.get(group_of(x["name"])) else ""), 30), f"po:s:{x['ext']}")
                     for x in sl[i:i + 2]])
    rb.send(chat, "🚚 Заказ: поставщикни танланг.\nҚавсда — бугунги накладнойлардан етишмаётган товарлар сони.", rb.ikb(rows))


def draft_view(con, m):
    ids, sel = json.loads(m["items"] or "[]"), set(json.loads(m["sel"] or "[]"))
    rows = {r["id"]: r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    out, btn = [f"🚚 Заказ — {m['sup']}", ""], []
    for i, iid in enumerate(ids, 1):
        r = rows.get(iid)
        if not r:
            continue
        refs = ""
        try:
            rl = json.loads(r["refs"] or "[]")
            refs = f" (№{rl[0]['no']})" if rl else ""
        except (ValueError, KeyError, IndexError):
            pass
        out.append(f"{'☑️' if iid in sel else '⬜'} {i}. {r['name']} — {fmt_q(r['need'])} {r['unit'] or ''}{refs}")
        btn.append((f"{'☑️' if iid in sel else '⬜'} {i}", f"po:t:{m['id']}:{iid}"))
    if not ids:
        out.append("Бугунги накладнойлардан етишмаётган товар йўқ. «➕ Товар қўшиш» билан ёзинг.")
    out += ["", f"Танланган: {len(sel & set(ids))} та"]
    kb = [btn[i:i + 5] for i in range(0, len(btn), 5)]
    kb += [[("➕ Товар қўшиш", f"po:a:{m['id']}")], [("🚚 Шофёрга юбориш", f"po:d:{m['id']}")], [("✖️ Бекор қилиш", f"po:x:{m['id']}")]]
    return "\n".join(out), _rb().ikb(kb)


def _draft_show(con, m, chat, mid=None):
    rb = _rb()
    t, kb = draft_view(con, m)
    if mid:
        rb.edit(chat, mid, t, kb)
    else:
        rb.send(chat, t, kb)


def _goods_search(con, text, limit=6):
    import kb as K
    src = "1c" if datetime.now().strftime("%Y-%m-%d") < (A.get_setting("cat_switch", "") or "2026-11-01") else "cat"
    words = [w for w in re.split(r"\s+", (text or "").casefold()) if w][:5]
    if not words:
        return []
    best = []
    for v in K.variants(" ".join(words)):
        ws = [w for w in v.casefold().split() if w]
        sql = ("SELECT ext, name, unit FROM erp_goods WHERE folder=0 AND deleted=0 AND COALESCE(cat,'')<>'svc' AND "
               + " AND ".join("lc LIKE ?" for _ in ws) + " ORDER BY CASE WHEN COALESCE(src,'1c')=? THEN 0 ELSE 1 END, length(name) LIMIT ?")
        best += [r for r in con.execute(sql, tuple(f"%{w}%" for w in ws) + (src, limit)).fetchall() if r["ext"] not in {b["ext"] for b in best}]
        if len(best) >= limit:
            break
    return best[:limit]


def _back_kb(mid):
    return _rb().ikb([[("⬅️ Заказга қайтиш", f"po:b:{mid}")]])


def order_cb(rw, cq):
    """po:… тугмалари. -> қисқа жавоб."""
    con = A.db()
    rb = _rb()
    if not dict(rw).get("p_order"):
        return "⛔ Буюртма беришга рухсат йўқ"
    parts = (cq.get("data") or "").split(":")
    msg = cq.get("message") or {}
    chat, bmid = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    act = parts[1] if len(parts) > 1 else ""
    if act == "s":                                          # поставщик танланди -> қоралама
        sx = parts[2]
        sc = con.execute("SELECT ext, name FROM erp_clients WHERE ext=?", (sx,)).fetchone()
        if not sc:
            return "Поставщик топилмади"
        grp = group_of(sc["name"])
        ids = [r["id"] for r in con.execute("SELECT id FROM pickup_items WHERE status IN ('open','missing') AND sup=? ORDER BY name", (grp,))]
        mid = con.execute("INSERT INTO pickup_msgs(sup, sup_ext, at, items, sel, by_rw, by_user, state) VALUES(?,?,?,?,?,?,?,'draft')",
                          (sc["name"], sx, now(), json.dumps(ids), json.dumps(ids), rw["id"], rw["name"])).lastrowid
        con.commit()
        _draft_show(con, con.execute("SELECT * FROM pickup_msgs WHERE id=?", (mid,)).fetchone(), chat, bmid)
        return sc["name"]
    if len(parts) < 3 or not parts[2].isdigit():
        return "?"
    m = con.execute("SELECT * FROM pickup_msgs WHERE id=?", (int(parts[2]),)).fetchone()
    if not m or m["state"] != "draft":
        return "Бу заказ аллақачон юборилган ёки бекор қилинган"
    if act == "t":                                          # белгилаш / олиб ташлаш
        iid = int(parts[3])
        sel = set(json.loads(m["sel"] or "[]"))
        sel ^= {iid}
        con.execute("UPDATE pickup_msgs SET sel=? WHERE id=?", (json.dumps(sorted(sel)), m["id"]))
        con.commit()
        _draft_show(con, con.execute("SELECT * FROM pickup_msgs WHERE id=?", (m["id"],)).fetchone(), chat, bmid)
        return ""
    if act == "a":
        st_set(con, rw["id"], "search", mid=m["id"])
        rb.edit(chat, bmid, "🔎 Товар номидан бир қисмини ёзинг (масалан: белый 16 ёки 19/1).", _back_kb(m["id"]))
        return ""
    if act == "g":                                          # қидирувдан танланди -> сони
        ext = ":".join(parts[3:])
        g = con.execute("SELECT ext, name, unit FROM erp_goods WHERE ext=?", (ext,)).fetchone()
        if not g:
            return "Товар топилмади"
        st_set(con, rw["id"], "qty", mid=m["id"], ext=ext)
        rb.edit(chat, bmid, f"{g['name']}\n\nНечта керак? Сонини ёзинг ({g['unit'] or 'дона'}).", _back_kb(m["id"]))
        return ""
    if act == "b":
        st_set(con, rw["id"])
        _draft_show(con, m, chat, bmid)
        return ""
    if act == "x":
        con.execute("UPDATE pickup_msgs SET state='cancel' WHERE id=?", (m["id"],))
        for iid in json.loads(m["items"] or "[]"):          # қўлда қўшилганлари — ўчирилади, накладнойдагилари рўйхатда қолади
            con.execute("DELETE FROM pickup_items WHERE id=? AND manual=1 AND status='open' AND msg IS NULL", (iid,))
        con.commit()
        st_set(con, rw["id"])
        rb.edit(chat, bmid, "✖️ Заказ бекор қилинди.")
        return "Бекор"
    sel = [i for i in json.loads(m["items"] or "[]") if i in set(json.loads(m["sel"] or "[]"))]
    if act == "d":                                          # шофёр танлаш
        if not sel:
            return "Аввал товар танланг"
        rows = [[(f"🚚 {d['name']}", f"po:v:{m['id']}:{d['id']}")] for d in drivers(con) if d["bot_uid"]]
        rows += [[("👤 Бошқа шофёр / заводнинг шофёри", f"po:e:{m['id']}")], [("⬅️ Орқага", f"po:b:{m['id']}")]]
        rb.edit(chat, bmid, f"🚚 {m['sup']}: {len(sel)} та товар. Ким олиб келади?", rb.ikb(rows))
        return ""
    if act == "v":                                          # бизнинг шофёр
        d = con.execute("SELECT * FROM ready_workers WHERE id=? AND active=1", (int(parts[3]),)).fetchone()
        if not d or not d["bot_uid"]:
            return "Шофёр ботга уланмаган"
        con.execute("UPDATE pickup_msgs SET items=?, by_user=? WHERE id=?", (json.dumps(sel), rw["name"], m["id"]))
        con.commit()
        err = _dispatch(con, m["id"], d)
        if err:
            return err[:180]
        rb.edit(chat, bmid, f"✅ {m['sup']}: {len(sel)} та товар {d['name']}га юборилди. Олганини ботда белгилайди.")
        return "Юборилди"
    if act == "e":                                          # бошқа шофёр — матн
        t = now()
        con.execute("UPDATE pickup_msgs SET items=?, state='sent', ext_driver='бошқа шофёр', rw_id=NULL, chat=NULL, at=?, by_user=? WHERE id=?",
                    (json.dumps(sel), t, rw["name"], m["id"]))
        for iid in sel:
            con.execute("UPDATE pickup_items SET status='sent', rw_id=NULL, sent_at=?, sent_by=?, sent_qty=need, msg=?, upd_at=? WHERE id=?",
                        (t, rw["name"], m["id"], t, iid))
        con.commit()
        rows = {r["id"]: r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(sel))})", sel)}
        txt = [f"Lumberture — {m['sup']}", f"{t[8:10]}.{t[5:7]}.{t[:4]}", ""]
        txt += [f"{i}. {rows[iid]['name']} — {fmt_q(rows[iid]['need'])} {rows[iid]['unit'] or ''}".rstrip() for i, iid in enumerate(sel, 1) if iid in rows]
        rb.edit(chat, bmid, f"✅ Заказ сақланди. Қуйидаги матнни шофёрга юборинг (босиб туриб «Копировать» ёки «Переслать»). Келганда «📥 Қабул»да текширилади.")
        rb.send(chat, "\n".join(txt))
        return "Сақланди"
    return "?"


def text_input(rw, chat, text):
    """Буюртма/қабул пайтида ёзилган матн (қидирув, сон). -> True агар қабул қилинса."""
    con = A.db()
    rb = _rb()
    mode, d = st_get(con, rw["id"])
    if mode not in ("search", "qty", "rqty"):                # бошқа ҳолатлар (масалан, кромка) — ўз модулида
        return False
    m = con.execute("SELECT * FROM pickup_msgs WHERE id=?", (d.get("mid") or 0,)).fetchone()
    if not m:
        st_set(con, rw["id"])
        return False
    if mode == "search":
        res = _goods_search(con, text)
        if not res:
            rb.send(chat, "Топилмади. Бошқача ёзиб кўринг (масалан: «дуб вотан», «19/1 кашемир»).", _back_kb(m["id"]))
            return True
        rb.send(chat, "Қайси бири?", rb.ikb([[(_short(r["name"], 60), f"po:g:{m['id']}:{r['ext']}")] for r in res] + [[("⬅️ Заказга қайтиш", f"po:b:{m['id']}")]]))
        return True
    num = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", text or "")
    if mode == "qty":
        if not num or float(num.group(1).replace(",", ".")) <= 0:
            rb.send(chat, "Фақат сон ёзинг, масалан: 5", _back_kb(m["id"]))
            return True
        q = float(num.group(1).replace(",", "."))
        g = con.execute("SELECT ext, name, unit FROM erp_goods WHERE ext=?", (d.get("ext"),)).fetchone()
        if g:
            iid = con.execute("INSERT INTO pickup_items(ext, good, name, unit, path, sup, need, status, first_at, upd_at, manual) "
                              "VALUES(?,?,?,?,?,?,?,'open',?,?,1)",
                              (f"m{m['id']}:{g['ext']}", g["ext"], g["name"], g["unit"], "", group_of(m["sup"]), q, now(), now())).lastrowid
            con.execute("UPDATE pickup_msgs SET items=?, sel=? WHERE id=?",
                        (json.dumps(json.loads(m["items"] or "[]") + [iid]), json.dumps(json.loads(m["sel"] or "[]") + [iid]), m["id"]))
            con.commit()
        st_set(con, rw["id"])
        _draft_show(con, con.execute("SELECT * FROM pickup_msgs WHERE id=?", (m["id"],)).fetchone(), chat)
        return True
    if mode == "rqty":
        if not num:
            rb.send(chat, "Фақат сон ёзинг (келмаган бўлса 0).", _rb().ikb([[("⬅️ Орқага", f"pr:o:{m['id']}")]]))
            return True
        con.execute("UPDATE pickup_items SET recv_qty=? WHERE id=? AND msg=?", (float(num.group(1).replace(",", ".")), d.get("iid"), m["id"]))
        con.commit()
        st_set(con, rw["id"])
        t, kb = recv_view(con, m)
        rb.send(chat, t, kb)
        return True
    st_set(con, rw["id"])
    return False


# ------------------------------------------------------------------ қабул
def recv_start(rw, chat):
    con = A.db()
    rb = _rb()
    st_set(con, rw["id"])
    since = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    ms = con.execute("SELECT m.*, w.name dname FROM pickup_msgs m LEFT JOIN ready_workers w ON w.id=m.rw_id "
                     "WHERE m.state='sent' AND m.at>=? ORDER BY m.at DESC LIMIT 20", (since,)).fetchall()
    if not ms:
        rb.send(chat, "📥 Қабул қилинадиган заказ йўқ.", rb.kb_for(rw))
        return
    rows = [[(_short(f"{x['sup']} · {x['at'][8:10]}.{x['at'][5:7]} {x['at'][11:16]} · {x['dname'] or x['ext_driver'] or ''}", 60), f"pr:o:{x['id']}")] for x in ms]
    rb.send(chat, "📥 Қайси заказ келди?", rb.ikb(rows))


def recv_view(con, m):
    ids = json.loads(m["items"] or "[]")
    rows = {r["id"]: r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    for iid, r in rows.items():                             # келган сон — олдиндан: олди/белгиланмаган -> юборилгани, йўқ экан -> 0
        if r["recv_qty"] is None:
            q = 0 if r["status"] == "missing" else (r["sent_qty"] or r["need"])
            con.execute("UPDATE pickup_items SET recv_qty=? WHERE id=?", (q, iid))
    con.commit()
    rows = {r["id"]: r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))})", ids)} if ids else {}
    out, btn = [f"📥 Қабул — {m['sup']} ({m['at'][8:10]}.{m['at'][5:7]})", "Келган сонини текширинг. Фарқ бўлса — рақамни босиб тўғриланг.", ""], []
    for i, iid in enumerate(ids, 1):
        r = rows.get(iid)
        if not r:
            continue
        diff = abs((r["recv_qty"] or 0) - (r["sent_qty"] or r["need"] or 0)) > 0.0005
        mk = "❌" if r["status"] == "missing" else ("⚠️" if diff else "✅")
        out.append(f"{mk} {i}. {r['name']} — келди {fmt_q(r['recv_qty'])} / {fmt_q(r['sent_qty'] or r['need'])} {r['unit'] or ''}")
        btn.append((f"✏️ {i}", f"pr:q:{m['id']}:{iid}"))
    kb = [btn[i:i + 5] for i in range(0, len(btn), 5)] + [[("✅ Қабул қилдим", f"pr:k:{m['id']}")]]
    return "\n".join(out), _rb().ikb(kb)


def make_receipt(con, m, who):
    """Қабулдан платформада «Поступление» қоралама. -> (doc_id, хато)."""
    import erp
    ids = json.loads(m["items"] or "[]")
    rows = [r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))})", ids)] if ids else []
    lines = []
    for r in rows:
        q = r["recv_qty"] or 0
        if q <= 0:
            continue
        ext = r["good"] or r["ext"]
        g = con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0", (ext,)).fetchone()
        if not g:
            continue
        lines.append(dict(good=ext, qty=q, price=(g["price_in"] or g["price_last"] or 0)))
    if not lines:
        return None, "келган товар йўқ"
    sx = m["sup_ext"] or default_sup_ext(con, group_of(m["sup"]))
    if not sx:
        return None, "поставщик танланмаган"
    form = dict(date=datetime.now().strftime("%Y-%m-%d"), client=sx, lines=lines,
                note=f"Заказ №{m['id']} ({m['sup']}) · қабул қилди: {who}. Нархларни завод накладнойи билан текширинг.")
    try:
        with A.app.test_request_context():
            return erp.save_doc2(con, "receipt", None, form, post=False), ""
    except ValueError as e:
        return None, str(e)


def recv_cb(rw, cq):
    con = A.db()
    rb = _rb()
    if not dict(rw).get("p_recv"):
        return "⛔ Қабул қилишга рухсат йўқ"
    parts = (cq.get("data") or "").split(":")
    msg = cq.get("message") or {}
    chat, bmid = (msg.get("chat") or {}).get("id"), msg.get("message_id")
    if len(parts) < 3 or not parts[2].isdigit():
        return "?"
    m = con.execute("SELECT * FROM pickup_msgs WHERE id=?", (int(parts[2]),)).fetchone()
    if not m or m["state"] != "sent":
        return "Бу заказ аллақачон қабул қилинган"
    act = parts[1]
    if act == "o":
        st_set(con, rw["id"])
        t, kb = recv_view(con, m)
        rb.edit(chat, bmid, t, kb)
        return ""
    if act == "q":
        iid = int(parts[3])
        r = con.execute("SELECT * FROM pickup_items WHERE id=?", (iid,)).fetchone()
        st_set(con, rw["id"], "rqty", mid=m["id"], iid=iid)
        rb.edit(chat, bmid, f"{r['name'] if r else ''}\n\nНечта келди? Сонини ёзинг (келмаган бўлса 0).", rb.ikb([[("⬅️ Орқага", f"pr:o:{m['id']}")]]))
        return ""
    if act == "k":
        st_set(con, rw["id"])
        recv_view(con, m)                                   # бўш қолганлари тўлдирилади
        doc_id, err = make_receipt(con, m, rw["name"])
        t = now()
        ids = json.loads(m["items"] or "[]")
        rows = [r for r in con.execute(f"SELECT * FROM pickup_items WHERE id IN ({','.join('?' * len(ids))})", ids)] if ids else []
        diffs = []
        for r in rows:
            q, sq = r["recv_qty"] or 0, r["sent_qty"] or r["need"] or 0
            if abs(q - sq) > 0.0005:
                diffs.append(f"• {r['name']} — келди {fmt_q(q)}, буюртма {fmt_q(sq)} {r['unit'] or ''}".rstrip())
            st = "missing" if q <= 0 else ("done" if r["manual"] else "taken")     # қўлда қўшилган — 1С'га боғлиқ эмас, келди — ёпилди
            con.execute("UPDATE pickup_items SET status=?, sent_qty=?, upd_at=?, closed_at=CASE WHEN ?='done' THEN ? ELSE closed_at END WHERE id=?",
                        (st, q if q > 0 else r["sent_qty"], t, st, t, r["id"]))
        con.execute("UPDATE pickup_msgs SET state='received', recv_at=?, recv_by=?, doc_id=?, err=? WHERE id=?", (t, rw["name"], doc_id, err, m["id"]))
        con.commit()
        head = f"📥 Қабул қилинди — {m['sup']} ({rw['name']})"
        info = (f"Платформада приход қоралама тайёр (№{con.execute('SELECT no FROM erp_docs WHERE id=?', (doc_id,)).fetchone()[0]}). "
                "Кассир кун охирида завод накладнойи билан текшириб проведен қилади." if doc_id else
                f"⚠️ Приход қоралама яратилмади: {err}. Кассир платформада қўлда ёзади.")
        rb.edit(chat, bmid, head + "\n" + info)
        tell(con, head + "\n" + info + (("\n\nФарқ:\n" + "\n".join(diffs)) if diffs else "\nФарқ йўқ."))
        return "Қабул қилинди"
    return "?"
