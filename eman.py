# -*- coding: utf-8 -*-
"""Эман Материалс каталоги (@emanmaterials_bot) — бот тузилишидагидек: категория → подкатегория → … → гуруҳ (➖) → товар, нархи билан.

Бот қандай ишлайди (ўрганилди):
  • «📒 Каталог» — янги хабар; папкалар (🗂) callback тугмалари, data = GUID. Шу GUID'ни каталог хабарига юборсак — хабар
    ўша папкага таҳрирланади (кетма-кет босиш шарт эмас). «🏠 Домой» = 00000000-…
  • Барг (➖ MP-1004) — inline сўров (switch_inline, same_peer). Натижалари: товар номи, «Артикул», «Код: /N».
  • «/N» юборилса — товар картаси: «💵 Цена: 97600 сум/шт» ва филиаллардаги қолдиқ. /N — охирги inline сўровга нисбатан.

Юклаш — хабар аккаунти орқали, фақат шу бот билан суҳбат, секин (FloodWait'дан сақланиш). Ҳолат eman_kv жадвалида,
тўхтаса — қолган жойидан давом этади. Ҳар янги юклаш — янги «run»; кўринмай қолган товар/папка — «йўқолган».
"""
import asyncio, csv, io, json, os, re, threading, time
from datetime import datetime

from flask import request, render_template, redirect, url_for, flash, abort, Response

A = None
BOT = "emanmaterials_bot"
ROOT = "00000000-0000-0000-0000-000000000000"
PAUSE_CLICK = 1.5          # папка очгандан кейин
PAUSE_PRICE = 2.2          # ҳар /N дан кейин
NEXT_RE = re.compile(r"(далее|след|вперед|вперёд|▶|►|»|➡|>>)", re.I)
NAV_RE = re.compile(r"(домой|назад|↩|🏠|◀|«|⬅|<<)", re.I)
PRICE_RE = re.compile(r"Цена:\s*([\d\s.,]+?)\s*([^\d\s/]+)\s*/\s*(\S+)")
_thr = None
_last = {}


# ------------------------------------------------------------------ жадваллар ва ҳолат
def init_tables(con):
    con.executescript("""
    CREATE TABLE IF NOT EXISTS eman_kv(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS eman_nodes(id INTEGER PRIMARY KEY, key TEXT UNIQUE, parent INTEGER, name TEXT, kind TEXT,
        guid TEXT, query TEXT, sort INTEGER DEFAULT 0, depth INTEGER DEFAULT 0, run INTEGER DEFAULT 0, done INTEGER DEFAULT 0,
        n INTEGER DEFAULT 0, at TEXT);
    CREATE TABLE IF NOT EXISTS eman_goods(id INTEGER PRIMARY KEY, node INTEGER, title TEXT, article TEXT DEFAULT '', code TEXT,
        price REAL, cur TEXT DEFAULT '', unit TEXT DEFAULT '', stock TEXT DEFAULT '', thumb TEXT DEFAULT '', detail TEXT DEFAULT '',
        sort INTEGER DEFAULT 0, run INTEGER DEFAULT 0, ok INTEGER DEFAULT 0, at TEXT, UNIQUE(node, title));
    CREATE INDEX IF NOT EXISTS eman_goods_node ON eman_goods(node);
    CREATE INDEX IF NOT EXISTS eman_nodes_parent ON eman_nodes(parent);
    """)


def kv(con, key, default=""):
    r = con.execute("SELECT value FROM eman_kv WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def kv_set(con, **kw):
    for k, v in kw.items():
        con.execute("INSERT INTO eman_kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    con.commit()


def fmt(v):
    return "" if v is None else (f"{v:.2f}".rstrip("0").rstrip("."))


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def start(con, prices=True):
    run = int(kv(con, "run", "0") or 0) + 1
    con.execute("INSERT INTO eman_nodes(key, parent, name, kind, guid, depth, run, done, at) VALUES('root', NULL, 'Каталог', 'folder', ?, 0, ?, 0, ?) "
                "ON CONFLICT(key) DO UPDATE SET run=excluded.run, done=0", (ROOT, run, now()))
    kv_set(con, run=run, state="tree", stop=0, prices=int(bool(prices)), started=now(), finished="", msg="бошланди", resume_at=0, err="")
    carry_skipped(con, run)


def skipped(con):
    """Янгиланмайдиган папкалар (key): бошқа жойдан олинадиган товарлар, кромка (нархи хизматда). Эски маълумоти сақланади (эгаси айтди)."""
    try:
        return set(json.loads(kv(con, "skip", "[]") or "[]"))
    except ValueError:
        return set()


def carry_skipped(con, run):
    """Янгиланмайдиган папкалар ва ичидагилар янги юклашга «кўрилган» бўлиб ўтади (done=1): бот очилмайди, маълумот ўчмайди."""
    keys = skipped(con)
    if not keys:
        return 0
    kids = {}
    for r in con.execute("SELECT id, parent FROM eman_nodes").fetchall():
        kids.setdefault(r[1], []).append(r[0])
    ids = []
    for r in con.execute(f"SELECT id FROM eman_nodes WHERE key IN ({','.join('?' * len(keys))})", tuple(keys)).fetchall():
        st = [r[0]]
        while st:
            x = st.pop(); ids.append(x); st += kids.get(x, [])
    for i in range(0, len(ids), 500):
        part = ids[i:i + 500]
        q = ",".join("?" * len(part))
        con.execute(f"UPDATE eman_nodes SET run=?, done=1 WHERE id IN ({q})", (run, *part))
        con.execute(f"UPDATE eman_goods SET run=? WHERE node IN ({q})", (run, *part))
    return len(ids)


def price_only(con):
    """Нарх ва қолдиқ олинадиган юқори бўлимлар (бўш — ҳаммаси). Кунлик текширувда — фақат плитали материаллар."""
    try:
        return set(json.loads(kv(con, "price_only", "[]") or "[]"))
    except ValueError:
        return set()


def plate_tops(con):
    import catalog as _C
    return [r["key"] for r in con.execute("SELECT key, name FROM eman_nodes WHERE depth=1")
            if _C.wh_for(r["name"]) == _C.WH_PLATE]


def selected(con):
    """Товар/нархи олинадиган юқори бўлимлар (key=GUID). Бўш — ҳали танланмаган."""
    try:
        return set(json.loads(kv(con, "only", "[]") or "[]"))
    except ValueError:
        return set()


def top_key(con, nid, cache=None):
    cache = cache if cache is not None else {}
    path = []
    while nid is not None:
        if nid in cache:
            r = cache[nid]
        else:
            r = con.execute("SELECT id, parent, key FROM eman_nodes WHERE id=?", (nid,)).fetchone()
            cache[nid] = r
        if not r:
            return None
        path.append(r)
        nid = r["parent"]
    return path[-2]["key"] if len(path) >= 2 else None        # path[-1] — илдиз «Каталог»


def next_leaf(con, run):
    sel = selected(con)
    if not sel:
        return None
    cache = {}
    for lf in con.execute("SELECT * FROM eman_nodes WHERE kind='leaf' AND run=? AND done=0 ORDER BY depth, parent, sort", (run,)).fetchall():
        if top_key(con, lf["id"], cache) in sel:
            return lf
    return None


def status(con):
    run = int(kv(con, "run", "0") or 0)
    s = dict(run=run, state=kv(con, "state"), msg=kv(con, "msg"), err=kv(con, "err"), started=kv(con, "started"), finished=kv(con, "finished"),
             last_done=int(kv(con, "last_done", "0") or 0), prices=kv(con, "prices", "1") == "1", resume_at=float(kv(con, "resume_at", "0") or 0))
    one = lambda sql: con.execute(sql, (run,)).fetchone()[0]
    s["folders"] = one("SELECT COUNT(*) FROM eman_nodes WHERE kind='folder' AND run=?")
    s["folders_left"] = one("SELECT COUNT(*) FROM eman_nodes WHERE kind='folder' AND run=? AND done=0")
    s["leaves"] = one("SELECT COUNT(*) FROM eman_nodes WHERE kind='leaf' AND run=?")
    s["leaves_left"] = one("SELECT COUNT(*) FROM eman_nodes WHERE kind='leaf' AND run=? AND done=0")
    s["goods"] = one("SELECT COUNT(*) FROM eman_goods WHERE run=?")
    s["priced"] = one("SELECT COUNT(*) FROM eman_goods WHERE run=? AND ok=1")
    s["selected"] = selected(con)
    s["skip"] = skipped(con)
    sk = s["skip"]
    s["skip_names"] = [r[0] for r in con.execute(f"SELECT name FROM eman_nodes WHERE key IN ({','.join('?' * len(sk))}) ORDER BY depth, name", tuple(sk))] if sk else []
    s["running"] = bool(_thr and _thr.is_alive())
    s["resume_txt"] = datetime.fromtimestamp(s["resume_at"]).strftime("%H:%M") if s["resume_at"] > time.time() else ""
    return s


# ------------------------------------------------------------------ бот хабарини ўқиш
def _raw(o):
    return json.loads(json.dumps(o.to_dict() if hasattr(o, "to_dict") else o, default=lambda x: x.hex() if isinstance(x, bytes) else str(x)))


def kb_rows(msg):
    """Тугмалар: [{text, data (GUID матн), query}] — Telethon янги турларни танимаса ҳам хом маълумотдан."""
    rm = getattr(msg, "reply_markup", None)
    if not rm:
        return []
    out = []
    for row in (_raw(rm).get("rows") or []):
        for b in row.get("buttons") or []:
            t = b.get("type") if isinstance(b.get("type"), dict) else b
            data = t.get("data")
            if isinstance(data, str) and data:
                try:
                    data = bytes.fromhex(data).decode("utf-8", "replace")
                except ValueError:
                    pass
            q = t.get("query")
            if q is None and "SwitchInline" in str(t.get("_", "")):
                q = ""
            out.append(dict(text=(b.get("text") or "").strip(), data=data or None, query=q))
    return out


def clean(t):
    return re.sub(r"^[^\w(\"«]+", "", t or "").strip()


def parse_detail(text):
    """Товар картаси -> dict(price, cur, unit, stock{филиал: сон}, article)."""
    r = dict(price=None, cur="", unit="", stock={}, article="")
    m = PRICE_RE.search(text or "")
    if m:
        v = re.sub(r"\s", "", m.group(1)).replace(",", ".")
        try:
            r["price"] = float(v)
        except ValueError:
            pass
        r["cur"], r["unit"] = m.group(2), m.group(3)
    first = (text or "").split("\n", 1)[0].strip()
    ma = re.search(r"\(([^()]*)\)$", first)
    if ma:
        r["article"] = ma.group(1).strip()
    for blk in re.finditer(r"🔸\s*([^\n]+)\n\s*В наличии:\s*([\d.,]+)", text or ""):
        try:
            r["stock"][blk.group(1).strip()] = float(blk.group(2).replace(",", "."))
        except ValueError:
            pass
    return r


# ------------------------------------------------------------------ юклаш (Telegram оқимида)
class Stop(Exception):
    pass


async def _crawl(c):
    from telethon import functions, errors
    con = A.connect()
    con.execute("PRAGMA busy_timeout=3000")      # Telegram оқимида ишлайди: база банд бўлса узоқ кутиб, бошқа хабарларни тўхтатмасин
    init_tables(con)
    ent = await c.get_entity(BOT)
    run = int(kv(con, "run", "0") or 0)
    cat = None
    cur = []

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

    async def wait_cat(after, timeout=15.0):
        t = 0.0
        while t < timeout:
            await asyncio.sleep(1.0); t += 1.0
            for m in sorted(await c.get_messages(ent, min_id=after, limit=10), key=lambda x: x.id):
                if not m.out and getattr(m, "reply_markup", None) and "КАТАЛОГ" in (m.message or "").upper():
                    return m
        return None

    async def new_catalog():
        sent = await c.send_message(ent, "/start")
        m0 = None
        for _ in range(10):
            await asyncio.sleep(1.0)
            for m in sorted(await c.get_messages(ent, min_id=sent.id, limit=10), key=lambda x: x.id):
                if not m.out and any("Каталог" in b["text"] for b in kb_rows(m)):
                    m0 = m
            if m0:
                break
        if not m0:
            raise RuntimeError("бот /start га жавоб бермади")
        b = next(b for b in kb_rows(m0) if "Каталог" in b["text"])
        try:
            await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=m0.id, data=(b["data"] or "catalog").encode()))
        except errors.FloodWaitError:
            raise
        except Exception:
            pass
        m = await wait_cat(m0.id)
        if not m:
            raise RuntimeError("каталог очилмади")
        return m

    class Fail(Exception):
        pass

    async def click(guid, name):
        """Жорий каталог хабарида битта тугма (бола папка ёки «Назад»). Бот фақат кўриниб турган тугмаларни қабул қилади."""
        nonlocal cat
        want = clean(name).casefold().rstrip(":").strip()
        for attempt in range(2):
            try:
                await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=cat.id, data=guid.encode()))
            except errors.FloodWaitError as e:
                await flood(e); continue
            except (errors.MessageIdInvalidError, errors.DataInvalidError):
                raise Fail()
            except Exception:
                pass                                  # BotResponseTimeout ва ш.к. — хабар барибир таҳрирланади
            for _ in range(12):
                await asyncio.sleep(0.8)
                m = await c.get_messages(ent, ids=cat.id)
                if not m:
                    raise Fail()
                if clean(m.message or "").casefold().rstrip(":").strip() == want:
                    cat = m; return m
        raise Fail()

    def chain(node):
        out, nid = [], node["id"]
        while nid is not None:
            r = con.execute("SELECT id, parent, key, guid, name FROM eman_nodes WHERE id=?", (nid,)).fetchone()
            if not r or r["key"] == "root":
                break
            out.append((r["guid"], r["name"])); nid = r["parent"]
        return list(reversed(out))

    async def goto(node):
        """Илдиздан папкагача кетма-кет (жорий жойдан энг яқин йўл: «Назад» билан юқорига, кейин пастга)."""
        nonlocal cat, cur
        path = chain(node)
        for attempt in range(3):
            try:
                if cat is None:
                    cat = await new_catalog(); cur = []
                    await asyncio.sleep(1.0)
                k = 0
                while k < min(len(cur), len(path)) and cur[k][0] == path[k][0]:
                    k += 1
                while len(cur) > k:
                    cur.pop()
                    if cur:
                        await click(*cur[-1])
                    else:
                        cat = await new_catalog()
                        await asyncio.sleep(1.0)
                for g, nm in path[k:]:
                    await click(g, nm); cur.append((g, nm))
                    await asyncio.sleep(0.5)
                return cat
            except Fail:
                cat = None; cur = []
        raise RuntimeError(f"папка очилмади: {node['name']}")

    def upsert_node(key, parent, name, kind, guid, query, sort, depth):
        con.execute("INSERT INTO eman_nodes(key, parent, name, kind, guid, query, sort, depth, run, done, at) VALUES(?,?,?,?,?,?,?,?,?,0,?) "
                    "ON CONFLICT(key) DO UPDATE SET parent=excluded.parent, name=excluded.name, kind=excluded.kind, query=excluded.query, "
                    "sort=excluded.sort, depth=excluded.depth, done=CASE WHEN eman_nodes.run=excluded.run THEN eman_nodes.done ELSE 0 END, "
                    "run=excluded.run, at=excluded.at", (key, parent, name, kind, guid, query, sort, depth, run, now()))

    async def scan_folder(node):
        m = await goto(node)
        sort, seen_pages = 0, set()
        while True:
            nxt = None
            for b in kb_rows(m):
                tx = b["text"]
                if b["query"] is not None:
                    q = b["query"] or clean(tx)
                    upsert_node(f"{node['guid']}|{q}", node["id"], clean(tx) or q, "leaf", None, q, sort, node["depth"] + 1); sort += 1
                elif b["data"]:
                    if b["data"] == ROOT or NAV_RE.search(tx[:3]):
                        continue
                    if NEXT_RE.search(tx) and not tx.startswith("🗂"):
                        nxt = b; continue
                    upsert_node(b["data"], node["id"], clean(tx), "folder", b["data"], None, sort, node["depth"] + 1); sort += 1
            con.commit()
            if not nxt or nxt["data"] in seen_pages or len(seen_pages) > 30:
                break
            seen_pages.add(nxt["data"])
            try:
                await c(functions.messages.GetBotCallbackAnswerRequest(peer=ent, msg_id=m.id, data=nxt["data"].encode()))
            except errors.FloodWaitError as e:
                await flood(e)
            except Exception:
                pass
            await asyncio.sleep(2.5)
            m = await c.get_messages(ent, ids=m.id)
        con.execute("UPDATE eman_nodes SET done=1 WHERE id=?", (node["id"],)); con.commit()

    async def inline(q):
        res, off = [], ""
        for _ in range(40):
            try:
                r = _raw(await c(functions.messages.GetInlineBotResultsRequest(bot=ent, peer=ent, query=q, offset=off)))
            except errors.FloodWaitError as e:
                await flood(e); continue
            items = r.get("results") or []
            res += items
            off = r.get("next_offset") or ""
            if not items or not off:
                break
            await asyncio.sleep(1.0)
        return res

    async def price(code, title):
        for attempt in range(2):
            try:
                sent = await c.send_message(ent, code)
            except errors.FloodWaitError as e:
                await flood(e); continue
            for _ in range(15):
                await asyncio.sleep(0.8)
                for m in sorted(await c.get_messages(ent, min_id=sent.id, limit=5), key=lambda x: x.id):
                    if not m.out and m.message and ("Цена" in m.message or (title or "")[:20] in m.message):
                        return m.message
            await asyncio.sleep(3)
        return None

    async def scan_leaf(node):
        # бот inline натижаларини фойдаланувчи ҳозир турган папкадан беради — аввал барг папкасини очамиз
        par = con.execute("SELECT * FROM eman_nodes WHERE id=?", (node["parent"],)).fetchone()
        if par is not None and par["key"] != "root" and (not cur or cur[-1][0] != par["guid"]):
            await goto(par)
            await asyncio.sleep(0.5)
        res = await inline(node["query"])
        qn = (node["query"] or "").casefold()
        own = [x for x in res if qn and qn in (x.get("title") or "").casefold()]
        if own and len(own) < len(res):
            res = own
        for i, x in enumerate(res):
            sm = x.get("send_message") or {}
            code = (sm.get("message") or "").strip()
            title = (x.get("title") or "").strip()
            ma = re.search(r"Артикул:\s*([^\n]+)", x.get("description") or "")
            th = (x.get("thumb") or {}).get("url") or ""
            con.execute("INSERT INTO eman_goods(node, title, article, code, thumb, sort, run, ok, at) VALUES(?,?,?,?,?,?,?,0,?) "
                        "ON CONFLICT(node, title) DO UPDATE SET article=excluded.article, code=excluded.code, thumb=excluded.thumb, "
                        "sort=excluded.sort, ok=CASE WHEN eman_goods.run=excluded.run THEN eman_goods.ok ELSE 0 END, run=excluded.run",
                        (node["id"], title, (ma.group(1).strip() if ma else ""), code, th, i, run, now()))
        con.commit()
        po = price_only(con)
        if kv(con, "prices", "1") == "1" and (not po or top_key(con, node["id"]) in po):
            for g in con.execute("SELECT id, code, title FROM eman_goods WHERE node=? AND run=? AND ok=0 ORDER BY sort", (node["id"], run)).fetchall():
                check_stop()
                if not (g["code"] or "").startswith("/"):
                    continue
                txt = await price(g["code"], g["title"])
                if txt and not txt.strip().startswith(g["title"][:15]):
                    await inline(node["query"])       # бошқа товар чиқди — сўровни янгилаб қайта
                    await asyncio.sleep(1.0)
                    txt = await price(g["code"], g["title"])
                    if txt and not txt.strip().startswith(g["title"][:15]):
                        txt = None
                if txt:
                    d = parse_detail(txt)
                    con.execute("UPDATE eman_goods SET price=?, cur=?, unit=?, stock=?, detail=?, ok=1, at=?, "
                                "article=CASE WHEN COALESCE(article,'')='' THEN ? ELSE article END WHERE id=?",
                                (d["price"], d["cur"], d["unit"], json.dumps(d["stock"], ensure_ascii=False), txt[:2000], now(), d["article"], g["id"]))
                    con.commit()
                await asyncio.sleep(PAUSE_PRICE)
        n = con.execute("SELECT COUNT(*) FROM eman_goods WHERE node=? AND run=?", (node["id"], run)).fetchone()[0]
        con.execute("UPDATE eman_nodes SET done=1, n=? WHERE id=?", (n, node["id"])); con.commit()

    try:
        while True:
            check_stop()
            node = con.execute("SELECT * FROM eman_nodes WHERE kind='folder' AND run=? AND done=0 ORDER BY depth, parent, sort LIMIT 1", (run,)).fetchone()
            if node:
                kv_set(con, state="tree", msg=f"папка: {node['name']}")
                await scan_folder(node)
                await asyncio.sleep(PAUSE_CLICK)
                continue
            leaf = next_leaf(con, run)
            if leaf:
                kv_set(con, state="goods", msg=f"товарлар: {leaf['name']}")
                await scan_leaf(leaf)
                await asyncio.sleep(PAUSE_CLICK)
                continue
            if not selected(con):
                kv_set(con, state="choose", msg="папкалар ўқилди — товар ва нархлари керак бўлган бўлимларни танланг")
                break
            kv_set(con, state="done", msg="юклаб бўлинди", finished=now(), last_done=run)
            break
    except Stop:
        pass
    except Exception as e:
        A.errlog.exception("eman crawl")
        busy = "locked" in str(e)
        try:
            con.rollback()
            kv_set(con, err=f"{e.__class__.__name__}: {e}"[:300], resume_at=time.time() + (60 if busy else 600),
                   msg="база банд — 1 дақиқадан кейин давом этади" if busy else "хато — 10 дақиқадан кейин давом этади")
        except Exception:
            pass
    finally:
        con.close()
    return True


def _runner():
    try:
        fx = _crawl
        try:                                          # энг янги eman.py (серверни қайта ёқмасдан тузатиш учун)
            src = os.path.abspath(__file__)
            ns = {"__name__": "eman_live", "__file__": src}
            exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns)
            ns["A"] = A
            fx = ns["_crawl"]
        except Exception:
            A.errlog.exception("eman reload")
        A.tgu_sender().call(_last["api_id"], _last["api_hash"], fx, timeout=24 * 3600)
    except Exception:
        A.errlog.exception("eman runner")


AUTO_HOUR = 21        # ҳар куни кечқурун янги текширув (нарх ва қолдиқ); ~7–8 соат, тунда тугайди. 21:00 — мижоз хабарлари тугагач (эгаси, 27.10)


def daily(con, dt=None):
    """Eman каталоги ҳар куни автомат қайта текширилади: нарх ўзгарса — платформа нархи ҳам, «Eman'да бор/йўқ» ҳам янгиланади.
    Олдинги текширув тугаган бўлса ва бугун ҳали бошланмаган бўлса — AUTO_HOUR'да бошланади. settings: eman_auto=0 — ўчириш."""
    from datetime import datetime as _dt
    dt = dt or _dt.now()
    try:
        auto = (con.execute("SELECT value FROM settings WHERE key='eman_auto'").fetchone() or ["1"])[0]
    except Exception:
        auto = "1"
    if auto == "0" or dt.hour < AUTO_HOUR:
        return False
    st = kv(con, "state", "")
    if st not in ("done", "stopped", "") or not selected(con):
        return False
    if (kv(con, "started", "") or "")[:10] >= dt.strftime("%Y-%m-%d"):
        return False
    start(con, prices=True)
    kv_set(con, price_only=json.dumps(plate_tops(con), ensure_ascii=False))     # нарх/қолдиқ — фақат плиталар (тезроқ тугайди)
    con.commit()
    return True


def kick(con):
    """Юклаш ҳолатида бўлса ва оқим ишламаётган бўлса — ишга туширади."""
    global _thr
    if kv(con, "state") not in ("tree", "goods"):
        return False
    if _thr and _thr.is_alive():
        return True
    if float(kv(con, "resume_at", "0") or 0) > time.time():
        return False
    if not A.tgu_ready():
        kv_set(con, msg="Telegram-аккаунт уланмаган")
        return False
    _last["api_id"], _last["api_hash"] = A.tgu_cfg()
    _thr = threading.Thread(target=_runner, name="eman-crawl", daemon=True)
    _thr.start()
    return True


# ------------------------------------------------------------------ кўриш
def tree(con):
    """Кўринадиган папкалар: охирги тугаган юклашдан бери кўрилганлар."""
    run = int(kv(con, "run", "0") or 0)
    last = int(kv(con, "last_done", "0") or 0)
    lo = last if (last and kv(con, "state") != "done") else run
    rows = [dict(r) for r in con.execute("SELECT * FROM eman_nodes WHERE run>=? ORDER BY depth, sort", (lo or 0,))]
    by = {r["id"]: r for r in rows}
    for r in rows:
        r["kids"] = []
    roots = []
    for r in sorted(rows, key=lambda r: r["sort"]):
        (by[r["parent"]]["kids"] if r["parent"] in by else roots).append(r)
    cnt = dict(con.execute("SELECT node, COUNT(*) FROM eman_goods WHERE run>=? GROUP BY node", (lo or 0,)).fetchall())

    def tot(r):
        r["total"] = cnt.get(r["id"], 0) + sum(tot(k) for k in r["kids"])
        return r["total"]
    for r in roots:
        tot(r)
    root = next((r for r in roots if r["key"] == "root"), None)
    return (root["kids"] if root else roots), by, lo


def path_of(by, nid):
    out = []
    while nid in by and by[nid]["key"] != "root":
        out.append(by[nid]["name"]); nid = by[nid]["parent"]
    return list(reversed(out))


def subtree_ids(by, nid):
    ids, st = [], [nid]
    while st:
        x = st.pop(); ids.append(x)
        st += [k["id"] for k in by[x]["kids"]] if x in by else []
    return ids


def _dfs_order(by):
    roots = sorted([r for r in by.values() if r["parent"] not in by], key=lambda r: r["sort"])
    out, st = [], list(reversed(roots))
    while st:
        x = st.pop(); out.append(x["id"])
        st += list(reversed(x["kids"]))
    return out


def goods_rows(con, by, lo, ids=None, qtext=""):
    sql, args = "SELECT * FROM eman_goods WHERE run>=?", [lo or 0]
    if ids is not None:
        sql += f" AND node IN ({','.join('?' * len(ids))})"; args += ids
    if qtext:
        import kb
        vs = kb.variants(qtext)
        sql += " AND (" + " OR ".join("title LIKE ? OR article LIKE ?" for _ in vs) + ")"
        for v in vs:
            args += [f"%{v}%", f"%{v}%"]
    out = []
    for g in con.execute(sql + " ORDER BY node, sort LIMIT 20000", args):
        g = dict(g)
        try:
            g["stock_d"] = json.loads(g["stock"] or "{}")
        except ValueError:
            g["stock_d"] = {}
        g["path"] = path_of(by, g["node"])
        out.append(g)
    order = {nid: i for i, nid in enumerate(_dfs_order(by))}
    out.sort(key=lambda g: (order.get(g["node"], 0), g["sort"]))
    return out


# ------------------------------------------------------------------ текшириш (data/eman_probe.req: «функция:бот»)
def probe_due():
    req = os.path.join(A.DATA_DIR, "eman_probe.req")
    if not os.path.exists(req) or not A.tgu_ready():
        return None
    try:
        txt = open(req, encoding="utf-8").read().strip()
    except OSError:
        txt = ""
    os.remove(req)
    mode, bot = (txt.split(":", 1) if ":" in txt else (txt, BOT))
    src = os.path.abspath(__file__)
    ns = {"__name__": "eman_live", "__file__": src}
    res = {}
    t0 = time.time()
    try:
        exec(compile(open(src, encoding="utf-8").read(), src, "exec"), ns)
        ns["A"] = A
        fx = ns.get(mode)
        if fx:
            api_id, api_hash = A.tgu_cfg()
            res = A.tgu_sender().call(api_id, api_hash, lambda cl: fx(cl, bot.lstrip("@"), out=res), timeout=240) or res
        else:
            res = dict(error=f"функция йўқ: {mode}")
    except Exception as e:
        import traceback
        res = dict(res, error=repr(e), tb=traceback.format_exc())
    res["took"] = round(time.time() - t0, 1)
    with open(os.path.join(A.DATA_DIR, "eman_probe.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    return res


# ------------------------------------------------------------------ рўйхатдан ўтказиш
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
            A.errlog.exception("eman probe")
        try:
            c2 = A.connect()
            try:
                daily(c2, dt)
                kick(c2)
            finally:
                c2.close()
        except Exception:
            A.errlog.exception("eman kick")
        return r
    A.scheduler_tick = tick

    @app.route("/erp/eman", methods=["GET", "POST"])
    @A.login_required
    def erp_eman():
        con = A.db()
        if request.method == "POST":
            if not A.is_admin():
                abort(403)
            act = request.form.get("act")
            if act == "start":
                start(con, prices=request.form.get("prices", "1") == "1")
                kv_set(con, price_only="[]")
                kick(con)
                flash("Юклаш бошланди — фонда ишлайди, саҳифани кейинроқ янгиланг", "ok")
            elif act == "stop":
                kv_set(con, stop=1)
                if not (_thr and _thr.is_alive()):
                    kv_set(con, state="stopped", msg="тўхтатилди", stop=0)
                flash("Тўхтатилмоқда", "ok")
            elif act == "select":
                keys = [k for k in request.form.getlist("top") if con.execute("SELECT 1 FROM eman_nodes WHERE key=?", (k,)).fetchone()]
                kv_set(con, only=json.dumps(keys, ensure_ascii=False))
                if keys and kv(con, "state") in ("choose", "done"):
                    kv_set(con, state="goods", finished="", resume_at=0)
                    kick(con)
                flash(f"Танланди: {len(keys)} бўлим", "ok")
            elif act == "resume":
                if kv(con, "state") == "stopped":
                    kv_set(con, state="tree")
                kv_set(con, resume_at=0, err="")
                kick(con)
                flash("Давом этмоқда", "ok")
            return redirect(url_for("erp_eman"))
        roots, by, lo = tree(con)
        nid = request.args.get("node", type=int)
        qtext = (request.args.get("q") or "").strip()
        sel = by.get(nid) if nid else None
        ids = subtree_ids(by, nid) if sel else None
        if request.args.get("csv") == "1":
            rows = goods_rows(con, by, lo, ids, qtext)
            depth = max([len(g["path"]) for g in rows] or [1])
            branches = sorted({k for g in rows for k in g["stock_d"]})
            import xlbrand
            head = [f"Уровень {i + 1}" for i in range(depth)] + ["Товар", "Артикул", "Цена", "Валюта", "Ед."] + branches
            data = [g["path"] + [""] * (depth - len(g["path"])) + [g["title"], g["article"], g["price"], g["cur"], g["unit"]]
                    + [g["stock_d"].get(b) for b in branches] for g in rows]
            kinds = ["text"] * (depth + 2) + ["money", "text", "text"] + ["qty"] * len(branches)
            return xlbrand.book("eman_catalog", "Каталог Eman", head, data, kinds=kinds, totals=False)
        rows = goods_rows(con, by, lo, ids, qtext) if (sel or qtext) else []
        branches = sorted({k for g in rows for k in g["stock_d"]})
        return render_template("erp_eman.html", st=status(con), roots=roots, by=by, rows=rows, sel=sel, nid=nid, qtext=qtext,
                               branches=branches, path=path_of(by, nid) if sel else [])
