# -*- coding: utf-8 -*-
"""Кромкани цех-ботга этикетка расми орқали киритиш (каталог режаси, 5-қадам). Миқдор — фақат метр.

«🏷 Кромка» (рухсати бор ишчи): этикетка расми -> матн (Windows OCR) -> каталогдан 3 та ўхшаш кромка (артикул/декор коди +
ўлчам + сўзлар) -> ишчи танлайди -> метрини ёзади. Танланган товарга этикеткадаги кодлар (HuUz, штрихкод рақами,
декор коди + ўлчам) боғланиб эсда қолади — кейинги сафар ўзи танилади.
Бозор кромкаси (номсиз): аввал ўлчам, кейин ранг; янги ранг бўлса — офис платформада тасдиқлайди (товарни танлайди).
Натижа — «Ввод остатков» саҳифасида (инвентаризацияга қўшилади)."""
import json, os, re
from datetime import datetime

A = None
ROLL_M = 100                    # (эски) рулон узунлиги — энди ҳар сафар сўралади: эслаб қолингани ёки этикеткадагиси биринчи
SIZES = ["19/0,4", "19/0,8", "19/1", "19/2", "21/1", "22/0,8", "22/1", "22/2", "23/0,8", "23/1", "35/1", "35/2", "42/1", "42/2", "45/1", "45/2"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_entries(id INTEGER PRIMARY KEY, rw_id INTEGER, good TEXT DEFAULT '', name TEXT DEFAULT '', qty_m REAL NOT NULL DEFAULT 0,
  rolls REAL, roll_m REAL, photo TEXT DEFAULT '', ocr TEXT DEFAULT '', codes TEXT DEFAULT '[]', state TEXT NOT NULL DEFAULT 'ok',
  note TEXT DEFAULT '', at TEXT, used_doc INTEGER);
CREATE TABLE IF NOT EXISTS edge_codes(code TEXT PRIMARY KEY, good TEXT NOT NULL, at TEXT);
CREATE TABLE IF NOT EXISTS edge_rolls(good TEXT PRIMARY KEY, m REAL NOT NULL);
"""
LOOK = str.maketrans("АВЕКМНОРСТХУавекмнорстху", "ABEKMHOPCTXYabekmhopctxy")   # кирилл/лотин бир хил кўринган ҳарфлар


def init_tables(con):
    con.executescript(SCHEMA)
    wc = {r[1] for r in con.execute("PRAGMA table_info(ready_workers)")}
    if "p_edge" not in wc:
        con.execute("ALTER TABLE ready_workers ADD COLUMN p_edge INTEGER NOT NULL DEFAULT 0")


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def norm(s):
    """Код солиштириш учун: катта ҳарф, кирилл->лотин ўхшашлари, бўшлиқ/белгиларсиз."""
    return re.sub(r"[^0-9A-Z]", "", (s or "").translate(LOOK).upper())


def sizes_in(text):
    """«19/1», «19/0,8», «23 X 0,80», «19x2» -> {(19, 1.0), …}"""
    out = set()
    for a, b in re.findall(r"(?<!\d)(\d{2})\s*[/xхXХ*×]\s*(0[.,]?\d{1,2}|\d(?:[.,]\d{1,2})?)(?![\d])", (text or "").replace("Х", "x")):
        try:
            w = int(a)
            t = float("0." + b[1:]) if re.fullmatch(r"0\d+", b) else float(b.replace(",", "."))   # «19/08» = 19/0,8
        except ValueError:
            continue
        if 10 <= w <= 60 and 0.2 <= t <= 3:
            out.add((w, round(t, 2)))
    return out


def codes_in(text):
    """Этикеткадаги кодлар: HuUz…, декор коди (U704, F416 …), узун рақамлар (штрихкод), артикуллар."""
    t = (text or "").translate(LOOK)
    out = set()
    for m in re.findall(r"HuUz\w+", t, re.I):
        out.add(norm(m))
    for m in re.findall(r"(?<![A-Z0-9])([A-Z]{1,3}\s?\d{3,5}[A-Z]{0,3})(?![A-Z0-9])", t.upper()):
        out.add(norm(m))
    for m in re.findall(r"(?<!\d)(\d{8,14})(?!\d)", t):
        out.add(m)
    return {c for c in out if len(c) >= 4}


def dkeys(s):
    """Декор калити: 1–2 ҳарф + 3–4 рақам («U704CA» -> U704, «F416 ST10» -> F416, «W1000» -> W1000)."""
    t = (s or "").translate(LOOK).upper()
    return set(re.findall(r"(?<![A-Z])([A-Z]{1,2}\d{3,4})(?!\d)", t))


def _edges(con):
    return con.execute("SELECT e.ext, e.name, e.unit, e.code FROM erp_goods e WHERE e.src='cat' AND e.folder=0 AND e.deleted=0 "
                       "AND COALESCE(e.cat,'')='good' AND e.lc LIKE '% кромк%'").fetchall()


def candidates(con, text, limit=3):
    """-> [(ext, name, score, learned)] энг ўхшашлари."""
    cs, ss, dk = codes_in(text), sizes_in(text), dkeys(text)
    learned = {}
    for c in cs:
        r = con.execute("SELECT good FROM edge_codes WHERE code=?", (c,)).fetchone()
        if r:
            learned[r[0]] = learned.get(r[0], 0) + 1
    words = {w for w in re.findall(r"[a-zа-яёўқғҳ]{4,}", (text or "").casefold()) if w not in ("кромка", "пвх", "abs", "kromka", "edge", "made")}
    res = []
    for g in _edges(con):
        sc = 0.0
        art = norm(g["code"])
        nm = g["name"] or ""
        if art and len(art) >= 4 and any(art == c or (len(c) >= 5 and (art in c or c in art)) for c in cs):
            sc += 8
        if dk and (dkeys(g["code"]) | dkeys(nm)) & dk:          # декор коди (артикулда ёки номда)
            sc += 7
        gs = sizes_in(nm)
        if ss and gs:
            if gs & ss:
                sc += 4
            else:
                sc -= 3
        low = nm.casefold()
        sc += sum(1 for w in words if w[:5] in low)
        if g["ext"] in learned:
            sc += 20
        if sc >= 4:
            res.append((g["ext"], nm, sc, g["ext"] in learned))
    res.sort(key=lambda x: -x[2])
    return res[:limit]


def search(con, q, limit=6):
    ws = [w for w in (q or "").casefold().split() if w][:5]
    ss = sizes_in(q)
    out = []
    for g in _edges(con):
        low = (g["name"] or "").casefold()
        if all(w in low for w in ws if not re.match(r"\d{2}\s*/", w)) and (not ss or sizes_in(g["name"]) & ss):
            out.append((g["ext"], g["name"], 0, False))
            if len(out) >= limit:
                break
    return out


def parse_qty(text, roll_m):
    """«3 рулон», «3р», «3 rulon» -> (300, 3); «250», «250 м» -> (250, None)."""
    t = (text or "").casefold().replace(",", ".")
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(р|рул\w*|rul\w*|r)\s*", t)
    if m:
        n = float(m.group(1))
        return n * roll_m, n
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(м|m|метр\w*|metr\w*)?\s*", t)
    if m:
        return float(m.group(1)), None
    return None, None


# ------------------------------------------------------------------ бот
def _rb():
    import raspil_bot
    return raspil_bot


def _pk():
    import pickup
    return pickup


def roll_m():
    try:
        return float(A.get_setting("edge_roll_m", "") or ROLL_M)
    except ValueError:
        return ROLL_M


def start(rw, chat):
    con = A.db()
    _pk().st_set(con, rw["id"], "edge")
    rb = _rb()
    rb.send(chat, "🏷 Кромка: этикетка расмини ташланг.\nЁки ўлчами ва рангини ёзинг (масалан: 19/1 кашемир).",
            rb.ikb([[("🛒 Бозор кромкаси (номсиз)", "pe:b")], [("🏁 Тугатдим", "pe:x")]]))


def _offer(con, rw, chat, cands, d):
    rb = _rb()
    rows = [[(("✅ " if c[3] else "") + c[1][:58], f"pe:g:{c[0]}")] for c in cands]
    rows += [[("🔎 Бошқасини қидириш", "pe:s")], [("🛒 Бозор кромкаси", "pe:b")], [("🏁 Тугатдим", "pe:x")]]
    _pk().st_set(con, rw["id"], "edge", **d)
    if cands:
        rb.send(chat, "Қайси бири?" + (" (✅ — аввал шу этикеткадан танланган)" if any(c[3] for c in cands) else ""), rb.ikb(rows))
    else:
        rb.send(chat, "Топилмади. Ўлчами ва рангини ёзинг (масалан: 19/1 кашемир) ёки «🔎 Бошқасини қидириш».", rb.ikb(rows))


def on_photo(rw, chat, msg):
    """Этикетка расми (ҳолат «edge»). -> True агар қабул қилинса."""
    con = A.db()
    mode, d = _pk().st_get(con, rw["id"])
    if mode not in ("edge", "edge_qty", "edge_search", "edge_len"):
        return False
    rb = _rb()
    ph = (msg.get("photo") or [])[-1] if msg.get("photo") else None
    if not ph:
        return False
    folder = os.path.join(A.DATA_DIR, "edge", datetime.now().strftime("%Y-%m"))
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, f"{datetime.now():%d_%H%M%S}_{rw['id']}.jpg")
    if not rb.download_try(ph["file_id"], path):
        rb.send(chat, "⚠️ Расм юкланмади (интернет). Қайта ташланг.")
        return True
    rb.send_typing(chat)
    import ready
    txt = ready.ocr_image(path) or ""
    if txt.startswith("#error"):
        txt = ""
    cands = candidates(con, txt)
    _offer(con, rw, chat, cands, dict(photo=path, ocr=txt[:2000], codes=sorted(codes_in(txt) | {f"{w}/{t}" for w, t in sizes_in(txt)})))
    return True


def on_text(rw, chat, text):
    con = A.db()
    pk = _pk()
    mode, d = pk.st_get(con, rw["id"])
    rb = _rb()
    if mode in ("edge", "edge_search"):
        cands = candidates(con, text) if mode == "edge" else []
        if not cands:
            cands = search(con, text)
        _offer(con, rw, chat, cands, dict(d))
        return True
    if mode == "edge_qty":
        q, rolls = parse_qty(text, 1)
        if rolls or not q or q <= 0:                       # фақат метр (эгаси айтди)
            rb.send(chat, "Метрини ёзинг, масалан: 250")
            return True
        if d.get("new_color") is None and not d.get("good"):
            pk.st_set(con, rw["id"], "edge")
            return True
        if rolls:                                          # рулон узунлиги ҳар хил — сўраймиз (эслаб қолинган/этикеткадагиси биринчи)
            pk.st_set(con, rw["id"], "edge_len", **dict(d, rolls=rolls))
            _ask_len(con, chat, d, rolls)
            return True
        _save(con, rw, chat, d, q, None, None)
        return True
    if mode == "edge_len":
        m_ = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(м|m|метр\w*)?\s*", (text or "").casefold())
        if not m_ or float(m_.group(1).replace(",", ".")) <= 0:
            rb.send(chat, "Битта рулон неча метр? Сонини ёзинг, масалан: 200")
            return True
        rm = float(m_.group(1).replace(",", "."))
        _save(con, rw, chat, d, d["rolls"] * rm, d["rolls"], rm)
        return True
    if mode == "edge_color":
        pk.st_set(con, rw["id"], "edge_qty", good="", label=f"Бозор кромка {d.get('size', '')} {text.strip()[:40]}".strip(), new_color=1,
                  photo=d.get("photo") or "", ocr=d.get("ocr") or "", codes=d.get("codes") or [])
        rb.send(chat, "Неча метр? Сонини ёзинг, масалан: 250")
        return True
    return False


LENS = [50, 100, 150, 200, 250, 300]


def roll_guess(con, d):
    """Рулон узунлиги: шу кромка учун эслаб қолингани, бўлмаса этикеткада ёзилгани («100м», «200 m»)."""
    if d.get("good"):
        r = con.execute("SELECT m FROM edge_rolls WHERE good=?", (d["good"],)).fetchone()
        if r:
            return r[0]
    m = re.search(r"(?<![\d/.,])(\d{2,3})\s*(?:м|m|метр|metr)\b", (d.get("ocr") or "").casefold())
    if m and 20 <= int(m.group(1)) <= 500:
        return float(m.group(1))
    return None


def _ask_len(con, chat, d, rolls):
    rb = _rb()
    g = roll_guess(con, d)
    lens = ([g] if g else []) + [x for x in LENS if x != g]
    rows = [[((f"✅ {x:g} м" if x == g else f"{x:g} м"), f"pe:l:{x:g}") for x in lens[i:i + 3]] for i in range(0, len(lens), 3)]
    n = int(rolls) if rolls == int(rolls) else rolls
    rb.send(chat, f"{n} рулон. Битта рулон неча метр?" + (f"\n✅ — {'аввал шу кромкада' if d.get('good') and con.execute('SELECT 1 FROM edge_rolls WHERE good=?', (d['good'],)).fetchone() else 'этикеткада'} шундай эди." if g else "")
            + "\nТугмани босинг ёки сонини ёзинг.", rb.ikb(rows))


def _save(con, rw, chat, d, q, rolls, rm):
    rb = _rb()
    pk = _pk()
    g = con.execute("SELECT name FROM erp_goods WHERE ext=?", (d.get("good") or "",)).fetchone()
    name = g["name"] if g else (d.get("label") or "")
    con.execute("INSERT INTO edge_entries(rw_id, good, name, qty_m, rolls, roll_m, photo, ocr, codes, state, note, at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (rw["id"], d.get("good") or "", name, q, rolls, rm, d.get("photo") or "", d.get("ocr") or "",
                 json.dumps(d.get("codes") or []), "ok" if d.get("good") else "new", d.get("label") or "", now()))
    if rolls and rm and d.get("good"):                     # шу кромканинг рулон узунлиги эслаб қолинади
        con.execute("INSERT INTO edge_rolls(good, m) VALUES(?,?) ON CONFLICT(good) DO UPDATE SET m=excluded.m", (d["good"], rm))
    con.commit()
    pk.st_set(con, rw["id"], "edge")
    extra = f" ({int(rolls) if rolls == int(rolls) else rolls} рулон × {rm:g} м)" if rolls else ""
    rb.send(chat, f"✅ Сақланди: {name} — {q:g} м{extra}." + ("\n🕓 Янги ранг — офис тасдиқлайди." if not d.get("good") else "")
            + "\n\nКейингисининг этикеткасини ташланг ёки «🏁 Тугатдим».", rb.ikb([[("🏁 Тугатдим", "pe:x")]]))


def on_cb(rw, cq):
    con = A.db()
    pk = _pk()
    rb = _rb()
    if not dict(rw).get("p_edge"):
        return "⛔ Рухсат йўқ"
    parts = (cq.get("data") or "").split(":")
    msg = cq.get("message") or {}
    chat = (msg.get("chat") or {}).get("id")
    mode, d = pk.st_get(con, rw["id"])
    act = parts[1] if len(parts) > 1 else ""
    if act == "x":
        pk.st_set(con, rw["id"])
        n, m = con.execute("SELECT COUNT(*), COALESCE(SUM(qty_m),0) FROM edge_entries WHERE rw_id=? AND at>=?",
                           (rw["id"], datetime.now().strftime("%Y-%m-%d"))).fetchone()
        rb.send(chat, f"🏁 Бугун киритилди: {n} та, жами {m:g} м. Раҳмат!", rb.kb_for(rw))
        return "Тугади"
    if act == "l":                                              # рулон узунлиги танланди
        if mode != "edge_len" or not d.get("rolls"):
            return "Аввал сонини ёзинг"
        rm = float(parts[2])
        _save(con, rw, chat, d, d["rolls"] * rm, d["rolls"], rm)
        return f"{rm:g} м"
    if act == "s":
        pk.st_set(con, rw["id"], "edge_search", **d)
        rb.send(chat, "🔎 Ўлчами ва номидан бир қисмини ёзинг (масалан: 19/1 кашемир).")
        return ""
    if act == "g":
        ext = ":".join(parts[2:])
        g = con.execute("SELECT name FROM erp_goods WHERE ext=?", (ext,)).fetchone()
        if not g:
            return "Топилмади"
        for c in d.get("codes") or []:                          # этикетка кодлари шу товарга боғланади
            if "/" not in c and len(c) >= 4:
                con.execute("INSERT INTO edge_codes(code, good, at) VALUES(?,?,?) ON CONFLICT(code) DO UPDATE SET good=excluded.good, at=excluded.at",
                            (c, ext, now()))
        con.commit()
        pk.st_set(con, rw["id"], "edge_qty", **dict(d, good=ext))
        rb.send(chat, f"{g['name']}\n\nНеча метр? Сонини ёзинг, масалан: 250")
        return ""
    if act == "b":                                              # бозор кромкаси: ўлчам
        rows = [[(s, f"pe:z:{s.replace(',', '.')}") for s in SIZES[i:i + 4]] for i in range(0, len(SIZES), 4)]
        rb.send(chat, "🛒 Бозор кромкаси: ўлчамини танланг.", rb.ikb(rows))
        return ""
    if act == "z":                                              # ўлчам -> бозор кромкалари (Местная / Бозор) рўйхати
        size = parts[2]
        w, t = size.split("/")
        locs = [g for g in _edges(con) if re.search(r"местн|бозор|bozor", (g["name"] or "").casefold())
                and (int(w), round(float(t), 2)) in sizes_in(g["name"])]
        rows = [[(g["name"][:58], f"pe:g:{g['ext']}")] for g in locs[:10]] + [[("➕ Янги ранг", f"pe:n:{size}")]]
        pk.st_set(con, rw["id"], "edge", **d)
        rb.send(chat, f"{size}: рангини танланг.", rb.ikb(rows))
        return ""
    if act == "n":
        pk.st_set(con, rw["id"], "edge_color", **dict(d, size=parts[2].replace(".", ",")))
        rb.send(chat, "Рангини ёзинг (масалан: кашемир, белый глянец).")
        return ""
    return "?"


def register(app_module):
    global A
    A = app_module
    con = A.connect()
    try:
        init_tables(con)
        con.commit()
    finally:
        con.close()
