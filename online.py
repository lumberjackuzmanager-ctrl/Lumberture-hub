# -*- coding: utf-8 -*-
"""Онлайн буюртмалар: сайт (lumberture.com) -> платформа.

Формат — лойиҳа ҳужжати «online-buyurtma-format» (v1). Ташқаридан (Cloudflare Tunnel, app.lumberture.com) фақат /api/online/*
очиқ: қолган саҳифалар 404 қайтаради (_public_gate — биринчи before_request).

API (JSON):
  GET  /api/online/catalog          — плитный материаллар (лист ўлчами), кромка хизматлари, распил/присадка нархлари (нархи йўқ — null)
  POST /api/online/calc             — ҳисоб (сақланмайди)
  POST /api/online/login/start      — {phone} -> код Telegram'да (@бот, мижозлар боти) ; aks holda bot_url (контакт юбориш)
  POST /api/online/login/verify     — {nonce, code} -> {token} (30 кун, «Authorization: Bearer …»)
  GET  /api/online/me ; POST /api/online/logout
  POST /api/online/order            — буюртма -> накладной черновиги (мижоз телефон бўйича, топилмаса янги)
  GET  /api/online/orders ; GET /api/online/order/<id>
Офис: /erp/online — навбат (Юборилди -> Текширилди -> Тўланди -> Кесилмоқда -> Тайёр), ҳар ҳолатда мижозга Telegram хабар."""
import base64, hashlib, hmac, json, math, os, re, secrets, threading, time
from datetime import date, datetime, timedelta

from flask import request, jsonify, render_template, redirect, url_for, flash, abort, Response, g

A = None
VER = 1
KERF, TRIM = 4, 10                 # пропил, лист четидан (мм) — формат ҳужжатидагидек
EDGE_K = 1.06                      # кромка: томон узунлиги × сон / 1000 × 1,06
SESSION_DAYS = 30
CODE_MIN = 10
MAX_PARTS = 400
CNC_MAX_W = 1190                   # присадка ЧПУ: эни 1190 мм дан катта деталь станокка сиғмайди (ровер) — эгаси айтди, 22.10

SCHEMA = """
CREATE TABLE IF NOT EXISTS online_orders(id INTEGER PRIMARY KEY, phone TEXT NOT NULL, client TEXT, name TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'sent', data TEXT NOT NULL, calc TEXT, doc_id INTEGER, err TEXT DEFAULT '', urgent INTEGER DEFAULT 0,
  total_uzs REAL, sum_sent REAL, created_at TEXT, ip TEXT DEFAULT '', upd_by TEXT, upd_at TEXT, hist TEXT DEFAULT '[]');
CREATE INDEX IF NOT EXISTS ix_online_orders_st ON online_orders(status);
CREATE INDEX IF NOT EXISTS ix_online_orders_ph ON online_orders(phone);
CREATE TABLE IF NOT EXISTS online_logins(nonce TEXT PRIMARY KEY, phone TEXT NOT NULL, code TEXT, exp REAL NOT NULL, tries INTEGER DEFAULT 0,
  sends INTEGER DEFAULT 0, chat_id INTEGER, ip TEXT DEFAULT '', created REAL, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS online_sessions(tok TEXT PRIMARY KEY, phone TEXT NOT NULL, exp REAL NOT NULL, created_at TEXT, seen_at TEXT, ip TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS online_chats(phone TEXT PRIMARY KEY, chat_id INTEGER NOT NULL, name TEXT DEFAULT '', username TEXT DEFAULT '',
  linked_at TEXT, active INTEGER DEFAULT 1);
"""

# ҳолатлар: (калит, платформада, мижозга)
STATUSES = [("sent", "Отправлен", "Юборилди"), ("checked", "Проверен", "Текширилди"), ("paid", "Оплачен", "Тўланди"),
            ("in_work", "Передан в работу", "Ишга берилди"), ("ready", "Готов", "Тайёр"), ("cancel", "Отменён", "Бекор қилинди")]
ST_RU = {k: r for k, r, _ in STATUSES}
ST_UZ = {k: u for k, _, u in STATUSES}
FLOW = ["sent", "checked", "paid", "in_work", "ready"]    # «Ишга берилди» — конструктор текширгач (эгаси, 23.10; олдин «cutting»)

PLATE_TOPS = [("лдсп", "ldsp"), ("лмдф", "lmdf"), ("мдф шлифованная", "mdf"), ("дсп", "dsp"), ("акрил", "akril"), ("лхдф", "lhdf"),
              ("хдф/двп", "hdf"), ("осп влагостойкий", "osb"), ("hpl", "hpl")]
AREA_SHEET = {5.796: (2800, 2070), 5.0325: (2750, 1830), 5.0352: (2750, 1830), 4.575: (2500, 1830), 5.175: (2500, 2070),
              4.4652: (2440, 1830), 4.465: (2440, 1830), 5.3253: (2910, 1830), 5.325: (2910, 1830), 2.9768: (2440, 1220)}
HOLE_KEYS = ("hinge", "eccentric", "confirmat", "shelfPin")
HOLE_RULES = {"hinge": [("petlya", 1)], "eccentric": [("ecc", 1), ("glukh", 1)], "confirmat": [("evro_l", 1), ("evro_t", 1)],
              "shelfPin": [("glukh", 1)]}       # ручка тешиги тешилмайди (эгаси қарори, 22.10)
DEF_ORIGINS = "https://lumberture.com, https://www.lumberture.com, https://*.pages.dev, http://localhost:*, http://127.0.0.1:*"
DEF_HOST = "app.lumberture.com"

_rl = {}
_rl_lock = threading.Lock()
_cat_cache = {"key": None, "at": 0, "data": None}


# ================================================================== умумий
_mig = {"done": False}


def init_tables(con):
    con.executescript(SCHEMA)
    if not _mig["done"]:
        cols = {r[1] for r in con.execute("PRAGMA table_info(online_orders)")}
        if "grp" not in cols:                     # сайт: бир буюртмада бир нечта материал — умумий group (22.10, сайт 4-версия)
            con.execute("ALTER TABLE online_orders ADD COLUMN grp TEXT DEFAULT ''")
        if "grp_n" not in cols:
            con.execute("ALTER TABLE online_orders ADD COLUMN grp_n INTEGER DEFAULT 0")
        if "files" not in cols:                   # сайт юборган цех пакети (zip): [{name, size, at, path}]
            con.execute("ALTER TABLE online_orders ADD COLUMN files TEXT DEFAULT ''")
        con.execute("UPDATE online_orders SET status='in_work' WHERE status='cutting'")
        if "notified" not in cols:                # «қабул қилинди» хабари кетганми (гуруҳ: охиргиси келганда ёки 10 дақиқадан кейин)
            con.execute("ALTER TABLE online_orders ADD COLUMN notified INTEGER DEFAULT 0")
            con.execute("UPDATE online_orders SET notified=1")
        con.commit()
        _mig["done"] = True


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm_phone(s):
    """-> «998XXXXXXXXX» ёки ''."""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 9:
        d = "998" + d
    return d if len(d) == 12 and d.startswith("998") else ""


def fmt_phone(p):
    p = norm_phone(p)
    return f"+{p[:3]} {p[3:5]} {p[5:8]}-{p[8:10]}-{p[10:12]}" if p else ""


def mask_phone(p):
    p = norm_phone(p)
    return f"+{p[:3]} {p[3:5]} ***-**-{p[10:12]}" if p else ""


def _sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def client_ip_real():
    return (request.headers.get("Cf-Connecting-Ip") or request.remote_addr or "")[:64]


def rate_ok(kind, key, limit, per):
    """Оддий чеклов (хотирада): per сония ичида limit мартадан кўп эмас."""
    t = time.time()
    with _rl_lock:
        q = [x for x in _rl.get((kind, key), []) if t - x < per]
        if len(q) >= limit:
            _rl[(kind, key)] = q
            return False
        q.append(t)
        _rl[(kind, key)] = q
        if len(_rl) > 20000:
            _rl.clear()
    return True


def err(code, msg, status=400, **kw):
    return jsonify(dict(ok=False, error=code, message=msg, **kw)), status


def _setting(key, default=""):
    v = A.get_setting(key, None)
    return default if v is None or str(v).strip() == "" else str(v)


def public_hosts():
    return {h.strip().lower() for h in re.split(r"[,\s]+", _setting("online_host", DEF_HOST)) if h.strip()}


def is_public_request():
    """Ташқаридан (Cloudflare орқали) келган сўров. Туннель localhost'га уланади, шунинг учун IP бўйича билиб бўлмайди."""
    h = request.headers
    host = (h.get("Host") or "").split(":")[0].strip().lower()
    return bool(h.get("Cf-Ray") or h.get("Cf-Connecting-Ip") or h.get("Cf-Visitor") or host in public_hosts())


def origin_ok(origin):
    if not origin:
        return False
    for pat in re.split(r"[,\s]+", _setting("online_origins", DEF_ORIGINS)):
        pat = pat.strip().rstrip("/")
        if not pat:
            continue
        rx = "^" + re.escape(pat).replace(r"\*", r"[A-Za-z0-9.-]*") + "$"
        if re.match(rx, origin.rstrip("/")):
            return True
    return False


def _cors(resp):
    o = request.headers.get("Origin") or ""
    if origin_ok(o):
        resp.headers["Access-Control-Allow-Origin"] = o
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Max-Age"] = "600"
    return resp


def _public_gate():
    """БИРИНЧИ before_request: ташқаридан фақат /api/online/* (қолгани — 404, сессия/кукиларсиз)."""
    p = request.path or ""
    api = p.startswith("/api/online/")
    if not api:
        if is_public_request():
            return Response("Not found", 404, mimetype="text/plain")
        return None
    g.online_api = True
    if request.method == "OPTIONS":
        return _cors(Response("", 204))
    return None


# ================================================================== бот (мижозлар боти: созламалар → Telegram-бот)
def bot_token():
    if A.get_setting("bot_enabled") != "1":
        return ""
    return A.get_setting("tg_token") or ""


def bot_username():
    return (A.get_setting("tg_bot_username") or "").lstrip("@")


def bot_send(chat_id, text, markup=None):
    tok = bot_token()
    if not tok:
        return False
    import tg, botlang
    lang = botlang.get("client", chat_id)
    if lang:                                          # мижоз танлаган тил (ўзбек / рус / инглиз)
        text, markup = botlang.tr(text, lang), botlang.tr_markup(markup, lang)
    try:
        tg.send_message(tok, chat_id, text, markup)
        return True
    except Exception as e:  # noqa
        A.errlog.warning("online bot send to %s: %s", chat_id, e)
        return False


def queue_msg(con, chat_id, text):
    """Мижозга хабар — умумий навбат орқали (Журнал сообщений'да кўринади)."""
    con.execute("INSERT INTO notifications(tenant_id,chat_id,kind,text,markup,status,created_at) VALUES(NULL,?,?,?,NULL,'pending',?)",
                (chat_id, "online", text, A.now()))
    try:
        A.outbox_event.set()
    except Exception:
        pass


CONTACT_KB = json.dumps({"keyboard": [[{"text": "📱 Рақамимни юбориш", "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True})
NO_KB = json.dumps({"remove_keyboard": True})


def contact_phone():
    return A.get_setting("contact_phone") or "+998 33 409-60-70"


def welcome_text():
    return ("Ассалому алайкум! Бу Lumberture боти.\nОнлайн крой ва присадка буюртмаси — сайтда: lumberture.com\n"
            f"Буюртмангиз ҳолати шу ерга келади.\nТел.: {contact_phone()}\n🌐 Тил / Язык / Language: /lang")


def _send_code(con, lg, chat_id):
    if (lg["sends"] or 0) >= 3:
        bot_send(chat_id, "Код кўп марта юборилди. Сайтда қайта «Кириш» босинг.", NO_KB)
        return False
    code = f"{secrets.randbelow(10 ** 6):06d}"
    con.execute("UPDATE online_logins SET code=?, chat_id=?, sends=COALESCE(sends,0)+1, tries=0 WHERE nonce=?",
                (_sha(lg["nonce"] + ":" + code), chat_id, lg["nonce"]))
    con.commit()
    return bot_send(chat_id, f"Lumberture: сайтга кириш коди — {code}\nКодни ҳеч кимга айтманг. {CODE_MIN} дақиқа амал қилади.", NO_KB)


def on_update(u):
    """Мижозлар боти: онлайн кириш (/start ol_…, контакт) ва Lumberture мижозлари. True — хабар шу ерда ишланди."""
    import botlang
    cq = u.get("callback_query")
    if cq:                                            # тил танлаш тугмаси
        data, ch = cq.get("data") or "", ((cq.get("message") or {}).get("chat") or {}).get("id")
        if data.startswith("lang:") and ch:
            lg = data[5:]
            if botlang.set_lang("client", ch, lg):
                import tg
                try:
                    tg.call(bot_token(), "answerCallbackQuery", {"callback_query_id": cq["id"]})
                    tg.call(bot_token(), "editMessageText", {"chat_id": ch, "message_id": cq["message"]["message_id"], "text": botlang.DONE[lg]})
                except Exception:
                    pass
                bot_send(ch, welcome_text())
            return True
        return False
    msg = u.get("message") or {}
    chat = msg.get("chat") or {}
    if not chat or chat.get("type") != "private":
        return False
    cid, frm = chat["id"], msg.get("from") or {}
    if botlang.from_tg(frm.get("language_code")):    # биринчи марта — Telegram тилига қараб (кейин /lang билан ўзгартирса бўлади)
        botlang.set_lang("client", cid, botlang.from_tg(frm.get("language_code")), auto=True)
    text = (msg.get("text") or "").strip()
    lang = botlang.get("client", cid)
    if lang and text:
        text = botlang.untr(text, lang)
    if botlang.is_lang_cmd(text):
        bot_send(cid, botlang.ASK, botlang.menu())
        return True
    contact = msg.get("contact")
    con = A.db()
    init_tables(con)
    t = time.time()
    if text.startswith("/start web_") or text.startswith("/start web-"):    # 3D бўлими (сайт /m/): рўйхат (web3d)
        import web3d
        return web3d.bot_start(con, cid, text.split(" ", 1)[1][4:], frm)
    if contact:
        import web3d
        if web3d.on_contact(con, cid, contact, frm):
            return True
    if text.startswith("/start ol_") or text.startswith("/start ol-"):
        nonce = re.sub(r"[^A-Za-z0-9_-]", "", text.split(" ", 1)[1][3:])[:64]
        lg = con.execute("SELECT * FROM online_logins WHERE nonce=? AND used=0 AND exp>?", (nonce, t)).fetchone()
        if not lg:
            bot_send(cid, "Кириш ҳаволаси эскирган. Сайтда қайта «Кириш» босинг.")
            return True
        con.execute("UPDATE online_logins SET chat_id=? WHERE nonce=?", (cid, nonce))
        con.commit()
        known = con.execute("SELECT 1 FROM online_chats WHERE phone=? AND chat_id=? AND active=1", (lg["phone"], cid)).fetchone()
        if known:
            _send_code(con, con.execute("SELECT * FROM online_logins WHERE nonce=?", (nonce,)).fetchone(), cid)
        else:
            bot_send(cid, f"Lumberture сайтига кириш: {mask_phone(lg['phone'])} рақамини тасдиқланг — пастдаги тугмани босинг 👇", CONTACT_KB)
        return True
    lg = con.execute("SELECT * FROM online_logins WHERE chat_id=? AND used=0 AND exp>? ORDER BY created DESC LIMIT 1", (cid, t)).fetchone() if contact else None
    if contact and lg:
        if contact.get("user_id") != frm.get("id"):
            bot_send(cid, "Ўз рақамингизни пастдаги тугма орқали юборинг.", CONTACT_KB)
            return True
        ph = norm_phone(contact.get("phone_number"))
        if ph != lg["phone"]:
            bot_send(cid, f"Бу рақам сайтда ёзилган рақамга ({mask_phone(lg['phone'])}) тўғри келмади. Сайтда рақамни текшириб, қайта киринг.", NO_KB)
            return True
        name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)[:80]
        con.execute("INSERT INTO online_chats(phone, chat_id, name, username, linked_at, active) VALUES(?,?,?,?,?,1) "
                    "ON CONFLICT(phone) DO UPDATE SET chat_id=excluded.chat_id, name=excluded.name, username=excluded.username, "
                    "linked_at=excluded.linked_at, active=1", (ph, cid, name, (frm.get("username") or "")[:60], _now()))
        con.commit()
        _send_code(con, lg, cid)
        return True
    if contact:                                       # сайтга киришсиз рақам юборди: мижозлар (1С + онлайн) орасидан, топилмаса ҳам боғланади
        if contact.get("user_id") != frm.get("id"):
            bot_send(cid, "Ўз рақамингизни пастдаги тугма орқали юборинг.", CONTACT_KB)
            return True
        ph = norm_phone(contact.get("phone_number"))
        if not ph:
            bot_send(cid, f"Бу рақамни боғлаб бўлмади. Савол бўлса: {contact_phone()}", NO_KB)
            return True
        name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)[:80]
        con.execute("INSERT INTO online_chats(phone, chat_id, name, username, linked_at, active) VALUES(?,?,?,?,?,1) "
                    "ON CONFLICT(phone) DO UPDATE SET chat_id=excluded.chat_id, name=excluded.name, username=excluded.username, "
                    "linked_at=excluded.linked_at, active=1", (ph, cid, name, (frm.get("username") or "")[:60], _now()))
        con.commit()
        lg = con.execute("SELECT * FROM online_logins WHERE phone=? AND used=0 AND exp>? ORDER BY created DESC LIMIT 1", (ph, t)).fetchone()
        if lg:                                        # сайтда шу рақам билан «Кириш» босилган — код дарров
            con.execute("UPDATE online_logins SET chat_id=? WHERE nonce=?", (cid, lg["nonce"]))
            con.commit()
            _send_code(con, con.execute("SELECT * FROM online_logins WHERE nonce=?", (lg["nonce"],)).fetchone(), cid)
            return True
        cl = find_client(con, ph)
        r = con.execute("SELECT name FROM erp_clients WHERE ext=?", (cl,)).fetchone() if cl else None
        bot_send(cid, linked_text(r["name"] if r else "") + _orders_tail(con, cid), NO_KB)
        return True
    # бот фақат Lumberture мижозлари учун — ижарага ҳеч қандай боғлиқ жойи йўқ (эгаси айтди, 22.10)
    if text.startswith("/start") or text:
        linked = con.execute("SELECT 1 FROM online_chats WHERE chat_id=? AND active=1", (cid,)).fetchone()
        if linked:
            bot_send(cid, welcome_text() + _orders_tail(con, cid), NO_KB)     # эски тугмалар (менюлар) олиб ташланади
        else:
            bot_send(cid, welcome_text() + "\n\n" + ASK_PHONE, CONTACT_KB)
        return True
    return False


ASK_PHONE = "Буюртмаларингиз ҳолати шу ерга келиши учун рақамингизни юборинг 👇"


def linked_text(cname=""):
    return (f"✅ Lumberture: рақамингиз уланди{(' — ' + cname) if cname else ''}.\nБуюртмаларингиз ҳолати шу ерга келади.\n"
            f"Онлайн крой ва присадка: lumberture.com\nТел.: {contact_phone()}")


def _orders_tail(con, cid):
    o = con.execute("SELECT o.id, o.status FROM online_orders o JOIN online_chats c ON c.phone=o.phone WHERE c.chat_id=? AND c.active=1 "
                    "ORDER BY o.id DESC LIMIT 3", (cid,)).fetchall()
    return ("\n\nОхирги буюртмалар:\n" + "\n".join(f"№{x['id']} — {ST_UZ.get(x['status'], x['status'])}" for x in o)) if o else ""


# ================================================================== каталог
def _tree(con):
    rows = con.execute("SELECT ext, name, parent, folder, unit, price, cur, cvis FROM erp_goods WHERE src='cat' AND deleted=0").fetchall()
    by = {r["ext"]: r for r in rows}
    kids = {}
    for r in rows:
        kids.setdefault(r["parent"] or "", []).append(r)
    return by, kids


def _walk(kids, ext, path=()):
    for r in sorted(kids.get(ext, []), key=lambda x: (x["name"] or "")):
        if r["folder"]:
            yield from _walk(kids, r["ext"], path + (r["name"],))
        else:
            yield r, path


def sheet_of(name):
    """Лист ўлчами товар номидан: «2800*2070», «2,8*2,07», «5,796» -> (L, W) мм ёки None."""
    n = (name or "").replace(",", ".")
    m = re.search(r"(?<![\d.])(\d{4})\s*[*×xхXХ]\s*(\d{3,4})(?![\d.])", n)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return (max(a, b), min(a, b))
    m = re.search(r"(?<![\d.])([1-3]\.\d{1,3})\s*[*×xх]\s*([0-2]\.\d{1,3})(?![\d.])", n)
    if m:
        a, b = round(float(m.group(1)) * 1000), round(float(m.group(2)) * 1000)
        return (max(a, b), min(a, b))
    m = re.search(r"(?<![\d.])([2-7]\.\d{2,4})(?![\d.])", n)
    if m:
        v = float(m.group(1))
        for k, s in AREA_SHEET.items():
            if abs(k - v) < 0.0006:
                return s
    return None


def thick_of(name):
    n = (name or "").replace(",", ".")
    m = re.search(r"(\d{1,2}(?:\.\d)?)\s*(?:мм|mm|mm\b)", n, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"\d{4}\s*[*×xх]\s*\d{3,4}\s*[*×xх]\s*(\d{1,2}(?:\.\d)?)", n)
    if m:
        return float(m.group(1))
    return None


def edge_size(name):
    m = re.search(r"(\d{2})\s*/\s*(0\d(?![\d.,])|\d(?:[.,]\d+)?)", name or "")
    if not m:
        return None, None
    t = m.group(2).replace(",", ".")
    tt = float("0." + t[1:]) if re.fullmatch(r"0\d", t) else float(t)
    return int(m.group(1)), tt


def _price(rp, r):
    p = rp.get(r["ext"]) or r["price"] or 0
    try:
        p = float(p)
    except (TypeError, ValueError):
        p = 0
    return (round(p, 2) if p > 0 else None), ("USD" if (r["cur"] or 1) == 2 else "UZS")


def build_catalog(con):
    import erp as E
    rp = E.reval_prices(con)
    by, kids = _tree(con)
    tops = {(r["name"] or "").strip().casefold(): r for r in kids.get("", []) if r["folder"]}
    mats, edges, svcs = [], [], []
    for tname, kind in PLATE_TOPS:
        t = tops.get(tname)
        if not t:
            continue
        for r, path in _walk(kids, t["ext"]):
            nm = r["name"] or ""
            if not r["cvis"] or "пусто" in nm.casefold():
                continue
            pr, cur = _price(rp, r)
            sh = sheet_of(nm)
            low = nm.casefold()
            sides = 1 if re.search(r"1/2|односторон|о/с", low) else (2 if re.search(r"двухсторон|д/с", low) else None)
            mats.append(dict(id=r["ext"], name=nm, kind=kind, path=list(path), thick=thick_of(nm), sides=sides,
                             sheet=dict(L=sh[0], W=sh[1]) if sh else None, unit=r["unit"] or "", price=pr, cur=cur))
    usl = tops.get("услуги")
    if usl:
        sub = {(r["name"] or "").strip().casefold(): r for r in kids.get(usl["ext"], []) if r["folder"]}
        for fname, typ in (("кромка", "pvc"), ("кромка овал", "oval")):
            f = sub.get(fname)
            if not f:
                continue
            for r, path in _walk(kids, f["ext"]):
                nm = r["name"] or ""
                if not r["cvis"]:
                    continue
                w, t = edge_size(nm)
                pr, cur = _price(rp, r)
                client = bool(path) and path[0].strip().casefold() in ("услуга пвх", "услуга овал")
                edges.append(dict(id=r["ext"], name=nm, type=typ, w=w, t=t, client=client, path=list(path), unit=r["unit"] or "пог.м",
                                  price=pr, cur=cur))
        for fname, grp in (("распил", "raspil"), ("присадка", "prisadka"), ("ровер", "rover")):
            f = sub.get(fname)
            if not f:
                continue
            for r, path in _walk(kids, f["ext"]):
                if not r["cvis"]:
                    continue
                if grp == "raspil" and path and "пакет" in path[0].casefold():
                    continue                                 # пильный центр (пакетный) — сайтда эмас
                pr, cur = _price(rp, r)
                svcs.append(dict(id=r["ext"], name=r["name"], group=grp, unit=r["unit"] or "", price=pr, cur=cur))
    import basis
    holes = {}
    for k, rules in HOLE_RULES.items():
        items = []
        for key, mult in rules:
            ext = basis.svc_default(con, key)
            r = by.get(ext)
            pr, cur = _price(rp, r) if r else (None, "UZS")
            items.append(dict(id=ext or None, name=(r["name"] if r else basis.SVC_NAME.get(key, key)), per=mult, price=pr, cur=cur))
        holes[k] = items
    # v1+ (сайт 4-версия): евровинт лицевая/торцевая алоҳида, паз (пог.м), фрезеровка — платформа нархи
    def one(ext, unit_dflt, per_note):
        r = by.get(ext) if ext else None
        pr, cur = _price(rp, r) if r else (None, "UZS")
        return dict(id=ext or None, name=r["name"] if r else None, unit=(r["unit"] if r else "") or unit_dflt, price=pr, cur=cur, per=per_note)
    extra = dict(confirmatFace=one(basis.svc_default(con, "evro_l"), "шт", "hole"),
                 confirmatEnd=one(basis.svc_default(con, "evro_t"), "шт", "hole"),
                 eccentricCam=one(basis.svc_default(con, "ecc"), "шт", "hole"),
                 grooveHdf=one(svc_find(con, *GROOVE_SVC["hdf"][:2]), "пог.м", "m"),
                 grooveLed=one(svc_find(con, *GROOVE_SVC["led"][:2]), "пог.м", "m"),
                 cut=dict(one(svc_find(con, *FREZ_SVC[:2]), "шт", "part"), price=None, note="рассчитаем под ваш проект"))
    rate = E.day_rate()[0] or 0
    return dict(ok=True, v=VER, updated=_now(), rate=rate, materials=mats, edges=edges, services=svcs, holes=holes, extra=extra,
                rules=dict(kerf=KERF, trim=TRIM, edge_k=EDGE_K, handle_holes=False, cnc_max_w=CNC_MAX_W))


def catalog_cached(con):
    key = con.execute("SELECT COUNT(*), MAX(rowid), COALESCE(SUM(price),0) FROM erp_goods WHERE src='cat'").fetchone()
    rv = con.execute("SELECT MAX(updated_at) FROM erp_docs WHERE kind='reval'").fetchone()
    k = (tuple(key), rv[0] if rv else None, date.today().isoformat())
    if _cat_cache["key"] == k and time.time() - _cat_cache["at"] < 600:
        return _cat_cache["data"]
    d = build_catalog(con)
    _cat_cache.update(key=k, at=time.time(), data=d)
    return d


# ================================================================== ҳисоб
class OrderError(ValueError):
    def __init__(self, code, msg, field=None):
        super().__init__(msg)
        self.code, self.msg, self.field = code, msg, field


def _num(v, lo, hi, field, integer=True):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise OrderError("bad_number", f"Нотўғри қиймат: {field}", field)
    if integer and x != int(x):
        raise OrderError("bad_number", f"Бутун сон бўлиши керак: {field}", field)
    if not (lo <= x <= hi):
        raise OrderError("out_of_range", f"{field}: {lo}–{hi} оралиғида бўлиши керак", field)
    return int(x) if integer else x


def validate(data):
    """Сайтдан келган JSON -> тозаланган dict (формат v1). Хато бўлса OrderError."""
    if not isinstance(data, dict):
        raise OrderError("bad_json", "JSON объект керак")
    if str(data.get("v") or "").strip() != str(VER):
        raise OrderError("bad_version", f"Формат версияси {VER} бўлиши керак", "v")
    m = data.get("material")
    if not isinstance(m, dict):
        raise OrderError("no_material", "Материал танланмаган", "material")
    owner = "client" if m.get("owner") == "client" else "ours"
    sh = m.get("sheet") if isinstance(m.get("sheet"), dict) else {}
    mat = dict(id=str(m.get("id") or "")[:40], name=str(m.get("name") or "")[:200], owner=owner,
               sheet=dict(L=_num(sh.get("L"), 300, 6000, "material.sheet.L"), W=_num(sh.get("W"), 300, 3000, "material.sheet.W")) if sh else None,
               thick=_num(m.get("thick"), 1, 60, "material.thick", False) if m.get("thick") not in (None, "") else None)
    parts = data.get("parts")
    if not isinstance(parts, list) or not parts:
        raise OrderError("no_parts", "Деталлар йўқ", "parts")
    if len(parts) > MAX_PARTS:
        raise OrderError("too_many_parts", f"Деталлар {MAX_PARTS} тадан кўп", "parts")
    out = []
    for i, p in enumerate(parts):
        if not isinstance(p, dict):
            raise OrderError("bad_part", f"{i + 1}-деталь нотўғри", f"parts[{i}]")
        f = f"parts[{i}]"
        L, W = _num(p.get("L"), 10, 6000, f + ".L"), _num(p.get("W"), 10, 3000, f + ".W")
        qty = _num(p.get("qty") or 1, 1, 1000, f + ".qty")
        e = p.get("edges") if isinstance(p.get("edges"), dict) else {}
        edges = {}
        for s in ("L1", "L2", "W1", "W2"):
            v = e.get(s)
            edges[s] = (str(v).strip()[:40] or None) if v not in (None, "", False) else None
        h = p.get("holes") if isinstance(p.get("holes"), dict) else {}
        holes = {k: _num(h.get(k) or 0, 0, 200, f"{f}.holes.{k}") for k in HOLE_KEYS}
        holes.update(_holes_extra(h, f))
        part = dict(pos=int(p.get("pos") or i + 1) if str(p.get("pos") or "").isdigit() else i + 1,
                    name=str(p.get("name") or "")[:80], L=L, W=W, qty=qty, grain=bool(p.get("grain", True)), edges=edges, holes=holes,
                    handle=bool((h.get("handle") or 0)), note=str(p.get("note") or "")[:200])
        c = p.get("cut")
        if isinstance(c, dict) and c.get("L") not in (None, "") and c.get("W") not in (None, ""):
            part["cut"] = dict(L=round(_num(c.get("L"), 10, 6000, f + ".cut.L", False), 1), W=round(_num(c.get("W"), 10, 3000, f + ".cut.W", False), 1))
        ops = _ops(p.get("ops"))
        if ops:
            part["ops"] = ops
        part.update(_site_extra(p))
        out.append(part)
    cl = data.get("client") if isinstance(data.get("client"), dict) else {}
    res = dict(v=VER, source=str(data.get("source") or "site")[:20], material=mat, urgent=bool(data.get("urgent")), parts=out,
               client=dict(name=str(cl.get("name") or "")[:80].strip()), note=str(data.get("note") or "")[:500],
               site_calc=data.get("calc") if isinstance(data.get("calc"), dict) else None)
    grp = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("group") or ""))[:40]
    if grp:
        res["group"] = grp
    if data.get("size_mode") in ("finished", "saw"):
        res["size_mode"] = data["size_mode"]
    return res


SIDES = ("L1", "L2", "W1", "W2")
HOLE_EXTRA = ("confirmatFace", "confirmatEnd", "eccentricCam", "eccentricPin", "nasechka")
FIX_KINDS = ("cfmFace", "cfmEnd", "eccCam", "eccPin")


def _fnum(v, lo=-50, hi=6000):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return round(x, 1) if lo <= x <= hi and x == x else None


def _holes_extra(h, f):
    """v1+ майдонлари: евровинт томонлари (лицевая/торцевая сони), эксцентрик, петля томони ва жойи — нарх ва «JSON для Базиса» учун."""
    out = {}
    for k in HOLE_EXTRA:
        if h.get(k) not in (None, ""):
            out[k] = _num(h.get(k) or 0, 0, 400, f"{f}.holes.{k}")
    if h.get("hingeSide") in SIDES:
        out["hingeSide"] = h["hingeSide"]
    if isinstance(h.get("hingeAuto"), bool):
        out["hingeAuto"] = h["hingeAuto"]
    if isinstance(h.get("hingePos"), list):
        ps = [x for x in (_fnum(v, 0) for v in h["hingePos"][:20]) if x is not None]
        if ps:
            out["hingePos"] = ps
    cs = h.get("confirmatSides")
    if isinstance(cs, dict):
        cc = {}
        for s in SIDES:
            v = cs.get(s)
            if isinstance(v, dict) and v.get("t") in ("face", "end"):
                x = dict(t=v["t"])
                if str(v.get("n") or "").isdigit() and 0 < int(v["n"]) <= 20:
                    x["n"] = int(v["n"])
                cc[s] = x
        if cc:
            out["confirmatSides"] = cc
    return out


def _site_extra(p):
    """Сайт ўзи учун юборган маълумот (буюртмани сайтда қайта очиш ва цех пакети учун): тешиклар Базисдаги каби (`raw`),
    сайт таҳрирлагичидаги операциялар (`ops_src`), кромка номлари. Ўзгаришсиз сақланади (ҳажми чекланган)."""
    out = {}
    for k, typ, lim in (("raw", list, 60000), ("ops_src", dict, 30000)):
        v = p.get(k)
        if isinstance(v, typ) and v:
            try:
                js = json.dumps(v, ensure_ascii=False)
            except (TypeError, ValueError):
                continue
            if len(js) <= lim:
                out[k] = json.loads(js)
    en = p.get("edge_names")
    if isinstance(en, dict):
        en = {s_: str(en[s_])[:60] for s_ in SIDES if en.get(s_) not in (None, "")}
        if en:
            out["edge_names"] = en
    return out


def _ops(o):
    """parts[].ops — Базис учун аниқ жойлар (мм). Нотўғри элементлар ташланади."""
    if not isinstance(o, dict):
        return None
    out = {}
    hs = [dict(side=x["side"], t=_fnum(x.get("t"))) for x in (o.get("hinges") or [])[:40]
          if isinstance(x, dict) and x.get("side") in SIDES and _fnum(x.get("t")) is not None]
    fx = [dict(side=x["side"], k=x["k"], t=_fnum(x.get("t"))) for x in (o.get("fix") or [])[:200]
          if isinstance(x, dict) and x.get("side") in SIDES and x.get("k") in FIX_KINDS and _fnum(x.get("t")) is not None]
    sh = [dict(x=_fnum(x.get("x")), y=_fnum(x.get("y"))) for x in (o.get("shelf") or [])[:400]
          if isinstance(x, dict) and _fnum(x.get("x")) is not None and _fnum(x.get("y")) is not None]
    gr = []
    for x in (o.get("grooves") or [])[:40]:
        if isinstance(x, dict) and x.get("side") in SIDES and x.get("kind") in ("hdf", "led"):
            g_ = dict(side=x["side"], kind=x["kind"], off=_fnum(x.get("off")) or 0)
            ln = _fnum(x.get("len"), 1)
            if ln:
                g_["len"] = ln
            gr.append(g_)
    for k, v in (("hinges", hs), ("fix", fx), ("shelf", sh), ("grooves", gr)):
        if v:
            out[k] = v
    cut = o.get("cut")
    if isinstance(cut, str) and cut.strip():
        out["cut"] = cut.strip()[:300]
    return out


def pack_sheets(parts, L, W, kerf=KERF, trim=TRIM):
    """Тахминий лист сони (полкали жойлаш). parts: [(l, w, qty, grain)], l — текстура бўйлаб. -> (листлар, катта деталлар)"""
    UL, UW = L - 2 * trim, W - 2 * trim
    items, big = [], []
    for l, w, q, grain in parts:
        if not grain and max(l, w) <= UL and min(l, w) <= UW:
            l, w = max(l, w), min(l, w)               # текстурасиз: узун томони лист бўйлаб (полка паст бўлади)
        if not (l <= UL and w <= UW) and not (not grain and w <= UL and l <= UW):
            big.append((l, w))
            continue
        items += [(l, w, grain)] * int(q)
    items.sort(key=lambda x: (-x[1], -x[0]))
    sheets = []                       # ҳар лист: {"used_w": .., "shelves": [[h, used_l]]}

    def place(l, w):
        for s in sheets:
            for sh in s["shelves"]:
                if w <= sh[0] and sh[1] + l <= UL:
                    sh[1] += l + kerf
                    return True
        for s in sheets:
            if s["used_w"] + w <= UW:
                s["shelves"].append([w, l + kerf])
                s["used_w"] += w + kerf
                return True
        return False
    for l, w, grain in items:
        opts = ([(l, w)] if l <= UL and w <= UW else []) + ([(w, l)] if not grain and w <= UL and l <= UW else [])
        if any(place(a, b) for a, b in opts):
            continue
        a, b = opts[0]
        sheets.append({"used_w": b + kerf, "shelves": [[b, a + kerf]]})
    return len(sheets), big


def _good(con, ext):
    return con.execute("SELECT * FROM erp_goods WHERE ext=? AND folder=0 AND deleted=0", (ext,)).fetchone() if ext else None


def _find_saw(con, kind, area, thick, owner, packet=False):
    """Распил хизмати: «Распил ОБЫЧНЫЙ» (двухпилка) ёки packet=True — «ПАКЕТНЫЙ» (пильный центр NPC 330; Базис лойиҳалари — эгаси айтди).
    Пакетлисида шу тур бўлмаса (масалан ДСП) — оддийси."""
    if packet:
        x = _find_saw_in(con, kind, area, thick, owner, True)
        if x:
            return x
    return _find_saw_in(con, kind, area, thick, owner, False)


def _find_saw_in(con, kind, area, thick, owner, packet):
    if owner == "client":
        pats = [r"т\s*а\s*ш\s*к\s*а\s*р\s*и\s*д\s*а\s*н"]
    elif kind in ("ldsp", "lmdf"):
        pats = [r"5[,.]796"] if area >= 5.5 else [r"4[,.]5\s*кв"]
    elif kind == "mdf":
        pats = [r"от\s*22"] if (thick or 0) >= 22 else [r"от\s*0?6\s*мм"]
    elif kind in ("hdf", "lhdf"):
        pats = [r"распил\s+хдф"]
    elif kind == "akril":
        pats = [r"распил\s+акрил"]
    elif kind == "dsp":                      # пакетлида ДСП — «ДСП/МДФ» хизматлари билан бир (эгаси айтди)
        pats = ([r"дсп/мдф.*от\s*22"] if (thick or 0) >= 22 else [r"дсп/мдф.*от\s*0?6"]) if packet else [r"распил\s+дсп"]
    else:
        return ""
    for r in con.execute("SELECT ext, name FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 AND lc LIKE '%распил%' ORDER BY length(name)"):
        n = (r["name"] or "").casefold()
        if ("пакет" in n) != packet or "срочн" in n:
            continue
        if any(re.search(p, n) for p in pats):
            return r["ext"]
    return ""


def _client_edge(con, thick, oval=False):
    """Мижоз кромкаси: «Услуга ПВХ от 19/04 до 22/2» (қалинлиги 22 мм гача) ёки «… от 35/1 до 48/2»."""
    big = (thick or 16) >= 22
    word = "овал" if oval else "пвх"
    for r in con.execute("SELECT ext, name FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 AND lc LIKE '%услуг%' ORDER BY length(name)"):
        n = (r["name"] or "").casefold()
        if word in n and re.search(r"\bот\s*\d", n) and (bool(re.search(r"от\s*35", n)) == big):
            return r["ext"]
    return ""


def mat_kind(con, g, name):
    """Материал тури: каталогдаги юқори папка бўйича, бўлмаса номидан."""
    if g:
        x, seen = g, 0
        while x and x["parent"] and seen < 12:
            p = con.execute("SELECT ext, name, parent FROM erp_goods WHERE ext=?", (x["parent"],)).fetchone()
            if not p:
                break
            x, seen = p, seen + 1
        top = (x["name"] or "").strip().casefold() if x else ""
        for tname, kind in PLATE_TOPS:
            if top == tname:
                return kind
    low = (name or "").casefold()
    for w, kind in (("лмдф", "lmdf"), ("лдсп", "ldsp"), ("лхдф", "lhdf"), ("хдф", "hdf"), ("двп", "hdf"), ("мдф", "mdf"), ("дсп", "dsp"),
                    ("акрил", "akril"), ("osb", "osb"), ("осп", "osb")):
        if w in low:
            return kind
    return ""


def compute(con, d):
    """Платформа ҳисоби (сайт ҳисоби фақат маълумот учун). -> calc dict"""
    import basis
    import erp as E
    rp = E.reval_prices(con)
    rate = E.day_rate()[0] or 0
    m = d["material"]
    g = _good(con, m["id"]) if m["owner"] == "ours" or m["id"] else None
    if not g and m["owner"] == "ours" and m["name"]:
        try:
            g = _good(con, basis.cat_best(con, "panel", m["name"], m.get("thick")))
        except Exception:
            g = None
    warn, missing, lines = [], [], []
    mname = (g["name"] if g else "") or m["name"] or "материал"
    kind = mat_kind(con, g, mname)
    thick = m.get("thick") or thick_of(mname)
    sh = m.get("sheet") or None
    if g and not sh:
        s = sheet_of(g["name"])
        sh = dict(L=s[0], W=s[1]) if s else None
    if not sh and re.search(r"(?<![\d.,])5\s*кв", mname):
        sh = dict(L=2750, W=1830)                  # «5кв» — маҳаллий 5 м² лист (Базис ҳисобидаги каби 2750×1830)
        warn.append("Лист ўлчами номида йўқ («5кв») — 2750×1830 деб ҳисобланди, текширинг")
    if not sh:
        sh = dict(L=2800, W=2070)
        warn.append("Лист ўлчами номаълум — 2800×2070 деб ҳисобланди")
    if m["owner"] == "ours" and not g:
        warn.append(f"Материал каталогда топилмади: «{m['name'] or m['id']}» — конструктор танлайди")
    nsheets, big = pack_sheets([((p.get("cut") or p)["L"], (p.get("cut") or p)["W"], p["qty"], p["grain"]) for p in d["parts"]],
                               sh["L"], sh["W"])                 # пила ўлчами (cut) бўлса — шу билан
    for l, w in big:
        warn.append(f"Деталь {l}×{w} листдан катта ({sh['L']}×{sh['W']})")
    area = sh["L"] * sh["W"] / 1e6

    def add(ext, qty, kind_, title, note="", noprice=False):
        r = _good(con, ext)
        if not r or qty <= 0:
            if qty > 0:
                missing.append(title)
            return
        pr = 0 if noprice else (rp.get(r["ext"]) or r["price"] or 0)
        cur = r["cur"] or 1
        same = next((x for x in lines if x["good"] == r["ext"]), None)      # мижоз кромкаси ва шу хизмат коди — бир қатор
        if same:
            same["qty"] = round(same["qty"] + qty, 2)
            if same["price"]:
                same["sum_uzs"] = round(same["qty"] * same["price"] * (rate if cur == 2 else 1))
            return
        line = dict(good=r["ext"], name=r["name"], qty=round(qty, 2), unit=r["unit"] or "", price=round(float(pr), 2) if pr else None,
                    cur="USD" if cur == 2 else "UZS", kind=kind_, note=note)
        if line["price"]:
            line["sum_uzs"] = round(line["qty"] * line["price"] * (rate if cur == 2 else 1))
        else:
            missing.append(r["name"])
        lines.append(line)
    if m["owner"] == "ours" and g:
        unit = (g["unit"] or "").casefold()
        q = nsheets * area if ("кв" in unit or "м2" in unit or "м²" in unit) else nsheets
        add(g["ext"], q, "material", mname, f"{nsheets} лист")
    saw = _find_saw(con, kind, area, thick, m["owner"])
    if saw:
        add(saw, nsheets, "raspil", "Распил")
    else:
        missing.append("Распил (" + (kind or "материал") + ")")
    # кромка: томон узунлиги × сон / 1000 × 1,06
    em = {}
    for p in d["parts"]:
        for s, code in p["edges"].items():
            if code:
                ln = p["L"] if s.startswith("L") else p["W"]
                em[code] = em.get(code, 0) + ln * p["qty"] / 1000
    edges_m = {}
    for code, mtr in em.items():
        mtr = round(mtr * EDGE_K, 2)
        edges_m[code] = mtr
        if code == "client":
            ext = _client_edge(con, thick)
            title = "Кромка (мижозники)"
        else:
            r = _good(con, code)
            ext = r["ext"] if r and re.search(r"услуг", (r["name"] or "").casefold()) else ""
            title = (r["name"] if r else code)
            if not ext:
                warn.append(f"Кромка коди «{code}» каталогда топилмади")
        add(ext, mtr, "edge", title)
    # присадка (ручка тешиги тешилмайди)
    hc = {k: sum(p["holes"][k] * p["qty"] for p in d["parts"]) for k in HOLE_KEYS}
    for k in ("confirmatFace", "confirmatEnd"):
        if any(k in p["holes"] for p in d["parts"]):
            hc[k] = sum(p["holes"].get(k, 0) * p["qty"] for p in d["parts"])
    svc = {}
    for p in d["parts"]:
        for key, n in part_svc(p["holes"]).items():
            svc[key] = svc.get(key, 0) + n * p["qty"]
    for key, n in svc.items():
        if n > 0:
            add(basis.svc_default(con, key), n, "hole", basis.SVC_NAME.get(key, key))
    # пазлар (ХДФ — «Паз (4мм)», подсветка — «Паз (под подсветки)»), пог.м
    gm = {}
    for p in d["parts"]:
        for g_ in (p.get("ops") or {}).get("grooves") or []:
            ln = g_.get("len") or (p["L"] if g_["side"].startswith("L") else p["W"])
            gm[g_["kind"]] = gm.get(g_["kind"], 0) + ln * p["qty"] / 1000
    for kind_, mtr in gm.items():
        key, pat, title = GROOVE_SVC[kind_]
        add(svc_find(con, key, pat), round(mtr, 2), "groove", title)
    # фрезеровка (ops.cut) — нархини конструктор қўяди (ҳажмига қараб: фрезеровка ЧПУ ёки ровер)
    cuts = [p for p in d["parts"] if (p.get("ops") or {}).get("cut")]
    if cuts:
        key, pat, title = FREZ_SVC
        add(svc_find(con, key, pat), sum(p["qty"] for p in cuts), "frez", title,
            "; ".join(f"№{p['pos']} {p['name']}".strip() for p in cuts)[:150], noprice=True)
        for p in cuts[:10]:
            warn.append(f"Фрезеровка: №{p['pos']} {p['name']} {p['L']}×{p['W']} ×{p['qty']} — {p['ops']['cut']}")
    if any(p.get("handle") for p in d["parts"]):
        warn.append("Ручка тешиги тешилмайди — ручка жойида ўрнатилади")
    wide = [p for p in d["parts"] if min(p["L"], p["W"]) > CNC_MAX_W and any(p["holes"].values())]
    if wide:
        warn.append(f"Эни {CNC_MAX_W} мм дан катта деталда присадка ЧПУ'да бўлмайди (ровер) — конструктор баҳолайди: "
                    + ", ".join(f"№{p['pos']} {p['L']}×{p['W']}" for p in wide[:5]))
    total = sum(l.get("sum_uzs") or 0 for l in lines)
    return dict(sheets=nsheets, sheet=sh, material=dict(id=g["ext"] if g else None, name=mname, kind=kind, thick=thick, owner=m["owner"]),
                edges_m=edges_m, holes=hc, prisadka=svc, lines=lines, missing=sorted(set(missing)), warnings=warn,
                total_uzs=round(total), rate=rate, priced_all=not missing, urgent=d["urgent"],
                parts=sum(p["qty"] for p in d["parts"]))


GROOVE_SVC = {"hdf": ("paz4", r"паз\s*\(?\s*4\s*мм", "Паз (4мм)"), "led": ("paz_led", r"паз\s*\(?\s*под\s*подсвет", "Паз (под подсветки)")}
FREZ_SVC = ("frez", r"фрезеровк|обработк\w*\s*чпу", "Фрезеровка (обработка ЧПУ) / Ровер")


def part_svc(h):
    """Бир дона деталь тешиклари -> присадка хизматлари {key: сон}.
    Евровинт: confirmatFace/confirmatEnd бўлса — лицевая/торцевая алоҳида (ҳар бири 750), бўлмаса эски confirmat (лицевая + торцевая).
    Эксцентрик: корпус Ø15 (eccentricCam, бўлмаса eccentric) — «Под эксцентрики» + глухое (шток)."""
    out = {}

    def put(k, n):
        if n:
            out[k] = out.get(k, 0) + n
    put("petlya", h.get("hinge", 0))
    ecc = h["eccentricCam"] if "eccentricCam" in h else h.get("eccentric", 0)
    put("ecc", ecc)
    put("glukh", ecc)
    if "confirmatFace" in h or "confirmatEnd" in h:
        put("evro_l", h.get("confirmatFace", 0))
        put("evro_t", h.get("confirmatEnd", 0))
    else:
        put("evro_l", h.get("confirmat", 0))
        put("evro_t", h.get("confirmat", 0))
    put("glukh", h.get("shelfPin", 0))
    put("nasechka", h.get("nasechka", 0))                  # Ø3×1 (петля шурупи, направляющая) — «Насечка (3мм)»
    return out


def svc_find(con, key, pat):
    """Хизмат товари: эслаб қолинган (basis_names «svc:…») → номи қолипга мос «Услуга …»."""
    r = con.execute("SELECT good FROM basis_names WHERE key=?", ("svc:" + key,)).fetchone() if con.execute(
        "SELECT 1 FROM sqlite_master WHERE name='basis_names'").fetchone() else None
    if r and _good(con, r[0]):
        return r[0]
    for g_ in con.execute("SELECT ext, name FROM erp_goods WHERE folder=0 AND deleted=0 AND src='cat' AND lc LIKE '%услуг%' ORDER BY length(name)"):
        if re.search(pat, (g_["name"] or "").casefold()):
            return g_["ext"]
    return ""


def public_calc(c):
    keep = ("sheets", "sheet", "material", "edges_m", "holes", "total_uzs", "missing", "warnings", "priced_all", "urgent", "parts")
    out = {k: c.get(k) for k in keep}
    out["lines"] = [{k: l.get(k) for k in ("name", "qty", "unit", "price", "cur", "sum_uzs", "kind")} for l in c.get("lines") or []]
    out["note"] = "Тахминий ҳисоб. Аниқ сумма — конструктор текширгандан кейин." if c.get("missing") or c.get("warnings") else "Тахминий ҳисоб."
    return out


# ================================================================== мижоз ва накладной
def find_client(con, phone):
    key = norm_phone(phone)[-9:]
    if not key:
        return None
    best = None
    for r in con.execute("SELECT ext, name, phones, CASE WHEN src='1c' OR COALESCE(src_orig,'')='1c' THEN '1c' ELSE src END src "
                         "FROM erp_clients WHERE folder=0 AND deleted=0 AND COALESCE(phones,'')<>''"):
        for p in re.split(r"[,;/]|\s{2,}", r["phones"] or ""):
            d = re.sub(r"\D", "", p)
            if len(d) >= 9 and d[-9:] == key:
                if not best or (r["src"] == "1c" and best["src"] != "1c"):     # 1С'дан келган (эски) мижоз биринчи
                    best = r
                break
    return best["ext"] if best else None


def make_client(con, phone, name):
    import erp as E
    base = (name or "").strip() or "Онлайн мижоз"
    nm = base if len(base) >= 2 else "Онлайн мижоз"
    taken = {(r["name"] or "").casefold().strip() for r in con.execute("SELECT name FROM erp_clients WHERE folder=0 AND deleted=0")}
    if nm.casefold() in taken:
        nm = f"{base} ({fmt_phone(phone)})"
    i = 2
    while nm.casefold() in taken:
        nm, i = f"{base} ({fmt_phone(phone)}) {i}", i + 1
    return E.save_client(con, None, {"name": nm, "phones": fmt_phone(phone), "cnote": "Онлайн буюртма орқали (сайт)", "ckind": "client"})


def make_draft(con, o):
    """Буюртма -> накладной черновиги. Нархи йўқ қаторлар черновикка тушмайди (save_sale нарх талаб қилади) — изоҳда."""
    import erp as E
    c = json.loads(o["calc"] or "{}")
    d = json.loads(o["data"] or "{}")
    lines = [dict(good=l["good"], qty=A.num(l["qty"]), price=A.num(l["price"]), note=(l.get("note") or "")[:150])
             for l in c.get("lines") or [] if l.get("price")]
    nop = [l["name"] for l in c.get("lines") or [] if not l.get("price")]
    miss = [x for x in c.get("missing") or [] if x not in nop]
    note = [f"Онлайн №{o['id']} · {fmt_phone(o['phone'])} · {c.get('parts', 0)} дет., {c.get('sheets', 0)} лист"]
    if (d.get("material") or {}).get("owner") == "client":
        note.append("Материал мижозники")
    if nop or miss:
        note.append("Нархсиз (конструктор қўшади): " + "; ".join(nop + miss))
    for w in c.get("warnings") or []:
        note.append("! " + w)
    if d.get("note"):
        note.append("Мижоз: " + d["note"])
    if not lines:
        raise ValueError("Нархли қатор йўқ — черновик конструктор томонидан тузилади")
    form = dict(client=o["client"], rate="", nal="1", note="\n".join(note)[:2000], discount="0", date=date.today().isoformat(),
                lines=lines, urgent="1" if o["urgent"] else "")
    did = E.save_sale(con, None, form, post=False)
    con.execute("UPDATE erp_docs SET created_by='онлайн', updated_by=COALESCE(NULLIF(updated_by,''),'онлайн'), user=COALESCE(NULLIF(user,''),'онлайн') "
                "WHERE id=?", (did,))
    return did


def _hist(con, oid, what):
    r = con.execute("SELECT hist FROM online_orders WHERE id=?", (oid,)).fetchone()
    try:
        h = json.loads(r["hist"] or "[]") if r else []
    except ValueError:
        h = []
    u = A.current_user() if A else None
    h.append(dict(at=_now(), by=(u["username"] if u else "сайт"), what=what))
    con.execute("UPDATE online_orders SET hist=? WHERE id=?", (json.dumps(h[-50:], ensure_ascii=False), oid))


def client_chat(con, phone):
    r = con.execute("SELECT chat_id FROM online_chats WHERE phone=? AND active=1", (phone,)).fetchone()
    return r["chat_id"] if r else None


def doc_sum_uzs(con, doc_id):
    d = con.execute("SELECT usd, uzs, discount, rate FROM erp_docs WHERE id=?", (doc_id,)).fetchone()
    if not d:
        return None
    return round((d["uzs"] or 0) - (d["discount"] or 0) * (d["rate"] or 0))


def fmt_sum(v):
    return f"{round(v or 0):,}".replace(",", " ")


def status_text(o, st, total=None, label=None):
    no = label or o["id"]
    if st == "sent":
        return f"📥 Lumberture: буюртма №{no} қабул қилинди. Конструктор текшириб, суммасини шу ерга ёзади."
    if st == "checked":
        return f"✅ Буюртма №{no} текширилди, сумма {fmt_sum(total)} сўм.\nТўлангандан кейин кесишга қўямиз. Савол бўлса: {contact_phone()}"
    if st == "paid":
        return f"💳 Буюртма №{no}: тўлов қабул қилинди. Навбатга қўйдик."
    if st == "in_work":                                   # мижозга матн — олдинги «кесилмоқда» (тасдиқланган)
        return f"🪚 Буюртма №{no} кесилмоқда."
    if st == "ready":
        return f"📦 Буюртма №{no} тайёр. Олиб кетиш ёки етказиб бериш бўйича: {contact_phone()}"
    if st == "cancel":
        return f"Буюртма №{no} бекор қилинди. Савол бўлса: {contact_phone()}"
    return ""


def group_rows(con, o):
    """Бир буюртманинг материаллари (сайт: умумий group). Бекор қилинганлари — фақат ўзи бўлса."""
    g_ = o["grp"] if "grp" in o.keys() else ""
    if not g_:
        return [o]
    rows = con.execute("SELECT * FROM online_orders WHERE grp=? AND phone=? ORDER BY id", (g_, o["phone"])).fetchall()
    return rows or [o]


def group_label(rows):
    return ", №".join(str(r["id"]) for r in rows)


def group_n(d):
    """Гуруҳдаги материаллар сони: «материал 1 из 2» (сайт изоҳи) -> 2."""
    m = re.search(r"(\d+)\s*(?:из|/|of|dan)\s*(\d+)", d.get("note") or "")
    return int(m.group(2)) if m and 1 < int(m.group(2)) <= 20 else 0


GROUP_WAIT_MIN = 10


def announce(con, rows):
    """Янги буюртма(лар) ҳақида: мижозга «қабул қилинди» (битта хабар) ва цехга. Хабар кетганлари белгиланади."""
    if not rows:
        return
    o = rows[0]
    lab = group_label(rows) if len(rows) > 1 else None
    for r in rows:
        con.execute("UPDATE online_orders SET notified=1 WHERE id=?", (r["id"],))
    notify_client(con, o, "sent", label=lab)
    con.commit()
    cs = [json.loads(r["calc"] or "{}") for r in rows]
    tell_staff(con, f"🌐 Онлайн буюртма №{lab or o['id']}: {o['name'] or fmt_phone(o['phone'])}, "
                    + (f"{len(rows)} материал, " if len(rows) > 1 else "")
                    + f"{sum(x.get('parts') or 0 for x in cs)} дет., {sum(x.get('sheets') or 0 for x in cs)} лист"
                    + (" · СРОЧНО" if any(r["urgent"] for r in rows) else "") + " — платформада текширинг (Онлайн-заказы).")


def group_fallback(now_=None):
    """Гуруҳнинг қолган материаллари 10 дақиқада келмади — келганлари бўйича хабар (мижоз хабарсиз қолмасин). -> нечта гуруҳ"""
    con = A.db()
    init_tables(con)
    lim = ((now_ or datetime.now()) - timedelta(minutes=GROUP_WAIT_MIN)).strftime("%Y-%m-%d %H:%M:%S")
    n = 0
    for g_ in con.execute("SELECT grp, phone FROM online_orders WHERE notified=0 AND grp<>'' GROUP BY grp, phone HAVING MIN(created_at)<=?",
                          (lim,)).fetchall():
        rows = [r for r in con.execute("SELECT * FROM online_orders WHERE grp=? AND phone=? ORDER BY id", (g_["grp"], g_["phone"])) if not r["notified"]]
        announce(con, rows)
        n += 1
    return n


def notify_client(con, o, st, total=None, label=None):
    ch = client_chat(con, o["phone"])
    t = status_text(o, st, total, label)
    if ch and t and bot_token():
        queue_msg(con, ch, t)
        return True
    return False


def staff_people(con):
    try:
        ids = json.loads(A.get_setting("online_notify", "") or "null")
    except ValueError:
        ids = None
    if ids is None:                               # стандарт: менежерлар, кройчи-менежер, шериклар ва Базис конструкторлари (эгаси, 23.10)
        return con.execute("SELECT r.* FROM ready_workers r LEFT JOIN k_workers w ON w.id=r.kw_id WHERE r.active=1 AND r.bot_uid IS NOT NULL "
                           "AND (r.mgr=1 OR r.owner=1 OR r.kroy=1 OR lower(COALESCE(w.position,'')) LIKE '%конструктор%' "
                           "OR COALESCE(w.position,'') LIKE '%Конструктор%')").fetchall()
    if not ids:
        return []
    return con.execute(f"SELECT * FROM ready_workers WHERE active=1 AND bot_uid IS NOT NULL AND id IN ({','.join('?' * len(ids))})",
                       tuple(int(i) for i in ids)).fetchall()


def tell_staff(con, text):
    try:
        import raspil_bot as RB
        for p in staff_people(con):
            RB.send(p["bot_uid"], text)
    except Exception:
        A.errlog.exception("online staff notify")


FILES_MAX = 18_000_000                             # zip ҳажми (base64 билан ~24 МБ; шу сўров учун чегара 25 МБ)


def files_dir(oid):
    d = os.path.join(A.DATA_DIR, "online_files", str(int(oid)))
    os.makedirs(d, exist_ok=True)
    return d


def order_files(o):
    try:
        return json.loads(o["files"] or "[]") if "files" in o.keys() else []
    except ValueError:
        return []


def save_files(con, o, name, raw, listing=None):
    """Сайт юборган цех пакетини буюртмага бириктириш. -> файл ёзуви (dict)"""
    import zipfile, io
    if not raw.startswith(b"PK"):
        raise OrderError("bad_zip", "Файл zip эмас")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [i.filename for i in z.infolist()]
            if any(n.startswith("/") or ".." in n.split("/") or ":" in n for n in names):
                raise OrderError("bad_zip", "Zip ичида нотўғри йўл")
            if sum(i.file_size for i in z.infolist()) > 60_000_000:
                raise OrderError("bad_zip", "Zip жуда катта")
    except zipfile.BadZipFile:
        raise OrderError("bad_zip", "Zip очилмади")
    nm = re.sub(r"[^A-Za-z0-9._-]", "_", name or "")[:80] or f"lumberture-{o['id']}.zip"
    if not nm.lower().endswith(".zip"):
        nm += ".zip"
    path = os.path.join(files_dir(o["id"]), nm)
    with open(path, "wb") as fh:
        fh.write(raw)
    rec = dict(name=nm, size=len(raw), at=_now(), n=len(names), list=[str(x)[:120] for x in (listing or [])][:300])
    fl = [f for f in order_files(o) if f.get("name") != nm] + [rec]
    con.execute("UPDATE online_orders SET files=? WHERE id=?", (json.dumps(fl, ensure_ascii=False), o["id"]))
    con.commit()
    return rec


def send_files_staff(con, o, rec):
    """Базисчилар ва менежерларга: буюртма цех пакети (zip) цех-ботда."""
    try:
        import raspil_bot as RB
        path = os.path.join(files_dir(o["id"]), rec["name"])
        cap = f"📎 Онлайн буюртма №{o['id']}: {o['name'] or fmt_phone(o['phone'])} — цех файллари (раскрой, бирка, присадка)"
        for p in staff_people(con):
            RB.send_file(p["bot_uid"], path, rec["name"], cap)
    except Exception:
        A.errlog.exception("online files notify")


# ================================================================== API ёрдамчилари
def auth_phone():
    h = request.headers.get("Authorization") or ""
    if not h.lower().startswith("bearer "):
        return None
    tok = h[7:].strip()
    if not tok or len(tok) > 200:
        return None
    con = A.db()
    r = con.execute("SELECT * FROM online_sessions WHERE tok=? AND exp>?", (_sha(tok), time.time())).fetchone()
    if not r:
        return None
    if (r["seen_at"] or "")[:13] != _now()[:13]:
        con.execute("UPDATE online_sessions SET seen_at=? WHERE tok=?", (_now(), r["tok"]))
        con.commit()
    return r["phone"]


def _json_body(limit=512 * 1024):
    if (request.content_length or 0) > limit:
        raise OrderError("too_large", "Сўров жуда катта")
    try:
        d = json.loads(request.get_data(cache=False, as_text=True) or "null")
    except ValueError:
        raise OrderError("bad_json", "JSON нотўғри")
    return d


def order_public(con, o, full=False):
    c = json.loads(o["calc"] or "{}")
    out = dict(id=o["id"], no=o["id"], status=o["status"], status_name=ST_UZ.get(o["status"], o["status"]), created_at=o["created_at"],
               material=(c.get("material") or {}).get("name"), sheets=c.get("sheets"), parts=c.get("parts"),
               total_uzs=o["sum_sent"] if o["sum_sent"] is not None else None, checked=o["status"] not in ("sent", "cancel"),
               group=(o["grp"] if "grp" in o.keys() else "") or None)
    if full:
        out["calc"] = public_calc(c)
        out["data"] = json.loads(o["data"] or "{}")
        out["history"] = [dict(at=h.get("at"), status=h.get("what")) for h in json.loads(o["hist"] or "[]") if h.get("what") in ST_UZ]
    return out


def set_status(con, o, st, by_page=True):
    """Ҳолатни ўзгартириш + мижозга хабар. Бир нечта материалли буюртма (group) — ҳаммасига бирдан, мижозга битта хабар.
    -> (ok, хабар)"""
    if st not in ST_RU:
        return False, "Неизвестный статус"
    rows = group_rows(con, o)
    if len(rows) > 1 and st != "cancel":
        rows = [r for r in rows if r["status"] != "cancel"] or [o]
    sums = {}
    if st == "checked":
        for r in rows:
            if not r["doc_id"] or not con.execute("SELECT 1 FROM erp_docs WHERE id=? AND status<>'deleted'", (r["doc_id"],)).fetchone():
                return False, ("Сначала создайте и проверьте черновик накладной" if len(rows) == 1 else
                               f"Заказ № {r['id']}: сначала создайте и проверьте черновик накладной")
            sums[r["id"]] = doc_sum_uzs(con, r["doc_id"])
            if not sums[r["id"]]:
                return False, ("Сумма накладной 0 — проверьте строки и цены" if len(rows) == 1 else
                               f"Заказ № {r['id']}: сумма накладной 0 — проверьте строки и цены")
    u = A.current_user()
    for r in rows:
        tot = sums.get(r["id"])
        con.execute("UPDATE online_orders SET status=?, upd_by=?, upd_at=?" + (", sum_sent=?" if tot is not None else "") + " WHERE id=?",
                    ((st, u["username"] if u else "", _now()) + ((tot,) if tot is not None else ()) + (r["id"],)))
        _hist(con, r["id"], st)
    total = sum(sums.values()) if sums else None
    sent = notify_client(con, o, st, total, group_label(rows) if len(rows) > 1 else None)
    con.commit()
    return True, ("Клиенту отправлено сообщение в Telegram" if sent else "Клиент не подключён к боту — сообщение не отправлено")


# ================================================================== Flask
def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con)
        _seed_roles(con)
        con.commit()
    finally:
        con.close()
    app.before_request_funcs.setdefault(None, []).insert(0, _public_gate)
    _orig_tick = A.scheduler_tick

    def scheduler_tick_online(dt=None):
        res = _orig_tick(dt)
        try:
            group_fallback()                      # гуруҳ буюртмаси: қолгани 10 дақиқада келмаса — келганлари бўйича хабар
        except Exception:
            A.errlog.exception("online group fallback")
        return res
    A.scheduler_tick = scheduler_tick_online

    @app.after_request
    def _online_cors(resp):
        try:
            if (request.path or "").startswith("/api/online/"):
                _cors(resp)
                resp.headers["Cache-Control"] = "no-store"
        except Exception:
            pass
        return resp

    def api_guard(kind, limit, per):
        ip = client_ip_real()
        if not rate_ok(kind, ip, limit, per):
            return err("rate_limited", "Кўп сўров. Бироздан кейин қайта уриниб кўринг.", 429)
        return None

    @app.route("/api/online/catalog", methods=["GET"])
    def api_online_catalog():
        x = api_guard("cat", 60, 60)
        if x:
            return x
        con = A.db()
        d = catalog_cached(con)
        only = {x for x in re.split(r"[,\s]+", request.args.get("only") or "") if x in ("materials", "edges", "services", "holes", "extra")}
        if only:                                          # ?only=materials,services — сайт керакли қисмини олади
            d = {k: v for k, v in d.items() if k not in ("materials", "edges", "services", "holes", "extra") or k in only}
        return jsonify(d)

    @app.route("/api/online/calc", methods=["POST"])
    def api_online_calc():
        x = api_guard("calc", 60, 60)
        if x:
            return x
        con = A.db()
        try:
            d = validate(_json_body())
            return jsonify(dict(ok=True, calc=public_calc(compute(con, d))))
        except OrderError as e:
            return err(e.code, e.msg, field=e.field)

    @app.route("/api/online/login/start", methods=["POST"])
    def api_online_login_start():
        x = api_guard("login", 20, 3600)
        if x:
            return x
        try:
            d = _json_body(4096) or {}
        except OrderError as e:
            return err(e.code, e.msg)
        ph = norm_phone((d or {}).get("phone"))
        if not ph:
            return err("bad_phone", "Телефон рақами нотўғри (+998 XX XXX-XX-XX)", field="phone")
        if not bot_token() or not bot_username():
            return err("bot_off", "Кириш вақтинча ишламаяпти. Телефон қилинг: " + contact_phone(), 503)
        if not rate_ok("login_ph", ph, 5, 3600) or not rate_ok("login_ph1", ph, 1, 30):
            return err("rate_limited", "Код яқинда юборилди. Бироздан кейин қайта уриниб кўринг.", 429)
        con = A.db()
        con.execute("DELETE FROM online_logins WHERE exp<?", (time.time() - 86400,))
        nonce = secrets.token_urlsafe(18).replace("-", "x").replace("_", "y")
        con.execute("INSERT INTO online_logins(nonce, phone, exp, ip, created) VALUES(?,?,?,?,?)",
                    (nonce, ph, time.time() + CODE_MIN * 60, client_ip_real(), time.time()))
        con.commit()
        url = f"https://t.me/{bot_username()}?start=ol_{nonce}"
        ch = client_chat(con, ph)
        sent = False
        if ch:
            sent = _send_code(con, con.execute("SELECT * FROM online_logins WHERE nonce=?", (nonce,)).fetchone(), ch)
        return jsonify(dict(ok=True, nonce=nonce, sent=bool(sent), bot_url=url, expires_in=CODE_MIN * 60,
                            message=("Код Telegram'га юборилди" if sent else "Telegram'да ботни очинг ва рақамингизни юборинг — код келади")))

    @app.route("/api/online/login/verify", methods=["POST"])
    def api_online_login_verify():
        x = api_guard("verify", 30, 600)
        if x:
            return x
        try:
            d = _json_body(4096) or {}
        except OrderError as e:
            return err(e.code, e.msg)
        nonce, code = str(d.get("nonce") or "")[:64], re.sub(r"\D", "", str(d.get("code") or ""))[:6]
        con = A.db()
        lg = con.execute("SELECT * FROM online_logins WHERE nonce=? AND used=0 AND exp>?", (nonce, time.time())).fetchone()
        if not lg:
            return err("expired", "Код эскирган. Қайта «Кириш» босинг.", 401)
        if (lg["tries"] or 0) >= 5:
            return err("too_many_tries", "Кўп марта нотўғри киритилди. Қайта «Кириш» босинг.", 401)
        if not lg["code"] or len(code) != 6 or not hmac.compare_digest(lg["code"], _sha(nonce + ":" + code)):
            con.execute("UPDATE online_logins SET tries=COALESCE(tries,0)+1 WHERE nonce=?", (nonce,))
            con.commit()
            return err("bad_code", "Код нотўғри", 401)
        tok = secrets.token_urlsafe(32)
        con.execute("UPDATE online_logins SET used=1 WHERE nonce=?", (nonce,))
        con.execute("DELETE FROM online_sessions WHERE exp<?", (time.time(),))
        con.execute("INSERT INTO online_sessions(tok, phone, exp, created_at, seen_at, ip) VALUES(?,?,?,?,?,?)",
                    (_sha(tok), lg["phone"], time.time() + SESSION_DAYS * 86400, _now(), _now(), client_ip_real()))
        con.commit()
        ch = con.execute("SELECT name FROM online_chats WHERE phone=?", (lg["phone"],)).fetchone()
        return jsonify(dict(ok=True, token=tok, phone=fmt_phone(lg["phone"]), name=(ch["name"] if ch else ""), expires_days=SESSION_DAYS))

    @app.route("/api/online/me", methods=["GET"])
    def api_online_me():
        ph = auth_phone()
        if not ph:
            return err("auth", "Кириш керак", 401)
        con = A.db()
        ch = con.execute("SELECT name FROM online_chats WHERE phone=?", (ph,)).fetchone()
        cl = find_client(con, ph)
        cn = con.execute("SELECT name FROM erp_clients WHERE ext=?", (cl,)).fetchone() if cl else None
        return jsonify(dict(ok=True, phone=fmt_phone(ph), name=(cn["name"] if cn else (ch["name"] if ch else "")), known=bool(cl)))

    @app.route("/api/online/logout", methods=["POST"])
    def api_online_logout():
        h = request.headers.get("Authorization") or ""
        if h.lower().startswith("bearer "):
            con = A.db()
            con.execute("DELETE FROM online_sessions WHERE tok=?", (_sha(h[7:].strip()),))
            con.commit()
        return jsonify(dict(ok=True))

    @app.route("/api/online/order", methods=["POST"])
    def api_online_order():
        ph = auth_phone()
        if not ph:
            return err("auth", "Кириш керак", 401)
        if not rate_ok("order", ph, 10, 3600):
            return err("rate_limited", "Бир соатда 10 тадан кўп буюртма бўлмайди", 429)
        con = A.db()
        try:
            d = validate(_json_body())
        except OrderError as e:
            return err(e.code, e.msg, field=e.field)
        c = compute(con, d)
        cl = find_client(con, ph)
        if not cl:
            ch = con.execute("SELECT name FROM online_chats WHERE phone=?", (ph,)).fetchone()
            try:
                cl = make_client(con, ph, d["client"]["name"] or (ch["name"] if ch else ""))
            except ValueError as e:
                A.errlog.warning("online client: %s", e)
                cl = None
        cname = ""
        if cl:
            r = con.execute("SELECT name FROM erp_clients WHERE ext=?", (cl,)).fetchone()
            cname = r["name"] if r else ""
        init_tables(con)
        oid = con.execute("INSERT INTO online_orders(phone, client, name, status, data, calc, urgent, total_uzs, created_at, ip, grp, grp_n) "
                          "VALUES(?,?,?,'sent',?,?,?,?,?,?,?,?)",
                          (ph, cl, cname, json.dumps(d, ensure_ascii=False), json.dumps(c, ensure_ascii=False), 1 if d["urgent"] else 0,
                           c["total_uzs"], _now(), client_ip_real(), d.get("group") or "", group_n(d) if d.get("group") else 0)).lastrowid
        _hist(con, oid, "sent")
        con.commit()
        o = con.execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone()
        if cl:
            try:
                did = make_draft(con, o)
                con.execute("UPDATE online_orders SET doc_id=?, err='' WHERE id=?", (did, oid))
                con.commit()
            except Exception as e:  # noqa — буюртма барибир сақланади, черновикни конструктор қайта яратади
                con.rollback()
                if not isinstance(e, ValueError):
                    A.errlog.exception("online draft %s", oid)
                con.execute("UPDATE online_orders SET err=? WHERE id=?", (str(e)[:300], oid))
                con.commit()
        else:
            con.execute("UPDATE online_orders SET err=? WHERE id=?", ("Клиент не создан — выберите вручную", oid))
            con.commit()
        o = con.execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone()
        rows = group_rows(con, o)
        if not o["grp"] or len(rows) >= (o["grp_n"] or 0) or any(r["notified"] for r in rows):
            # бир нечта материал (group): охиргиси келганда — мижозга битта хабар, цехга битта хабар
            # (кечикиб келгани — фақат ўзи ҳақида; ҳеч бири келмаса — group_fallback, 10 дақиқадан кейин)
            announce(con, [r for r in rows if not r["notified"]])
        return jsonify(dict(ok=True, order=order_public(con, o, full=True)))

    @app.route("/api/online/order/files", methods=["POST"])
    def api_online_order_files():
        """Сайт: буюртмадан кейин цех пакети {order_id, number, group, name, zip_b64, files:[...]} -> буюртмага бириктирилади,
        Базисчилар ва менежерларга цех-ботда юборилади."""
        try:
            request.max_content_length = 25 * 1024 * 1024    # фақат шу сўровга (Flask 3.1): катта буюртма zip'и ҳам сиғсин
        except Exception:
            pass
        ph = auth_phone()
        if not ph:
            A.errlog.warning("online files: кириш йўқ (токен) — %s байт", request.content_length)
            return err("auth", "Кириш керак", 401)
        if not rate_ok("files", ph, 40, 3600):
            return err("rate_limited", "Жуда кўп сўров", 429)
        con = A.db()
        init_tables(con)
        try:
            d = _json_body(limit=25 * 1024 * 1024)
            if not isinstance(d, dict):
                raise OrderError("bad_json", "JSON объект керак")
            oid = d.get("order_id") or d.get("number")
            o = con.execute("SELECT * FROM online_orders WHERE id=? AND phone=?", (int(oid), ph)).fetchone() if str(oid or "").isdigit() else None
            if not o:
                A.errlog.warning("online files: буюртма топилмади order_id=%r number=%r", d.get("order_id"), d.get("number"))
                return err("not_found", "Буюртма топилмади", 404)
            try:
                raw = base64.b64decode(re.sub(r"^data:[^,]*,", "", str(d.get("zip_b64") or "")), validate=False)
            except (ValueError, TypeError):
                raise OrderError("bad_zip", "zip_b64 нотўғри")
            if not raw or len(raw) > FILES_MAX:
                raise OrderError("too_large" if raw else "bad_zip", "Файл йўқ ёки жуда катта")
            rec = save_files(con, o, d.get("name") or "", raw, d.get("files") if isinstance(d.get("files"), list) else None)
        except OrderError as e:
            A.errlog.warning("online files: %s — %s (%s байт)", e.code, e.msg, request.content_length)
            return err(e.code, e.msg, field=e.field)
        A.errlog.warning("online files: №%s — %s, %s КБ, %s файл", o["id"], rec["name"], rec["size"] // 1024, rec["n"])
        send_files_staff(con, o, rec)
        return jsonify(dict(ok=True, order_id=o["id"], file=dict(name=rec["name"], size=rec["size"])))

    @app.route("/api/online/orders", methods=["GET"])
    def api_online_orders():
        ph = auth_phone()
        if not ph:
            return err("auth", "Кириш керак", 401)
        con = A.db()
        rows = con.execute("SELECT * FROM online_orders WHERE phone=? ORDER BY id DESC LIMIT 50", (ph,)).fetchall()
        return jsonify(dict(ok=True, orders=[order_public(con, o) for o in rows]))

    @app.route("/api/online/order/<int:oid>", methods=["GET"])
    def api_online_order_get(oid):
        ph = auth_phone()
        if not ph:
            return err("auth", "Кириш керак", 401)
        con = A.db()
        o = con.execute("SELECT * FROM online_orders WHERE id=? AND phone=?", (oid, ph)).fetchone()
        if not o:
            return err("not_found", "Буюртма топилмади", 404)
        return jsonify(dict(ok=True, order=order_public(con, o, full=True)))

    @app.route("/api/online/<path:rest>", methods=["GET", "POST"])
    def api_online_404(rest):
        return err("not_found", "Not found", 404)

    # ---------------------------------------------------------------- офис: навбат
    @app.route("/erp/online", methods=["GET", "POST"])
    @A.login_required
    def erp_online():
        con = A.db()
        init_tables(con)
        if request.method == "POST":
            f = request.form
            act = f.get("act") or ""
            if act == "settings":
                if not A.is_admin():
                    abort(403)
                A.set_settings(online_host=(f.get("online_host") or "").strip()[:200],
                               online_origins=(f.get("online_origins") or "").strip()[:1000],
                               online_notify=json.dumps([int(x) for x in f.getlist("who") if x.isdigit()]))
                flash("Сохранено", "ok")
                return redirect(url_for("erp_online") + "#set")
            return redirect(url_for("erp_online"))
        st = request.args.get("st") or "active"
        where = {"active": "status NOT IN ('ready','cancel')", "all": "1=1"}.get(st) or "status=?"
        args = () if st in ("active", "all") else (st,)
        rows = [dict(r) for r in con.execute(f"SELECT * FROM online_orders WHERE {where} ORDER BY id DESC LIMIT 300", args)]
        for r in rows:
            c = json.loads(r["calc"] or "{}")
            r["mat"] = (c.get("material") or {}).get("name") or ""
            r["sheets"], r["parts"] = c.get("sheets"), c.get("parts")
            r["warn"] = len(c.get("warnings") or []) + len(c.get("missing") or [])
            r["doc"] = con.execute("SELECT id, no, status, uzs FROM erp_docs WHERE id=?", (r["doc_id"],)).fetchone() if r["doc_id"] else None
            r["chat"] = bool(client_chat(con, r["phone"]))
            r["zips"] = order_files(r)
            r["grp_ids"] = [x[0] for x in con.execute("SELECT id FROM online_orders WHERE grp=? AND phone=? AND id<>? ORDER BY id",
                                                      (r["grp"], r["phone"], r["id"]))] if r.get("grp") else []
        cnt = {k: n for k, n in con.execute("SELECT status, COUNT(*) FROM online_orders GROUP BY status")}
        people = con.execute("SELECT id, name FROM ready_workers WHERE active=1 AND bot_uid IS NOT NULL ORDER BY name").fetchall() if A.is_admin() else []
        npl = {p["id"] for p in staff_people(con)} if A.is_admin() else set()
        return render_template("erp_online.html", rows=rows, st=st, cnt=cnt, ST_RU=ST_RU, STATUSES=STATUSES, fmt_phone=fmt_phone,
                               bot=bot_username(), bot_on=bool(bot_token()), host=_setting("online_host", DEF_HOST),
                               origins=_setting("online_origins", DEF_ORIGINS), people=people, npl=npl, fmt_sum=fmt_sum, is_admin=A.is_admin())

    @app.route("/erp/online/<int:oid>", methods=["GET", "POST"])
    @A.login_required
    def erp_online_view(oid):
        con = A.db()
        init_tables(con)
        o = con.execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone()
        if not o:
            abort(404)
        if request.method == "POST":
            f = request.form
            act = f.get("act") or ""
            if act == "status":
                ok, msg = set_status(con, o, f.get("st") or "")
                flash(msg, "ok" if ok else "error")
            elif act == "zip":                            # менежер zip'ни қўлда бириктиради (сайт юбора олмаган бўлса)
                fz = request.files.get("zip")
                try:
                    raw = fz.read() if fz else b""
                    if not raw:
                        raise OrderError("bad_zip", "Файл танланмаган")
                    rec = save_files(con, o, fz.filename or "", raw)
                    flash(f"Файл прикреплён: {rec['name']}", "ok")
                    if f.get("send"):
                        send_files_staff(con, con.execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone(), rec)
                except OrderError as e:
                    flash(e.msg, "error")
            elif act == "draft":
                if o["doc_id"] and con.execute("SELECT 1 FROM erp_docs WHERE id=? AND status<>'deleted'", (o["doc_id"],)).fetchone():
                    flash("Черновик уже есть", "warn")
                else:
                    cl = (f.get("client") or "").strip() or o["client"]
                    if not cl or not con.execute("SELECT 1 FROM erp_clients WHERE ext=?", (cl,)).fetchone():
                        flash("Выберите клиента", "error")
                        return redirect(url_for("erp_online_view", oid=oid))
                    con.execute("UPDATE online_orders SET client=? WHERE id=?", (cl, oid))
                    d = validate(json.loads(o["data"]))
                    con.execute("UPDATE online_orders SET calc=? WHERE id=?", (json.dumps(compute(con, d), ensure_ascii=False), oid))
                    con.commit()
                    o = con.execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone()
                    try:
                        did = make_draft(con, o)
                        con.execute("UPDATE online_orders SET doc_id=?, err='' WHERE id=?", (did, oid))
                        _hist(con, oid, "draft")
                        con.commit()
                        return redirect(url_for("erp_sale_edit", doc_id=did))
                    except ValueError as e:
                        con.rollback()
                        con.execute("UPDATE online_orders SET err=? WHERE id=?", (str(e)[:300], oid))
                        con.commit()
                        flash(str(e), "error")
            return redirect(url_for("erp_online_view", oid=oid))
        c = json.loads(o["calc"] or "{}")
        d = json.loads(o["data"] or "{}")
        doc = con.execute("SELECT id, no, status, uzs, usd, rate, discount FROM erp_docs WHERE id=?", (o["doc_id"],)).fetchone() if o["doc_id"] else None
        cname = ""
        if o["client"]:
            x = con.execute("SELECT name FROM erp_clients WHERE ext=?", (o["client"],)).fetchone()
            cname = x["name"] if x else ""
        hist = json.loads(o["hist"] or "[]")
        return render_template("erp_online_view.html", o=o, c=c, d=d, doc=doc, cname=cname, hist=hist, ST_RU=ST_RU, FLOW=FLOW, grp=group_rows(con, o),
                               STATUSES=STATUSES, fmt_phone=fmt_phone, fmt_sum=fmt_sum, chat=bool(client_chat(con, o["phone"])), zips=order_files(o),
                               doc_sum=doc_sum_uzs(con, doc["id"]) if doc else None)

    @app.route("/erp/online/<int:oid>/files/<name>")
    @A.login_required
    def erp_online_file(oid, name):
        o = A.db().execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone()
        if not o or name not in {f.get("name") for f in order_files(o)}:
            abort(404)
        from flask import send_file
        return send_file(os.path.join(files_dir(oid), name), as_attachment=True, download_name=name, mimetype="application/zip")

    @app.route("/erp/online/<int:oid>.json")
    @A.login_required
    def erp_online_json(oid):
        """Базис скрипти учун: буюртма JSON (формат v1)."""
        o = A.db().execute("SELECT id, data FROM online_orders WHERE id=?", (oid,)).fetchone()
        if not o:
            abort(404)
        con = A.db()
        init_tables(con)
        rows = group_rows(con, con.execute("SELECT * FROM online_orders WHERE id=?", (oid,)).fetchone())

        def one(r):
            d = json.loads(r["data"])
            d.pop("site_calc", None)
            d["number"] = r["id"]                        # сайт: «Номер заказа (на бирке и в QR)»
            d["online_id"] = r["id"]
            if r["doc_id"]:
                x = con.execute("SELECT no FROM erp_docs WHERE id=? AND status<>'deleted'", (r["doc_id"],)).fetchone()
                if x:
                    d["doc_no"] = str(x["no"]).lstrip("0") or x["no"]
            return d
        # бир нечта материал (group) — ҳаммаси битта файлда: сайт «Открыть заказ с платформы» уларни бирдан очади
        body = {"v": VER, "orders": [one(r) for r in rows]} if len(rows) > 1 else one(rows[0])
        return Response(json.dumps(body, ensure_ascii=False, indent=1), mimetype="application/json",
                        headers={"Content-Disposition": f"attachment; filename=online-{o['id']}.json"})


def _seed_roles(con):
    """Бир марта: «Менеджер (крой)» ва «Конструктор (Базис)» ролларига «Онлайн-заказы» бўлими."""
    if con.execute("SELECT 1 FROM settings WHERE key='online_roles'").fetchone():
        return False
    try:
        if not con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='roles'").fetchone():
            return False
        for key in ("manager", "basis"):
            r = con.execute("SELECT sections FROM roles WHERE key=?", (key,)).fetchone()
            if r:
                s = json.loads(r[0] or "[]")
                if "online" not in s:
                    s.append("online")
                    con.execute("UPDATE roles SET sections=? WHERE key=?", (json.dumps(s), key))
        con.execute("INSERT INTO settings(key, value) VALUES('online_roles', '1')")
        return True
    except Exception:
        A.errlog.exception("online roles seed")
        return False
