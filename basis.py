"""Базис-Мебельщик → платформа: конструктор лойиҳани чизгач, Базисдаги скрипт («Lumberture Hub: накладная») лойиҳадаги
материаллар (панеллар — м², деталлар сони), кромкалар (пог.м) ва фурнитурани платформага юборади. Платформа уларни товарларга
мослайди (декор коди ва қалинлик бўйича, ёки олдин танланганини эслаб қолади), конструктор мижозни танлаб «Создать накладную»
босади — черновик накладной тайёр, проведенни кассир қилади.

  /setup/basis.js        — шу фойдаланувчи учун скрипт (сервер манзили ва шахсий калит ичида)
  POST /api/basis/import — скрипт юборади (X-LB-Token), жавоб: {id, url}
  /erp/basis/<id>        — текшириш: ҳар бир қатор → платформа товари, сони; мижоз; «Создать накладную»
  /erp/basis             — юборилган лойиҳалар рўйхати"""
import json, math, re, secrets, time
from datetime import date

A = None
ALIVE = {}          # uid -> охирги «/api/basis/last» вақти (платформа ойнаси очиқми)
ALIVE_SEC = 75      # Chrome орқадаги ойнада таймерни минутига бир мартагача секинлатади

SCHEMA = """
CREATE TABLE IF NOT EXISTS api_tokens(token TEXT PRIMARY KEY, uid INTEGER NOT NULL, purpose TEXT NOT NULL, created_at TEXT, last_at TEXT);
CREATE TABLE IF NOT EXISTS basis_imports(id INTEGER PRIMARY KEY, uid INTEGER, at TEXT, project TEXT DEFAULT '', data TEXT NOT NULL,
  doc_id INTEGER, client TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS basis_names(key TEXT PRIMARY KEY, good TEXT NOT NULL, updated_at TEXT);
"""
WASTE_SHEET = 1.15      # листга: кесиш чиқиндиси — Базис ҳисоботидаги коэффициент (1,15)
WASTE_HDF = 1.25        # ХДФ: Базисда 1,25
WASTE_EDGE = 1.06       # кромка: Базисда 1,06
DEFAULT_SHEET_M2 = 5.0325   # 2750×1830
EDGE_ADD_MM = 40            # XML: кромка метражи — ҳар томон узунлиги + 40 мм (эгаси, 27.10)

CODE_RE = re.compile(r"\b([A-ZА-Я]{0,2}\d{3,5})(?:\s*([A-ZА-Я]{1,3}\d{0,2}))?\b")


def norm(s):
    s = (s or "").casefold().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w,./*×x-]+", " ", s)).strip()


def codes(name):
    """Декор коди: «H1714 ST19», «7045 BS», «0101 PE», «W1000 ST9» …"""
    out = []
    for a, b in CODE_RE.findall((name or "").upper()):
        out.append((a + (" " + b if b else "")).strip())
        out.append(a)
    return list(dict.fromkeys(out))


def sheet_m2(name):
    """Лист юзаси товар номидан: «5,796кв», «2800*2070», бўлмаса 2750×1830."""
    n = (name or "").replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*кв", n)
    if m and 2 < float(m.group(1)) < 8:
        return float(m.group(1))
    m = re.search(r"(?<![\d.])([2-7]\.\d{2,4})(?![\d.])", n)          # «ЛДСП Эггер 5,796 …», «ЛМДФ 5,0325 …»
    if m:
        return float(m.group(1))
    m = re.search(r"(\d{4})\s*[*×xх]\s*(\d{4})", n)
    if m:
        return int(m.group(1)) * int(m.group(2)) / 1e6
    return DEFAULT_SHEET_M2


def auto_match(con, kind, name, thick=None, width=None):
    """Платформа товари: эслаб қолинган → декор коди + қалинлик/эн бўйича энг яқини. -> ext | ''"""
    r = con.execute("SELECT good FROM basis_names WHERE key=?", (kind + ":" + norm(name),)).fetchone()
    if r and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND deleted=0 AND folder=0 AND src='cat'", (r[0],)).fetchone():
        return r[0]
    nn = norm(name)
    for g in con.execute("SELECT ext, name FROM erp_goods WHERE folder=0 AND deleted=0 AND src='cat' AND lc LIKE ?", ("%" + nn.split(" ")[0] + "%",)) if nn else []:
        if norm(g["name"]) == nn:                         # номи бир хил (фурнитура ва ҳ.к.)
            return g["ext"]
    return cat_best(con, kind, name, thick, width)


TYPES = ("лхдф", "лмдф", "лдсп", "мдф", "хдф", "дсп", "акрил", "осп", "двп")
STOP = set(TYPES) | {"мм", "кв", "кромка", "пвх", "услуга", "овал", "abs", "эггер", "egger", "premium", "под", "заказ", "лист", "односторонний", "двухсторонний"}
_CAT = {"at": 0.0, "rows": []}


def _toks(t):
    t = (t or "").casefold().replace("ё", "е")
    return re.findall(r"[a-zа-я]{3,}", t)


def _size(t):
    m = re.search(r"(\d{2})\s*/\s*(\d+(?:[.,]\d+)?)", t or "")
    return (m.group(1), float(m.group(2).replace(",", "."))) if m else None


def cat_best(con, kind, name, thick=None, width=None):
    """Каталог товари: тури (ЛМДФ/ЛДСП/ЛХДФ…), декор сўзлари, декор коди, қалинлик ёки кромка ўлчами бўйича энг яқини."""
    import time as _t
    sig = tuple(con.execute("SELECT COUNT(*), MAX(rowid) FROM erp_goods WHERE src='cat' AND folder=0").fetchone())
    if _t.time() - _CAT["at"] > 300 or _CAT.get("sig") != sig:
        _CAT["sig"] = sig
        _CAT["rows"] = [(r["ext"], r["name"] or "", (r["name"] or "").casefold(), r["cstock"] or 0) for r in con.execute(
            "SELECT ext, name, cstock FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 AND (cat='good' OR lc LIKE '% услуга пвх %' OR lc LIKE '% услуга овал %')")]
        _CAT["at"] = _t.time()
    low = (name or "").casefold().replace("ё", "е")
    tw = set(_toks(low))
    typ = next((t for t in TYPES if t in tw), None)
    decor = {w for w in tw if w not in STOP}
    cs = {c.upper() for c in codes(name)}
    edge = kind in ("edge", "edgefig")
    sz = _size(name) if edge else None
    if edge and not sz and width and thick:
        sz = (str(int(round(width))), float(thick))
    best, bsc = "", 0
    for ext, gn, gl, stock in _CAT["rows"]:
        if kind == "edge":                          # кромка — товар эмас, хизмат: «Услуга ПВХ <кромка>» (кромка ва клей нормативи ичида)
            if not gl.startswith("услуга пвх"):
                continue
        elif kind == "edgefig":                     # фигурали контур (см. чертёж) — «Услуга Овал <кромка>»
            if not gl.startswith("услуга овал"):
                continue
        elif "кромк" in gl or "услуг" in gl:
            continue
        gw = set(_toks(gl.replace("ё", "е")))
        if kind == "panel" and typ and typ not in gw:
            continue
        com = decor & gw
        hit = bool(cs) and any(c in gn.upper() for c in cs)
        if not com and not hit:
            continue
        sc = 3 * len(com) - 0.5 * len((gw - STOP) - decor)
        if hit:
            sc += 6
        if kind == "panel" and thick:
            if re.search(rf"(?<![\d,.]){int(round(thick))}\s*мм", gl):
                sc += 3
            elif re.search(r"\d+\s*мм", gl):
                sc -= 4
        if edge and sz:
            gs = _size(gl)
            if not gs or gs[0] != sz[0] or abs(gs[1] - sz[1]) > 1e-6:
                continue
            sc += 3
        if stock > 0:
            sc += 0.5
        if sc > bsc:
            best, bsc = ext, sc
    return best if bsc >= 5 else ""


HDF_RE = re.compile(r"х\s*д\s*ф|hdf|двп|оргалит", re.I)

# Присадка хизматлари (9 Присадка папкаси). Товар номи бўйича топилади; конструктор бошқасини танласа — эслаб қолинади.
SVC = [  # key, қатор номи, товар номидаги қолип
    ("evro_l", "Евростяжка лицевая (5×7)", r"евростяжк\w*\s*\(?\s*лицев"),
    ("evro_t", "Евростяжка торцевая (7×4,5)", r"евростяжк\w*\s*\(?\s*торц"),
    ("ecc", "Под эксцентрики", r"под\s+эксцентрик(?!\w*\s*\(?\s*d\s*25)"),
    ("ecc25", "Под эксцентрики D25", r"эксцентрик\w*\s*\(?\s*d\s*25"),
    ("raffix", "Под раффикс", r"раф+икс"),
    ("glukh", "Глухие отверстия", r"глух"),
    ("petlya", "Под петли (35 мм)", r"под\s+петл"),
    ("nasechka", "Насечка (3 мм)", r"насечк"),
    ("cnc35", "Обработка ЧПУ (отверстие больше 35 мм)", r"фрезеровк|обработк\w*\s*чпу"),
]
SVC_NAME = {k: n for k, n, _ in SVC}

# Фурнитура → қайси присадка хизмати ва бир донасига нечта (эгаси айтган қоидалар). Биринчи мос келгани олинади.
FURN_RULES = [
    (r"евровинт|конфирмат|евростяжк", [("evro_l", 1), ("evro_t", 1)]),
    (r"раф+икс|rafix", [("raffix", 1), ("glukh", 1)]),
    (r"эксцентрик|эксентрик|минификс|minifix", [("ecc", 1), ("glukh", 1)]),     # эксцентрик 3 тешикда. «Месан» — бренд номи, тури эмас
    (r"полкодерж", [("glukh", 1)]),
    (r"^\s*6\s*[xх×*]\s*10\b", [("glukh", 1)]),     # 6x10 — направляющийнинг орқа тешиги (эгаси айтди)
    (r"петл", [("petlya", 1)]),
    (r"шуруп|саморез", [("nasechka", 1)]),
    (r"(тандем|tandem|blum|блюм|movento|legrabox|леграбокс).*d\s*25|d\s*25.*(тандем|blum|блюм)", [("ecc25", 1)]),
    (r"тандем|tandem|blum|блюм|movento|legrabox|леграбокс", [("glukh", 2)]),  # орқасида 2 тешик
    (r"шарик|направляющ|клей|пила|ручк|ножк|опор|заглушк", []),   # тешиги моделда бўлмаса — станок тешмайди
]


def is_hdf(name, thick=None):
    if re.search(r"зеркал|стекл|акрил", (name or "").casefold()):    # 4 мм ойна/шиша — ХДФ эмас
        return False
    return bool(HDF_RE.search(name or "")) or (thick is not None and 0 < float(thick or 0) <= 4)


GUIDE_NAS = 4      # тешиги йўқ направляющий: битта томонига (L ёки R) насечка, созламада ўзгартирилади


def guide_nas(con, name):
    """Тешиги йўқ направляющийнинг бир томонига неча насечка: шарикли / тандем алоҳида созлама (basis_guide_nas_ball / _tandem)."""
    key = "basis_guide_nas_ball" if re.search(r"шарик|роликов|ball", (name or "").casefold()) else "basis_guide_nas_tandem"
    try:
        r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        v = float(r[0]) if r and str(r[0]).strip() else GUIDE_NAS
    except (ValueError, TypeError, Exception):
        v = GUIDE_NAS
    return v if v >= 0 else GUIDE_NAS


def furn_rule(con, name):
    """-> ([(svc_key, кўпайтма)], эслаб қолинганми / қоида топилдими)"""
    r = con.execute("SELECT good FROM basis_names WHERE key=?", ("furn:" + norm(name),)).fetchone()
    if r:
        try:
            v = json.loads(r[0])
            return [(k, float(m)) for k, m in v if k in SVC_NAME], True
        except (ValueError, TypeError):
            pass
    low = (name or "").casefold()
    for pat, out in FURN_RULES:
        if re.search(pat, low):
            return list(out), True
    return [], False


def svc_default(con, key):
    """Присадка хизмати товари: эслаб қолинган → номи қолипга мос «Услуга …»."""
    r = con.execute("SELECT good FROM basis_names WHERE key=?", ("svc:" + key,)).fetchone()
    if r and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND deleted=0 AND folder=0 AND src='cat'", (r[0],)).fetchone():
        return r[0]
    pat = dict((k, p) for k, _, p in SVC).get(key)
    if not pat:
        return ""
    for g in con.execute("SELECT ext, name FROM erp_goods WHERE folder=0 AND deleted=0 AND src='cat' AND lc LIKE '%услуг%' ORDER BY length(name)"):
        if re.search(pat, (g["name"] or "").casefold()):
            return g["ext"]
    return ""


def konstr_default(con, user):
    """Конструктор хизмати: эслаб қолинган → шу конструктор папкасидаги «Услуга конструктора СТАНДАРТ»."""
    if user:
        r = con.execute("SELECT good FROM basis_names WHERE key=?", (f"svc:konstr:{user['id']}",)).fetchone()
        if r and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND deleted=0 AND folder=0 AND src='cat'", (r[0],)).fetchone():
            return r[0]
    rows = con.execute("SELECT g.ext, g.name, IFNULL(p.name,'') pn FROM erp_goods g LEFT JOIN erp_goods p ON p.ext=g.parent "
                       "WHERE g.folder=0 AND g.deleted=0 AND g.src='cat' AND g.lc LIKE '%конструктор%' AND g.lc LIKE '%услуг%'").fetchall()
    if not rows:
        return ""

    def k(x):
        return re.sub(r"[ъь'ʼ`’\s]", "", (x or "").casefold())
    who = k(((user["full_name"] if user else "") or "").split(" ")[0]) if user else ""
    def score(g):
        sc = 0
        pn = k(g["pn"])
        if who and pn and (who.startswith(pn[:5]) or pn.startswith(who[:5])):
            sc += 10
        if "стандарт" in (g["name"] or "").casefold():
            sc += 5
        return sc
    return max(rows, key=score)["ext"]


def E_last_rate(con):
    try:
        import erp as E
        return E.last_rate(con)
    except Exception:
        return 0


def gprice(con, g):
    """Товар нархи — накладнойдагидек: платформа переоценкаси → 1С нархи → охирги сотув нархи. -> (нарх, валюта: 1 сўм / 2 $)"""
    if not g:
        return 0, 1
    try:
        import erp as E
        rp = E.reval_prices(con).get(g["ext"])
    except Exception:
        rp = None
    return (rp or g["price"] or g["price_last"] or 0), (g["cur"] or 1)


VYREZ_SUM = 5000          # энг кичик иш: Гола профилига 1 та вырез — 5 000 сўм
VYREZ_MM = 150            # вырез контури 150 мм гача — 5 000; ҳар бошланган 150 мм учун яна 5 000
CNC_MAX_W = 1190          # присадка ЧПУ: деталь эни 1190 мм дан катта бўлса станокка сиғмайди (эгаси айтди, 22.10)
UT_RE = re.compile(r"^\s*ут\b|утолщ", re.I)   # «УТ …» — утолщение: эни қанча бўлса ҳам станокда тешилади (эгаси айтди)


def is_ut(name):
    return bool(UT_RE.search((name or "").strip()))
LED_RE = re.compile(r"подсвет|led|свет|диод", re.I)
MILL = [  # key, қатор номи, хизмат товари номидаги сўзлар (биринчи топилгани)
    ("vyrez", "Вырезы и фигурная фрезеровка (ЧПУ)", (r"фрезеровк", r"чпу")),
    ("paz_hdf", "Паз под ХДФ / четверть", (r"паз\W*\(?\s*4\s*мм", r"паз(?!.*подсвет)")),
    ("paz_led", "Паз под подсветку", (r"паз.*подсвет", r"подсвет.*паз")),
]


def vyrez_units(length_mm):
    """Битта вырез: 150 мм гача — 1 бирлик (5 000 сўм), ҳар бошланган 150 мм — яна 1."""
    return max(1, math.ceil(round(float(length_mm or 0), 1) / VYREZ_MM - 1e-9))


def hole_d(k):
    try:
        return float((str(k).split("|") + ["0"])[0] or 0)
    except ValueError:
        return 0.0


def hole_svc(k):
    """Битта тешик -> присадка хизмати: 3 мм гача насечка; евровинт (Ø7 лицевая, Ø4,5 чуқур торцевая), эксцентрик Ø15, Ø25;
    35 мм — петля; 35 дан катта — обработка ЧПУ; қолган 3–35 мм — глухие отверстия."""
    d = hole_d(k)
    try:
        dep = float((str(k).split("|") + ["0", "0"])[1] or 0)
    except ValueError:
        dep = 0
    if d <= 3.5 or (0 < dep <= 3.05 and d <= 40.5):
        # насечка: Ø3 гача (саморез/шуруп тешиги) — ҳар қандай чуқурликда; Ø3 дан катта (Ø40 гача) — чуқурлиги 1–3 мм бўлса (эгаси аниқлаштирди)
        return "nasechka"
    if 34.5 <= d <= 35.5:
        return "petlya"
    if d > 35.5:
        return "cnc35"
    if 13 <= d <= 17:
        return "ecc"
    if 22 <= d <= 28:
        return "ecc25"
    if 6.5 <= d <= 7.5 and dep < 25:
        return "evro_l"
    if 4 <= d <= 5.5 and dep >= 25:
        return "evro_t"
    return "glukh"


def hole_slots(holes, n):
    """Номаълум фурнитура: тешиклари диаметри бўйича (эгаси айтган қоида, 1 донасига):
    3 мм гача — насечка (шуруп), 3 дан 35 мм гача — глухие отверстия, 35 мм — под петли, 35 дан катта — обработка ЧПУ."""
    tot = {}
    for k, cnt in (holes or {}).items():
        d = hole_d(k)
        if d <= 0:
            continue
        tot[hole_svc(k)] = tot.get(hole_svc(k), 0) + float(cnt or 0)
    # эксцентрик: Ø15 ва унинг шток тешиги Ø8 — битта «Под эксцентрики» (1350); Ø10 алоҳида глухой (эгаси айтди)
    stem = sum(float(c or 0) for k, c in (holes or {}).items() if 7.5 <= hole_d(k) <= 9)
    if tot.get("ecc") and stem and tot.get("glukh"):
        tot["glukh"] -= min(stem, tot["ecc"], tot["glukh"])
    # эксцентрик D25 (VB, дюбель билан): 2 тешик — битта «Под эксцентрики D25» (эгаси айтди)
    if tot.get("ecc25") and tot.get("glukh"):
        tot["glukh"] -= min(tot["ecc25"], tot["glukh"])
    n = max(1, int(n or 1))
    return [(k, round(v / n, 3)) for k, v in tot.items() if v > 0]


def nc_minus(nch, furn):
    """Роверга кетадиган деталлардаги тешиклар: оддий присадкадан айирилади (аввал ЧПУ присадка, кейин ровер — фақат сиққан деталга).
    nch: {фурнитура номи: {"d|depth|mode": сони}} -> {svc_key: сони}"""
    by = {}
    for x in furn:
        by.setdefault(norm(x["src"]), {k for k, _ in x["slots"]})
    out = {}
    for nm, holes in (nch or {}).items():
        keys = by.get(norm(mat_name(nm)))
        if not keys:
            continue
        for k, cnt in (holes or {}).items():
            if hole_d(k) <= 0:
                continue
            key = hole_svc(k)
            if key == "glukh" and (("ecc" in keys and 7.5 <= hole_d(k) <= 9) or "ecc25" in keys):   # эксцентрикнинг иккинчи тешиги — ўз хизмати ичида
                continue
            if key in keys:
                out[key] = out.get(key, 0) + float(cnt or 0)
    return out


FURN_TYPES = [  # (тур, қолип) — биринчи мос келгани; мижозга рўйхат учун
    ("Петли 360°", r"петл.*360|360.*петл"),
    ("Петли 180°", r"петл.*180|180.*петл"),
    ("Петли полунакладные", r"петл.*полу\s*наклад|полунаклад|пол\s*гарбат"),
    ("Петли вкладные", r"петл.*вклад"),
    ("Петли угловые", r"петл.*(угл|45|90|135|165)"),
    ("Петли накладные (прямые)", r"петл.*(наклад|прям)"),
    ("Петли", r"петл"),
    ("Направляющие", r"направляющ|тандем|tandem|шарик|роликов|метабокс|movento|legrabox|леграбокс"),
    ("Ручки", r"ручк|профиль\s*gola|гола"),
    ("Раффиксы", r"раф+икс|rafix"),
    ("Эксцентрики", r"эксцентрик|эксентрик|минификс|minifix|дуфикс"),
    ("Евровинты", r"евровинт|конфирмат|евростяжк"),
    ("Полкодержатели", r"полкодерж|полк\w*\s*держ"),
    ("Шканты", r"шкант"),
    ("Сушилки", r"сушилк"),
    ("Шурупы", r"шуруп|саморез"),
    ("Опоры и ножки", r"ножк|опор"),
]


def furn_type(name, holes=None):
    """Тури номидан («Месан» — бренд, тур эмас); номдан чиқмаса — тешигидан (Ø15/Ø25 бўлса эксцентрик)."""
    low = (name or "").casefold()
    for t, pat in FURN_TYPES:
        if re.search(pat, low):
            return t
    if any(hole_svc(k) in ("ecc", "ecc25") for k in (holes or {})):
        return "Эксцентрики"
    if any(hole_svc(k) == "petlya" for k in (holes or {})):          # Ø35 — петля («Примой даводка», «пуш» …)
        return "Петли накладные (прямые)" if re.search(r"прям|примой|наклад", low) else "Петли"
    return "Прочее"


def furn_summary(items):
    """items: [(ном, артикул, сони)] -> [{type, n, rows:[{name, art, n}]}]. Чап/ўнг направляющий — комплект (каттаси)."""
    groups = {}
    for it in items:
        nm, art, n = it[0], it[1], it[2]
        groups.setdefault(furn_type(nm, it[3] if len(it) > 3 else None), []).append(dict(name=nm, art=art, n=int(n or 0)))
    out = []
    order = [t for t, _ in FURN_TYPES] + ["Прочее"]
    for t in order:
        rows = groups.get(t)
        if not rows:
            continue
        rows.sort(key=lambda x: x["name"].casefold())
        if t == "Направляющие":       # «… Направляющая левая / правая» — бир комплект
            kits = {}
            for x in rows:
                base = re.sub(r"\s*(направляющая\s+)?(левая|правая|л|п|l|r)\.?\s*$", "", x["name"], flags=re.I)
                full = (x["name"] + " " + (x["art"] or "")).strip()      # Базис: «350 шарик» + артикул «(350) Направляющая левая»
                side = ("l" if re.search(r"(лев\w*|\bl|\bл)\.?\s*$", x["name"], re.I) or re.search(r"\bлев\w*", full, re.I) else
                        ("r" if re.search(r"(прав\w*|\br|\bп)\.?\s*$", x["name"], re.I) or re.search(r"\bправ\w*", full, re.I) else ""))
                k = kits.setdefault(base, dict(sides={}, art=re.sub(r"\s*(направляющая\s+)?(левая|правая)\s*$", "", x["art"] or "", flags=re.I)))
                k["sides"][side] = k["sides"].get(side, 0) + x["n"]
            rows = []                  # рўйхатда ҳам чап/ўнг алоҳида эмас — битта комплект қатори
            for base, k in kits.items():
                v = k["sides"]
                pair = set(v) == {"l", "r"}
                rows.append(dict(name=base.strip() + (" (L+R)" if pair else ""), art=k["art"],
                                 n=max(v.values()) if set(v) <= {"l", "r"} else sum(v.values())))
            rows.sort(key=lambda x: x["name"].casefold())
            total = sum(x["n"] for x in rows)
            unit = "компл."
        else:
            total, unit = sum(x["n"] for x in rows), "шт"
        out.append(dict(type=t, n=total, unit=unit, rows=rows))
    return out


def mill_calc(data):
    """Скрипт юборган деталлар (pm): вырезлар (контур), пазлар (метр), ЧПУ присадка мумкин эмаслари."""
    units = nv = 0
    vl = 0.0
    hdf = led = 0.0
    nocnc = []
    for p in data.get("pm") or []:
        q = max(1, int(p.get("q") or 1))
        for L in p.get("cut") or []:
            units += vyrez_units(L) * q
            nv += q
            vl += float(L or 0) * q
        fb = int(p.get("fb") or 0)                 # нуқталар ўқилмади: сегментлар сонидан тахмин
        if fb > 0 and not p.get("cut"):
            k = max(1, int(round(fb / 3)))
            units += k * q
            nv += k * q
        for z in p.get("pz") or []:
            m = float(z.get("l") or 0) / 1000 * q
            pw = float(z.get("pw") or 0)
            zn = (z.get("n") or "").casefold()
            if LED_RE.search(zn):          # номида аниқ ёзилган бўлса — номи бўйича
                is_led = True
            elif HDF_RE.search(zn):
                is_led = False
            elif pw > 0:                   # кесим ўлчами бор: эни 4 мм гача — ХДФ пази, 4 мм дан кенг — подсветка (эгаси айтди)
                is_led = pw > 4.05
            else:
                is_led = False
            if is_led:
                led += m
            else:
                hdf += m
        why = []
        w, h = float(p.get("w") or 0), float(p.get("h") or 0)
        if is_hdf(p.get("m") or ""):          # ХДФ (задняя стенка) — присадка қилинмайди, роверга кетмайди
            continue
        if min(w, h) > CNC_MAX_W and not is_ut(p.get("nm")):
            why.append(f"шире {CNC_MAX_W} мм")
        if p.get("arcs") or any(z.get("a") for z in p.get("pz") or []):
            why.append("радиусная фрезеровка")
        if why:
            nocnc.append(dict(nm=p.get("nm") or "", m=(p.get("m") or "").split("\r")[0], w=w, h=h, q=q, why=", ".join(why)))
    return dict(units=units, nv=nv, vl=round(vl), hdf=round(hdf, 2), led=round(led, 2), nocnc=nocnc)


def mill_good(con, key, pats):
    r = con.execute("SELECT good FROM basis_names WHERE key=?", ("svc:" + key,)).fetchone()
    if r and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND deleted=0 AND folder=0 AND src='cat'", (r[0],)).fetchone():
        return r[0]
    gs = con.execute("SELECT ext, name FROM erp_goods WHERE folder=0 AND deleted=0 AND src='cat' AND lc LIKE '%услуг%' ORDER BY length(name)").fetchall()
    for pat in pats:
        for g in gs:
            if re.search(pat, (g["name"] or "").casefold()):
                return g["ext"]
    return ""


def mat_name(s):
    """Базис материали «Ном\rартикул» — номи (артикул алоҳида)."""
    return (s or "").split("\r")[0].strip()


def saw_for(con, good, src, thick=None):
    """Распил хизмати (пакетный) танланган материал товари бўйича: тури (ЛДСП/ЛМДФ/ХДФ…) ва лист ўлчами (5,0325 → «4,5–5 кв», 5,796 → «5,796»)."""
    try:
        import online as OL
        gp = con.execute("SELECT * FROM erp_goods WHERE ext=?", (good,)).fetchone() if good else None
        return OL._find_saw(con, OL.mat_kind(con, gp, src), sheet_m2(gp["name"] if gp else src), float(thick or 0) or None, "ours", packet=True) or ""
    except Exception:
        return ""


def item_k(data):
    """Буюмлар сони (Базис: модел хоссаси «Количество изделий»): базисчи саҳифада ўзгартирган (nqty) ёки скрипт юборган (qty).
    XML'да ҳар буюм ўз сонига аллақачон кўпайтирилган (Изделие/Количество) — 1."""
    if data.get("src") == "xml":
        return 1
    for key in ("nqty", "qty"):
        try:
            v = int(round(float(data.get(key) or 0)))
        except (TypeError, ValueError):
            v = 0
        if 1 <= v <= 999:
            return v
    return 1


def build_rows(con, data, user=None):
    """Скрипт юборган маълумот → текшириш жадвали қаторлари ва фурнитура (→ присадка хизматлари).
    -> (rows, furn) ; rows: panel / edge / svc ; furn: [{src, n, slots:[(key, mult)], known}]
    28.10: «Количество изделий» (K) — моделдан ҳисобланадиган ҳамма нарса 1 буюмга ҳисобланиб, K га кўпайтирилади (q1 × K):
    присадка, фрезеровка (вырез, паз), ровер, деталлар/юза (маълумот). Кромка, листлар сони ва проектирование — K га боғлиқ эмас.
    furn[].n — 1 буюмга (саҳифада × K кўрсатилади)."""
    rows = []
    useful = 0.0
    xml = data.get("src") == "xml"
    K = item_k(data)
    data["_k"] = K

    def gd(ext):
        return con.execute("SELECT * FROM erp_goods WHERE ext=?", (ext,)).fetchone() if ext else None
    for p in data.get("panels") or []:
        full = (p.get("m") or "").strip()
        name = mat_name(full) or "без материала"
        ext = (auto_match(con, "panel", full, p.get("t")) if "\r" in full and con.execute("SELECT 1 FROM basis_names WHERE key=?", ("panel:" + norm(full),)).fetchone()
               else auto_match(con, "panel", name, p.get("t")))
        g = gd(ext)
        area = float(p.get("a") or 0) / 1e6
        hdf = is_hdf(name, p.get("t"))
        ap = float(p.get("ap") or 0) / 1e6          # ишлов берилган деталлар юзаси (ХДФ учун)
        useful += (ap if hdf else area)
        unit = (g["unit"] or "").casefold() if g else "лист"
        k = WASTE_HDF if hdf else WASTE_SHEET
        m2 = bool(g and ("кв" in unit or "м2" in unit or "м²" in unit))
        if m2:
            qty = round(area * k * K, 2)
        else:
            qty = ""           # лист сони — карта раскроядан, базисчи ўзи ёзади (эгаси айтди)
        n1 = int(p.get("n") or 0)
        head = panel_head(n1, area, k, p.get("t"), K)
        tail = " · листов — по карте раскроя" if qty == "" else ""
        if hdf:
            tail += f" · ХДФ: в площадь проекта {ap:.2f} м²" if ap else " · ХДФ: не входит в площадь проекта"
        rows.append(dict(kind="panel", src=name, info=head + tail, head=head, tail=tail, pn1=n1, pa1=area, pk=k,
                         q1=(area * k if m2 else None), dec=2, good=ext, gname=g["name"] if g else "", qty=qty,
                         price=gprice(con, g)[0], cur=gprice(con, g)[1], t=p.get("t")))
    # распил: ҳар бир материалга — хизмат тури материал ва лист ўлчамидан (эслаб қолинган бўлса — шу); сони = листлар сони.
    # Қатор шу материал остида туради (27.10: алоҳида рўйхат адаштиради)
    panels_, rows = rows, []
    for pr_ in panels_:
        rows.append(pr_)
        sub = pr_["qty"] == ""          # листда: распил қатори кўринмайди, тури танланган товар листидан (накладнойда ўзи)
        r_ = None if sub else con.execute("SELECT good FROM basis_names WHERE key=?", ("saw:" + norm(pr_["src"]),)).fetchone()
        sx = r_[0] if r_ and gd(r_[0]) else ""
        if not sx:
            sx = saw_for(con, pr_["good"], pr_["src"], pr_.get("t"))
        g = gd(sx)
        if sub:     # распил қатори кўринмайди — накладнойга ўзи қўшилади (эгаси, 27.10)
            pr_["info"] += " · распил добавится в накладную сам: по листам, одной строкой на каждый вид распила"
        rows.append(dict(kind="saw", src=pr_["src"], good=sx, gname=g["name"] if g else "", sub=pr_["qty"] == "",
                         info="количество = листов этого материала" if pr_["qty"] == "" else "листов — по карте раскроя, укажите вручную",
                         qty="", price=gprice(con, g)[0], cur=gprice(con, g)[1], use=bool(sx)))
    for b in sorted(data.get("butts") or [], key=lambda b: 1 if b.get("f") else 0):
        full = (b.get("m") or "").strip()
        name = mat_name(full) or "кромка"
        kind = "edgefig" if b.get("f") else "edge"
        ext = (auto_match(con, kind, full, b.get("t"), b.get("w")) if "\r" in full and con.execute("SELECT 1 FROM basis_names WHERE key=?", (kind + ":" + norm(full),)).fetchone()
               else auto_match(con, kind, name, b.get("t"), b.get("w")))
        if not ext and kind == "edgefig":          # овал хизмати топилмади — оддий ПВХ хизмати (қўлда алмаштириш мумкин)
            ext = auto_match(con, "edge", name, b.get("t"), b.get("w"))
        g = gd(ext)
        m = float(b.get("l") or 0) / 1000
        if xml:     # XML: метраж ҳисобланади — ҳар томон узунлиги + 40 мм (эгаси, 27.10)
            qty = round(m, 2)
            info = (f"{b.get('w') or '?'}/{b.get('t') or '?'} · сторон: {int(b.get('s') or 0)} · "
                    f"Σ(длина стороны + {EDGE_ADD_MM} мм) = {qty:g} пог.м")
        else:       # метраж — қўлда (овал, «см. чертёж», вырезлар бор — эгаси айтди): фақат қатор
            qty, info = "", f"{b.get('w') or '?'}/{b.get('t') or '?'} · метраж укажите вручную"
        rows.append(dict(kind=kind, src=name, info=info + (" · кромка по фигурному контуру (вырез, радиус) — см. чертёж" if kind == "edgefig" else ""),
                         good=ext, gname=g["name"] if g else "", qty=qty, price=gprice(con, g)[0], cur=gprice(con, g)[1]))
    ext = konstr_default(con, user)
    g = gd(ext)
    rows.append(dict(kind="svc", svc="konstr", src="Проектирование в Базисе", info="полезная площадь листов, м² (ХДФ — только с обработкой)",
                     good=ext, gname=g["name"] if g else "", qty=round(useful, 2), price=gprice(con, g)[0], cur=gprice(con, g)[1], use=useful > 0))
    furn, tot = [], {}
    # лойиҳада алоҳида шуруплар бўлса — направляющий тешиклари шуларда бўлиши мумкин: қоида қўшилмайди, фақат огоҳлантирилади
    scr = sum(int(f.get("n") or 0) for f in data.get("furn") or [] if re.search(r"шуруп|саморез", (f.get("m") or "").casefold()))
    for f in data.get("furn") or []:
        full = (f.get("m") or "").strip()
        name = mat_name(full)
        if not name:
            continue
        n = int(f.get("n") or 1)
        if xml:     # XML: присадка деталлардаги тешиклардан (пастда) — фурнитура фақат рўйхат ва товар учун
            furn.append(dict(src=name, art="", n=n, slots=[], known=True, byh=False, guess=False))
            continue
        learned_full = "\r" in full and con.execute("SELECT 1 FROM basis_names WHERE key=?", ("furn:" + norm(full),)).fetchone()
        slots, known = furn_rule(con, full if learned_full else name)
        byh = False
        if re.search(r"без\s*присад", name.casefold()):     # «… без присадка» — тешилмайди
            slots, known, f = [], True, dict(f, h=None)
        if f.get("h"):            # станок тешадигани: фурнитуранинг деталлардаги тешиклари (эгаси айтди) — ном қоидасидан устун
            hs = hole_slots(f.get("h"), n)
            if re.search(r"шуруп|саморез", name.casefold()):     # саморез/шуруп тешиги — ҳаммаси насечка
                hs = [("nasechka", round(sum(float(c or 0) for c in f["h"].values()) / max(1, n), 3))]
            if hs:
                byh = not known       # номи таниш эмас — «проверьте»
                slots, known = hs, True
        guess = False
        learned = learned_full or con.execute("SELECT 1 FROM basis_names WHERE key=?", ("furn:" + norm(name),)).fetchone()
        if not f.get("h") and not learned and furn_type(name) == "Направляющие":
            # мижоз лойиҳаси: направляющий тешиксиз (3D шакл) — насечка қоида бўйича, базисчи текширади (эгаси айтди)
            gn = guide_nas(con, name)
            if scr:
                guess = dict(scr=scr, n=gn)          # шуруплар бор: қўшмаймиз, базисчи кўради
            else:
                slots = [x for x in slots if x[0] != "nasechka"] + [("nasechka", gn)]
                guess = dict(scr=0, n=gn)
            known, byh = True, True
        furn.append(dict(src=name, art=full.split("\r", 1)[1].strip() if "\r" in full else "", n=n, slots=slots, known=known, byh=byh, guess=guess))
        for k, m in slots:
            tot[k] = tot.get(k, 0) + n * m
    furn.sort(key=lambda x: (0 if (not x["known"] or x.get("byh")) else (2 if not x["slots"] else 1)))   # текшириш кераги тепада, присадкасизи пастда
    mc = mill_calc(data)
    # роверга кетадиган деталлар бўлмаса, скрипт санаган тешиклар айрилмайди (эски скрипт 1 м ва УТ ни ҳам санаган)
    minus = nc_minus(data.get("nch") or {}, furn) if mc["nocnc"] else {}
    if xml:         # XML: ҳар буюм тешиклари → присадка (ровердаги деталлар тешиклари алоҳида — айрилган)
        tot, minus, xh = {}, {}, []
        for it in data.get("items") or []:
            sl = dict(hole_slots(it.get("holes") or {}, 1))
            for k, v in sl.items():
                tot[k] = tot.get(k, 0) + v
            for k, v in hole_slots(it.get("holes_nc") or {}, 1):
                minus[k] = minus.get(k, 0) + v
            xh.append(dict(label=it.get("label") or it.get("file") or "", qty=it.get("qty") or 1, parts=it.get("parts") or 0,
                           slots={k: int(round(v)) for k, v in sl.items()}))
        data["_xh"] = xh
        data["_xhk"] = [(k, t, sum(x["slots"].get(k, 0) for x in xh)) for k, t, _ in SVC if any(x["slots"].get(k) for x in xh)]
        for k, v in minus.items():          # пастда q = tot − minus: tot ровердагилар билан бирга бўлсин
            tot[k] = tot.get(k, 0) + v
    for key, title, _ in SVC:
        ext = svc_default(con, key)
        g = gd(ext)
        mi = int(round(minus.get(key, 0)))
        q1 = max(0, int(round(tot.get(key, 0))) - mi)          # 1 буюмга
        q = q1 * K
        info = "по фурнитуре" + (f" · минус {mi * K} отв. у деталей на ровере" if mi else "")
        if xml:
            info = "по отверстиям в деталях (XML)" + (f" · минус {mi} отв. у деталей на ровере" if mi else "")
        rows.append(dict(kind="svc", svc=key, furn=True, minus=mi, src=title, info=info, good=ext, gname=g["name"] if g else "", qty=q,
                         q1=q1, dec=0, auto=True, price=gprice(con, g)[0], cur=gprice(con, g)[1], use=q > 0))
    rate = 0
    for key, title, pats in MILL:
        ext = mill_good(con, key, pats)
        g = gd(ext)
        pr, cur = gprice(con, g)
        if key == "vyrez" and xml:     # XML'да контур геометрияси йўқ: фигурали деталлар сони, вырез — қўлда
            nf = sum(int(x.get("q") or 1) for x in data.get("fig") or [])
            if cur == 2:
                rate = rate or (E_last_rate(con) or 0)
                pr = round(VYREZ_SUM / rate, 2) if rate else pr
            else:
                pr = VYREZ_SUM
            rows.append(dict(kind="svc", svc=key, mill=True, manual=bool(nf), src=title,
                             info=(f"фигурных деталей (контур не прямоугольный): {nf} шт — вырезы укажите вручную по чертежу · "
                                   "5 000 сум за каждые начатые 150 мм контура") if nf else "фигурных деталей нет",
                             good=ext, gname=g["name"] if g else "", qty=0, price=pr, cur=cur, use=False))
            continue
        if key == "vyrez":
            q = mc["units"]
            info = f"{mc['nv']} шт · контур {mc['vl']} мм · 5 000 сум за каждые начатые 150 мм контура"
            if cur == 2:
                rate = rate or (E_last_rate(con) or 0)
                pr = round(VYREZ_SUM / rate, 2) if rate else pr
            else:
                pr = VYREZ_SUM
        else:
            q = mc["hdf" if key == "paz_hdf" else "led"]
            info = "по траектории пазов в проекте, пог.м"
        q1, dec = q, (0 if key == "vyrez" else 2)
        q = round(q1 * K, dec) if dec else q1 * K
        rows.append(dict(kind="svc", svc=key, mill=True, src=title, info=info, good=ext, gname=g["name"] if g else "",
                         qty=q, q1=q1, dec=dec, auto=True, price=pr, cur=cur, use=q > 0))
    data["_mill"] = mc
    for key, title, words in (("rover", "Ровер (по сложности)", ("ровер", "фрезеровк", "чпу")), ("utol", "Утолщение", ("утолщ", "обманк"))):
        r = con.execute("SELECT good FROM basis_names WHERE key=?", ("svc:" + key,)).fetchone()
        ext = r[0] if r else ""
        if not ext:
            for w in words:
                x = con.execute("SELECT ext FROM erp_goods WHERE folder=0 AND deleted=0 AND src='cat' AND lc LIKE '%услуг%' AND lc LIKE ? ORDER BY length(name) LIMIT 1",
                                ("%" + w + "%",)).fetchone()
                if x:
                    ext = x["ext"]
                    break
        g = gd(ext)
        if key == "rover":          # ровер нархи шакл ва мураккабликка қараб — конструктор қўлда киритади (эгаси айтди)
            q1 = sum(int(x.get("q") or 1) for x in mc["nocnc"])
            q = q1 * K
            info = ("цена по форме и сложности — укажите вручную" +
                    (f" · деталей, где присадка на ЧПУ невозможна: {q} шт" if q else ""))
            rows.append(dict(kind="svc", svc=key, mill=True, manual=True, src=title, info=info, good=ext,
                             gname=g["name"] if g else "", qty=q, q1=q1, dec=0, price=0, cur=gprice(con, g)[1], use=False))
            continue
        rows.append(dict(kind="svc", svc=key, manual=True, src=title, info="введите количество и цену вручную", good=ext,   # қўлда (эгаси айтди)
                         gname=g["name"] if g else "", qty=0, price=gprice(con, g)[0], cur=gprice(con, g)[1], use=False))
    if xml:     # фурнитура номи бўйича жамланган — каталогдаги товарни базисчи танлайди (эслаб қолинади); танланмаса накладнойга тушмайди
        for x in furn:
            ext = furn_good(con, x["src"])
            g = gd(ext)
            rows.append(dict(kind="fgood", src=x["src"], info=f"{furn_type(x['src'])} · из проекта: {x['n']} шт", good=ext,
                             gname=g["name"] if g else "", qty=x["n"], price=gprice(con, g)[0], cur=gprice(con, g)[1], use=bool(ext)))
    return rows, furn


def panel_head(n1, area1, coef, t, K):
    """Панел қатори маълумоти: деталлар ва юза K буюмга («42 дет. × 2 изд. = 84 дет. · …»)."""
    a = area1 * K
    det = f"{n1 * K} дет. ({n1} × {K} изд.)" if K > 1 else f"{n1} дет."
    return f"{det} · {a:.4f} м² × {coef:g} = {a * coef:.4f} м² · {t or '?'} мм"


def furn_good(con, name):
    """Фурнитура товари (XML): эслаб қолинган ёки каталогда номи айнан бир хил. Тахмин қилинмайди."""
    r = con.execute("SELECT good FROM basis_names WHERE key=?", ("fgood:" + norm(name),)).fetchone()
    if r and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND deleted=0 AND folder=0", (r[0],)).fetchone():
        return r[0]
    nn = norm(name)
    if not nn:
        return ""
    for g in con.execute("SELECT ext, name FROM erp_goods WHERE folder=0 AND deleted=0 AND lc LIKE ?", ("%" + nn.split(" ")[0] + "%",)):
        if norm(g["name"]) == nn:
            return g["ext"]
    return ""


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
    from flask import request, render_template, redirect, url_for, flash, abort, jsonify, Response
    import erp as E

    def user_by_token(tok):
        if not tok or len(tok) < 20:
            return None
        r = A.q("SELECT uid FROM api_tokens WHERE token=? AND purpose='basis'", (tok,), one=True)
        if not r:
            return None
        u = A.q("SELECT * FROM users WHERE id=? AND active=1", (r["uid"],), one=True)
        if u:
            A.db().execute("UPDATE api_tokens SET last_at=? WHERE token=?", (A.now(), tok))
        return u

    @app.route("/setup/basis.js")
    @A.login_required
    def setup_basis_js():
        """Базис-Мебельщик учун скрипт: шу фойдаланувчи номидан юборади (калит ичида). Ҳар юклаганда янги калит, эскиси бекор."""
        u = A.current_user()
        db = A.db()
        db.execute("DELETE FROM api_tokens WHERE uid=? AND purpose='basis'", (u["id"],))
        tok = secrets.token_urlsafe(24)
        db.execute("INSERT INTO api_tokens(token, uid, purpose, created_at) VALUES(?,?,?,?)", (tok, u["id"], "basis", A.now()))
        db.commit()
        host = request.host if re.fullmatch(r"[A-Za-z0-9.\-]+(:\d+)?", request.host or "") else "localhost:8081"
        js = render_template("basis_script.js", server=json.dumps(f"http://{host}/"), token=json.dumps(tok),
                             user=json.dumps(u["full_name"] or u["username"], ensure_ascii=True))
        js = js.encode("ascii", "backslashreplace").decode("ascii")     # Базисда кодировка муаммоси бўлмасин — фақат ASCII
        return Response(js, mimetype="application/javascript",
                        headers={"Content-Disposition": "attachment; filename=Lumberture_Hub_nakladnaya.js"})

    @app.route("/api/basis/import", methods=["POST"])
    def api_basis_import():
        u = user_by_token(request.headers.get("X-LB-Token") or "")
        if not u:
            return jsonify(error="token"), 403
        data = request.get_json(silent=True, force=True)
        if not isinstance(data, dict) or not (data.get("panels") or data.get("butts") or data.get("furn")):
            return jsonify(error="empty"), 400
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw) > 4_000_000:
            return jsonify(error="too big"), 400
        db = A.db()
        bid = db.execute("INSERT INTO basis_imports(uid, at, project, data) VALUES(?,?,?,?)",
                         (u["id"], A.now(), str(data.get("project") or "")[:200], raw)).lastrowid
        db.commit()
        return jsonify(ok=True, id=bid, url=request.host_url.rstrip("/") + url_for("erp_basis", bid=bid),
                       open=time.time() - ALIVE.get(u["id"], 0) < ALIVE_SEC)

    @app.route("/api/basis/last")
    @A.login_required
    def erp_basis_last():
        """Очиқ платформа ойнаси шу фойдаланувчининг охирги Базис проектини билиб туради — скрипт янги ойна очмайди."""
        u = A.current_user()
        ALIVE[u["id"]] = time.time()                       # платформа ойнаси очиқ — скрипт янги ойна очмасин
        r = A.q("SELECT id FROM basis_imports WHERE uid=? AND doc_id IS NULL ORDER BY id DESC LIMIT 1", (u["id"],), one=True)
        return jsonify(id=r["id"] if r else 0, url=url_for("erp_basis", bid=r["id"]) if r else "")

    @app.route("/api/clientlog", methods=["POST"])
    @A.login_required
    def api_clientlog():
        """Браузердаги муаммони ташхислаш: саҳифа ҳодисаларини errors.log га ёзади (қисқа)."""
        d = request.get_json(silent=True) or {}
        u = A.current_user()
        A.errlog.warning("CLIENTLOG %s %s %s: %s", u["username"] if u else "?", request.remote_addr,
                         str(d.get("page", ""))[:80], str(d.get("ev", ""))[:1500].replace("\n", " | "))
        return jsonify(ok=True)

    @app.context_processor
    def _basis_ctx():
        def basis_poll():
            try:
                u = A.current_user()
                return bool(u) and bool(A.q("SELECT 1 FROM api_tokens WHERE uid=? AND purpose='basis'", (u["id"],), one=True))
            except Exception:
                return False
        return dict(basis_poll=basis_poll)

    def _own(bid):
        r = A.q("SELECT * FROM basis_imports WHERE id=?", (bid,), one=True)
        u = A.current_user()
        if not r or (r["uid"] != u["id"] and u["role"] != "admin" and not A.can("post")):
            abort(404)
        return r

    @app.route("/erp/basis")
    @A.login_required
    def erp_basis_list():
        u = A.current_user()
        allx = u["role"] == "admin" or A.can("post")
        rows = A.q("SELECT b.id, b.at, b.project, b.doc_id, u.username, u.full_name, d.no doc_no, d.status doc_status FROM basis_imports b "
                   "LEFT JOIN users u ON u.id=b.uid LEFT JOIN erp_docs d ON d.id=b.doc_id "
                   + ("" if allx else "WHERE b.uid=? ") + "ORDER BY b.id DESC LIMIT 200", () if allx else (u["id"],))
        return render_template("basis_list.html", rows=rows)

    @app.route("/erp/basis/xml", methods=["POST"])
    @A.login_required
    def erp_basis_xml():
        """Базис XML спецификациялари (бир нечта буюм) → битта лойиҳа. bid — шу лойиҳага яна файл қўшиш
        (браузер файлларни биттадан юборади: 5 МБ чегарасига сиғади)."""
        import basis_xml as X
        u = A.current_user()
        wants_json = "application/json" in (request.headers.get("Accept") or "")
        def fail(msg):
            if wants_json:
                return jsonify(error=msg), 400
            flash(msg, "error")
            return redirect(url_for("erp_basis_list"))
        ups = [(f.filename or "", f.read()) for f in request.files.getlist("files") if f and f.filename]
        try:
            items = X.parse_files(ups)
        except X.XmlError as e:
            return fail(str(e))
        db = A.db()
        bid = request.form.get("bid", type=int)
        if bid:
            r = A.q("SELECT * FROM basis_imports WHERE id=? AND uid=? AND doc_id IS NULL", (bid, u["id"]), one=True)
            old = json.loads(r["data"]) if r else None
            if not old or old.get("src") != "xml":
                return fail("Проект не найден — загрузите файлы заново")
            have = {(it.get("file"), it.get("label")) for it in old.get("items") or []}
            items = (old.get("items") or []) + [it for it in items if (it.get("file"), it.get("label")) not in have]
        data = X.combine(items)
        raw = json.dumps(data, ensure_ascii=False)
        if len(raw) > 8_000_000:
            return fail("Слишком большой проект")
        if bid:
            db.execute("UPDATE basis_imports SET data=?, project=?, at=? WHERE id=?", (raw, data["project"], A.now(), bid))
        else:
            bid = db.execute("INSERT INTO basis_imports(uid, at, project, data) VALUES(?,?,?,?)",
                             (u["id"], A.now(), data["project"], raw)).lastrowid
        db.commit()
        url = url_for("erp_basis", bid=bid)
        if wants_json:
            return jsonify(ok=True, id=bid, url=url, items=[it["label"] for it in items])
        return redirect(url)

    def _set_k(con, r, data, v):
        """«Количество изделий» — базисчи саҳифада ўзгартирди: лойиҳада сақланади (қайта очганда ҳам шу)."""
        try:
            k = int(round(float(str(v).replace(",", "."))))
        except (TypeError, ValueError):
            return None
        if not 1 <= k <= 999:
            return None
        data["nqty"] = k
        con.execute("UPDATE basis_imports SET data=? WHERE id=?", (json.dumps({x: y for x, y in data.items() if not x.startswith("_")}, ensure_ascii=False), r["id"]))
        con.commit()
        return k

    @app.route("/erp/basis/<int:bid>/k", methods=["POST"])
    @A.login_required
    def erp_basis_k(bid):
        r = _own(bid)
        data = json.loads(r["data"])
        if data.get("src") == "xml":            # XML: ҳар буюм сони файлда (Изделие/Количество)
            return jsonify(error="xml"), 400
        v = (request.get_json(silent=True) or {}).get("k") if request.is_json else request.form.get("k")
        k = _set_k(A.db(), r, data, v)
        return (jsonify(ok=True, k=k) if k else (jsonify(error="1–999"), 400))

    @app.route("/erp/basis/<int:bid>/print")
    @A.login_required
    def erp_basis_print(bid):
        """Мижозга: фурнитура рўйхати ва/ёки деталлар рўйхати — алоҳида қоғозга (доим русча)."""
        r = _own(bid)
        data = json.loads(r["data"])
        what = request.args.get("what") or "all"
        fur, fh = {}, {}
        for f in data.get("furn") or []:
            full = (f.get("m") or "").strip()
            nm, art = mat_name(full), (full.split("\r", 1)[1].strip() if "\r" in full else "")
            if nm:
                k = (nm, art)
                fur[k] = fur.get(k, 0) + int(f.get("n") or 0)
                fh[k] = f.get("h")
        K = item_k(data)          # «Количество изделий»: мижозга рўйхат — ҳамма буюмларга
        furl = furn_summary([(k[0], k[1], v * K, fh.get(k)) for k, v in fur.items()])
        parts = {}
        for p in data.get("pl") or []:
            p = dict(p, q=int(p.get("q") or 0) * K)
            m = mat_name(p.get("m"))
            parts.setdefault((p.get("it") + " · " + m) if p.get("it") else m, []).append(p)
        for m in parts:
            parts[m].sort(key=lambda p: (-(float(p.get("h") or 0) * float(p.get("w") or 0)), p.get("nm") or ""))
        con = A.db()
        cl = ""
        if r["client"]:
            x = con.execute("SELECT name FROM erp_clients WHERE ext=?", (r["client"],)).fetchone()
            cl = x["name"] if x else ""
        ui = A.ui_settings()
        d = (r["at"] or "")[:10]
        try:
            dd = date.fromisoformat(d)
            import c1
            dtext = f"{dd.day} {c1.MONTHS_GEN[dd.month]} {dd.year} г."
        except Exception:
            dtext = d
        p = dict(project=r["project"] or "", client=cl, date_text=dtext, k=K, back=url_for("erp_basis", bid=bid, again=1),
                 logo=(url_for("ui_logo") + "?v=" + str(ui.get("ver", "1"))) if ui.get("logo") else "",
                 company=A.get_setting("nakl_company") or "Lumberture", phone=A.get_setting("contact_phone") or "+998 33 409-60-70")
        return render_template("basis_print.html", p=p, what=what, furn=furl, parts=parts,
                               nparts=sum(int(x.get("q") or 0) for v in parts.values() for x in v))

    @app.route("/erp/basis/<int:bid>", methods=["GET", "POST"])
    @A.login_required
    def erp_basis(bid):
        r = _own(bid)
        con = A.db()
        data = json.loads(r["data"])
        if r["doc_id"] and request.method == "GET" and request.args.get("again") != "1":
            d = con.execute("SELECT id FROM erp_docs WHERE id=?", (r["doc_id"],)).fetchone()
            if d:
                flash("Накладная по этому проекту уже создана", "ok")
                return redirect(url_for("erp_doc", doc_id=d["id"]))
        if request.method == "POST" and data.get("src") != "xml" and request.form.get("nqty"):
            _set_k(con, r, data, request.form.get("nqty"))
        rows, furn = build_rows(con, data, A.current_user())
        if request.method == "POST":
            f = request.form
            hl = []                                             # фурнитура → хизматлар: конструктор ўзгартирса — эслаб қоламиз
            for j, x in enumerate(furn):
                sl = []
                for k in (0, 1, 2):
                    key = (f.get(f"fc_{j}_{k}") or "").strip()
                    mult = A.parse_float(f.get(f"fm_{j}_{k}"), 0) or 0
                    if key in SVC_NAME and mult > 0:
                        sl.append([key, float(mult)])
                if f"fc_{j}_0" in f:
                    same = sorted((k, float(m)) for k, m in x["slots"]) == sorted((k, m) for k, m in sl)
                    x["slots"] = [tuple(z) for z in sl]
                    if not (x.get("guess") and same):      # тешиксиз направляющий: базисчи ўзгартирмаса — эсламаймиз (ҳар лойиҳа ҳар хил)
                        hl.append(("furn:" + norm(x["src"]), json.dumps(sl)))
            client = (f.get("client") or "").strip()
            lines, learn = [], []
            # распил сони = шу материал листлари сони (эгаси, 27.10: алоҳида ёзиш адаштиради); материал кв.м да бўлса — қўлда
            sheets = {row["src"]: A.parse_float(f.get(f"qty_{i}"), 0) for i, row in enumerate(rows)
                      if row["kind"] == "panel" and row["qty"] == "" and f.get(f"use_{i}")}
            pgood = {row["src"]: ((f.get(f"good_{i}") or "").strip(), row.get("t")) for i, row in enumerate(rows) if row["kind"] == "panel"}
            saws, at_ = {}, 0              # распил тури бўйича битта қатор (27.10: 5,796 лар — битта, 4,5–5 — битта, акрил/ПВС — акрил)
            for i, row in enumerate(rows):
                if row["kind"] == "saw" and row.get("sub"):     # кўринмас распил: материал танланган ва листи ёзилган бўлса — ўзи
                    if sheets.get(row["src"], 0) <= 0:
                        continue
                elif not f.get(f"use_{i}"):
                    continue
                good = (f.get(f"good_{i}") or "").strip()
                if row.get("sub"):          # кўринмас распил: тури — базисчи танлаган материал листидан (27.10: 5,0325 га 5,796 тушган эди)
                    pg, pt = pgood.get(row["src"], ("", None))
                    good = saw_for(con, pg, row["src"], pt) or good
                qty = A.parse_float(f.get(f"qty_{i}"), 0)
                if row["kind"] == "saw" and sheets.get(row["src"], 0) > 0:
                    qty = sheets[row["src"]]
                if not good or qty <= 0:
                    continue
                g = con.execute("SELECT ext, price FROM erp_goods WHERE ext=? AND folder=0 AND deleted=0", (good,)).fetchone()
                if not g:
                    continue
                gg = con.execute("SELECT * FROM erp_goods WHERE ext=?", (g["ext"],)).fetchone()
                pr = A.parse_float(f.get(f"price_{i}"), 0) or gprice(con, gg)[0]
                if row["kind"] == "saw":
                    x = saws.setdefault(g["ext"], [0.0, pr])
                    x[0] += qty
                else:
                    lines.append(dict(good=g["ext"], qty=A.num(qty), price=A.num(pr), note=""))
                    if row["kind"] == "panel":
                        at_ = len(lines)
                if row["kind"] == "svc":
                    learn.append((f"svc:konstr:{A.current_user()['id']}" if row["svc"] == "konstr" else "svc:" + row["svc"], g["ext"]))
                elif not row.get("sub"):        # кўринмас распил эслаб қолинмайди (ҳар сафар материалдан)
                    learn.append((row["kind"] + ":" + norm(row["src"]), g["ext"]))
            lines[at_:at_] = [dict(good=k, qty=A.num(v[0]), price=A.num(v[1]), note="") for k, v in saws.items()]   # материаллардан кейин
            konstr = ""
            w = con.execute("SELECT id FROM k_workers WHERE user_id=? AND (fired IS NULL OR fired='')", (A.current_user()["id"],)).fetchone()
            if w and w["id"] in {x["id"] for x in E.staff_lists(con)[0]}:
                konstr = str(w["id"])
            form = dict(client=client, rate="", nal="1", note=(f.get("note") or "").strip() or ("Базис: " + (r["project"] or "")).strip(),
                        discount="0", date=date.today().isoformat(), konstr=konstr, lines=lines, minus_ok="1")
            try:
                nid = E.save_sale(con, None, form, post=False)
                for k, ext in learn + hl:
                    con.execute("INSERT OR REPLACE INTO basis_names(key, good, updated_at) VALUES(?,?,?)", (k, str(ext), A.now()))
                con.execute("UPDATE basis_imports SET doc_id=?, client=? WHERE id=?", (nid, client, bid))
                con.commit()
                flash("Черновик накладной создан из проекта Базис. Проверьте и сохраните; провести — кассир.", "ok")
                return redirect(url_for("erp_sale_edit", doc_id=nid))
            except ValueError as e:
                con.rollback()
                flash(str(e), "error")
                for i, row in enumerate(rows):                     # танланганлар қайтиб чиқсин
                    if f.get(f"good_{i}"):
                        row["good"] = f.get(f"good_{i}")
                        gg = con.execute("SELECT name, price FROM erp_goods WHERE ext=?", (row["good"],)).fetchone()
                        row["gname"], row["price"] = (gg["name"], gg["price"]) if gg else ("", 0)
                    row["qty"] = f.get(f"qty_{i}") or row["qty"]
                    row["use"] = bool(f.get(f"use_{i}"))
        cname = ""
        cl = request.form.get("client") if request.method == "POST" else ""
        if cl:
            x = con.execute("SELECT name FROM erp_clients WHERE ext=?", (cl,)).fetchone()
            cname = x["name"] if x else ""
        hmap = {mat_name(f.get("m")): f.get("h") for f in data.get("furn") or []}
        fsum = furn_summary([(x["src"], x.get("art", ""), x["n"], hmap.get(x["src"])) for x in furn])
        return render_template("basis_import.html", r=r, data=data, rows=rows, furn=furn, SVC=SVC, fsum=fsum, client=cl or "", cname=cname,
                               nmatched=sum(1 for x in rows if x["good"] and x["kind"] not in ("svc", "saw")), nmat=sum(1 for x in rows if x["kind"] not in ("svc", "saw")))
