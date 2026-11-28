# -*- coding: utf-8 -*-
"""Кастамону (KS Group) каталоги — @ksgroup_agent_bot. Eman'дан АЛОҲИДА модул (эгаси, 28.11: «Eman билан Кастамону
аралашиб кетмасин»): ўз файллари (data/ks_*), кейин ўз жадваллари (ks_*) ва каталогда ўз папкаси. eman.py га тегилмайди.

1-босқич — бот тузилишини ўрганиш (эгаси рухсати билан, 28.11). data/ks_probe.req (матн — функция номи, бўш бўлса
«explore») қўйилса, платформанинг Telegram-аккаунти ботга /start юборади, тугмаларни (фақат каталог/папка) бир неча марта
босади ва кўрганини data/ks_probe.json га ёзади. Буюртма, сотиб олиш, телефон/контакт юбориш тугмалари БОСИЛМАЙДИ.
Ҳар сафар шу файл қайта ўқилади — сервер қайта ёқилмасдан янги текширув функцияси қўшиш мумкин.
"""
import asyncio, csv, io, json, os, re, sys, threading, time
from datetime import datetime

from flask import request, render_template, redirect, url_for, flash, abort, Response

A = None
BOT = "ksgroup_agent_bot"
PAUSE = 2.5                      # ҳар босишдан кейин (FloodWait'дан сақланиш)
MAX_ACTIONS = 14
PROBES = ("explore", "products")      # data/ks_probe.req да ишлатиш мумкин бўлган функциялар
# босилмайдиган тугмалар: буюртма, сават, тўлов, контакт, админ билан алоқа, тил ўзгартириш ва ҳ.к.
DENY_RE = re.compile(r"(заказ|оформ|корзин|сават|buyurtma|savat|order|cart|buy|купить|оплат|to'lov|tolov|тўлов|pay|"
                     r"контакт|kontakt|contact|телефон|telefon|phone|номер|raqam|рақам|менеджер|manager|оператор|operator|"
                     r"связ|aloqa|алоқа|жалоб|отзыв|feedback|удал|delete|отмен|cancel|выход|logout|язык|til|тил|lang|"
                     r"регистр|ro'yxat|рўйхат|register|sign)", re.I)


def _btns(markup):
    """Тугмалар: [{text, kind, data (bytes|None), url, row, col}] — Telethon турларидан мустақил (хом to_dict)."""
    if not markup:
        return []
    try:
        d = markup.to_dict()
    except Exception:
        return []
    out = []
    for ri, row in enumerate(d.get("rows") or []):
        for ci, b in enumerate(row.get("buttons") or []):
            t = b.get("type") if isinstance(b.get("type"), dict) else b
            kind = str(t.get("_") or b.get("_") or "")
            data = t.get("data")
            if isinstance(data, str):
                try:
                    data = bytes.fromhex(data)
                except ValueError:
                    data = data.encode()
            out.append(dict(text=(b.get("text") or "").strip(), kind=kind, data=data if isinstance(data, (bytes, bytearray)) else None,
                            url=t.get("url") or "", row=ri, col=ci, markup=str(d.get("_") or ""), query=t.get("query")))
    return out


def _desc(m):
    bs = _btns(getattr(m, "reply_markup", None))
    return dict(id=m.id, out=bool(getattr(m, "out", False)), text=(getattr(m, "message", "") or "")[:3000],
                media=type(m.media).__name__ if getattr(m, "media", None) else None,
                buttons=[dict(text=b["text"], kind=b["kind"], data=(b["data"] or b"").decode("utf-8", "replace")[:80] if b["data"] else None,
                              url=b["url"][:200], query=b["query"]) for b in bs])


def allowed(b):
    """Фақат каталогни очадиган оддий тугмалар: callback ёки оддий матнли (reply keyboard). Контакт/жойлашув/ҳавола/тўлов — йўқ."""
    k = b["kind"]
    if any(x in k for x in ("RequestPhone", "RequestGeo", "RequestPeer", "Url", "Buy", "Game", "WebView", "SimpleWebView", "Auth", "Copy")):
        return False
    if DENY_RE.search(b["text"] or ""):
        return False
    return ("Callback" in k and b["data"] is not None) or k in ("KeyboardButton", "") and bool(b["text"])


async def explore(c, out, bot=BOT, max_actions=MAX_ACTIONS):
    """/start → жавоб хабарлари → рухсат этилган тугмаларни кенглик бўйича (BFS) босиш, ҳар қадамда нима келганини ёзиш."""
    from telethon import functions, errors
    ent = await c.get_entity(bot)
    out.update(bot=bot, steps=[], started=time.strftime("%Y-%m-%d %H:%M:%S"))
    seen_sig, queue = set(), []

    async def fresh(after):
        await asyncio.sleep(PAUSE)
        return sorted([m for m in (await c.get_messages(ent, min_id=after, limit=20) or []) if not getattr(m, "out", False)],
                      key=lambda m: m.id)

    def enqueue(m, depth):
        sig = ((getattr(m, "message", "") or "")[:200], tuple(b["text"] for b in _btns(getattr(m, "reply_markup", None))))
        if sig in seen_sig:
            return
        seen_sig.add(sig)
        for b in _btns(getattr(m, "reply_markup", None)):
            if allowed(b):
                queue.append((m.id, b, depth + 1))

    async def last_id():
        ms = await c.get_messages(ent, limit=1) or []
        return max([m.id for m in ms] or [0])

    sent = await c.send_message(ent, "/start")
    got = await fresh(sent.id)
    out["steps"].append(dict(action="/start", got=[_desc(m) for m in got]))
    for m in got:
        enqueue(m, 0)
    n = 0
    while queue and n < max_actions:
        mid, b, depth = queue.pop(0)
        if depth > 3:
            continue
        n += 1
        after = await last_id()
        step = dict(action="click" if b["data"] is not None else "text", button=b["text"], depth=depth)
        try:
            if b["data"] is not None:
                try:
                    ans = await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=mid, data=bytes(b["data"])))
                    step["answer"] = (getattr(ans, "message", None) or "")[:300] if ans else ""
                except errors.FloodWaitError as e:
                    step["flood"] = int(getattr(e, "seconds", 0) or 0)
                    out["steps"].append(step)
                    break
                got = await fresh(after)
                edited = await c.get_messages(ent, ids=mid)
                if edited is not None:
                    step["edited"] = _desc(edited)
                    enqueue(edited, depth)
            else:
                await c.send_message(ent, b["text"])
                got = await fresh(after)
        except Exception as e:                       # битта тугма хатоси — текширув давом этади
            step["error"] = f"{e.__class__.__name__}: {e}"[:300]
            got = []
        step["got"] = [_desc(m) for m in got]
        out["steps"].append(step)
        for m in got:
            enqueue(m, depth)
    out["actions"] = n
    out["left"] = [dict(text=b["text"], depth=d) for _, b, d in queue[:40]]
    return out


async def products(c, out, bot=BOT):
    """2-қадам: товар ва нарх қандай берилади. Каталог → «📁 MDF» → биринчи «▬» гуруҳ (callback) ва биринчи inline-папка
    (масалан Acrylic) — inline натижалар (товар номлари) ва биринчи товар картаси («/N» юборилади)."""
    from telethon import functions
    ent = await c.get_entity(bot)
    out.update(bot=bot, steps=[], started=time.strftime("%Y-%m-%d %H:%M:%S"))

    async def newer(after, want=None):
        for _ in range(8):
            await asyncio.sleep(PAUSE)
            ms = sorted([m for m in (await c.get_messages(ent, min_id=after, limit=10) or []) if not getattr(m, "out", False)], key=lambda m: m.id)
            if ms and (want is None or any(want(m) for m in ms)):
                return ms
        return []

    async def press(m, data):
        await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=m.id, data=bytes(data)))
        await asyncio.sleep(PAUSE)
        return await c.get_messages(ent, ids=m.id)

    sent = await c.send_message(ent, "/start")
    ms = await newer(sent.id, lambda m: any(b["data"] == b"cat" for b in _btns(m.reply_markup)))
    home = next((m for m in ms if any(b["data"] == b"cat" for b in _btns(m.reply_markup))), None)
    if not home:
        out["error"] = "/start жавобида «Каталог» йўқ"; return out
    after = max(m.id for m in ms)
    await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=home.id, data=b"cat"))
    ms = await newer(after, lambda m: "КАТАЛОГ" in (m.message or "").upper())
    cat = next((m for m in ms if "КАТАЛОГ" in (m.message or "").upper()), None)
    if not cat:
        out["error"] = "каталог очилмади"; return out
    out["steps"].append(dict(action="catalog", got=_desc(cat)))
    inl = [b for b in _btns(cat.reply_markup) if "SwitchInline" in b["kind"]]
    fold = next((b for b in _btns(cat.reply_markup) if b["data"] and b["data"].startswith(b"b:")), None)
    if fold:                                              # «📁 MDF» → «▬ …» гуруҳ
        m = await press(cat, fold["data"])
        out["steps"].append(dict(action="click", button=fold["text"], edited=_desc(m)))
        leaf = next((b for b in _btns(m.reply_markup) if b["data"] and b["data"].startswith(b"t:")), None)
        if leaf:
            last = max([x.id for x in (await c.get_messages(ent, limit=1) or [])] or [0])
            m2 = await press(m, leaf["data"])
            got = await newer(last)
            out["steps"].append(dict(action="click", button=leaf["text"], edited=_desc(m2), got=[_desc(x) for x in got]))
            inl = [b for b in _btns(m2.reply_markup) if "SwitchInline" in b["kind"]] + [b for x in got for b in _btns(x.reply_markup) if "SwitchInline" in b["kind"]] + inl
    for b in inl[:2]:                                     # inline натижалар (товарлар) — 2 та папка
        q = b["query"] or ""
        r = (await c(functions.messages.GetInlineBotResultsRequest(bot=ent, peer=ent, query=q, offset=""))).to_dict()
        items = r.get("results") or []
        step = dict(action="inline", button=b["text"], query=q, count=len(items), next_offset=r.get("next_offset"),
                    sample=[dict(title=i.get("title"), description=i.get("description"),
                                 message=((i.get("send_message") or {}).get("message") or "")[:300]) for i in items[:6]])
        out["steps"].append(step)
        code = next((s_["message"] for s_ in step["sample"] if (s_["message"] or "").startswith("/")), None)
        if code and not out.get("card"):                  # биринчи товар картаси
            last = max([x.id for x in (await c.get_messages(ent, limit=1) or [])] or [0])
            await c.send_message(ent, code.split()[0])
            got = await newer(last)
            out["card"] = dict(code=code.split()[0], got=[_desc(x) for x in got])
        await asyncio.sleep(PAUSE)
    return out


def probe_due():
    req = os.path.join(A.DATA_DIR, "ks_probe.req")
    if not os.path.exists(req):
        return None
    if not A.tgu_ready():
        return None                                   # аккаунт уланмаган — сўров кейинга қолади
    try:
        mode = open(req, encoding="utf-8").read().strip() or "explore"
    except OSError:
        mode = "explore"
    os.remove(req)
    src = os.path.abspath(__file__)
    ns = {"__name__": "ksgroup_live", "__file__": src}
    res, t0 = {}, time.time()
    try:
        exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns)
        ns["A"] = A
        fx = ns.get(mode)
        if fx and mode in ns.get("PROBES", ()):          # рухсат этилган текширувлар рўйхати — янги файлдан
            api_id, api_hash = A.tgu_cfg()
            A.tgu_sender().call(api_id, api_hash, lambda cl: fx(cl, res), timeout=600)
        else:
            res = dict(error=f"функция йўқ: {mode}")
    except Exception as e:
        import traceback
        res = dict(res, error=repr(e), tb=traceback.format_exc()[-3000:])
    res["took"] = round(time.time() - t0, 1)
    with open(os.path.join(A.DATA_DIR, "ks_probe.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return res


# ====================================================================== 2-босқич: юклагич (28.11, эгаси: «Бошла»)
# Бот тузилиши (ks_probe натижаси): /start → «📒 Каталог» (callback «cat», ЯНГИ хабар) → 📁 папкалар (callback «b:N»),
# гуруҳлар («t:N:M») ёки бирданига inline-папка (Acrylic…); гуруҳ ичида қалинлик тугмалари (inline сўров) ва «📋 Все толщины».
# Inline натижа: товар номи, «Артикул: …\nКод: /N»; «/N» юборилса — карта: «💵 Цена: 373 900 сум/шт», «Толщина», «В наличии».
# Бот фақат кўриниб турган тугмаларни қабул қилади — ҳар папкага илдиздан йўл бўйича борилади, кейин «↩️ Назад».
PAUSE_CARD = 2.5          # ҳар «/N» дан кейин
AUTO_HOUR = 7             # ҳар куни эрталаб (Eman кечқурун 21:00 да; Eman ишлаётган бўлса — у тугагач)
PRICE_RE = re.compile(r"Цена:\s*([\d\s\u00a0.,]+?)\s*([^\d\s/]+)\s*/\s*([^\s\n]+)")
_thr = None
_last = {}


def init_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS ks_kv(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS ks_nodes(id INTEGER PRIMARY KEY, key TEXT UNIQUE, parent INTEGER DEFAULT 0, name TEXT, kind TEXT,
        query TEXT DEFAULT '', path TEXT DEFAULT '', sort INTEGER DEFAULT 0, depth INTEGER DEFAULT 0, run INTEGER DEFAULT 0,
        done INTEGER DEFAULT 0, n INTEGER DEFAULT 0, at TEXT);
    CREATE TABLE IF NOT EXISTS ks_goods(id INTEGER PRIMARY KEY, node INTEGER, title TEXT, article TEXT UNIQUE, code TEXT DEFAULT '',
        thick TEXT DEFAULT '', price REAL, cur TEXT DEFAULT '', unit TEXT DEFAULT '', stock TEXT DEFAULT '', grade2 INTEGER DEFAULT 0,
        sort INTEGER DEFAULT 0, run INTEGER DEFAULT 0, ok INTEGER DEFAULT 0, at TEXT, price_at TEXT);
    CREATE INDEX IF NOT EXISTS ks_goods_node ON ks_goods(node);
    """)


def kv(con, key, default=""):
    r = con.execute("SELECT value FROM ks_kv WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def kv_set(con, **kw):
    for k, v in kw.items():
        con.execute("INSERT INTO ks_kv(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    con.commit()


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clean(t):
    t = re.sub(r"^[^\w(\"«]+", "", t or "").strip()
    return re.sub(r"\s*\(\d+\)$", "", t).strip()                 # «6 мм (2)» -> «6 мм»


def parse_card(text):
    """Товар картаси -> dict(price, cur, unit, thick, stock, article, title)."""
    r = dict(price=None, cur="", unit="", thick="", stock="", article="", title="")
    t = text or ""
    m = PRICE_RE.search(t)
    if m:
        try:
            r["price"] = float(re.sub(r"[\s\u00a0]", "", m.group(1)).replace(",", "."))
        except ValueError:
            pass
        r["cur"], r["unit"] = m.group(2), m.group(3)
    for key, rx in (("thick", r"Толщина:\s*([^\n]+)"), ("article", r"Артикул:\s*([^\n]+)")):
        mm = re.search(rx, t)
        if mm:
            r[key] = mm.group(1).strip()
    st = next((ln for ln in t.split("\n") if "наличи" in ln.lower()), "")
    r["stock"] = clean(st)[:120]
    r["title"] = (t.split("\n", 1)[0] or "").strip()
    return r


def parse_item(it):
    """Inline натижа -> (title, article, code)."""
    title = (it.get("title") or "").strip()
    desc = it.get("description") or ""
    msg = ((it.get("send_message") or {}).get("message") or "").strip()
    a = re.search(r"Артикул:\s*([^\n]+)", desc)
    c = re.search(r"(/\d+)", msg) or re.search(r"Код:\s*(/\d+)", desc)
    return clean(title) if title.startswith("❌") else title, (a.group(1).strip() if a else ""), (c.group(1) if c else "")


def start(con, prices=True):
    run = int(kv(con, "run", "0") or 0) + 1
    con.execute("UPDATE ks_nodes SET done=0")
    kv_set(con, run=run, state="tree", msg="папкалар ўқилмоқда", err="", started=now(), finished="", prices=1 if prices else 0,
           stop=0, resume_at=0)
    return run


def status(con):
    run = int(kv(con, "run", "0") or 0)
    one = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    s = dict(run=run, state=kv(con, "state"), msg=kv(con, "msg"), err=kv(con, "err"), started=kv(con, "started"),
             finished=kv(con, "finished"), prices=kv(con, "prices", "1") == "1", resume_at=float(kv(con, "resume_at", "0") or 0))
    s["leaves"] = one("SELECT COUNT(*) FROM ks_nodes WHERE kind='leaf' AND run=?", run)
    s["leaves_left"] = one("SELECT COUNT(*) FROM ks_nodes WHERE kind='leaf' AND run=? AND done=0", run)
    s["goods"] = one("SELECT COUNT(*) FROM ks_goods WHERE run=?", run)
    s["priced"] = one("SELECT COUNT(*) FROM ks_goods WHERE run=? AND ok=1", run)
    s["all"] = one("SELECT COUNT(*) FROM ks_goods")
    s["running"] = bool(_thr and _thr.is_alive())
    s["resume_txt"] = datetime.fromtimestamp(s["resume_at"]).strftime("%H:%M") if s["resume_at"] > time.time() else ""
    return s


class Stop(Exception):
    pass


async def _crawl(c):
    from telethon import functions, errors
    con = A.connect()
    con.execute("PRAGMA busy_timeout=3000")
    init_tables(con)
    ent = await c.get_entity(BOT)
    run = int(kv(con, "run", "0") or 0)
    st = {"cat": None}

    def check_stop():
        if kv(con, "stop", "0") == "1":
            kv_set(con, state="stopped", msg="тўхтатилди", stop=0)
            raise Stop()

    async def flood(e):
        sec = int(getattr(e, "seconds", 60) or 60)
        if sec <= 120:
            kv_set(con, msg=f"Telegram кутишни сўради: {sec} с")
            await asyncio.sleep(sec + 5)
            return
        kv_set(con, resume_at=time.time() + sec + 60, msg=f"Telegram чеклови: {sec // 60} дақ. кейин давом этади")
        raise Stop()

    async def last_id():
        ms = await c.get_messages(ent, limit=1) or []
        return max([m.id for m in ms] or [0])

    async def wait_new(after, want, tries=12):
        for _ in range(tries):
            await asyncio.sleep(1.0)
            for m in sorted(await c.get_messages(ent, min_id=after, limit=10) or [], key=lambda x: -x.id):
                if not getattr(m, "out", False) and want(m):
                    return m
        return None

    async def press(m, data):
        for attempt in range(3):
            try:
                await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=m.id, data=bytes(data)))
                break
            except errors.FloodWaitError as e:
                await flood(e)
        await asyncio.sleep(PAUSE)
        return await c.get_messages(ent, ids=m.id)

    async def root():
        """Каталог илдизи (📒 КАТАЛОГ): жорий хабар «Назад» билан қайтарилади, бўлмаса /start → «Каталог»."""
        m = st["cat"]
        for _ in range(6):
            if m is None:
                break
            if "КАТАЛОГ" in (m.message or "").upper() and any((b["data"] or b"").startswith(b"b:") or "SwitchInline" in b["kind"] for b in _btns(m.reply_markup)):
                st["cat"] = m
                return m
            back = next((b for b in _btns(m.reply_markup) if "назад" in b["text"].lower() and b["data"]), None)
            if not back:
                break
            m = await press(m, back["data"])
        for attempt in range(3):
            try:
                sent = await c.send_message(ent, "/start")
                break
            except errors.FloodWaitError as e:
                await flood(e)
        else:
            raise RuntimeError("/start юборилмади")
        home = await wait_new(sent.id, lambda x: any(b["data"] == b"cat" for b in _btns(x.reply_markup)))
        if not home:
            raise RuntimeError("бот /start га жавоб бермади")
        after = await last_id()
        await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=home.id, data=b"cat"))
        m = await wait_new(after, lambda x: "КАТАЛОГ" in (x.message or "").upper())
        if not m:
            raise RuntimeError("каталог очилмади")
        st["cat"] = m
        return m

    async def goto(datas):
        m = await root()
        for d in datas:
            if not any(b["data"] == d for b in _btns(m.reply_markup)):
                raise RuntimeError("тугма топилмади: " + d.decode("utf-8", "replace"))
            m = await press(m, d)
        st["cat"] = m
        return m

    def node(key, name, parent, kind, sort, depth, query="", path=""):
        con.execute("INSERT INTO ks_nodes(key,parent,name,kind,query,path,sort,depth,run,done,at) VALUES(?,?,?,?,?,?,?,?,?,0,?) "
                    "ON CONFLICT(key) DO UPDATE SET parent=excluded.parent, name=excluded.name, kind=excluded.kind, query=excluded.query, "
                    "path=excluded.path, sort=excluded.sort, depth=excluded.depth, run=excluded.run, at=excluded.at",
                    (key, parent, name, kind, query, path, sort, depth, run, now()))
        return con.execute("SELECT id FROM ks_nodes WHERE key=?", (key,)).fetchone()[0]

    seq = [0]

    async def visit(m, datas, pid, depth, path):
        bs = _btns(m.reply_markup)
        inl = [b for b in bs if "SwitchInline" in b["kind"] and "поиск" not in b["text"].lower()]
        allq = [b for b in inl if b["text"].startswith("📋")]
        for b in (allq or inl):
            seq[0] += 1
            name = path[-1] if (allq and path) else clean(b["text"])
            node("q:" + (b["query"] or ""), name, pid, "leaf", seq[0], depth, b["query"] or "", " → ".join(path + ([] if allq and path else [name])))
        kids = [b for b in bs if b["data"] and b["data"] not in (b"home", b"cat") and "назад" not in b["text"].lower()
                and "домой" not in b["text"].lower()]
        con.commit()
        for b in kids:
            check_stop()
            if depth >= 5:
                break
            seq[0] += 1
            name = clean(b["text"])
            fid = node("f:" + b["data"].decode("utf-8", "replace"), name, pid, "folder", seq[0], depth, "", " → ".join(path + [name]))
            mm = await goto(datas + [b["data"]])
            await visit(mm, datas + [b["data"]], fid, depth + 1, path + [name])
            kv_set(con, msg="папкалар ўқилмоқда: " + " → ".join(path + [name]))

    async def inline_all(q):
        res, off = [], ""
        for _ in range(40):
            try:
                r = (await c(functions.messages.GetInlineBotResultsRequest(bot=ent, peer=ent, query=q, offset=off))).to_dict()
            except errors.FloodWaitError as e:
                await flood(e); continue
            items = r.get("results") or []
            res += items
            off = r.get("next_offset") or ""
            if not items or not off:
                break
            await asyncio.sleep(1.0)
        return res

    async def card(code):
        for attempt in range(2):
            after = await last_id()
            try:
                await c.send_message(ent, code)
            except errors.FloodWaitError as e:
                await flood(e); continue
            m = await wait_new(after, lambda x: "Цена" in (x.message or "") or "Артикул" in (x.message or ""))
            await asyncio.sleep(PAUSE_CARD)
            if m:
                return parse_card(m.message)
        return None

    try:
        kv_set(con, err="")
        if kv(con, "state") == "tree":
            m = await root()
            await visit(m, [], 0, 1, [])
            kv_set(con, state="goods", msg="товар ва нархлар ўқилмоқда")
        prices = kv(con, "prices", "1") == "1"
        leaves = con.execute("SELECT id, name, query, path FROM ks_nodes WHERE kind='leaf' AND run=? AND done=0 ORDER BY sort", (run,)).fetchall()
        for lf in leaves:
            check_stop()
            items = await inline_all(lf[2])
            for i, it in enumerate(items):
                title, art, code = parse_item(it)
                key = art or title
                if not key:
                    continue
                g = con.execute("SELECT id, run, ok FROM ks_goods WHERE article=?", (key,)).fetchone()
                if g and g[1] == run and (g[2] or not prices):
                    continue                                   # бу юклашда олинган (бошқа гуруҳда ҳам бор)
                con.execute("INSERT INTO ks_goods(node,title,article,code,grade2,sort,run,ok,at) VALUES(?,?,?,?,?,?,?,0,?) "
                            "ON CONFLICT(article) DO UPDATE SET node=excluded.node, title=excluded.title, code=excluded.code, "
                            "grade2=excluded.grade2, sort=excluded.sort, run=excluded.run, ok=0, at=excluded.at",
                            (lf[0], title, key, code, 1 if ("2 сорт" in key.lower() or "2 сорт" in title.lower()) else 0, i, run, now()))
                con.commit()
                if prices and code:
                    check_stop()
                    d = await card(code)
                    if d and d["price"] is not None:
                        con.execute("UPDATE ks_goods SET price=?, cur=?, unit=?, thick=?, stock=?, ok=1, price_at=? WHERE article=?",
                                    (d["price"], d["cur"], d["unit"], d["thick"], d["stock"], now(), key))
                        con.commit()
            con.execute("UPDATE ks_nodes SET done=1, n=? WHERE id=?", (len(items), lf[0]))
            con.commit()
            kv_set(con, msg=f"товарлар: {lf[3]}")
        kv_set(con, state="done", msg="юклаб бўлинди", finished=now())
    except Stop:
        pass
    except Exception as e:
        kv_set(con, state="stopped", err=f"{e.__class__.__name__}: {e}"[:400], msg="хато билан тўхтади")
        A.errlog.exception("ksgroup crawl")
    finally:
        con.close()


def _runner():
    try:
        fx = _crawl
        try:                                           # энг янги ksgroup.py (сервер қайта ёқилмасдан тузатиш учун)
            src = os.path.abspath(__file__)
            ns = {"__name__": "ksgroup_live", "__file__": src}
            exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns)
            ns["A"] = A
            fx = ns["_crawl"]
        except Exception:
            A.errlog.exception("ksgroup reload")
        A.tgu_sender().call(_last["api_id"], _last["api_hash"], fx, timeout=8 * 3600)
    except Exception:
        A.errlog.exception("ksgroup runner")


def eman_busy():
    """Eman юклагичи ишлаяптими (бир аккаунтдан иккита бот бир вақтда ўқилмасин — Telegram чеклови хавфи). Eman'га тегилмайди."""
    em = sys.modules.get("eman")
    t = getattr(em, "_thr", None) if em else None
    return bool(t and t.is_alive())


def kick(con):
    global _thr
    if kv(con, "state") not in ("tree", "goods"):
        return False
    if _thr and _thr.is_alive():
        return True
    if float(kv(con, "resume_at", "0") or 0) > time.time():
        return False
    if eman_busy():
        kv_set(con, msg="Eman каталоги юкланмоқда — у тугагач бошланади")
        return False
    if not A.tgu_ready():
        kv_set(con, msg="Telegram-аккаунт уланмаган")
        return False
    _last["api_id"], _last["api_hash"] = A.tgu_cfg()
    _thr = threading.Thread(target=_runner, name="ksgroup-crawl", daemon=True)
    _thr.start()
    return True


def daily(con, dt=None):
    """Ҳар куни AUTO_HOUR'дан кейин бир марта (олдингиси тугаган бўлса). settings: ks_auto=0 — ўчириш."""
    dt = dt or datetime.now()
    try:
        auto = (con.execute("SELECT value FROM settings WHERE key='ks_auto'").fetchone() or ["1"])[0]
    except Exception:
        auto = "1"
    if auto == "0" or dt.hour < AUTO_HOUR or kv(con, "state", "") not in ("done", "stopped", ""):
        return False
    if (kv(con, "started", "") or "")[:10] >= dt.strftime("%Y-%m-%d"):
        return False
    start(con, prices=True)
    return True


def rows(con, q="", folder=None):
    run = int(kv(con, "run", "0") or 0)
    sql = ("SELECT g.*, n.path, n.name AS leaf FROM ks_goods g LEFT JOIN ks_nodes n ON n.id=g.node WHERE 1=1")
    a = []
    if q:
        for w in q.casefold().split():
            sql += " AND (lower(g.title) LIKE ? OR lower(g.article) LIKE ?)"
            a += [f"%{w}%", f"%{w}%"]
    if folder:
        sql += " AND (n.path=? OR n.path LIKE ?)"
        a += [folder, folder + " → %"]
    out = []
    for r in con.execute(sql + " ORDER BY n.sort, g.sort, g.id", a):
        d = dict(r)
        d["gone"] = bool(run and d["run"] != run and kv(con, "state") == "done")
        out.append(d)
    return out


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con); con.commit()
    finally:
        con.close()
    _orig = A.scheduler_tick

    def tick(dt=None):
        r = _orig(dt)
        try:
            probe_due()
        except Exception:
            A.errlog.exception("ksgroup probe")
        try:
            c2 = A.connect()
            try:
                init_tables(c2)
                daily(c2, dt)
                kick(c2)
            finally:
                c2.close()
        except Exception:
            A.errlog.exception("ksgroup kick")
        return r
    A.scheduler_tick = tick

    @app.route("/erp/kastamonu", methods=["GET", "POST"])
    @A.login_required
    def erp_kastamonu():
        if not A.is_admin():
            abort(403)
        con = A.db()
        init_tables(con)
        if request.method == "POST":
            act = request.form.get("act")
            if act == "start":
                start(con, prices=True)
                kick(con)
                flash("Юклаш бошланди — фонда ишлайди, саҳифани кейинроқ янгиланг", "ok")
            elif act == "stop":
                kv_set(con, stop=1)
                if not (_thr and _thr.is_alive()):
                    kv_set(con, state="stopped", msg="тўхтатилди", stop=0)
                flash("Тўхтатилмоқда", "ok")
            elif act == "resume":
                if kv(con, "state") == "stopped":
                    kv_set(con, state="goods" if con.execute("SELECT 1 FROM ks_nodes WHERE kind='leaf' AND run=?",
                                                             (int(kv(con, "run", "0") or 0),)).fetchone() else "tree", resume_at=0, err="")
                kick(con)
                flash("Давом этмоқда", "ok")
            return redirect(url_for("erp_kastamonu"))
        q = (request.args.get("q") or "").strip()
        folder = (request.args.get("f") or "").strip()
        rs = rows(con, q, folder)
        if request.args.get("csv"):
            buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
            w.writerow(["Папка", "Товар", "Артикул", "Толщина", "Цена", "Валюта", "Ед.", "Наличие", "Обновлено"])
            for r in rs:
                w.writerow([r["path"] or "", r["title"], r["article"], r["thick"], r["price"] if r["price"] is not None else "",
                            r["cur"], r["unit"], r["stock"], r["price_at"] or ""])
            return Response("\ufeff" + buf.getvalue(), mimetype="text/csv; charset=utf-8",
                            headers={"Content-Disposition": "attachment; filename=kastamonu.csv"})
        tops = [r[0] for r in con.execute("SELECT path FROM ks_nodes WHERE depth=1 ORDER BY sort")]
        return render_template("erp_kastamonu.html", st=status(con), rows=rs, q=q, folder=folder, tops=tops)
