# -*- coding: utf-8 -*-
"""Логистика: шофёрлар рейслари ва харажатлари, ҳафталик ҳисоб (эгаси айтган тартиб, 22.10).

• Шофёр цех-ботда ёзади: «🚗 Рейс» — поставщикдан олиб келиш (белгиланган нарх: База Жомий Эман 80 000, Чинобод 120 000,
  Панельный Сергели 250 000 …) ёки мижозга/бошқа рейс (қаердан → қаерга ва суммаси); «⛽ Харажат» — бензин, газ, СТО, бошқа.
• Ҳафталик ҳисоб (душанба–якшанба): ишланган пулдан бензин, газ ва қўшимча харажат айрилади, қолгани — шофёр ставкаси
  (масалан 45%) ва машина эгаси (55%). СТО алоҳида: машина эгаси 55%, шофёр 45%. Ставкалар ҳар шофёр учун платформада ўзгаради.
• Душанба эрталаб ўтган ҳафта ҳисоби шофёрга ва машина эгасига цех-ботда юборилади."""
import json, re
from datetime import date, datetime, timedelta

from flask import request, render_template, redirect, url_for, flash, abort

A = None
SCHEMA = """
CREATE TABLE IF NOT EXISTS lg_routes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL DEFAULT 0, sort INTEGER DEFAULT 0,
  active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS lg_cars(id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner TEXT DEFAULT '', owner_rw INTEGER, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS lg_drivers(rw_id INTEGER PRIMARY KEY, car_id INTEGER, trip_pct REAL DEFAULT 45, sto_pct REAL DEFAULT 45);
CREATE TABLE IF NOT EXISTS lg_trips(id INTEGER PRIMARY KEY, rw_id INTEGER NOT NULL, car_id INTEGER, at TEXT NOT NULL, kind TEXT NOT NULL,
  route_id INTEGER, route TEXT DEFAULT '', amount REAL NOT NULL DEFAULT 0, note TEXT DEFAULT '', status TEXT DEFAULT 'ok',
  edited_by TEXT, edited_at TEXT);
CREATE INDEX IF NOT EXISTS ix_lg_trips_at ON lg_trips(at);
CREATE TABLE IF NOT EXISTS lg_exp(id INTEGER PRIMARY KEY, rw_id INTEGER NOT NULL, car_id INTEGER, at TEXT NOT NULL, kind TEXT NOT NULL,
  amount REAL NOT NULL DEFAULT 0, note TEXT DEFAULT '', status TEXT DEFAULT 'ok', edited_by TEXT, edited_at TEXT);
CREATE INDEX IF NOT EXISTS ix_lg_exp_at ON lg_exp(at);
"""
SEED_ROUTES = [("Eman Жомий", 80000), ("Eman Чинобод", 120000), ("Панельный (Сергели)", 250000)]
# 23.10 (эгаси): цехлар ва йўналишлар. Янги йўналишлар нархи 0 — платформада (Логистика → Настройки) қўйилади,
# шунгача шофёр суммани ўзи ёзади. Тариф иккала цех учун бир хил.
SHOPS = [("lumberture", "Lumberture"), ("chinor", "Chinor")]
SHOP_NAME = dict(SHOPS)
ROUTES_V2 = [("База Жомий (Эман)", "Eman Жомий"), ("Чинобод", "Eman Чинобод")]
ROUTES_NEW = ["Eman С.Рахимова", "Eman Чилонзор", "Eman Кора сув"]
PAYERS = {"client": "клиент оплачивает", "shop": "цех оплачивает (скидка клиенту)", "": "не указано"}
PAYER_UZ = {"client": "мижоз тўлайди", "shop": "цех тўлайди", "": "ким тўлаши белгиланмаган"}
EXP_KINDS = [("fuel", "⛽ Бензин", "Бензин"), ("gas", "🔥 Газ", "Газ"), ("sto", "🔧 СТО", "СТО"), ("other", "➕ Бошқа", "Бошқа харажат")]
EXP_NAME = {k: n for k, _, n in EXP_KINDS}
EXP_RU = {"fuel": "Бензин", "gas": "Газ", "sto": "СТО (ремонт)", "other": "Прочее"}
DEF_TRIP_PCT, DEF_STO_PCT = 45.0, 45.0
WEEK_SEND_HOUR = 9                      # душанба 9:00 — ўтган ҳафта ҳисоби ботда

B_TRIP = "🚗 Рейс"
B_EXP = "⛽ Харажат"
B_WEEK = "📊 Ҳафта ҳисоби"


# ================================================================== умумий
def init_tables(con):
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM lg_routes LIMIT 1").fetchone() and not _setting(con, "lg_seeded"):
        for i, (n, p) in enumerate(SEED_ROUTES):
            con.execute("INSERT INTO lg_routes(name, price, sort) VALUES(?,?,?)", (n, p, i))
    if not con.execute("SELECT 1 FROM lg_cars LIMIT 1").fetchone() and not _setting(con, "lg_seeded"):
        ow = con.execute("SELECT id, name FROM ready_workers WHERE name LIKE '%Нурмухамедов%' ORDER BY active DESC, id LIMIT 1").fetchone() \
            if con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ready_workers'").fetchone() else None
        con.execute("INSERT INTO lg_cars(name, owner, owner_rw) VALUES(?,?,?)", ("Машина", ow["name"] if ow else "Жамшид Нурмухамедов", ow["id"] if ow else None))
    if not _setting(con, "lg_seeded"):
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('lg_seeded', '1')")
    cols = {r[1] for r in con.execute("PRAGMA table_info(lg_trips)")}
    for col, ddl in (("shop", "TEXT DEFAULT 'lumberture'"), ("payer", "TEXT DEFAULT ''"), ("doc_no", "TEXT DEFAULT ''")):
        if col not in cols:
            con.execute(f"ALTER TABLE lg_trips ADD COLUMN {col} {ddl}")
    if not _setting(con, "lg_routes_v2"):                 # йўналишлар номи «Eman …», янгилари қўшилади (бир марта)
        for old, new in ROUTES_V2:
            con.execute("UPDATE lg_routes SET name=? WHERE name=?", (new, old))
        have = {(r[0] or "").casefold() for r in con.execute("SELECT name FROM lg_routes WHERE active=1")}
        mx = (con.execute("SELECT MAX(sort) FROM lg_routes").fetchone()[0] or 0)
        for i, n in enumerate(ROUTES_NEW):
            if n.casefold() not in have:
                con.execute("INSERT INTO lg_routes(name, price, sort) VALUES(?,?,?)", (n, 0, mx + 1 + i))
        con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('lg_routes_v2', '1')")


def _setting(con, key):
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r[0] if r else ""
    except Exception:
        return ""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt(v):
    v = round(float(v or 0))
    return f"{v:,}".replace(",", " ").replace("-", "−")


def week_start(d=None):
    d = d or date.today()
    if isinstance(d, datetime):
        d = d.date()
    return d - timedelta(days=d.weekday())


def parse_sum(text):
    """«150000», «150 000», «150 минг», «1,5 млн» -> сўм ёки None. Матндаги охирги сон олинади."""
    t = (text or "").casefold().replace(" ", " ")
    ms = list(re.finditer(r"(\d+(?:[  ]\d{3})*(?:[.,]\d+)?)\s*(минг|ming|k\b|к\b|млн|mln)?", t))
    if not ms:
        return None, text
    m = ms[-1]
    try:
        v = float(m.group(1).replace(" ", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None, text
    mul = {"минг": 1000, "ming": 1000, "k": 1000, "к": 1000, "млн": 1e6, "mln": 1e6}.get((m.group(2) or "").strip(), 1)
    v *= mul
    if v < 1000:                                           # «150» — минг деб тушунамиз (шофёрлар шундай ёзади)
        v *= 1000
    note = (text[:m.start()] + text[m.end():]).strip(" -—:,.")
    return round(v), note


def driver_cfg(con, rw_id):
    r = con.execute("SELECT * FROM lg_drivers WHERE rw_id=?", (rw_id,)).fetchone()
    car = None
    if r and r["car_id"]:
        car = con.execute("SELECT * FROM lg_cars WHERE id=?", (r["car_id"],)).fetchone()
    if not car:
        car = con.execute("SELECT * FROM lg_cars WHERE active=1 ORDER BY id LIMIT 1").fetchone()
    tp = float(r["trip_pct"]) if r and r["trip_pct"] is not None else DEF_TRIP_PCT
    sp = float(r["sto_pct"]) if r and r["sto_pct"] is not None else DEF_STO_PCT
    return dict(car=car, car_id=car["id"] if car else None, trip_pct=tp, sto_pct=sp)


def calc(con, rw_id, ws):
    """Шофёрнинг ҳафтаси (ws — душанба). -> dict"""
    a, b = ws.isoformat(), (ws + timedelta(days=7)).isoformat()
    cfg = driver_cfg(con, rw_id)
    trips = con.execute("SELECT * FROM lg_trips WHERE rw_id=? AND status='ok' AND at>=? AND at<? ORDER BY at", (rw_id, a, b)).fetchall()
    exps = con.execute("SELECT * FROM lg_exp WHERE rw_id=? AND status='ok' AND at>=? AND at<? ORDER BY at", (rw_id, a, b)).fetchall()
    rev = sum(t["amount"] for t in trips)
    by = {k: sum(e["amount"] for e in exps if e["kind"] == k) for k, _, _ in EXP_KINDS}
    common = by["fuel"] + by["gas"] + by["other"]
    sto = by["sto"]
    net = rev - common
    p, sp = cfg["trip_pct"], cfg["sto_pct"]
    drv = net * p / 100 - sto * sp / 100
    own = net * (100 - p) / 100 - sto * (100 - sp) / 100
    by_shop = {code: sum(t["amount"] for t in trips if (t["shop"] or "lumberture") == code) for code, _ in SHOPS}
    deliv = {k: sum(t["amount"] for t in trips if t["kind"] == "delivery" and (t["payer"] or "") == k) for k in ("client", "shop", "")}
    return dict(rw_id=rw_id, ws=ws, trips=trips, exps=exps, rev=rev, by=by, common=common, sto=sto, net=net, trip_pct=p, sto_pct=sp,
                driver=round(drv), owner=round(own), car=cfg["car"], n_trips=len(trips), by_shop=by_shop, deliv=deliv)


def week_text(con, rw, ws, title=None):
    c = calc(con, rw["id"], ws)
    we = ws + timedelta(days=6)
    car = c["car"]
    lines = [title or f"📊 Ҳафта ҳисоби: {ws:%d.%m} – {we:%d.%m}", f"👤 {rw['name']}" + (f" · 🚗 {car['name']}" if car else ""),
             f"\nРейслар: {c['n_trips']} та — {fmt(c['rev'])} сўм"]
    for code, nm in SHOPS:                                # қаердан қаерга юрди — цех бўйича
        ts = [t for t in c["trips"] if (t["shop"] or "lumberture") == code]
        if not ts:
            continue
        lines.append(f"\n🏭 {nm}: {len(ts)} та — {fmt(sum(t['amount'] for t in ts))}")
        for t in ts[:30]:
            lines.append(f"• {t['at'][8:10]}.{t['at'][5:7]} {trip_label(t)} — {fmt(t['amount'])}")
        if len(ts) > 30:
            lines.append(f"… яна {len(ts) - 30} та")
    for k, _, n in EXP_KINDS:
        if c["by"][k]:
            lines.append(f"{n}: −{fmt(c['by'][k])}")
    lines.append(f"\nСоф (бензин, газ, бошқа айрилгач): {fmt(c['net'])}")
    lines.append(f"Шофёрга {c['trip_pct']:g}%: {fmt(c['net'] * c['trip_pct'] / 100)}")
    lines.append(f"Машина эгасига {100 - c['trip_pct']:g}%: {fmt(c['net'] * (100 - c['trip_pct']) / 100)}")
    if c["sto"]:
        lines.append(f"СТО {fmt(c['sto'])}: шофёр {c['sto_pct']:g}% (−{fmt(c['sto'] * c['sto_pct'] / 100)}), "
                     f"эгаси {100 - c['sto_pct']:g}% (−{fmt(c['sto'] * (100 - c['sto_pct']) / 100)})")
    lines.append(f"\n💰 Шофёрга жами: {fmt(c['driver'])} сўм\n🚗 Машина эгасига жами: {fmt(c['owner'])} сўм")
    return "\n".join(lines)


def trip_label(t):
    """Рейс номи: «📦 Eman Жомий», «🏠 Чилонзор №6470 (цех тўлайди)», «🚚 Жомий → Чилонзор»."""
    if t["kind"] == "delivery":
        return f"🏠 {t['route']}" + (f" ({PAYER_UZ.get(t['payer'] or '', '')})" if (t["payer"] or "") in ("client", "shop") else "")
    return ("📦 " if t["kind"] == "pickup" else "🚚 ") + (t["route"] or "")


# ================================================================== бот
def _rb():
    import raspil_bot
    return raspil_bot


def _pk():
    import pickup
    return pickup


def buttons(rw):
    return [[{"text": B_TRIP}, {"text": B_EXP}], [{"text": B_WEEK}]] if dict(rw or {}).get("driver") else []


def shop_menu():
    return _rb().ikb([[(f"🏭 {nm}", f"lg:s:{code}") for code, nm in SHOPS]])


def trip_menu(con, shop="lumberture"):
    rb = _rb()
    rows = [[(f"📦 {r['name']}" + (f" — {fmt(r['price'])}" if r["price"] else ""), f"lg:r:{r['id']}:{shop}")]
            for r in con.execute("SELECT * FROM lg_routes WHERE active=1 ORDER BY sort, name")]
    rows.append([("🏠 Мижозга доставка", f"lg:d:{shop}")])
    rows.append([("🚚 Бошқа рейс", f"lg:c:{shop}")])
    return rb.ikb(rows)


def _shop(v):
    return v if v in SHOP_NAME else "lumberture"


def _docno(text):
    m = re.search(r"№\s*(\d{3,7})", text or "")
    return m.group(1) if m else ""


def on_text(rw, chat, text):
    """-> True агар шу ерда ишланса."""
    if not dict(rw).get("driver"):
        return False
    con = A.db()
    init_tables(con)
    rb, pk = _rb(), _pk()
    if text == B_TRIP:
        pk.st_set(con, rw["id"])
        rb.send(chat, "🚗 Қайси цех учун?", shop_menu())
        return True
    if text == B_EXP:
        pk.st_set(con, rw["id"])
        rb.send(chat, "⛽ Қандай харажат?", rb.ikb([[(lbl, f"lg:e:{k}") for k, lbl, _ in EXP_KINDS[:2]],
                                                    [(lbl, f"lg:e:{k}") for k, lbl, _ in EXP_KINDS[2:]]]))
        return True
    if text == B_WEEK:
        rb.send(chat, week_text(con, rw, week_start()), rb.ikb([[("◀ Ўтган ҳафта", "lg:w:-1")]]))
        return True
    mode, d = pk.st_get(con, rw["id"])
    if mode in ("lg_trip", "lg_exp", "lg_deliv", "lg_rsum") and (text.startswith("/") or text in (rb.B_PICK, rb.B_ORDER, rb.B_RECV, rb.B_EDGE)):
        pk.st_set(con, rw["id"])                           # бошқа тугма босилди — рейс/харажат ёзиш бекор
        return False
    if mode == "lg_rsum":                                  # йўналиш нархи платформада қўйилмаган — шофёр суммани ёзади
        v, _note = parse_sum(text)
        r = con.execute("SELECT * FROM lg_routes WHERE id=?", (d.get("route") or 0,)).fetchone()
        if not v or not r:
            rb.send(chat, "Суммасини ёзинг, масалан: 100000")
            return True
        shop = _shop(d.get("shop"))
        cfg = driver_cfg(con, rw["id"])
        tid = con.execute("INSERT INTO lg_trips(rw_id, car_id, at, kind, route_id, route, amount, shop) VALUES(?,?,?,?,?,?,?,?)",
                          (rw["id"], cfg["car_id"], now(), "pickup", r["id"], r["name"], v, shop)).lastrowid
        pk.st_set(con, rw["id"])
        con.commit()
        rb.send(chat, f"✅ Рейс ёзилди: 🏭 {SHOP_NAME[shop]} · 📦 {r['name']} — {fmt(v)} сўм", rb.ikb([[("🗑 Хато — ўчириш", f"lg:dt:{tid}")]]))
        return True
    if mode in ("lg_trip", "lg_deliv"):
        v, note = parse_sum(text)
        if not v or len(note) < 3:
            rb.send(chat, "Қаерга, накладной рақами ва суммасини ёзинг, масалан: Чилонзор №6470 150000" if mode == "lg_deliv"
                    else "Қаердан қаерга ва суммасини ёзинг, масалан: Жомий → Чилонзор 150000")
            return True
        shop = _shop(d.get("shop"))
        cfg = driver_cfg(con, rw["id"])
        kind = "delivery" if mode == "lg_deliv" else "client"
        tid = con.execute("INSERT INTO lg_trips(rw_id, car_id, at, kind, route, amount, note, shop, doc_no) VALUES(?,?,?,?,?,?,?,?,?)",
                          (rw["id"], cfg["car_id"], now(), kind, note[:120], v, "", shop, _docno(note))).lastrowid
        pk.st_set(con, rw["id"])
        con.commit()
        if kind == "delivery":
            rb.send(chat, f"🏠 {note[:120]} — {fmt(v)} сўм\n\nДоставка пулини ким тўлайди?",
                    rb.ikb([[("👤 Мижоз", f"lg:p:{tid}:client"), ("🏭 Цех (скидка)", f"lg:p:{tid}:shop")],
                            [("❓ Билмайман — офис белгилайди", f"lg:p:{tid}:x")], [("🗑 Хато — ўчириш", f"lg:dt:{tid}")]]))
        else:
            rb.send(chat, f"✅ Рейс ёзилди: 🏭 {SHOP_NAME[shop]} · {note[:120]} — {fmt(v)} сўм", rb.ikb([[("🗑 Хато — ўчириш", f"lg:dt:{tid}")]]))
        return True
    if mode == "lg_exp":
        v, note = parse_sum(text)
        if not v:
            rb.send(chat, "Суммасини ёзинг, масалан: 200000 ёки 200 минг (изоҳ ҳам ёзсангиз бўлади: 350000 мой алмаштириш)")
            return True
        k = d.get("kind") if d.get("kind") in EXP_NAME else "other"
        cfg = driver_cfg(con, rw["id"])
        eid = con.execute("INSERT INTO lg_exp(rw_id, car_id, at, kind, amount, note) VALUES(?,?,?,?,?,?)",
                          (rw["id"], cfg["car_id"], now(), k, v, note[:200])).lastrowid
        pk.st_set(con, rw["id"])
        con.commit()
        rb.send(chat, f"✅ {EXP_NAME[k]}: {fmt(v)} сўм" + (f" — {note[:200]}" if note else ""), rb.ikb([[("🗑 Хато — ўчириш", f"lg:de:{eid}")]]))
        return True
    return False


def on_cb(rw, cq):
    if not dict(rw).get("driver"):
        return "Рухсат йўқ"
    con = A.db()
    init_tables(con)
    rb, pk = _rb(), _pk()
    chat, mid = cq["message"]["chat"]["id"], cq["message"]["message_id"]
    p = (cq.get("data") or "").split(":")
    cfg = driver_cfg(con, rw["id"])
    if p[1] == "s" and len(p) > 2:                        # цех танланди → йўналишлар
        shop = _shop(p[2])
        rb.edit(chat, mid, f"🏭 {SHOP_NAME[shop]} — қайси рейс?", trip_menu(con, shop))
        return ""
    if p[1] == "r" and len(p) > 2 and p[2].isdigit():
        shop = _shop(p[3] if len(p) > 3 else "")
        r = con.execute("SELECT * FROM lg_routes WHERE id=? AND active=1", (int(p[2]),)).fetchone()
        if not r:
            return "Йўналиш топилмади"
        if not r["price"]:                                # нархи қўйилмаган — шофёр суммани ёзади
            pk.st_set(con, rw["id"], "lg_rsum", route=r["id"], shop=shop)
            rb.edit(chat, mid, f"🏭 {SHOP_NAME[shop]} · 📦 {r['name']}\nНархи ҳали қўйилмаган — суммасини ёзинг, масалан: 100000")
            return ""
        tid = con.execute("INSERT INTO lg_trips(rw_id, car_id, at, kind, route_id, route, amount, shop) VALUES(?,?,?,?,?,?,?,?)",
                          (rw["id"], cfg["car_id"], now(), "pickup", r["id"], r["name"], r["price"], shop)).lastrowid
        con.commit()
        rb.edit(chat, mid, f"✅ Рейс ёзилди: 🏭 {SHOP_NAME[shop]} · 📦 {r['name']} — {fmt(r['price'])} сўм", rb.ikb([[("🗑 Хато — ўчириш", f"lg:dt:{tid}")]]))
        return "Ёзилди"
    if p[1] in ("c", "d"):
        shop = _shop(p[2] if len(p) > 2 else "")
        pk.st_set(con, rw["id"], "lg_deliv" if p[1] == "d" else "lg_trip", shop=shop)
        rb.edit(chat, mid, f"🏭 {SHOP_NAME[shop]} · 🏠 Мижозга доставка\nҚаерга, накладной рақами ва неча пул? Масалан: Чилонзор №6470 150000"
                if p[1] == "d" else f"🏭 {SHOP_NAME[shop]} · 🚚 Қаердан қаерга ва неча пул? Масалан: Жомий → Чилонзор 150000")
        return ""
    if p[1] == "p" and len(p) > 3 and p[2].isdigit():     # доставка: ким тўлайди
        r = con.execute("SELECT * FROM lg_trips WHERE id=? AND rw_id=? AND kind='delivery'", (int(p[2]), rw["id"])).fetchone()
        if not r:
            return "Топилмади"
        pay = p[3] if p[3] in ("client", "shop") else ""
        con.execute("UPDATE lg_trips SET payer=? WHERE id=?", (pay, r["id"]))
        con.commit()
        rb.edit(chat, mid, f"✅ Доставка ёзилди: 🏭 {SHOP_NAME[_shop(r['shop'])]} · 🏠 {r['route']} — {fmt(r['amount'])} сўм\n{PAYER_UZ[pay]}",
                rb.ikb([[("🗑 Хато — ўчириш", f"lg:dt:{r['id']}")]]))
        return "Ёзилди"
    if p[1] == "e" and len(p) > 2 and p[2] in EXP_NAME:
        pk.st_set(con, rw["id"], "lg_exp", kind=p[2])
        rb.edit(chat, mid, f"{EXP_NAME[p[2]]}: суммасини ёзинг (изоҳ ҳам бўлади), масалан: 200000" + (" мой алмаштириш" if p[2] == "sto" else ""))
        return ""
    if p[1] in ("dt", "de") and len(p) > 2 and p[2].isdigit():
        tbl = "lg_trips" if p[1] == "dt" else "lg_exp"
        r = con.execute(f"SELECT * FROM {tbl} WHERE id=? AND rw_id=?", (int(p[2]), rw["id"])).fetchone()
        if not r or r["status"] != "ok":
            return "Аллақачон ўчирилган"
        if r["at"][:10] < (date.today() - timedelta(days=2)).isoformat():
            return "Эски ёзув — офисда ўзгартирилади"
        con.execute(f"UPDATE {tbl} SET status='del', edited_by=?, edited_at=? WHERE id=?", (rw["name"], now(), r["id"]))
        con.commit()
        rb.edit(chat, mid, "🗑 Ўчирилди.")
        return "Ўчирилди"
    if p[1] == "w":
        ws = week_start() + timedelta(days=7 * int(p[2] if len(p) > 2 and re.fullmatch(r"-?\d+", p[2]) else 0))
        k = int(p[2]) if len(p) > 2 and re.fullmatch(r"-?\d+", p[2]) else 0
        rb.edit(chat, mid, week_text(con, rw, ws), rb.ikb([[("◀", f"lg:w:{k - 1}")] + ([("▶", f"lg:w:{k + 1}")] if k < 0 else [])]))
        return ""
    return ""


def tick(dt=None):
    """Душанба 9:00 дан кейин — ўтган ҳафта ҳисоби шофёрга ва машина эгасига (бир марта)."""
    dt = dt or datetime.now()
    if dt.weekday() != 0 or dt.hour < WEEK_SEND_HOUR:
        return
    ws = week_start(dt.date()) - timedelta(days=7)
    if (A.get_setting("lg_week_sent", "") or "") >= ws.isoformat():
        return
    con = A.db()
    init_tables(con)
    A.set_settings(lg_week_sent=ws.isoformat())
    rb = _rb()
    ids = {r[0] for r in con.execute("SELECT rw_id FROM lg_trips WHERE at>=? AND at<? UNION SELECT rw_id FROM lg_exp WHERE at>=? AND at<?",
                                     (ws.isoformat(), (ws + timedelta(days=7)).isoformat()) * 2)}
    for rid in ids:
        rw = con.execute("SELECT * FROM ready_workers WHERE id=?", (rid,)).fetchone()
        if not rw:
            continue
        t = week_text(con, rw, ws)
        if rw["bot_uid"]:
            rb.send(rw["bot_uid"], t)
        car = driver_cfg(con, rid)["car"]
        if car and car["owner_rw"] and car["owner_rw"] != rid:
            ow = con.execute("SELECT bot_uid FROM ready_workers WHERE id=? AND active=1", (car["owner_rw"],)).fetchone()
            if ow and ow["bot_uid"]:
                rb.send(ow["bot_uid"], t)


# ================================================================== платформа
def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con)
        con.commit()
    finally:
        con.close()
    _orig = A.scheduler_tick

    def sched(dt=None):
        r = _orig(dt)
        try:
            tick(dt)
        except Exception:
            A.errlog.exception("logist tick")
        return r
    A.scheduler_tick = sched

    @app.route("/erp/logist", methods=["GET", "POST"])
    @A.login_required
    def erp_logist():
        con = A.db()
        init_tables(con)
        u = A.current_user()
        adm = u["role"] == "admin"
        try:
            ws = date.fromisoformat(request.values.get("w") or "")
            ws = week_start(ws)
        except ValueError:
            ws = week_start()
        if request.method == "POST":
            f = request.form
            act = f.get("act") or ""
            who = u["username"]
            if act == "payer":                             # доставка: ким тўлайди (мижоз / цех — скидка)
                pay = f.get("payer") if f.get("payer") in ("client", "shop") else ""
                con.execute("UPDATE lg_trips SET payer=?, edited_by=?, edited_at=? WHERE id=? AND kind='delivery'",
                            (pay, who, now(), int(f.get("id") or 0)))
                con.commit()
                flash("Сохранено", "ok")
                return redirect(url_for("erp_logist", w=ws.isoformat()))
            if act in ("del_trip", "del_exp", "fix_trip", "fix_exp"):
                tbl = "lg_trips" if act.endswith("trip") else "lg_exp"
                rid = int(f.get("id") or 0)
                if act.startswith("del"):
                    con.execute(f"UPDATE {tbl} SET status=CASE WHEN status='ok' THEN 'del' ELSE 'ok' END, edited_by=?, edited_at=? WHERE id=?",
                                (who, now(), rid))
                else:
                    v = A.parse_float(f.get("amount"), -1)
                    if v < 0:
                        flash("Сумма неверная", "error")
                        return redirect(url_for("erp_logist", w=ws.isoformat()))
                    con.execute(f"UPDATE {tbl} SET amount=?, edited_by=?, edited_at=? WHERE id=?", (v, who, now(), rid))
                con.commit()
                flash("Сохранено", "ok")
            elif act == "add" and f.get("rw"):
                rid = int(f.get("rw"))
                v = A.parse_float(f.get("amount"), 0)
                d = A.parse_date(f.get("date")) or date.today()
                at = d.isoformat() + " 12:00:00"
                cfg = driver_cfg(con, rid)
                kind = f.get("kind") or ""
                if v <= 0:
                    flash("Укажите сумму", "error")
                elif kind in EXP_NAME:
                    con.execute("INSERT INTO lg_exp(rw_id, car_id, at, kind, amount, note, edited_by, edited_at) VALUES(?,?,?,?,?,?,?,?)",
                                (rid, cfg["car_id"], at, kind, v, (f.get("note") or "")[:200], who, now()))
                else:
                    note = (f.get("note") or "Рейс")[:120]
                    con.execute("INSERT INTO lg_trips(rw_id, car_id, at, kind, route, amount, edited_by, edited_at, shop, payer, doc_no) "
                                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (rid, cfg["car_id"], at, "delivery" if kind == "delivery" else "client", note, v, who, now(), _shop(f.get("shop")),
                                 (f.get("payer") if f.get("payer") in ("client", "shop") else "") if kind == "delivery" else "", _docno(note)))
                con.commit()
                flash("Добавлено", "ok")
            elif act == "settings" and adm:
                for r in con.execute("SELECT id FROM lg_routes").fetchall():
                    nm = (f.get(f"rn_{r['id']}") or "").strip()
                    if f.get(f"rd_{r['id']}") or not nm:
                        con.execute("UPDATE lg_routes SET active=0 WHERE id=?", (r["id"],))
                    else:
                        con.execute("UPDATE lg_routes SET name=?, price=?, active=1 WHERE id=?",
                                    (nm[:80], A.parse_float(f.get(f"rp_{r['id']}"), 0), r["id"]))
                if (f.get("rn_new") or "").strip():
                    con.execute("INSERT INTO lg_routes(name, price, sort) VALUES(?,?,?)",
                                (f.get("rn_new").strip()[:80], A.parse_float(f.get("rp_new"), 0), 99))
                for r in con.execute("SELECT id FROM lg_cars").fetchall():
                    nm = (f.get(f"cn_{r['id']}") or "").strip()
                    if nm:
                        ow = int(f.get(f"co_{r['id']}") or 0) or None
                        on = con.execute("SELECT name FROM ready_workers WHERE id=?", (ow,)).fetchone() if ow else None
                        con.execute("UPDATE lg_cars SET name=?, owner_rw=?, owner=? WHERE id=?",
                                    (nm[:60], ow, on["name"] if on else (f.get(f"cw_{r['id']}") or "")[:60], r["id"]))
                if (f.get("cn_new") or "").strip():
                    con.execute("INSERT INTO lg_cars(name) VALUES(?)", (f.get("cn_new").strip()[:60],))
                for d in drivers(con):
                    car = int(f.get(f"dc_{d['id']}") or 0) or None
                    tp = min(100, max(0, A.parse_float(f.get(f"dp_{d['id']}"), DEF_TRIP_PCT)))
                    sp = min(100, max(0, A.parse_float(f.get(f"ds_{d['id']}"), DEF_STO_PCT)))
                    con.execute("INSERT INTO lg_drivers(rw_id, car_id, trip_pct, sto_pct) VALUES(?,?,?,?) ON CONFLICT(rw_id) DO UPDATE SET "
                                "car_id=excluded.car_id, trip_pct=excluded.trip_pct, sto_pct=excluded.sto_pct", (d["id"], car, tp, sp))
                con.commit()
                flash("Настройки сохранены", "ok")
            return redirect(url_for("erp_logist", w=ws.isoformat()))
        dl = drivers(con)
        rows = []
        ids = {d["id"] for d in dl} | {r[0] for r in con.execute(
            "SELECT rw_id FROM lg_trips WHERE at>=? AND at<? UNION SELECT rw_id FROM lg_exp WHERE at>=? AND at<?",
            (ws.isoformat(), (ws + timedelta(days=7)).isoformat()) * 2)}
        names = {r["id"]: r["name"] for r in con.execute("SELECT id, name FROM ready_workers")}
        for rid in sorted(ids, key=lambda i: names.get(i, "")):
            c = calc(con, rid, ws)
            c["name"] = names.get(rid, f"#{rid}")
            c["all_trips"] = con.execute("SELECT * FROM lg_trips WHERE rw_id=? AND at>=? AND at<? ORDER BY at", (rid, ws.isoformat(), (ws + timedelta(days=7)).isoformat())).fetchall()
            c["all_exps"] = con.execute("SELECT * FROM lg_exp WHERE rw_id=? AND at>=? AND at<? ORDER BY at", (rid, ws.isoformat(), (ws + timedelta(days=7)).isoformat())).fetchall()
            rows.append(c)
        cfgs = {d["id"]: driver_cfg(con, d["id"]) for d in dl}
        tot = dict(shop={code: sum(c["by_shop"][code] for c in rows) for code, _ in SHOPS},
                   deliv={k: sum(c["deliv"][k] for c in rows) for k in ("client", "shop", "")})
        return render_template("erp_logist.html", ws=ws, we=ws + timedelta(days=6), prev=(ws - timedelta(days=7)).isoformat(),
                               next=(ws + timedelta(days=7)).isoformat(), rows=rows, drivers=dl, cfgs=cfgs, fmt=fmt, EXP_KINDS=EXP_KINDS, EXP_RU=EXP_RU,
                               routes=con.execute("SELECT * FROM lg_routes WHERE active=1 ORDER BY sort, name").fetchall(),
                               cars=con.execute("SELECT * FROM lg_cars WHERE active=1 ORDER BY id").fetchall(),
                               people=con.execute("SELECT id, name FROM ready_workers WHERE active=1 ORDER BY name").fetchall(),
                               is_admin=adm, today=date.today().isoformat(), SHOPS=SHOPS, SHOP_NAME=SHOP_NAME, PAYERS=PAYERS, tot=tot)


def drivers(con):
    return con.execute("SELECT * FROM ready_workers WHERE active=1 AND driver=1 ORDER BY name").fetchall()
