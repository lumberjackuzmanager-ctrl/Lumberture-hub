# -*- coding: utf-8 -*-
"""Платформанинг янги каталоги (1С товарларидан алоҳида).

• Товарлар Eman каталогидан олинади: категориялар ботдагидек тартибда, ҳар бир юқори категорияга склад берилади
  (Плитные материалы / Кромка / Фурнитура / Клей ва химия / Инструмент ва сарф). Eman «Клей» олинмайди —
  «Клей ва химия» складига ўзимиз ишлатадиган клейлар қўйилади.
• Хизматлар доим каталог тепасида. Номи ва нархи 1С'дан (1 Распил, 4 Ровер, 7 Базис, 8 Утолшенка, 9 Присадка, 99 Доставка…).
  Кромка хизмати 1С'дан олинмайди: Eman кромкаларидан нусха — «Услуга ПВХ …» ва «Услуга Овал …» (ҳар бир кромка номи билан),
  нархи 1С'даги мос кромка хизматидан; норматив: шу кромка ×1 + клей (энига қараб).
• 1С товарлари билан боғланмайди, остатка ҳам ўзиники. Эски ҳужжатларга таъсир қилмайди.
"""
import json, re
from collections import Counter, defaultdict
from datetime import datetime

from flask import request, render_template, jsonify, abort

A = None
CAT_VER = "24"          # қоида ўзгарса оширилади — сервер қайта ишга тушганда каталог бир марта қайта қурилади

SCHEMA = """
CREATE TABLE IF NOT EXISTS cat_wh(id INTEGER PRIMARY KEY, name TEXT NOT NULL, sort INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS cat_nodes(id INTEGER PRIMARY KEY, parent INTEGER DEFAULT 0, name TEXT, sort INTEGER DEFAULT 0, wh INTEGER,
  svc INTEGER DEFAULT 0, skey TEXT UNIQUE, deleted INTEGER DEFAULT 0, manual INTEGER DEFAULT 0, gone INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS cat_nodes_p ON cat_nodes(parent);
CREATE TABLE IF NOT EXISTS cat_items(id INTEGER PRIMARY KEY, node INTEGER, name TEXT, lc TEXT, article TEXT DEFAULT '', unit TEXT DEFAULT '',
  cur INTEGER DEFAULT 1, price REAL, price_eman REAL, svc INTEGER DEFAULT 0, kind TEXT DEFAULT 'good', skey TEXT UNIQUE, c1_src TEXT DEFAULT '',
  base INTEGER, sort INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0, manual INTEGER DEFAULT 0, src_manual INTEGER DEFAULT 0,
  gone INTEGER DEFAULT 0, at TEXT);
CREATE INDEX IF NOT EXISTS cat_items_n ON cat_items(node);
CREATE TABLE IF NOT EXISTS cat_norms(item INTEGER, comp INTEGER, qty REAL, auto INTEGER DEFAULT 1, PRIMARY KEY(item, comp));
"""

WH = [(1, "Плитные материалы", 1), (2, "Кромка", 2), (3, "Фурнитура", 3), (4, "Клей и химия", 4), (5, "Инструменты и расходники", 5)]
WH_PLATE, WH_EDGE, WH_FURN, WH_GLUE, WH_TOOL = 1, 2, 3, 4, 5

# Eman юқори категорияси -> склад (бошқалари — Фурнитура)
_PLATE = re.compile(r"^(лдсп|лмдф|лхдф|мдф|дсп|хдф|осп|акрил|perfect sense|faton|hpl|бсп|wps|шаттдекор|столешниц)", re.I)
_EDGE = re.compile(r"^(кромка|пленки пвх)", re.I)
_TOOL = re.compile(r"^(сопутствующ|инструмент|канцеляр)", re.I)
_SKIP = re.compile(r"^клей$", re.I)                     # Eman клейлари олинмайди


def wh_for(name):
    n = (name or "").strip()
    if _PLATE.search(n):
        return WH_PLATE
    if _EDGE.search(n):
        return WH_EDGE
    if _TOOL.search(n):
        return WH_TOOL
    return WH_FURN


def lc(t):
    t = (t or "").casefold().replace("ё", "е")
    return " " + re.sub(r"[^\w/.,*]+", " ", t).strip() + " "


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


SUPPLIERS = ["Эман", "Назиф дунё, Гилам Сарой, Ибн Сино, Панельный", "Фаёз Каштан", "Кастамону Фаррух", "Бозор", "Timber zone",
             "Craftwood (Тапоич — Крафт Вуд Окажон)"]


def sup_for(path):
    """Eman категорияси йўли (юқоридан пастга) -> етказиб берувчи (эгаси айтган қоида)."""
    t = " / ".join(path).casefold()
    if "timber" in t or "тимбер" in t:
        return SUPPLIERS[5]
    if "крафтвуд" in t or "craftwood" in t:
        return SUPPLIERS[6]
    if "старвуд" in t or "эконом" in t:
        return SUPPLIERS[1]
    if "pakchoob" in t or "пакчоб" in t:
        return SUPPLIERS[2]
    if ("кастамону" in t or "kastamonu" in t) and "акрил" in t:
        return SUPPLIERS[3]
    if "кромка" in t and "местн" in t:
        return SUPPLIERS[4]
    return SUPPLIERS[0]


def eman_name(name):
    """1С'дагидек: Eman'даги «Старвуд» плиталари бизда «Эконом»."""
    return re.sub(r"(?i)старвуд", "Эконом", name or "")


def init(con):
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(cat_nodes)")}
    for col, typ in (("sup", "TEXT"), ("sup_manual", "INTEGER DEFAULT 0"), ("disc", "INTEGER")):
        if col not in cols:
            con.execute(f"ALTER TABLE cat_nodes ADD COLUMN {col} {typ}")
    if "disc" not in {r[1] for r in con.execute("PRAGMA table_info(cat_items)")}:
        con.execute("ALTER TABLE cat_items ADD COLUMN disc INTEGER")       # скидка: NULL — папкадан, 1 — мумкин, 0 — йўқ
    for i, n, s in WH:
        con.execute("INSERT OR IGNORE INTO cat_wh(id,name,sort) VALUES(?,?,?)", (i, n, s))
    try:
        goods_cols(con)
    except Exception:
        pass                                  # erp_goods ҳали йўқ (тест ёки биринчи ишга тушиш) — кейинги init'да
    rename_sup(con)


# поставщик гуруҳининг эски номлари (Панельный — жой эмас, шу ламинат заводларидан бири; эгаси, 23.10)
SUP_OLD = {"Назиф дунё, Гилам Сарой, Ибн Сино (Панельный)": SUPPLIERS[1], "Панельный (Назим дунё, Гилам Сарой, Ибн Сино)": SUPPLIERS[1]}


def rename_sup(con):
    """Каталог ва олиб келиш рўйхатларидаги эски гуруҳ номини янгисига (бир марта, ҳар ишга тушганда текширилади)."""
    for old, new in SUP_OLD.items():
        for t in ("cat_nodes", "pickup_items", "pickup_sup", "pickup_msgs"):
            try:
                con.execute(f"UPDATE {t} SET sup=? WHERE sup=?", (new, old))
            except Exception:
                pass                          # жадвал ҳали йўқ
    try:
        r = con.execute("SELECT value FROM settings WHERE key='pickup_sup_map'").fetchone()
        mp = json.loads(r[0] or "{}") if r else {}
        if any(k in mp for k in SUP_OLD):
            for old, new in SUP_OLD.items():
                if old in mp:
                    mp.setdefault(new, mp.pop(old))
            con.execute("UPDATE settings SET value=? WHERE key='pickup_sup_map'", (json.dumps(mp, ensure_ascii=False),))
    except Exception:
        pass


# ================================================================ кромка: ўлчам, бренд, тур
def nonum(name):
    """1С папкасининг олдидаги тартиб рақами керак эмас: «1 Распил» -> «Распил», «99 Услуги доставки» -> «Услуги доставки»."""
    n = (name or "").strip()
    return re.sub(r"^\d+\.?\s+", "", n) or n


def size_of(t):
    """«Кромка ПВХ 19/0,4 …» -> ('19', '0.4'); 19/04 -> 0.4, 22/08 -> 0.8, 19/1.0 -> 1."""
    m = re.search(r"(\d{2})\s*/\s*(\d+(?:[.,]\d+)?)", t or "")
    if not m:
        return None
    w, th = m.group(1), m.group(2).replace(",", ".")
    if re.fullmatch(r"0\d", th):
        th = "0." + th[1]
    try:
        th = ("%g" % float(th))
    except ValueError:
        pass
    return (w, th)


_FIN = [("однотон", r"однотон"), ("текстур", r"текстур"), ("суперматт", r"супер\s*-?\s*мат"), ("глянец", r"глян"),
        ("синхро", r"синхро"), ("смарт", r"смарт|smart"), ("abs", r"\babs\b"), ("3d", r"\b3d\b"), ("перл", r"перл|pearl"),
        ("метал", r"метал"), ("gold", r"gold"), ("3p", r"\b3p\b"), ("infinity", r"infinity")]


def fin_of(t):
    t = (t or "").casefold()
    return {k for k, rx in _FIN if re.search(rx, t)}


def mat_of(t):
    """Кромка материали: «abs» / «pvc» (ПВХ, PVC) / «» — номида ёзилмаган."""
    t = (t or "").casefold()
    if re.search(r"\babs\b", t):
        return "abs"
    if re.search(r"пвх|\bpvc\b", t):
        return "pvc"
    return ""


def brand_of(t):
    t = (t or "").casefold()
    if "central asia" in t:
        return "hca"
    if "huali" in t:
        return "huali"
    if "agt" in t or "portakal" in t:
        return "agt"
    if "эггер" in t or "egger" in t:
        return "egger"
    if "karebant" in t:
        return "karebant"
    if "pakchoob" in t:
        return "pakchoob"
    if "местн" in t or "базар" in t or "бозор" in t:
        return "local"
    return ""


# Клей сарфи плита четининг юзасига қараб (кг/м²). Плита қалинлиги ≈ кромка эни − 3 мм (19 → 16, 22 → 19, 35 → 32, 42 → 39).
# 1С'даги кирим тўлиқ эмас (баъзи клей кирим қилинмаган), овал яқинда қўйилган — шунинг учун норма соҳа маълумотидан:
#  • PUR (тўғри станок, Хенкел челак 20 кг, Хитой): ЛДСП/МДФ учун 100–150 г/м² → 150 × 1,12 (чиқинди коэф.) = 168 г/м²
#  • EVA гранула (овал, Хенкел қоп 25 кг, Туркия): ЛДСП учун 120–200 г/м² → 200 × 1,12 = 224 г/м²
# Манба: inner.su «Нормы расхода материалов в мебели». Платформада кирим тўғри юритилгач — ҳақиқий сарф билан аниқланади.
GLUE_RATE = {"pur": 0.168, "eva": 0.224}


def _glue_rate(kind):
    rate = GLUE_RATE["eva" if kind == "eva" else "pur"]
    try:
        return float(A.get_setting("cat_glue_" + kind, "") or rate) if A else rate
    except Exception:
        return rate


def glue_norm(w, kind="pur"):
    """1 пог.м кромкага клей сарфи (кг): сарф (кг/м²) × плита қалинлиги (м)."""
    try:
        t = max(int(w) - 3, 8) / 1000.0
    except (TypeError, ValueError):
        t = 0.016
    rate = GLUE_RATE["eva" if kind == "eva" else "pur"]
    try:
        rate = float(A.get_setting("cat_glue_" + kind, "") or rate) if A else rate
    except Exception:
        pass
    return round(rate * t, 5)


# ================================================================ 1С манбалари
def _c1_rows(con):
    rp = {}
    try:
        import erp
        rp = erp.reval_prices(con)
    except Exception:
        pass
    rows = {}
    for r in con.execute("SELECT ext, name, unit, cur, price, price_last, parent, folder, deleted, COALESCE(cat,'') cat "
                         "FROM erp_goods WHERE src='1c' OR src IS NULL OR src='pf'"):
        d = dict(r)
        d["eff"] = rp.get(r["ext"]) or r["price"] or r["price_last"] or 0
        rows[r["ext"]] = d
    return rows


def _kids(rows):
    k = defaultdict(list)
    for r in rows.values():
        k[r["parent"] or "0"].append(r)
    return k


def _top_folder(rows, kids, rx):
    for r in kids.get("0", []):
        if r["folder"] and not r["deleted"] and re.match(rx, (r["name"] or "").strip(), re.I):
            return r["ext"]
    return None


def _walk(kids, ext):
    for r in kids.get(ext, []):
        yield r
        if r["folder"]:
            yield from _walk(kids, r["ext"])


def _mode_price(items):
    c = Counter((r["cur"] or 1, r["eff"]) for r in items if r["eff"] and not r["deleted"])
    if not c:
        return None
    (cur, pr), _ = c.most_common(1)[0]
    unit = Counter(r["unit"] or "" for r in items if not r["deleted"]).most_common(1)[0][0]
    return dict(cur=cur, price=pr, unit=unit)


def price_of(con, src, rows=None):
    """c1_src: '<ext>' — 1С товари/хизмати нархи; 'F:<ext>' — 1С папкасидаги хизматларнинг асосий нархи."""
    if not src:
        return None
    rows = rows if rows is not None else _c1_rows(con)
    if src.startswith("F:"):
        kids = _kids(rows)
        return _mode_price([r for r in _walk(kids, src[2:]) if not r["folder"]])
    r = rows.get(src)
    if not r or r["folder"]:
        return None
    return dict(cur=r["cur"] or 1, price=r["eff"], unit=r["unit"] or "")


def src_name(con, src, rows=None):
    if not src:
        return ""
    rows = rows if rows is not None else _c1_rows(con)
    r = rows.get(src[2:] if src.startswith("F:") else src)
    return ((("папка: " if src.startswith("F:") else "") + (r["name"] or "")) if r else "?")


def _pvc_index(rows, kids):
    """1С «2 Кромка» ичидаги папкалар: [(ext, brand, size, fin)]."""
    top = _top_folder(rows, kids, r"2\s")
    out = []
    if not top:
        return out
    for r in _walk(kids, top):
        if r["folder"] and not r["deleted"] and not re.match(r"\s*услуг", r["name"] or "", re.I):
            sz = size_of(r["name"])
            its = [x for x in kids.get(r["ext"], []) if not x["folder"] and not x["deleted"]]
            if sz and any(x["eff"] for x in its):
                ms = [mat_of(x["name"]) for x in its]                      # папка материали — ичидаги кўпчилик (ABS / PVC)
                mat = mat_of(r["name"]) or max(("abs", "pvc"), key=ms.count) if any(ms) else mat_of(r["name"])
                out.append((r["ext"], brand_of(r["name"]), sz, fin_of(r["name"]), mat))
    return out


def _oval_index(rows, kids):
    top = _top_folder(rows, kids, r"3\s")
    out = []
    if not top:
        return out
    for r in _walk(kids, top):
        if not r["folder"] and not r["deleted"]:
            sz = size_of(r["name"])
            if sz:
                out.append((r["ext"], brand_of(r["name"]), sz, fin_of(r["name"]), mat_of(r["name"])))
    return out


def _best(idx, brand, sz, fin, need_brand=True, mat=""):
    best, bs = None, None
    for ext, b, s, f, *m in idx:
        if s != sz:
            continue
        if mat == "pvc" and m and m[0] == "abs":
            continue                                  # ПВХ кромкага ABS нархи олинмайди (Эггер ПВХ арзон)
        if need_brand and b != brand:
            continue
        sc = 2 * len(fin & f) - len(fin ^ f)
        if bs is None or sc > bs:
            best, bs = ext, sc
    return best


def match_pvc(idx, brand, sz, fin, mat=""):
    if not sz:
        return None
    return _best(idx, brand, sz, fin, mat=mat)


OVAL_MIN = 0.8                # овал кромка фақат 0,8 мм ва қалинроғига (19/0,4 — овал қилиб бўлмайди)
_SPEC = {"суперматт", "синхро", "смарт", "gold", "abs", "3d"}


def oval_ok(sz):
    try:
        return bool(sz) and float(sz[1]) >= OVAL_MIN - 1e-9
    except ValueError:
        return False


def match_oval(idx, brand, sz, fin, mat=""):
    """1С «3 Кромка Овал» нархи кромка ўлчами бўйича (эни/қалинлиги). Портакал (AGT), Эггер ва махсус турлар
    (Смарт, Синхро, Суперматт, GOLD, ABS, 3D) — 1С'да алоҳида ёзилган бўлса, шуниси; бўлмаса оддий «Кромка <ўлчам> ПВХ».
    Аниқ ўлчам бўлмаса — шу энидаги энг яқин қалинлик."""
    if not oval_ok(sz):
        return None
    spec = fin & _SPEC

    def pick(cands):
        if not cands:
            return None
        return max(cands, key=lambda x: (2 * len(spec & x[3]) - len((x[3] & _SPEC) ^ spec)))[0]
    if brand in ("egger", "agt", "local"):
        e = pick([x for x in idx if x[1] == brand and x[2] == sz and not (mat == "pvc" and x[4:5] == ("abs",))])
        if e:
            return e
        if brand == "egger" and mat == "pvc":
            return None                               # Эггер ПВХ — 1С'да нархи йўқ: қўлда (ABS ёки оддий ПВХ нархи олинмайди)
    plain = [x for x in idx if x[1] not in ("egger", "agt")]
    if spec:
        e = pick([x for x in plain if x[2] == sz and x[3] & spec])
        if e:
            return e
    e = pick([x for x in plain if x[2] == sz and not (x[3] & _SPEC)])
    if e:
        return e
    w, t = sz[0], float(sz[1])
    near = [x for x in plain if x[2][0] == sz[0] and not (x[3] & _SPEC)]
    if near:
        return min(near, key=lambda x: (abs(float(x[2][1]) - t), -float(x[2][1])))[0]
    return None


# ================================================================ қуриш (Eman + 1С)
def _node(con, skey, name, parent, sort, wh=None, svc=0):
    r = con.execute("SELECT id, manual FROM cat_nodes WHERE skey=?", (skey,)).fetchone()
    if r:
        if r["manual"]:
            con.execute("UPDATE cat_nodes SET gone=0 WHERE id=?", (r["id"],))
        else:
            con.execute("UPDATE cat_nodes SET name=?, parent=?, sort=?, svc=?, gone=0, deleted=0, wh=COALESCE(wh,?) WHERE id=?",
                        (name, parent, sort, svc, wh, r["id"]))
        return r["id"]
    return con.execute("INSERT INTO cat_nodes(parent,name,sort,wh,svc,skey) VALUES(?,?,?,?,?,?)",
                       (parent, name, sort, wh, svc, skey)).lastrowid


def _item(con, skey, node, name, **kw):
    """Янги — қўшади; бор — қўлда ўзгартирилмаган майдонларини янгилайди. -> id"""
    r = con.execute("SELECT id, manual, src_manual FROM cat_items WHERE skey=?", (skey,)).fetchone()
    t = now()
    if r:
        sets, args = ["gone=0", "at=?"], [t]
        if not r["manual"]:
            sets += ["node=?", "name=?", "lc=?", "deleted=0"]
            args += [node, name, lc(name)]
            for k in ("article", "unit", "sort", "svc", "kind", "base"):
                if k in kw:
                    sets.append(f"{k}=?"); args.append(kw[k])
            if "cur" in kw and kw.get("cur_force"):
                sets.append("cur=?"); args.append(kw["cur"])
        if "price_eman" in kw:
            sets.append("price_eman=?"); args.append(kw["price_eman"])
        if "c1_src" in kw and not r["src_manual"]:
            sets.append("c1_src=?"); args.append(kw["c1_src"])
        con.execute(f"UPDATE cat_items SET {', '.join(sets)} WHERE id=?", args + [r["id"]])
        return r["id"]
    cols = dict(node=node, name=name, lc=lc(name), skey=skey, at=t)
    for k in ("article", "unit", "cur", "price", "price_eman", "svc", "kind", "c1_src", "base", "sort"):
        if k in kw:
            cols[k] = kw[k]
    return con.execute(f"INSERT INTO cat_items({','.join(cols)}) VALUES({','.join('?' * len(cols))})", list(cols.values())).lastrowid


def _norm(con, item, comp, qty):
    r = con.execute("SELECT auto FROM cat_norms WHERE item=? AND comp=?", (item, comp)).fetchone()
    if r and not r["auto"]:
        return
    con.execute("INSERT INTO cat_norms(item,comp,qty,auto) VALUES(?,?,?,1) ON CONFLICT(item,comp) DO UPDATE SET qty=excluded.qty",
                (item, comp, qty))


def _manual_glue(con, item):
    """Хизматда қўлда белгиланган клей нормативи борми (auto=0, «Клей» папкасидаги товар)."""
    return bool(con.execute("SELECT 1 FROM cat_norms n JOIN cat_items i ON i.id=n.comp JOIN cat_nodes d ON d.id=i.node "
                            "WHERE n.item=? AND n.auto=0 AND d.skey='g:glue'", (item,)).fetchone())


def _cur(v):
    v = (v or "").casefold()
    return 2 if ("$" in v or "у.е" in v or "usd" in v or "долл" in v) else 1


def build(con):
    """Каталогни Eman ва 1С'дан янгилаш (қўлда ўзгартирилганлар сақланади, ўчирилмайди). -> статистика"""
    init(con)
    st = Counter()
    con.execute("UPDATE cat_nodes SET gone=1 WHERE manual=0 AND skey NOT LIKE 'x:%' AND skey NOT LIKE 'g:%'")
    con.execute("UPDATE cat_items SET gone=1 WHERE manual=0 AND skey NOT LIKE 'x:%' AND skey NOT LIKE 'g:%'")

    # ---------- 1) Хизматлар (доим тепада) — 1С'дан
    svc_root = _node(con, "s:root", "Услуги", 0, -1000, None, 1)
    rows = _c1_rows(con)
    kids = _kids(rows)
    tops = sorted([r for r in kids.get("0", []) if r["folder"] and not r["deleted"]
                   and re.match(r"\s*([1-9]|99)\s", r["name"] or "") and not re.match(r"\s*[2356]\s", r["name"] or "")],
                  key=lambda r: int(re.match(r"\s*(\d+)", r["name"]).group(1)))
    import erp
    extra_parent = None
    for i, t in enumerate(tops):
        nid = _node(con, "c:" + t["ext"], nonum(t["name"]), svc_root, 10 * int(re.match(r"\s*(\d+)", t["name"]).group(1)), None, 1)
        if re.match(r"\s*99\s", t["name"]):
            extra_parent = nid

        flat = bool(re.search(r"конструктор", t["name"] or "", re.I))   # Базис конструктор: конструктор папкалари (Абдулахад, Садулла) керак эмас — битта рўйхат
        seen = set()

        def mirror(fext, pid):
            for k, r in enumerate(sorted(kids.get(fext, []), key=lambda r: (not r["folder"], r["name"] or ""))):
                if r["deleted"]:
                    continue
                if r["folder"]:
                    if re.search(r"срочн", r["name"] or "", re.I):
                        continue                          # «Распил СРОЧНО» — керак эмас: накладнойда «Срочно» белгиланса нарх ўзи ўзгаради
                    mirror(r["ext"], pid if flat else _node(con, "c:" + r["ext"], nonum(r["name"]), pid, k, None, 1))
                elif erp.is_service(con, r["ext"]):
                    nm = (r["name"] or "").strip()
                    if flat:
                        if lc(nm) in seen:
                            continue                      # бир хил хизмат иккинчи конструктор папкасида — битта қолади
                        seen.add(lc(nm))
                    _item(con, "c:" + r["ext"], pid, nm, unit=r["unit"] or "", cur=r["cur"] or 1, svc=1,
                          kind="svc", c1_src=r["ext"], sort=k)
                    st["svc1c"] += 1
        if extra_parent == nid:
            continue                                  # «99 Услуги доставки» — 1С хизматлари керак эмас (эгаси, 24.10); папкада фақат Доставка, Упаковка, Грузчик, Сборка
        mirror(t["ext"], nid)
    # Кромка хизматлари 1С'дагидек: «Кромка» папкаси ичида — брендлар (бизнинг кромка, нархи хизматда) ва «Услуга ПВХ» (мижоз кромкаси).
    # «наша кромка / кромка клиента» деб ёзилмайди — ишчиларни чалғитади (эгаси айтди)
    pvc_root = _node(con, "k:pvc", "Кромка", svc_root, 20, None, 1)
    oval_root = _node(con, "k:oval", "Кромка Овал", svc_root, 30, None, 1)
    # 1С «2 Кромка / Услуга ПВХ» — фақат хизмат (мижоз ўз кромкасини олиб келса), кромка сарфи йўқ
    k2 = _top_folder(rows, kids, r"2\s")
    for f in (kids.get(k2, []) if k2 else []):
        if f["folder"] and not f["deleted"] and re.match(r"\s*услуг", f["name"] or "", re.I):
            cid = _node(con, "c:" + f["ext"], "Услуга ПВХ", pvc_root, 0, None, 1)
            for k, r in enumerate(sorted(kids.get(f["ext"], []), key=lambda r: r["name"] or "")):
                if not r["folder"] and not r["deleted"]:
                    _item(con, "c:" + r["ext"], cid, (r["name"] or "").strip(), unit=r["unit"] or "", cur=r["cur"] or 1, svc=1,
                          kind="svc", c1_src=r["ext"], sort=k)
                    st["svc1c"] += 1
    if extra_parent is None:
        extra_parent = _node(con, "x:extra", "Доставка ва қўшимча хизматлар", svc_root, 990, None, 1)
    have = [lc(r[0]) for r in con.execute("SELECT name FROM cat_items WHERE node=? AND deleted=0", (extra_parent,))]
    for k, nm in enumerate(("Доставка", "Упаковка", "Грузчик", "Сборка")):
        if con.execute("SELECT 1 FROM cat_items WHERE skey=?", ("x:" + nm,)).fetchone() or any(lc(nm).strip() in h for h in have):
            continue
        _item(con, "x:" + nm, extra_parent, nm, unit="услуга", cur=1, svc=1, kind="svc", sort=100 + k)
        st["extra"] += 1

    # ---------- 2) Клей ва химия: кромка клейлари (Eman'дан эмас)
    gnode = _node(con, "g:glue", "Клей", 0, 9000, WH_GLUE, 0)
    glue = {}
    for key, nm, art in (("g:pur", "Клей прямой Хенкел ПУР", "челак 20 кг, Хитой"), ("g:oval", "Клей овал Хенкел (гранула)", "қоп 25 кг, Туркия")):
        glue[key] = _item(con, key, gnode, nm, unit="кг", cur=2, svc=0, kind="good", article=art)

    # 1С хизматларининг нормативидаги клей сарфи (масалан «Услуга ПВХ от 19/04 до 22/2» — мижоз кромкаси, клей ×0,005) — бизнинг клейга
    try:
        nm1 = erp.norms()
    except Exception:
        nm1 = {}
    for r in con.execute("SELECT id, c1_src FROM cat_items WHERE skey LIKE 'c:%' AND c1_src<>''").fetchall():
        for g, q in nm1.get(r["c1_src"], ()):
            gn = (rows.get(g) or {}).get("name") or ""
            if re.search(r"клей", gn, re.I):      # 1С'даги миқдор нотўғри — ўлчамдан ўзимиз ҳисоблаймиз
                ov = bool(re.search(r"овал", gn, re.I))
                nmx = con.execute("SELECT name FROM cat_items WHERE id=?", (r["id"],)).fetchone()[0]
                sz = size_of(nmx)
                if not _manual_glue(con, r["id"]):
                    _norm(con, r["id"], glue["g:oval" if ov else "g:pur"], glue_norm(sz[0] if sz else "19", "eva" if ov else "pur"))
                st["glue_norms"] += 1

    # Овал — мижоз кромкаси (1С'да йўқ): фақат хизмат + овал клей; нархи қўлда
    ocl = _node(con, "x:oval_cl", "Услуга Овал", oval_root, 0, None, 1)
    for key, nm, w, pr in (("x:oval_cl1", "Услуга Овал от 19/08 до 22/2", "19", 5000), ("x:oval_cl2", "Услуга Овал от 35/1 до 48/2", "35", 7000)):
        oid = _item(con, key, ocl, nm, unit="пог.м", cur=1, price=pr, svc=1, kind="svc", sort=int(key[-1]))
        _norm(con, oid, glue["g:oval"], glue_norm(w, "eva"))

    # ---------- 3) Eman товарлари (бот тартибида)
    try:
        nodes = {r["id"]: dict(r) for r in con.execute("SELECT id, key, parent, name, sort FROM eman_nodes")}
    except Exception:
        nodes = {}
    root = next((n for n in nodes.values() if n["key"] == "root"), None)
    ekids = defaultdict(list)
    for n in nodes.values():
        ekids[n["parent"]].append(n)
    for v in ekids.values():
        v.sort(key=lambda n: (n["sort"] or 0, n["name"] or ""))
    egoods = defaultdict(list)
    if nodes:
        for g in con.execute("SELECT id, node, title, article, price, cur, unit, sort FROM eman_goods ORDER BY node, sort, id"):
            egoods[g["node"]].append(dict(g))
    kromka = []                                       # (cat_item_id, title, brand-path, leaf name)
    # 31.10 (эгаси): маҳаллий (бозор) кромка — Eman'нинг «Кромка юМестная» ўрнига 1С'даги «Кромка Местная … (Базар)» (товар папкаси,
    # хизматлар «2 Кромка»да эмас). 1С'да топилмаса — Eman'ники қолади (рўйхат бўш қолмасин)
    loc1c = sorted([r for r in rows.values() if r["folder"] and not r["deleted"] and re.search(r"кромка\s+местн", r["name"] or "", re.I)
                    and not re.match(r"\s*\d+\s", (rows.get(r["parent"] or "") or {}).get("name") or "")
                    and any(not x["folder"] and not x["deleted"] for x in kids.get(r["ext"], []))], key=lambda r: size_of(r["name"]) or ("", ""))

    def emirror(en, pid, wh, path):
        if (loc1c and len(path) == 2 and path[0].casefold() == "кромка" and re.search(r"местн", path[1], re.I)
                and re.search(r"местн", en["name"] or "", re.I)):
            return                                    # Eman «Кромка юМестная / Кромка Местная 19/1 …» — 1С'дагиси билан алмаштирилди (пастда, 3ж2);
                                                      # шу папкадаги Craftwood ва «Ташкаридан» — қолади
        own = wh if pid == 0 else None
        if pid and wh == WH_FURN and _TOOL.search((en["name"] or "").strip()):      # Yechim / Инструменты, Канцелярия
            wh = own = WH_TOOL
        cid = _node(con, "e:" + en["key"], eman_name((en["name"] or "").strip()), pid, en["sort"] or 0, own, 0)
        con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (sup_for(path + [en["name"] or ""]), cid))
        for g in egoods.get(en["id"], []):
            iid = _item(con, f"e:{en['key']}|{g['title']}", cid, (g["title"] or "").strip(), article=g["article"] or "",
                        unit=g["unit"] or "", cur=_cur(g["cur"]), price_eman=g["price"], svc=0, kind="good", sort=g["sort"] or 0)
            st["goods"] += 1
            if wh == WH_EDGE and path and re.match(r"кромка", path[0], re.I):
                kromka.append((iid, g["title"] or "", path + [en["name"] or ""]))
        for k in ekids.get(en["id"], []):
            emirror(k, cid, wh, path + [en["name"] or ""])

    if root:
        for t in ekids.get(root["id"], []):
            if _SKIP.search((t["name"] or "").strip()):
                continue
            emirror(t, 0, wh_for(t["name"]), [])

    # ---------- 3а) Эконом ва Craftwood (ЛДСП, ЛМДФ) — Eman'дан эмас, 1С'дан (эгаси, 24.10): Eman «Эконом ЛДСП/ЛМДФ» (ичидаги Craftwood
    #   ҳам) ўчирилади; 1С «ЛМДФ Эконом» ва «ЛМДФ Крафтвуд» ЛМДФ ичига кўчади; ЛДСП ичида — худди шу рўйхат, номида «ЛМДФ» ўрнига «ЛДСП»
    #   (нархи қўлда / переоценка). Акрил ичига 1С «Акрил Крафтвуд», «Акрил Хитой» ва «Акрил ПВС».
    def _top(nm):
        return con.execute("SELECT id FROM cat_nodes WHERE parent=0 AND svc=0 AND deleted=0 AND name=? ORDER BY (skey LIKE 'e:%') DESC LIMIT 1",
                           (nm,)).fetchone()                  # SQLite UPPER() кириллни ўзгартирмайди — номи аниқ
    for typ in ("ЛДСП", "ЛМДФ"):
        tp = _top(typ)
        if not tp:
            continue
        for ek_ in con.execute("SELECT id, skey FROM cat_nodes WHERE parent=? AND skey LIKE 'e:%' AND name LIKE 'Эконом%'", (tp["id"],)).fetchall():
            con.execute("UPDATE cat_nodes SET deleted=1 WHERE id=? OR (skey LIKE ? AND skey LIKE 'e:%')", (ek_["id"], ek_["skey"] + "|%"))

    def _ldsp(t):
        return re.sub(r"ЛМДФ", "ЛДСП", re.sub(r"лмдф", "лдсп", t or ""))

    def c1_copy(fext, pid, pfx, ren, sup, with_price):
        for k, r in enumerate(sorted(kids.get(fext, []), key=lambda r: (not r["folder"], r["name"] or ""))):
            if r["deleted"]:
                continue
            nm = re.sub(r"\s+", " ", ren((r["name"] or "").strip()))
            if r["folder"]:
                nid2 = _node(con, pfx + r["ext"], nm, pid, k, None, 0)
                con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (sup, nid2))
                c1_copy(r["ext"], nid2, pfx, ren, sup, with_price)
            else:
                _item(con, pfx + r["ext"], pid, nm, unit=r["unit"] or "", cur=r["cur"] or 2,
                      price=(r["eff"] or None) if with_price else None, svc=0, kind="good", sort=k)
                st["goods_1c"] += 1

    def c1_folder(rx):
        return next((r for r in kids.get("0", []) if r["folder"] and not r["deleted"] and re.match(rx, (r["name"] or "").strip(), re.I)), None)

    same = lambda t: t
    for rx, label, sup, sort in ((r"лмдф\s+эконом$", "Эконом", SUPPLIERS[1], 903), (r"лмдф\s+(крафтвуд|craftwood)$", "Craftwood", SUPPLIERS[6], 904)):
        fx = c1_folder(rx)
        if not fx:
            continue
        for typ, pfx, ren, wp in (("ЛМДФ", "c1g:", same, True), ("ЛДСП", "c1l:", _ldsp, False)):
            tp = _top(typ)
            rid = _node(con, pfx + fx["ext"], f"{label} {typ}" if label == "Эконом" else label, tp["id"] if tp else 0, sort, None if tp else WH_PLATE, 0)
            con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (sup, rid))
            c1_copy(fx["ext"], rid, pfx, ren, sup, wp)
    mdf0 = _top("МДФ шлифованная") or _top("МДФ")      # 1С «МДФ Эконом» (шлифованный) — МДФ ичида, бренд папкалари қаторида (эгаси, 25.10)
    fx = c1_folder(r"мдф\s+эконом$")
    if fx:
        rid = _node(con, "c1g:" + fx["ext"], "МДФ Эконом", mdf0["id"] if mdf0 else 0, 903, None if mdf0 else WH_PLATE, 0)
        c1_copy(fx["ext"], rid, "c1g:", same, None, True)
    akr0 = _top("Акрил")
    # 1С «Акрил ПВС» ҳам Акрил ичига (эгаси, 27.10), нархи 1С'дан
    for k0, (rx, sup) in enumerate(((r"акрил\s+(крафтвуд|craftwood)$", SUPPLIERS[6]), (r"акрил\s+(хитой|китай)$", SUPPLIERS[0]),
                                    (r"акрил\s+(пвс|pvs)$", None))):
        fx = c1_folder(rx)
        if not fx:
            continue
        rid = _node(con, "c1g:" + fx["ext"], re.sub(r"\s+", " ", fx["name"].strip()), akr0["id"] if akr0 else 0, 901 + k0, None if akr0 else WH_PLATE, 0)
        con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (sup, rid))
        c1_copy(fx["ext"], rid, "c1g:", same, sup, True)

    # ---------- 3б) Eman'да йўқ товарлар 1С'дан нусха (боғланмайди): Акрил Кастамону (Кастамону Фаррух)
    akr = con.execute("SELECT id FROM cat_nodes WHERE parent=0 AND svc=0 AND skey LIKE 'e:%' AND name='Акрил'").fetchone()
    for fx in [r for r in kids.get("0", []) if r["folder"] and not r["deleted"] and re.search(r"акрил\s+кастамону", r["name"] or "", re.I)]:
        root_id = _node(con, "c1g:" + fx["ext"], "Акрил Кастамону", akr["id"] if akr else 0, 900, None if akr else WH_PLATE, 0)
        con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[3], root_id))

        def gcopy(fext, pid):
            for k, r in enumerate(sorted(kids.get(fext, []), key=lambda r: (not r["folder"], r["name"] or ""))):
                if r["deleted"]:
                    continue
                if r["folder"]:
                    nid2 = _node(con, "c1g:" + r["ext"], (r["name"] or "").strip(), pid, k, None, 0)
                    con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[3], nid2))
                    gcopy(r["ext"], nid2)
                else:
                    _item(con, "c1g:" + r["ext"], pid, (r["name"] or "").strip(), unit=r["unit"] or "", cur=r["cur"] or 1,
                          price=r["eff"] or None, svc=0, kind="good", sort=k)
                    st["goods_1c"] += 1
        gcopy(fx["ext"], root_id)

    # ---------- 3в) 1С'даги Б/У плиталар (ДСП Б/У, ЛМДФ Б/У) — Eman'да йўқ, нархи қўлда
    for k0, fx in enumerate([r for r in kids.get("0", []) if r["folder"] and not r["deleted"] and re.search(r"б\s*/\s*у", r["name"] or "", re.I)]):
        bid = _node(con, "c1g:" + fx["ext"], (fx["name"] or "").strip(), 0, 950 + k0, WH_PLATE, 0)
        for k, r in enumerate(sorted(kids.get(fx["ext"], []), key=lambda r: r["name"] or "")):
            if r["deleted"] or r["folder"]:
                continue
            _item(con, "c1g:" + r["ext"], bid, (r["name"] or "").strip(), unit=r["unit"] or "", cur=r["cur"] or 2,
                  price=r["eff"] or None, svc=0, kind="good", sort=k)
            st["goods_1c"] += 1

    # ---------- 3г) 1С «5 Остатки (кусок)»: кусоклар ($/м²) ва апилка, ўтин (хизмат) — плиталар қаторида, нархи 1С'дан
    for k0, fx in enumerate([r for r in kids.get("0", []) if r["folder"] and not r["deleted"] and re.search(r"остатк", r["name"] or "", re.I)]):
        oid = _node(con, "c1g:" + fx["ext"], nonum(fx["name"]), 0, 960 + k0, WH_PLATE, 0)
        for k, r in enumerate(sorted(kids.get(fx["ext"], []), key=lambda r: r["name"] or "")):
            if r["deleted"] or r["folder"]:
                continue
            sv = 1 if (r["cat"] if "cat" in r.keys() else "") == "svc" else 0
            _item(con, "c1g:" + r["ext"], oid, nonum(r["name"]), unit=r["unit"] or "", cur=r["cur"] or 1,
                  price=r["eff"] or None, svc=sv, kind="svc" if sv else "good", sort=k)
            st["goods_1c"] += 1

    # ---------- 3д) 1С «ЛДСП / ЛМДФ / ЛХДФ Timber zone» (Панельный) — Eman'да йўқ: каталогдаги ЛДСП, ЛМДФ, ЛХДФ ичига «Timber zone» папкаси
    def tz_copy(fext, pid):
        for k, r in enumerate(sorted(kids.get(fext, []), key=lambda r: (not r["folder"], r["name"] or ""))):
            if r["deleted"]:
                continue
            if r["folder"]:
                nid2 = _node(con, "c1g:" + r["ext"], (r["name"] or "").strip(), pid, k, None, 0)
                con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[5], nid2))
                tz_copy(r["ext"], nid2)
            else:
                _item(con, "c1g:" + r["ext"], pid, re.sub(r"\s+", " ", (r["name"] or "").replace("Timder", "Timber")).strip(), unit=r["unit"] or "",
                      cur=r["cur"] or 2, price=r["eff"] or None, svc=0, kind="good", sort=k)
                st["goods_1c"] += 1
    tz = [r for r in (rows.values() if isinstance(rows, dict) else rows) if r["folder"] and not r["deleted"] and re.match(r"\s*(ЛДСП|ЛМДФ)\s+timber\s*zone\s*$", r["name"] or "", re.I)]
    for fx in tz:
        typ = re.match(r"\s*(\S+)", fx["name"]).group(1).upper()
        top = con.execute("SELECT id FROM cat_nodes WHERE parent=0 AND svc=0 AND deleted=0 AND UPPER(name)=? ORDER BY (skey LIKE 'e:%') DESC LIMIT 1",
                          (typ,)).fetchone()
        tid = _node(con, "c1g:" + fx["ext"], "Timber zone", top["id"] if top else 0, 905, None if top else WH_PLATE, 0)
        if not top:
            con.execute("UPDATE cat_nodes SET name=? WHERE id=?", (f"{typ} Timber zone", tid))
        con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[5], tid))
        tz_copy(fx["ext"], tid)

    # ---------- 3д2) 1С «ХДФ» ичидаги «ЛХДФ Timber zone», «ЛХДФ Крафтвуд (ТАПОиЧ)», «ЛХДФ Эконом (местный)» — каталогдаги «ХДФ/ДВП»
    #   ичига папкаси билан; «ХДФ/ДВП Б/У» папкаси — 1С «ХДФ»даги Б/У товарлар (ДВП 3мм Поддон) (эгаси, 25.10)
    hdf = _top("ХДФ/ДВП")
    hx = next((r for r in kids.get("0", []) if r["folder"] and not r["deleted"] and (r["name"] or "").strip().upper() == "ХДФ"), None)
    if hdf and hx:
        for k0, (rx, sup) in enumerate(((r"timber\s*zone", SUPPLIERS[5]), (r"кр[аa]?фтвуд|craftwood", SUPPLIERS[6]), (r"эконом", None))):
            for fx in [r for r in kids.get(hx["ext"], []) if r["folder"] and not r["deleted"] and re.search(rx, r["name"] or "", re.I)]:
                rid = _node(con, "c1g:" + fx["ext"], re.sub(r"\s+", " ", fx["name"].strip()).replace("Крфтвуд", "Крафтвуд"), hdf["id"], 910 + k0, None, 0)
                if sup:
                    con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (sup, rid))
                c1_copy(fx["ext"], rid, "c1g:", same, sup, True)
        bu = [r for r in _walk(kids, hx["ext"]) if not r["folder"] and not r["deleted"] and re.search(r"б\s*/\s*у", r["name"] or "", re.I)]
        if bu:
            bid = _node(con, "x:hdf_bu", "ХДФ/ДВП Б/У", hdf["id"], 920, None, 0)
            for k, r in enumerate(sorted(bu, key=lambda r: r["name"] or "")):
                _item(con, "c1g:" + r["ext"], bid, re.sub(r"\s+", " ", (r["name"] or "").strip()), unit=r["unit"] or "", cur=r["cur"] or 2,
                      price=r["eff"] or None, svc=0, kind="good", sort=k)
                st["goods_1c"] += 1

    # ---------- 3е) Craftwood (Тапоич) — алоҳида етказиб берувчи: «Эконом ЛДСП/ЛМДФ» ичидан чиқиб, ЛДСП ва ЛМДФ ичида ўз папкаси
    for cw in con.execute("SELECT c.id, p.parent gp FROM cat_nodes c JOIN cat_nodes p ON p.id=c.parent WHERE c.deleted=0 AND c.svc=0 "
                          "AND COALESCE(c.manual,0)=0 AND (c.name LIKE '%CRAFTWOOD%' OR c.name LIKE '%Craftwood%' OR c.name LIKE '%Крафтвуд%') "
                          "AND p.name LIKE '%Эконом%'").fetchall():
        if cw["gp"]:
            con.execute("UPDATE cat_nodes SET parent=?, name='Craftwood', sort=904 WHERE id=?", (cw["gp"], cw["id"]))
    con.execute("UPDATE cat_nodes SET sup=? WHERE COALESCE(sup_manual,0)=0 AND svc=0 AND (name LIKE '%CRAFTWOOD%' OR name LIKE '%Craftwood%' OR name LIKE '%Крафтвуд%')",
                (SUPPLIERS[6],))

    # ---------- 3ж) GOLD кромка (тиллали, бозордан) — 1С «2 Кромка»даги GOLD: товар + ПВХ/Овал хизмати, нарх 1С'дан
    GOLD_SZ = ("19/1", "21/1", "35/1", "42/1")
    ek = con.execute("SELECT id FROM cat_nodes WHERE parent=0 AND svc=0 AND deleted=0 AND name='Кромка' ORDER BY (skey LIKE 'e:%') DESC LIMIT 1").fetchone()
    gold_items = {}
    if ek:
        gid = _node(con, "x:gold", "Кромка GOLD (бозор)", ek["id"], 990, None, 0)
        con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[4], gid))
        for k, sz in enumerate(GOLD_SZ):
            t = f"Кромка {sz} ПВХ GOLD"
            iid = _item(con, f"x:gold:{sz}", gid, t, unit="пог.м", cur=1, price=None, svc=0, kind="good", sort=k)
            gold_items[iid] = sz
            kromka.append((iid, t, ["Кромка", "Кромка GOLD (бозор)"]))

    # ---------- 3ж2) Маҳаллий кромка (бозор) — 1С'дан: «Кромка Местная (Базар)» → «Кромка Местная 19/1 (Базар)» → ранглар
    if ek and loc1c:
        lid = _node(con, "c1loc:root", "Кромка Местная (Базар)", ek["id"], 980, None, 0)
        con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[4], lid))
        for k, fx in enumerate(loc1c):
            fnm = re.sub(r"\s+", " ", (fx["name"] or "").strip())
            fid = _node(con, "c1g:" + fx["ext"], fnm, lid, k, None, 0)
            con.execute("UPDATE cat_nodes SET sup=? WHERE id=? AND COALESCE(sup_manual,0)=0", (SUPPLIERS[4], fid))
            for j, r in enumerate(sorted([x for x in kids.get(fx["ext"], []) if not x["folder"] and not x["deleted"]], key=lambda r: r["name"] or "")):
                t = re.sub(r"\s*\(n\)\s*$", "", re.sub(r"\s+", " ", (r["name"] or "").strip()))    # 1С'даги «(n)» белгиси — номда керак эмас
                iid = _item(con, "c1g:" + r["ext"], fid, t, unit="м", cur=1, svc=0, kind="good", sort=j)
                st["goods_1c"] += 1
                st["edge_local"] += 1
                kromka.append((iid, t, ["Кромка", "Кромка Местная (Базар)", fnm]))

    def gold_src(top_re, sz):
        """1С'даги GOLD хизмати (шу ўлчам): «2 Кромка» — ПВХ, «3 Кромка Овал» — овал."""
        top = _top_folder(rows, kids, top_re)
        if not top:
            return ""
        for r in _walk(kids, top):
            if not r["folder"] and re.search(r"\bgold\b|\bзолото\b", r["name"] or "", re.I) and re.search(re.escape(sz) + r"(?![\d,.])", r["name"] or "") \
                    and not re.search(r"голд\s*лайн|metallic|picasso|cashmere", r["name"] or "", re.I):
                return r["ext"]
        return ""

    # ---------- 4) Кромка хизматлари: Eman кромкаларидан нусха, нарх 1С'дан
    pidx, oidx = _pvc_index(rows, kids), _oval_index(rows, kids)
    for iid, title, path in kromka:
        brand = next((b for b in (brand_of(p) for p in reversed(path[1:])) if b), "") or brand_of(title)
        sz = size_of(title) or size_of(path[-1] if path else "")
        fin = fin_of(path[-1] if path else "") | fin_of(title)
        mat = mat_of(title) or next((m for m in (mat_of(p) for p in reversed(path[1:])) if m), "")
        sub = [p for p in path[1:]]                  # «Кромка» дан кейинги папкалар
        for root_id, pref, kind, idx, mf, gkey in ((pvc_root, "Услуга ПВХ", "svc_pvc", pidx, match_pvc, "g:pur"),
                                                   (oval_root, "Услуга Овал", "svc_oval", oidx, match_oval, "g:oval")):
            if kind == "svc_oval" and not oval_ok(sz):
                continue                              # 0,4–0,6 мм кромкани овал қилиб бўлмайди
            pid = root_id
            acc = []
            for p in sub:
                acc.append(p)
                pid = _node(con, f"{kind}:" + "/".join(acc), p.strip(), pid, 10, None, 1)    # бренд папкаси — Eman номи («Кромка Эггер»), «Услуга ПВХ» билан адашмасин
            nm = re.sub(r"^\s*Кромка(\s+ПВХ)?\b", pref, title.strip()) if re.match(r"\s*Кромка\b", title) else f"{pref} {title.strip()}"
            m = mf(idx, brand, sz, fin, mat)
            src = ("F:" + m if kind == "svc_pvc" else m) if m else ""
            sid = _item(con, f"{kind}:{iid}", pid, nm, unit="пог.м", cur=1, svc=1, kind=kind, base=iid, c1_src=src)
            if not src:                               # 1С'да мос нарх йўқ — эски (нотўғри) нарх қолмасин; қўлда ёзилгани тегилмайди
                con.execute("UPDATE cat_items SET price=NULL WHERE id=? AND COALESCE(manual,0)=0 AND COALESCE(src_manual,0)=0", (sid,))
            _norm(con, sid, iid, 1.0)
            if gkey and sz and not _manual_glue(con, sid):     # папкада қўлда белгиланган клей (бошқа тури ҳам) — тегилмайди
                _norm(con, sid, glue[gkey], glue_norm(sz[0], "eva" if kind == "svc_oval" else "pur"))
            st[kind] += 1
            if not src:
                st[kind + "_noprice"] += 1
    for iid, sz in gold_items.items():                # GOLD: нарх аниқ 1С хизматидан (ўлчам бўйича), топилмаса — қўлда
        for kind, top_re in (("svc_pvc", r"2\s"), ("svc_oval", r"3\s")):
            src = gold_src(top_re, sz)
            con.execute("UPDATE cat_items SET c1_src=? WHERE skey=? AND COALESCE(src_manual,0)=0", (src, f"{kind}:{iid}"))
            if not src:                               # 1С'да бу ўлчамдаги GOLD йўқ — нарх қўлда (бошқа кромка нархи олинмасин)
                con.execute("UPDATE cat_items SET price=NULL WHERE skey=? AND COALESCE(src_manual,0)=0 AND COALESCE(manual,0)=0", (f"{kind}:{iid}",))
    # юпқа кромканинг илгари яратилган овал хизматлари (ва бўш қолган папкалари) — ўчирилади
    con.execute("UPDATE cat_items SET deleted=1 WHERE kind='svc_oval' AND gone=1 AND manual=0")
    con.execute("UPDATE cat_nodes SET deleted=1 WHERE skey LIKE 'svc_oval:%' AND gone=1 AND manual=0")
    sync_prices(con, rows)
    con.commit()
    con.execute("INSERT INTO settings(key,value) VALUES('cat_built',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now(),))
    con.commit()
    return dict(st)


def sync_prices(con, rows=None):
    """Хизматлар нархи доим 1С'дан (c1_src бўйича)."""
    rows = rows if rows is not None else _c1_rows(con)
    n = 0
    for r in con.execute("SELECT id, c1_src, price, cur, unit FROM cat_items WHERE svc=1 AND c1_src<>''").fetchall():
        p = price_of(con, r["c1_src"], rows)
        if p and (p["price"] != r["price"] or p["cur"] != r["cur"]):
            con.execute("UPDATE cat_items SET price=?, cur=? WHERE id=?", (p["price"], p["cur"], r["id"]))
            n += 1
    return n


# ================================================================ ўқиш
def node_wh(con):
    """{node_id: склад} — юқори категориядан мерос."""
    nodes = {r["id"]: (r["parent"], r["wh"], r["svc"]) for r in con.execute("SELECT id, parent, wh, svc FROM cat_nodes")}
    out = {}

    def get(i, depth=0):
        if i in out:
            return out[i]
        p, w, s = nodes.get(i, (0, None, 0))
        v = w if w is not None else (get(p, depth + 1) if p and depth < 30 else None)
        out[i] = v
        return v
    for i in nodes:
        get(i)
    return out


def node_sup(con):
    """{node_id: етказиб берувчи}: юқори группада қўлда ёзилган бўлса — шу, бўлмаса ўзиники (Eman йўлидан)."""
    nodes = {r["id"]: (r["parent"], r["sup"], r["sup_manual"]) for r in con.execute("SELECT id, parent, sup, COALESCE(sup_manual,0) sup_manual FROM cat_nodes")}
    out = {}
    for i in nodes:
        p, own, x, d = i, nodes[i][1], None, 0
        while p and d < 30:
            pp, sp, mp = nodes.get(p, (0, None, 0))
            if mp and sp:
                x = sp
            p, d = pp, d + 1
        out[i] = x or own
    return out


def paths(con):
    nodes = {r["id"]: (r["parent"], r["name"]) for r in con.execute("SELECT id, parent, name FROM cat_nodes")}
    out = {}

    def get(i, d=0):
        if i in out:
            return out[i]
        p, n = nodes.get(i, (0, ""))
        v = ((get(p, d + 1) + " / ") if p and d < 30 else "") + (n or "")
        out[i] = v
        return v
    for i in nodes:
        get(i)
    return out


def item_dict(con, r, whm=None, fp=None, rows=None, supm=None):
    whm = whm if whm is not None else node_wh(con)
    supm = supm if supm is not None else node_sup(con)
    d = dict(id=r["id"], code=f"K{r['id']}", name=r["name"], unit=r["unit"] or "", cur=r["cur"] or 1, price=r["price"],
             price_eman=r["price_eman"], svc=bool(r["svc"]), kind=r["kind"], wh=None if r["svc"] else whm.get(r["node"]),
             node=r["node"], gone=bool(r["gone"]), manual=bool(r["manual"]), c1_src=r["c1_src"] or "", article=r["article"] or "",
             deleted=bool(r["deleted"]), sup="" if r["svc"] else (supm.get(r["node"]) or ""))
    if fp is not None:
        d["path"] = fp.get(r["node"], "")
    pl = not r["svc"] and whm.get(r["node"]) == WH_PLATE
    if pl and r["price_eman"]:                           # плита: сотув нархи — Eman нархи бугунги курсда $ да
        try:
            rate = A.fx_rate() if A else None
        except Exception:
            rate = None
        if rate:
            d["price"], d["cur"] = round(r["price_eman"] / rate, 4), 2
    if pl:
        g = con.execute("SELECT cstock, price, cur, unit FROM erp_goods WHERE ext=?", (f"ci{r['id']}",)).fetchone()
        d["eman"] = g[0] if g else None
        if g and g[1]:
            d["price"], d["cur"] = g[1], g[2]              # накладнойдаги нарх (Eman $ курсда / донага / переоценка)
        if g and r["price_eman"] and (r["unit"] or "").strip().casefold() in M2_UNITS and (g[3] or "") == "шт":
            d["unit"], d["m2"] = "шт", True                # донага ҳисобланган (Eman м² нархи × юза)
    if not r["svc"] and whm.get(r["node"]) == WH_EDGE:  # кромка — накладнойдаги нарх ва валюта ($, 28.10)
        g = con.execute("SELECT price, cur FROM erp_goods WHERE ext=?", (f"ci{r['id']}",)).fetchone()
        if g and g[1] == 2:
            d["price"], d["cur"] = (g[0] or None), 2
    if r["svc"]:
        d["src"] = src_name(con, r["c1_src"], rows) if r["c1_src"] else ""
    return d


# ================================================================ саҳифалар
# ================================================================ каталог → накладной товарлари (erp_goods, src='cat')
# Платформада ёзиладиган ҳужжатлар фақат шу каталогдан олинади. 1С товарлари (src='1c') — фақат 1С ҳужжатларини ўқиш учун.
# ext: папка «cn<id>», товар «ci<id>» (1С ext'лари катта ҳарф — тўқнашмайди).
# Нарх: Eman нархи (сўм) — бугунги курсда $ га (cur=2); Eman нархи йўқ хизматлар — ўз нархи/валютаси билан.
# cvis=1 — ҳамма фойдаланувчига кўринади (плита, хизматлар); 2 — қолган товарлар (кромка, фурнитура…) приходда ҳаммага; 0 — фақат админга; қолганлари фақат админга. cstock — Eman филиалларидаги жами қолдиқ.
VIS_WH = (WH_PLATE,)          # кромка — товар сифатида эмас, хизмат («Услуга ПВХ/Овал …») бўлиб кўринади
HIDE_RE = re.compile(r"^\s*реклам", re.I)


def goods_cols(con):
    cols = {r[1] for r in con.execute("PRAGMA table_info(erp_goods)")}
    for col, typ in (("cvis", "INTEGER DEFAULT 0"), ("cstock", "REAL"), ("cstock_at", "TEXT"), ("eman_sum", "REAL"), ("msort", "INTEGER DEFAULT 0"),
                     ("disc", "INTEGER DEFAULT 0")):
        if col not in cols:
            con.execute(f"ALTER TABLE erp_goods ADD COLUMN {col} {typ}")


def disc_maps(con):
    """Скидка мумкинми (эгаси, 25.10: листларнинг айримларига ва хизматларга): товарда белгиланган бўлса — шу, бўлмаса энг яқин
    юқори папкадагиси, ҳеч қаерда бўлмаса — йўқ. -> ({node: 0/1}, {item: 0/1})"""
    try:
        nodes = {r[0]: (r[1], r[2]) for r in con.execute("SELECT id, parent, disc FROM cat_nodes")}
    except Exception:
        return {}, {}
    ne = {}

    def eff(nid, depth=0):
        if nid in ne:
            return ne[nid]
        p, d = nodes.get(nid, (0, None))
        v = d if d is not None else (eff(p, depth + 1) if p and p in nodes and depth < 40 else 0)
        ne[nid] = 1 if v else 0
        return ne[nid]
    for n in nodes:
        eff(n)
    ie = {}
    for iid, node, d in con.execute("SELECT id, node, disc FROM cat_items"):
        ie[iid] = (1 if d else 0) if d is not None else ne.get(node, 0)
    return ne, ie


def disc_set(con, fid=None, items=None, value=None):
    """Скидка белгиси: папка (ичидагилари билан — уларнинг ўз белгиси олиб ташланади) ёки товарлар. value: 1 / 0 / None (папкадан)."""
    v = None if value is None else (1 if value else 0)
    n = 0
    if fid:
        nodes = _subtree_nodes(con, fid)
        ph = ",".join("?" * len(nodes))
        con.execute("UPDATE cat_nodes SET disc=? WHERE id=?", (v, fid))
        con.execute(f"UPDATE cat_nodes SET disc=NULL WHERE id IN ({ph}) AND id<>?", (*nodes, fid))
        n = con.execute(f"UPDATE cat_items SET disc=NULL WHERE node IN ({ph}) AND deleted=0", nodes).rowcount
        n = con.execute(f"SELECT COUNT(*) FROM cat_items WHERE node IN ({ph}) AND deleted=0", nodes).fetchone()[0]
    for iid in items or []:
        n += con.execute("UPDATE cat_items SET disc=? WHERE id=?", (v, int(iid))).rowcount
    return n


def eman_stock(con):
    """{cat_items.skey: (жами қолдиқ, {филиал: сон}, нарх сўм, вақт)} — Eman ботидан олинган охирги маълумот."""
    out = {}
    try:
        rows = con.execute("SELECT n.key, g.title, g.stock, g.price, g.at FROM eman_goods g JOIN eman_nodes n ON n.id=g.node WHERE g.ok=1").fetchall()
    except Exception:
        return out
    import json as _j
    for r in rows:
        try:
            st = _j.loads(r["stock"] or "{}") or {}
        except ValueError:
            st = {}
        tot = sum(v for v in st.values() if isinstance(v, (int, float)))
        out[f"e:{r['key']}|{r['title']}"] = (tot, st, r["price"], r["at"])
    return out


M2_UNITS = {"м2", "м²", "кв.м", "кв м", "m2", "кв.м."}


def piece_area(texts):
    """Бир дона юзаси (м²) — номдан, бўлмаса папкалар номидан (пастдан юқорига): «3050x600», «4,2*0,6*38», «4,2*600*38мм». None — топилмади."""
    for t in texts:
        t = (t or "").replace("х", "x").replace("Х", "x").replace("×", "x")
        m = re.search(r"(?<![\d.,])(\d{3,4})\s*[*x]\s*(\d{3,4})(?![\d.,])", t)
        if m and int(m.group(1)) >= 300 and int(m.group(2)) >= 100:
            return round(int(m.group(1)) * int(m.group(2)) / 1e6, 4)
        m = re.search(r"(\d[.,]\d+)\s*[*x]\s*(\d[.,]\d+)", t)
        if m:
            a, b = float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", "."))
            if 1 <= a <= 6 and 0.1 <= b <= 2:
                return round(a * b, 4)
        m = re.search(r"(\d[.,]\d+)\s*[*x]\s*(\d{3,4})(?![\d.,])", t)
        if m:
            a, b = float(m.group(1).replace(",", ".")), int(m.group(2)) / 1000
            if 1 <= a <= 6 and 0.1 <= b <= 2:
                return round(a * b, 4)
    return None


def mirror(con, rate=None):
    """Каталогни накладной товарларига кўчириш (янгилаш). -> сони."""
    goods_cols(con)
    if not rate:
        r = con.execute("SELECT rate FROM fx_rates ORDER BY id DESC LIMIT 1").fetchone()
        rate = r[0] if r else 0
    nodes = {r["id"]: dict(r) for r in con.execute("SELECT id, parent, name, wh, svc, deleted, gone FROM cat_nodes")}

    def top(nid):
        seen = 0
        while nid in nodes and nodes[nid]["parent"] and nodes[nid]["parent"] in nodes and seen < 40:
            nid = nodes[nid]["parent"]; seen += 1
        return nodes.get(nid)

    def edge_top(nid):
        t = top(nid)
        return bool(t) and (t["name"] or "").strip().casefold() == "кромка" and t["wh"] == WH_EDGE

    def hidden(nid):
        """«Реклама» (Eman) ва шунга ўхшаш папкалар — фақат админга."""
        seen = 0
        while nid in nodes and seen < 40:
            if HIDE_RE.search(nodes[nid]["name"] or ""):
                return True
            nid = nodes[nid]["parent"]; seen += 1
        return False

    def plate(nid):
        t = top(nid)
        return bool(t) and not t["svc"] and t["wh"] == WH_PLATE

    def vis(nid):
        t = top(nid)
        if not t or hidden(nid):
            return 0
        return 1 if (t["svc"] or t["wh"] in VIS_WH) else 2     # 2 — кромка, фурнитура…: приход/инвентаризацияда ҳаммага (28.10), сотувда — фақат админга

    def dead(nid):
        seen = 0
        while nid in nodes and seen < 40:
            if nodes[nid]["deleted"] or nodes[nid]["gone"]:
                return True
            nid = nodes[nid]["parent"]; seen += 1
        return False
    def npath(nid):
        out, seen = [], 0
        while nid in nodes and seen < 40:
            out.append(nodes[nid]["name"] or ""); nid = nodes[nid]["parent"]; seen += 1
        return list(reversed(out))
    at = now()
    frows = []
    for n in nodes.values():
        frows.append((f"cn{n['id']}", "", (n["name"] or "").strip(), f"cn{n['parent']}" if n["parent"] in nodes else "", 1 if dead(n["id"]) else 0,
                      lc(n["name"]), "svc" if top(n["id"]) and top(n["id"])["svc"] else "good", vis(n["id"])))
    con.executemany("INSERT INTO erp_goods(ext,code,name,parent,folder,deleted,lc,cat,src,cvis,unit,cur,price) VALUES(?,?,?,?,1,?,?,?,'cat',?,'',2,0) "
                    "ON CONFLICT(ext) DO UPDATE SET name=excluded.name,parent=excluded.parent,deleted=excluded.deleted,lc=excluded.lc,"
                    "cat=excluded.cat,cvis=excluded.cvis WHERE erp_goods.src='cat'", frows)
    es = eman_stock(con)
    rv = {}                                                # переоценка (платформада проведен) — каталог товари нархи қўлда белгиланган
    try:
        for g, p in con.execute("SELECT l.good, l.price FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.src='pf' AND d.kind='reval' "
                                "AND d.status='posted' AND l.good LIKE 'ci%' ORDER BY d.date, d.time, d.id, l.lineno"):
            if p and p > 0:
                rv[g] = p
    except Exception:
        rv = {}
    irows = []
    _, dmap = disc_maps(con)
    for r in con.execute("SELECT id, node, name, article, unit, cur, price, price_eman, svc, skey, deleted, gone FROM cat_items"):
        pl = not r["svc"] and plate(r["node"])
        e = es.get(r["skey"] or "") if pl else None        # Eman нархи ва қолдиғи — фақат плитали материалларда
        pe = ((e[2] if e and e[2] else None) or r["price_eman"] or 0) if pl else 0   # ботдаги энг охирги нарх (кунлик текширувдан)
        unit = r["unit"] or ""
        if pe > 0 and rate:
            cur, price = 2, round(pe / rate, 4)            # Eman нархи — сотув нархи, $ да (бугунги курс)
            if unit.strip().casefold() in M2_UNITS:       # Eman м² нархи (столешница …) — биз донада сотамиз: × бир дона юзаси
                ar = piece_area([r["name"]] + list(reversed(npath(r["node"]))))
                if ar:
                    price, unit = round(pe * ar / rate, 4), "шт"
        elif not r["svc"] and edge_top(r["node"]):       # 28.10 (эгаси): кромка товарлари — долларда (приход $ да); нарх: қўлда ёзилгани,
            if r["price"]:                                #   бўлмаса Eman нархи бугунги курсда
                cur, price = 2, (r["price"] if (r["cur"] or 1) == 2 else (round(r["price"] / rate, 4) if rate else 0))
            else:
                cur, price = 2, (round(r["price_eman"] / rate, 4) if (r["price_eman"] and rate) else 0)
        else:
            cur, price = (r["cur"] or 1), (r["price"] or 0)     # қолган товарлар — қўлда ёзилган нарх
        if f"ci{r['id']}" in rv:                          # переоценка устун: сегмент бўйича белгиланган нарх (Eman нархидан ҳам)
            price = rv[f"ci{r['id']}"]
            if pl:
                cur = 2
        np_ = " / ".join(npath(r["node"]))
        ms = 1 if (not r["svc"] and re.match(r"\s*ЛМДФ\b", np_, re.I) and re.search(r"эконом|craftwood|крафтвуд|timber|узб", np_, re.I)) else 0
        irows.append((f"ci{r['id']}", r["article"] or "", (r["name"] or "").strip(), unit, cur, price, f"cn{r['node']}",
                      1 if (r["deleted"] or r["gone"] or dead(r["node"])) else 0, lc((r["name"] or "") + " " + (r["article"] or "")),
                      "svc" if r["svc"] else "good", vis(r["node"]), e[0] if e else None, e[3] if e else None, pe or None, ms, dmap.get(r["id"], 0)))
    con.executemany("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,cat,src,cvis,cstock,cstock_at,eman_sum,msort,disc,price_in) "
                    "VALUES(?,?,?,?,?,?,?,0,?,?,?,'cat',?,?,?,?,?,?,0) "
                    "ON CONFLICT(ext) DO UPDATE SET code=excluded.code,name=excluded.name,unit=excluded.unit,cur=excluded.cur,price=excluded.price,"
                    "parent=excluded.parent,deleted=excluded.deleted,lc=excluded.lc,cat=excluded.cat,cvis=excluded.cvis,cstock=excluded.cstock,"
                    "cstock_at=excluded.cstock_at,eman_sum=excluded.eman_sum,msort=excluded.msort,disc=excluded.disc WHERE erp_goods.src='cat'", irows)
    con.execute("INSERT INTO settings(key,value) VALUES('cat_mirror',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (f"{rate}|{at}",))
    return len(irows)


# ================================================================ папка бўйича норматив (эгаси, 25.10: ўнг тугма → «Норматив папки»)
def _subtree_nodes(con, fid):
    out, stk = [], [fid]
    while stk:
        x = stk.pop()
        out.append(x)
        stk += [r[0] for r in con.execute("SELECT id FROM cat_nodes WHERE parent=? AND deleted=0", (x,))]
    return out


def glue_items(con):
    """Клей товарлари: «Клей» папкасидагилар (g:glue)."""
    return [dict(r) for r in con.execute(
        "SELECT i.id, i.name, i.unit FROM cat_items i JOIN cat_nodes n ON n.id=i.node WHERE n.skey='g:glue' AND i.deleted=0 AND COALESCE(i.svc,0)=0 "
        "ORDER BY i.id")]


def folder_norm_info(con, fid):
    """-> dict(n, glues, cur_glue, qmin, qmax, rate)"""
    nodes = _subtree_nodes(con, fid)
    ph = ",".join("?" * len(nodes))
    items = [r[0] for r in con.execute(f"SELECT id FROM cat_items WHERE node IN ({ph}) AND svc=1 AND deleted=0 AND COALESCE(gone,0)=0", nodes)]
    gl = glue_items(con)
    gids = {g["id"] for g in gl}
    cur, qs = {}, []
    if items:
        ph2 = ",".join("?" * len(items))
        for r in con.execute(f"SELECT comp, qty FROM cat_norms WHERE item IN ({ph2})", items):
            if r["comp"] in gids:
                cur[r["comp"]] = cur.get(r["comp"], 0) + 1
                qs.append(r["qty"] or 0)
    top = max(cur, key=cur.get) if cur else (gl[0]["id"] if gl else None)
    kind = "eva" if top and any(g["id"] == top and re.search(r"овал|гранул", g["name"], re.I) for g in gl) else "pur"
    return dict(n=len(items), glues=gl, cur_glue=top, qmin=min(qs) if qs else None, qmax=max(qs) if qs else None,
                rate=_glue_rate(kind), with_glue=len(qs))


def folder_norm_apply(con, fid, glue, mode, value):
    """Папкадаги (ички папкалар билан) ҳамма хизмат: клей нормативи алмашади (кромка 1:1 — тегилмайди). auto=0 — каталог
    қайта қурилганда сақланади. mode: 'fixed' (кг/пог.м) | 'width' (кг/м² × (эни − 3 мм, ≥ 8 мм)). -> n"""
    gids = {g["id"] for g in glue_items(con)}
    if glue not in gids:
        raise ValueError("Клей танланмаган")
    if not (0 <= value <= (5 if mode == "width" else 0.1)):
        raise ValueError("Миқдор нотўғри")
    nodes = _subtree_nodes(con, fid)
    ph = ",".join("?" * len(nodes))
    n = 0
    for it in con.execute(f"SELECT id, name FROM cat_items WHERE node IN ({ph}) AND svc=1 AND deleted=0", nodes).fetchall():
        if mode == "width":
            sz = size_of(it["name"])
            try:
                t = max(int(sz[0]) - 3, 8) / 1000.0 if sz else 0.016
            except (TypeError, ValueError):
                t = 0.016
            q = round(value * t, 5)
        else:
            q = round(value, 5)
        gp = ",".join("?" * len(gids))
        con.execute(f"DELETE FROM cat_norms WHERE item=? AND comp IN ({gp})", (it["id"], *gids))
        con.execute("INSERT INTO cat_norms(item, comp, qty, auto) VALUES(?,?,?,0)", (it["id"], glue, q))   # 0 ҳам сақланади (қўлда)
        n += 1
    return n


def folder_price_apply(con, fid, price):
    """Папкадаги (ички папкалар билан) ҳамма хизматга битта нарх (сўм / пог.м). 1С'дан нарх олиш ўчади (src_manual=1) —
    каталог қайта қурилганда ҳам сақланади. -> n"""
    if not (0 < price < 10_000_000):
        raise ValueError("Нарх нотўғри")
    nodes = _subtree_nodes(con, fid)
    if not nodes:
        raise ValueError("Топилмади")
    ph = ",".join("?" * len(nodes))
    return con.execute(f"UPDATE cat_items SET price=?, cur=1, c1_src='', src_manual=1, at=? WHERE node IN ({ph}) AND svc=1 AND deleted=0",
                       (price, now(), *nodes)).rowcount


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init(con); con.commit()
    finally:
        con.close()
    lr = A.login_required
    _orig = A.scheduler_tick

    def tick(dt=None):
        """Eman юклаб бўлингач — каталог автомат янгиланади; хизмат нархлари ҳар соатда 1С'дан."""
        r = _orig(dt)
        try:
            c2 = A.connect()
            try:
                init(c2)
                g = lambda k: (c2.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone() or [""])[0] or ""
                fin = (c2.execute("SELECT value FROM eman_kv WHERE key='finished'").fetchone() or [""])[0] or ""
                built = g("cat_built")
                if not built or (fin and fin > built) or g("cat_ver") != CAT_VER:
                    build(c2)
                    c2.execute("INSERT INTO settings(key,value) VALUES('cat_ver',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (CAT_VER,))
                    c2.commit()
                elif g("cat_px") < now()[:13]:
                    sync_prices(c2)
                    c2.execute("INSERT INTO settings(key,value) VALUES('cat_px',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                               (now()[:13],))
                    c2.commit()
                mm = g("cat_mirror")
                rt = (c2.execute("SELECT rate FROM fx_rates ORDER BY id DESC LIMIT 1").fetchone() or [0])[0]
                from datetime import timedelta as _td
                old_t = (datetime.now() - _td(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
                mp = mm.split("|") if mm else ["", ""]
                if not mm or mp[0] != str(rt) or mp[1] < old_t or g("cat_built") > mp[1]:
                    mirror(c2, rt)
                    c2.commit()
            finally:
                c2.close()
        except Exception:
            A.errlog.exception("catalog tick")
        return r
    A.scheduler_tick = tick

    def _edit_ok():
        return A.can("super")

    def _all_vis():
        u = A.current_user()
        return bool(u) and u["role"] == "admin"

    _vc = {}

    def _node_vis(con, nid, whm):
        """Админ бўлмаганга каталогда фақат хизматлар ва плитали материаллар (кромка — хизмат бўлиб; «Реклама» — йўқ)."""
        if nid in _vc:
            return _vc[nid]
        n, seen, svc, hid = nid, 0, False, False
        while n and seen < 40:
            r = con.execute("SELECT parent, svc, name FROM cat_nodes WHERE id=?", (n,)).fetchone()
            if not r:
                break
            svc = svc or bool(r["svc"])
            hid = hid or bool(HIDE_RE.search(r["name"] or ""))
            n = r["parent"]; seen += 1
        v = not hid and (svc or whm.get(nid) in VIS_WH)
        _vc[nid] = v
        return v

    @app.route("/erp/catalog")
    @lr
    def erp_catalog():
        con = A.db()
        init(con)
        allv = _all_vis()
        wh = [dict(r) for r in con.execute("SELECT * FROM cat_wh ORDER BY sort") if allv or r["id"] in VIS_WH]   # фойдаланувчига: фақат услуги ва плитные
        n = con.execute("SELECT SUM(svc=0), SUM(svc=1) FROM cat_items WHERE deleted=0 AND COALESCE(gone,0)=0").fetchone()
        return render_template("erp_catalog.html", wh=wh, allv=allv, sups=SUPPLIERS, ngoods=n[0] or 0, nsvc=n[1] or 0, edit=_edit_ok(),
                               built=A.get_setting("cat_built", ""), pick=request.args.get("pick") == "1")

    @app.route("/erp/api/cat/tree")
    @lr
    def erp_api_cat_tree():
        con = A.db()
        _vc.clear()
        whm = node_wh(con)
        supm = node_sup(con)
        cnt = dict(con.execute("SELECT node, COUNT(*) FROM cat_items WHERE deleted=0 AND COALESCE(gone,0)=0 GROUP BY node").fetchall())
        allv = _all_vis()
        dn, _ = disc_maps(con)
        out = [dict(id=r["id"], name=r["name"], parent=r["parent"] or 0, n=cnt.get(r["id"], 0), svc=bool(r["svc"]), wh=whm.get(r["id"]),
                    own_wh=r["wh"], gone=bool(r["gone"]), sup=supm.get(r["id"]) or "", own_sup=r["sup"] if r["sup_manual"] else "",
                    disc=dn.get(r["id"], 0), own_disc=r["disc"])
               for r in con.execute("SELECT * FROM cat_nodes WHERE deleted=0 AND COALESCE(gone,0)=0 ORDER BY svc DESC, sort, name")
               if allv or _node_vis(con, r["id"], whm)]
        return jsonify(out)

    @app.route("/erp/api/cat/list")
    @lr
    def erp_api_cat_list():
        con = A.db()
        f = int(request.args.get("f") or 0)
        qs = (request.args.get("q") or "").strip()
        w = request.args.get("wh") or ""
        whm = node_wh(con)
        rows = _c1_rows(con)
        fp = paths(con)
        if qs or w:
            sql, args = "SELECT * FROM cat_items WHERE deleted=0 AND COALESCE(gone,0)=0", []
            import kb
            gs = []
            for v in kb.variants(qs):
                ws = [x for x in lc(v).split() if x]
                if ws:
                    gs.append("(" + " AND ".join("lc LIKE ?" for _ in ws) + ")")
                    args += [f"% {word}%" if len(word) > 2 else f"%{word}%" for word in ws]
            if gs:
                sql += " AND (" + " OR ".join(gs) + ")"
            if w == "svc":
                sql += " AND svc=1"
            elif w:                                       # склад бўйича — SQL'да (олдин 2000 та хизматдан кейин товар қолмасди)
                ns = [n for n, v in whm.items() if str(v) == w]
                sql += f" AND svc=0 AND node IN ({','.join('?' * len(ns)) or 'NULL'})"
                args += ns
            items = con.execute(sql + " ORDER BY svc DESC, name LIMIT 2000", args).fetchall()
            if w == "svc":
                items = [r for r in items if r["svc"]]
            elif w:
                items = [r for r in items if not r["svc"] and str(whm.get(r["node"])) == w]
            items = items[:800]
            folders = []
        else:
            folders = con.execute("SELECT * FROM cat_nodes WHERE deleted=0 AND COALESCE(gone,0)=0 AND parent=? ORDER BY svc DESC, sort, name", (f,)).fetchall()
            items = con.execute("SELECT * FROM cat_items WHERE deleted=0 AND COALESCE(gone,0)=0 AND node=? ORDER BY sort, name", (f,)).fetchall()
        if not _all_vis():
            folders = [r for r in folders if _node_vis(con, r["id"], whm)]
            items = [r for r in items if _node_vis(con, r["node"], whm)]
        cnt = dict(con.execute("SELECT node, COUNT(*) FROM cat_items WHERE deleted=0 AND COALESCE(gone,0)=0 GROUP BY node").fetchall())
        out = [dict(id=r["id"], folder=True, name=r["name"], svc=bool(r["svc"]), wh=whm.get(r["id"]), n=cnt.get(r["id"], 0),
                    gone=bool(r["gone"])) for r in folders]
        supm = node_sup(con)
        out += [dict(item_dict(con, r, whm, fp if (qs or w) else None, rows, supm), folder=False) for r in items]
        dn, di = disc_maps(con)
        for o in out:
            o["disc"] = (dn if o["folder"] else di).get(o["id"], 0)
        par = con.execute("SELECT parent FROM cat_nodes WHERE id=?", (f,)).fetchone() if f else None
        return jsonify(dict(items=out, path=fp.get(f, "") if f else "", parent=(par[0] if par else None)))

    @app.route("/erp/api/cat/fnorm/<int:fid>")
    @lr
    def erp_api_cat_fnorm(fid):
        if not _edit_ok():
            return jsonify(error="Бу ҳаракатга доступ йўқ. Рухсатни админ беради."), 403
        con = A.db()
        r = con.execute("SELECT name, svc FROM cat_nodes WHERE id=?", (fid,)).fetchone()
        if not r or not r["svc"]:
            return jsonify(error="Только для папок услуг"), 400
        return jsonify(dict(folder_norm_info(con, fid), name=r["name"]))

    @app.route("/erp/api/cat/item/<int:iid>")
    @lr
    def erp_api_cat_item(iid):
        con = A.db()
        r = con.execute("SELECT * FROM cat_items WHERE id=?", (iid,)).fetchone()
        if not r:
            abort(404)
        d = item_dict(con, r, fp=paths(con))
        if not r["svc"]:
            try:
                import erp as _E
                d["stock"] = round(_E.stock_all(con).get(f"ci{iid}") or 0, 3)     # омбордаги қолдиқ (приход − накладной ± инвентаризация)
            except Exception:
                d["stock"] = None
            try:                                                                  # кусок: бот омборидаги донлар (РайГаз / мижозлар)
                import raspil as _P
                kc = _P.kusok_catalog(con).get(f"ci{iid}")
                if kc or f"ci{iid}" in _E._kusok_goods(con):
                    d["kusok"] = kc or dict(m2=0, n=0, own=[], cli=[], res=[])
            except Exception:
                pass
        d["norms"] = [dict(comp=x["comp"], code=f"K{x['comp']}", name=x["name"], unit=x["unit"], qty=x["qty"], auto=bool(x["auto"]))
                      for x in con.execute("SELECT n.*, i.name, i.unit FROM cat_norms n JOIN cat_items i ON i.id=n.comp WHERE n.item=?", (iid,))]
        if r["base"]:
            b = con.execute("SELECT id, name FROM cat_items WHERE id=?", (r["base"],)).fetchone()
            d["base"] = dict(id=b["id"], name=b["name"]) if b else None
        return jsonify(d)

    @app.route("/erp/api/cat/c1src")
    @lr
    def erp_api_cat_c1src():
        """Хизмат нархи манбаи учун 1С хизматлари ва папкаларини қидириш."""
        con = A.db()
        qs = (request.args.get("q") or "").strip()
        rows = _c1_rows(con)
        words = [w for w in lc(qs).split() if w]
        out = []
        for r in rows.values():
            if r["deleted"]:
                continue
            l = lc(r["name"])
            if words and all(w in l for w in words):
                p = price_of(con, ("F:" if r["folder"] else "") + r["ext"], rows)
                out.append(dict(src=("F:" if r["folder"] else "") + r["ext"], name=("📁 " if r["folder"] else "") + (r["name"] or ""),
                                price=p["price"] if p else None, cur=p["cur"] if p else 1))
            if len(out) >= 40:
                break
        return jsonify(out)

    @app.after_request
    def _remirror(resp):
        """Каталог қўлда ўзгартирилса — накладной товарлари дарров янгиланади."""
        try:
            if request.endpoint == "erp_api_cat_save" and resp.status_code == 200:
                con = A.db()
                mirror(con)
                con.commit()
        except Exception:
            A.errlog.exception("catalog mirror")
        return resp

    @app.route("/erp/api/cat/quick", methods=["POST"])
    @lr
    def erp_api_cat_quick():
        """Товар танлаш ойнасидан янги товар (эгаси, 26.10): «қўшиш» рухсати билан, танланган каталог папкасига. -> {ext}"""
        if not A.can("add"):
            return jsonify(error="Бу ҳаракатга доступ йўқ. Рухсатни админ беради."), 403
        con = A.db()
        j = request.get_json(force=True) or {}
        try:
            eid = str(j.get("id") or "")
            if eid:                                       # F2: номи/бирлиги/нархи (эгаси, 26.10: кассир товар номини ўзгартира олмаяпти)
                iid = int(eid[2:]) if eid.startswith("ci") and eid[2:].isdigit() else 0
                r = con.execute("SELECT * FROM cat_items WHERE id=? AND deleted=0", (iid,)).fetchone() if iid else None
                if not r:
                    raise ValueError("Товар топилмади")
                name = re.sub(r"\s+", " ", (j.get("name") or "")).strip()
                if len(name) < 3:
                    raise ValueError("Номини ёзинг")
                cur = 2 if str(j.get("cur")) == "2" else 1
                pr = str(j.get("price") or "").replace(" ", "").replace(",", ".")
                price = float(pr) if pr else None
                if price is not None and price < 0:
                    raise ValueError("Нарх манфий бўлмайди")
                con.execute("UPDATE cat_items SET name=?, lc=?, unit=?, cur=?, manual=1, at=? WHERE id=?",
                            (name, lc(name), (j.get("unit") or "").strip()[:20], cur, now(), iid))
                if price is not None and (price != r["price"] or cur != r["cur"]):
                    con.execute("UPDATE cat_items SET price=?, src_manual=1, c1_src=CASE WHEN svc=1 THEN '' ELSE c1_src END WHERE id=?", (price, iid))
                try:
                    import erp as _E
                    _E.log(con, None, "cat_edit", f"ci{iid}: {r['name']} -> {name}" + (f", нарх {price}" if price is not None else ""))
                except Exception:
                    pass
                sync_prices(con)
                mirror(con)
                con.commit()
                return jsonify(ok=True, ext=f"ci{iid}", existed=True)
            f = str(j.get("folder") or "")
            node = int(f[2:]) if f.startswith("cn") and f[2:].isdigit() else 0
            nd = con.execute("SELECT id, svc FROM cat_nodes WHERE id=?", (node,)).fetchone() if node else None
            if not nd:
                raise ValueError("Аввал чапдан папкани танланг")
            name = re.sub(r"\s+", " ", (j.get("name") or "")).strip()
            if len(name) < 3:
                raise ValueError("Номини ёзинг")
            cur = 2 if str(j.get("cur")) == "2" else 1
            pr = str(j.get("price") or "").replace(" ", "").replace(",", ".")
            price = float(pr) if pr else None
            if price is not None and price < 0:
                raise ValueError("Нарх манфий бўлмайди")
            ex = con.execute("SELECT id FROM cat_items WHERE node=? AND deleted=0 AND (name=? OR lc=?)", (node, name, lc(name))).fetchone()
            if ex:
                iid = ex["id"]
            else:
                svc = 1 if nd["svc"] else 0
                iid = con.execute("INSERT INTO cat_items(node,name,lc,unit,cur,price,svc,kind,skey,c1_src,manual,src_manual,article,at) "
                                  "VALUES(?,?,?,?,?,?,?,?,?,'',1,1,'',?)",
                                  (node, name, lc(name), (j.get("unit") or "").strip()[:20], cur, price, svc, "svc" if svc else "good",
                                   f"m:{datetime.now().timestamp()}", now())).lastrowid
                try:
                    import erp as _E
                    _E.log(con, None, "cat_add", f"ci{iid} {name} (танлаш ойнасидан)")
                except Exception:
                    pass
            sync_prices(con)
            mirror(con)
            con.commit()
            return jsonify(ok=True, ext=f"ci{iid}", existed=bool(ex))
        except ValueError as e:
            con.rollback()
            return jsonify(error=str(e)), 400

    @app.route("/erp/api/cat/save", methods=["POST"])
    @lr
    def erp_api_cat_save():
        if not _edit_ok():
            return jsonify(error="Бу ҳаракатга доступ йўқ. Рухсатни админ беради."), 403
        con = A.db()
        j = request.get_json(force=True) or {}
        act = j.get("act")
        try:
            if act == "item":
                iid = int(j.get("id") or 0)
                name = (j.get("name") or "").strip()
                if not name:
                    raise ValueError("Номини ёзинг")
                cur = 2 if str(j.get("cur")) == "2" else 1
                price = j.get("price")
                price = float(str(price).replace(" ", "").replace(",", ".")) if str(price or "").strip() else None
                src = (j.get("c1_src") or "").strip()
                if iid:
                    r = con.execute("SELECT * FROM cat_items WHERE id=?", (iid,)).fetchone()
                    if not r:
                        raise ValueError("Топилмади")
                    con.execute("UPDATE cat_items SET name=?, lc=?, unit=?, cur=?, article=?, manual=1, at=? WHERE id=?",
                                (name, lc(name), (j.get("unit") or "").strip(), cur, (j.get("article") or "").strip(), now(), iid))
                    if r["svc"]:
                        if src != (r["c1_src"] or ""):
                            con.execute("UPDATE cat_items SET c1_src=?, src_manual=1 WHERE id=?", (src, iid))
                        if not src:
                            con.execute("UPDATE cat_items SET price=? WHERE id=?", (price, iid))
                    else:
                        con.execute("UPDATE cat_items SET price=? WHERE id=?", (price, iid))
                else:
                    node = int(j.get("node") or 0)
                    nd = con.execute("SELECT svc FROM cat_nodes WHERE id=?", (node,)).fetchone()
                    if not nd:
                        raise ValueError("Аввал папкани танланг")
                    svc = 1 if nd["svc"] else 0
                    iid = con.execute("INSERT INTO cat_items(node,name,lc,unit,cur,price,svc,kind,skey,c1_src,manual,src_manual,article,at) "
                                      "VALUES(?,?,?,?,?,?,?,?,?,?,1,1,?,?)",
                                      (node, name, lc(name), (j.get("unit") or "").strip(), cur, price, svc, "svc" if svc else "good",
                                       f"m:{datetime.now().timestamp()}", src if svc else "", (j.get("article") or "").strip(), now())).lastrowid
                sync_prices(con)
                con.commit()
                return jsonify(ok=True, id=iid)
            if act == "folder":
                fid = int(j.get("id") or 0)
                name = (j.get("name") or "").strip()
                if not name:
                    raise ValueError("Номини ёзинг")
                if fid:
                    con.execute("UPDATE cat_nodes SET name=?, manual=1 WHERE id=?", (name, fid))
                else:
                    par = int(j.get("parent") or 0)
                    svc = 0
                    if par:
                        p = con.execute("SELECT svc FROM cat_nodes WHERE id=?", (par,)).fetchone()
                        svc = p["svc"] if p else 0
                    fid = con.execute("INSERT INTO cat_nodes(parent,name,sort,svc,skey,manual,wh) VALUES(?,?,?,?,?,1,?)",
                                      (par, name, 5000, svc, f"m:{datetime.now().timestamp()}",
                                       (int(j["wh"]) if (not par and not svc and j.get("wh")) else None))).lastrowid
                con.commit()
                return jsonify(ok=True, id=fid)
            if act == "sup":
                fid = int(j.get("id") or 0)
                v = (j.get("sup") or "").strip()[:120] or None
                con.execute("UPDATE cat_nodes SET sup=?, sup_manual=? WHERE id=? AND svc=0", (v, 1 if v else 0, fid))
                con.commit()
                return jsonify(ok=True)
            if act == "wh":
                fid = int(j.get("id") or 0)
                w = int(j.get("wh") or 0) or None
                con.execute("UPDATE cat_nodes SET wh=? WHERE id=? AND svc=0", (w, fid))
                con.commit()
                return jsonify(ok=True)
            if act == "del":
                ids = [int(x) for x in j.get("items") or []]
                fids = [int(x) for x in j.get("folders") or []]
                for i in ids:
                    con.execute("UPDATE cat_items SET deleted=1, manual=1 WHERE id=?", (i,))
                for f in fids:                              # папка ичидагилари билан (эгаси, 24.10): ҳаммаси «помечен на удаление»,
                    stk, seen = [f], set()                  # manual=1 — каталог қайта қурилганда тикланмайди
                    while stk:
                        x = stk.pop()
                        if x in seen:
                            continue
                        seen.add(x)
                        stk += [r[0] for r in con.execute("SELECT id FROM cat_nodes WHERE parent=? AND deleted=0", (x,))]
                        con.execute("UPDATE cat_items SET deleted=1, manual=1 WHERE node=? AND deleted=0", (x,))
                        con.execute("UPDATE cat_nodes SET deleted=1, manual=1 WHERE id=?", (x,))
                con.commit()
                return jsonify(ok=True)
            if act == "move":                             # товар ёки папкани бошқа папкага (эгаси, 26.10); manual=1 — қайта қурилганда жойида қолади
                to = int(j.get("to") or 0)
                tn = con.execute("SELECT id, svc FROM cat_nodes WHERE id=? AND deleted=0", (to,)).fetchone() if to else None
                if to and not tn:
                    raise ValueError("Папка топилмади")
                n = 0
                for i in [int(x) for x in j.get("items") or []]:
                    r = con.execute("SELECT id, svc FROM cat_items WHERE id=? AND deleted=0", (i,)).fetchone()
                    if not r:
                        continue
                    if not tn:
                        raise ValueError("Товарни папкага кўчириш керак")
                    if bool(r["svc"]) != bool(tn["svc"]):
                        raise ValueError("Хизматни товар папкасига (ёки товарни хизматлар папкасига) кўчириб бўлмайди")
                    n += con.execute("UPDATE cat_items SET node=?, manual=1, at=? WHERE id=?", (to, now(), i)).rowcount
                for f in [int(x) for x in j.get("folders") or []]:
                    r = con.execute("SELECT id, svc FROM cat_nodes WHERE id=? AND deleted=0", (f,)).fetchone()
                    if not r:
                        continue
                    x, seen = to, set()
                    while x and x not in seen:                # ўз ичига кўчириб бўлмайди
                        if x == f:
                            raise ValueError("Папкани ўзининг ичига кўчириб бўлмайди")
                        seen.add(x)
                        pr = con.execute("SELECT parent FROM cat_nodes WHERE id=?", (x,)).fetchone()
                        x = pr[0] if pr else 0
                    if tn and bool(r["svc"]) != bool(tn["svc"]):
                        raise ValueError("Хизматлар папкасини товарлар ичига (ёки аксинча) кўчириб бўлмайди")
                    n += con.execute("UPDATE cat_nodes SET parent=?, manual=1 WHERE id=?", (to, f)).rowcount
                con.commit()
                return jsonify(ok=True, n=n)
            if act == "fnorm":
                n = folder_norm_apply(con, int(j.get("id") or 0), int(j.get("glue") or 0), "width" if j.get("mode") == "width" else "fixed",
                                      float(str(j.get("value") or "0").replace(",", ".")))
                con.commit()
                try:
                    import erp
                    erp._norms["at"] = 0                  # накладной нормативи кэши янгилансин
                except Exception:
                    pass
                return jsonify(ok=True, n=n)
            if act == "disc":                             # скидка белгиси: папка (ичидагилари билан) ёки танланган товарлар
                v = j.get("value")
                n = disc_set(con, fid=int(j.get("id") or 0) or None, items=j.get("items") or [], value=None if v in (None, "") else bool(int(v)))
                con.commit()
                return jsonify(ok=True, n=n)
            if act == "fprice":
                n = folder_price_apply(con, int(j.get("id") or 0), float(str(j.get("price") or "0").replace(" ", "").replace(",", ".")))
                con.commit()
                return jsonify(ok=True, n=n)
            if act == "norm":
                iid, comp = int(j.get("id") or 0), int(j.get("comp") or 0)
                q = j.get("qty")
                if q in (None, ""):
                    con.execute("DELETE FROM cat_norms WHERE item=? AND comp=?", (iid, comp))
                else:
                    con.execute("INSERT INTO cat_norms(item,comp,qty,auto) VALUES(?,?,?,0) ON CONFLICT(item,comp) DO UPDATE SET qty=excluded.qty, auto=0",
                                (iid, comp, float(str(q).replace(",", "."))))
                con.commit()
                return jsonify(ok=True)
            if act == "build":
                if not A.is_admin():
                    raise ValueError("Фақат админ")
                st = build(con)
                return jsonify(ok=True, st=st)
        except ValueError as e:
            return jsonify(error=str(e)), 400
        return jsonify(error="?"), 400
