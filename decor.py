# -*- coding: utf-8 -*-
"""Эконом ва Craftwood листлари: битта декор + параметрлар (материал, қалинлик, томон, МДФ сорт).

• Манбалар: 1С папкалари («ЛДСП/ЛМДФ Эконом», «ЛДСП/ЛМДФ Крафтвуд») ва Eman «Старвуд ЛДСП/ЛМДФ» (CRAFTWOOD — Craftwood).
  Бу листлар Ўзбекистонда босилади — декор ЛДСП ва ЛМДФ учун бир хил, шунинг учун декор битта.
• Номи аниқ бир хил — ўзи бирлашади; ўхшашлари («Каньен/Каньон») — эгаси «Бир хил / Ҳар хил» деб тасдиқлайди.
• Нарх: материал+қалинлик бўйича асосий нарх (Эконом: Eman Старвуд нархи = Асино), МДФ сорт устамаси
  (Асино 0, Хитой/М +1, Л/Гомель +2, Алтай +3), бир томонли −$2. Craftwood — 1С'дагидек гуруҳлар (Платинум, Премиум, Филвуд, Эконом).
"""
import difflib
import json
import re
from collections import Counter, defaultdict

A = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS cat_decor(id INTEGER PRIMARY KEY, fam TEXT, name TEXT, key TEXT, grp TEXT DEFAULT 'std',
  merged_into INTEGER, deleted INTEGER DEFAULT 0, src TEXT DEFAULT '[]', manual INTEGER DEFAULT 0, UNIQUE(fam, key));
CREATE TABLE IF NOT EXISTS cat_decor_pair(id INTEGER PRIMARY KEY, fam TEXT, a INTEGER, b INTEGER, score REAL,
  status TEXT DEFAULT 'pending', by TEXT, at TEXT, UNIQUE(a, b));
"""

FAMS = {"econom": "Эконом (Панельный)", "craft": "Craftwood (Тапоич — Крафт Вуд Окажон)"}
SORTS = [("Асино", 0), ("Хитой", 1), ("М", 1), ("Л", 2), ("Гомель", 2), ("Алтай", 3)]
LMDF_TH = ["5,5", "8", "10", "12", "16", "18"]
GRP = {"econom": [("std", "Стандарт"), ("mono", "Однотонные")],
       "craft": [("platinum", "Платинум"), ("premium", "Премиум"), ("filwood", "Филвуд"), ("econom", "Эконом")]}
ONE_SIDE_LESS = 2.0

COLORS = {"белый", "белая", "черный", "черная", "серый", "серая", "желтый", "красный", "синий", "зеленый", "салатовый", "бежевый",
          "розовый", "оранжевый", "оранживый", "голубой", "коричневый", "крем", "светлый", "светлая", "темный", "темная", "золотой",
          "золотая", "серебряный", "графит", "антрацит", "венге", "орех", "дуб", "сосна", "вишня", "бук", "клен", "ясень", "мрамор",
          "гранит", "глянец", "гладкий", "текстура", "апельсин", "шагрень", "матовый"}
_STOP = (r"лдсп|лмдф|лдмф|мдф|крафтвуд|craftwood|crw|премиум|платинум|эконом|филвуд|стандарт|местный|узб|под|заказ|пр|цв|"
         r"одностонный|одностронний|односторонний|одностороний|двусторонний|томонли|new")


def key_of(t):
    t = (t or "").casefold().replace("ё", "е")
    t = re.sub(r"\(.*?\)", " ", t)
    t = re.sub(r"\b[12]\s*/\s*2\b|о/с|д/с", " ", t)
    t = re.sub(r"\d+[.,]?\d*\s*(мм|кв\.?\s*м|кв|м2)?", " ", t)
    t = re.sub(r"\b(" + _STOP + r")\b", " ", t)
    t = re.sub(r"шагрин", "шагрень", t)
    t = re.sub(r"\bодн\w*|однотон\w*", " ", t)
    t = re.sub(r"[^а-яa-z ]+", " ", t)
    return " ".join(t.split())


def name_of(t):
    """Декорнинг кўринадиган номи: материал, ўлчам, «премиум» каби сўзларсиз."""
    s = re.sub(r"\(.*?\)", " ", t or "")
    s = re.sub(r"\b[12]\s*/\s*2\b|[ОоДд]/[Сс]", " ", s)
    s = re.sub(r"\d+[.,]?\d*\s*(мм|кв\.?\s*м|кв|м2)?", " ", s)
    s = re.sub(r"(?i)\b(" + _STOP + r")\b", " ", s)
    s = re.sub(r"(?i)[-\s]+одн\w*", " ", s)
    s = re.sub(r"[-–,.*]+\s*$", "", " ".join(s.split())).strip(" -–,.")
    return s[:1].upper() + s[1:] if s else (t or "").strip()


def init(con):
    con.executescript(SCHEMA)


# ================================================================ манбалар
def _c1_tree(con):
    rows = {r["ext"]: dict(r) for r in con.execute("SELECT ext, name, parent, folder, deleted, unit, cur, price, price_last FROM erp_goods "
                                                    "WHERE COALESCE(src,'1c')<>'cat'")}
    kids = defaultdict(list)
    for r in rows.values():
        kids[r["parent"] or "0"].append(r)
    return rows, kids


def _walk(kids, ext, path=()):
    for r in kids.get(ext, []):
        if r["deleted"]:
            continue
        if r["folder"]:
            yield from _walk(kids, r["ext"], path + (r["name"] or "",))
        else:
            yield r, path


def sources(con):
    """-> [dict(fam, title, path, src, ext, grp, price, cur)]"""
    out = []
    rows, kids = _c1_tree(con)
    for top in kids.get("0", []):
        n = (top["name"] or "").casefold()
        if not top["folder"] or top["deleted"]:
            continue
        fam = "craft" if "крафтвуд" in n else ("econom" if re.match(r"\s*(лдсп|лмдф)\s+эконом", n) else None)
        if not fam:
            continue
        for r, path in _walk(kids, top["ext"], (top["name"],)):
            p = " / ".join(path).casefold() + " " + (r["name"] or "").casefold()
            if fam == "craft":
                grp = next((g for g, w in (("platinum", "платинум"), ("premium", "премиум"), ("filwood", "филвуд"), ("econom", "эконом"))
                            if w in (r["name"] or "").casefold()), None) or next(
                    (g for g, w in (("platinum", "платинум"), ("premium", "премиум"), ("filwood", "филвуд"), ("econom", "эконом")) if w in p), "")
            else:
                grp = "std"
            out.append(dict(fam=fam, title=r["name"], path=" / ".join(path), src="1c", ext=r["ext"], grp=grp,
                            price=r["price"] or r["price_last"] or 0, cur=r["cur"] or 1))
    try:
        nodes = {r["id"]: dict(r) for r in con.execute("SELECT id, parent, name FROM eman_nodes")}
    except Exception:
        nodes = {}
    ek = defaultdict(list)
    for n in nodes.values():
        ek[n["parent"]].append(n)
    for top in [n for n in nodes.values() if re.match(r"\s*старвуд\s+(лдсп|лмдф)", n["name"] or "", re.I)]:
        st = [(top["id"], (top["name"],))]
        while st:
            i, path = st.pop()
            for k in ek.get(i, []):
                st.append((k["id"], path + (k["name"],)))
            for g in con.execute("SELECT title, price, cur FROM eman_goods WHERE node=?", (i,)):
                if not (g["title"] or "").strip() or "пусто" in (g["title"] or "").casefold():
                    continue
                p = " / ".join(path)
                if re.search(r"уст-илим|4[.,]46", p + " " + (g["title"] or ""), re.I):
                    continue                       # Россия плиталари (4,4652 кв) — декор эмас, оддий товар бўлиб қолади
                fam = "craft" if "craftwood" in p.casefold() else "econom"
                grp = "mono" if ("однотон" in p.casefold() or re.search(r"\bодн", (g["title"] or "").casefold())) else ("std" if fam == "econom" else "")
                out.append(dict(fam=fam, title=g["title"], path=p, src="eman", ext="", grp=grp, price=g["price"] or 0,
                                cur=2 if "$" in (g["cur"] or "") else 1))
    return out


# ================================================================ қуриш
def build(con):
    """Декорлар рўйхати ва ўхшаш жуфтлар (қўлда ўзгартирилганлари сақланади). -> статистика"""
    init(con)
    st = Counter()
    by = defaultdict(list)
    for s in sources(con):
        k = key_of(s["title"])
        if len(k) < 3:
            continue
        by[(s["fam"], k)].append(s)
    for (fam, k), ss in by.items():
        r = con.execute("SELECT id, manual FROM cat_decor WHERE fam=? AND key=?", (fam, k)).fetchone()
        nm = name_of(next((x["title"] for x in ss if x["src"] == "eman"), ss[0]["title"]))
        grp = "mono" if any(x["grp"] == "mono" for x in ss) else (Counter(x["grp"] for x in ss if x["grp"]).most_common(1) or [("std" if fam == "econom" else "", 0)])[0][0]
        src = json.dumps([dict(src=x["src"], title=x["title"], path=x["path"]) for x in ss], ensure_ascii=False)
        if r:
            if r["manual"]:
                con.execute("UPDATE cat_decor SET src=? WHERE id=?", (src, r["id"]))
            else:
                con.execute("UPDATE cat_decor SET name=?, grp=?, src=? WHERE id=?", (nm, grp, src, r["id"]))
        else:
            con.execute("INSERT INTO cat_decor(fam, name, key, grp, src) VALUES(?,?,?,?,?)", (fam, nm, k, grp, src))
            st["new"] += 1
        st[fam] += 1
        st["dup_" + fam] += max(0, len(ss) - 1) if len({x["src"] for x in ss}) > 1 else 0
    # ўхшаш жуфтлар (бир оила ичида)
    for fam in FAMS:
        ds = [dict(r) for r in con.execute("SELECT id, key FROM cat_decor WHERE fam=? AND merged_into IS NULL AND deleted=0", (fam,))]
        for i, a in enumerate(ds):
            for b in ds[i + 1:]:
                if a["key"] == b["key"]:
                    continue
                sc = difflib.SequenceMatcher(None, a["key"], b["key"]).ratio()
                ta, tb = set(a["key"].split()), set(b["key"].split())
                ca, cb = (ta - tb) & COLORS, (tb - ta) & COLORS
                if ca and cb:
                    continue                       # «Серый шагрень» / «Черный шагрень» — ҳар хил ранг
                if sc >= 0.84 or (ta and tb and (ta <= tb or tb <= ta) and len(ta & tb) >= 2):
                    x, y = sorted((a["id"], b["id"]))
                    if not con.execute("SELECT 1 FROM cat_decor_pair WHERE a=? AND b=?", (x, y)).fetchone():
                        con.execute("INSERT INTO cat_decor_pair(fam, a, b, score) VALUES(?,?,?,?)", (fam, x, y, round(sc, 3)))
                        st["pairs"] += 1
    con.commit()
    return dict(st)


def resolve(con, pid, same, who=""):
    """Жуфт: бир хил — b декори a'га қўшилади (ўчирилади); ҳар хил — иккаласи қолади."""
    r = con.execute("SELECT * FROM cat_decor_pair WHERE id=?", (pid,)).fetchone()
    if not r:
        raise ValueError("Топилмади")
    import datetime
    t = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if same:
        keep, drop = r["a"], r["b"]
        con.execute("UPDATE cat_decor SET merged_into=? WHERE id=?", (keep, drop))
        con.execute("UPDATE cat_decor SET merged_into=? WHERE merged_into=?", (keep, drop))
        con.execute("UPDATE cat_decor_pair SET status='skip' WHERE status='pending' AND (a=? OR b=?) AND id<>?", (drop, drop, pid))
    con.execute("UPDATE cat_decor_pair SET status=?, by=?, at=? WHERE id=?", ("same" if same else "diff", who, t, pid))
    con.commit()


# ================================================================ нарх
def _setting(key, default):
    try:
        v = json.loads(A.get_setting(key, "") or "null") if A else None
    except ValueError:
        v = None
    return v if v is not None else default


def eman_rate(con):
    """Eman нархлари сўмда — $ га ўтказиш учун Eman курси (платформа кун курси)."""
    try:
        import erp
        return erp.day_rate()[0] or 12200.0
    except Exception:
        return 12200.0


def default_matrix(con):
    """Бошланғич нархлар ($): Эконом — Eman Старвуд нархидан (= Асино), Craftwood — 1С'даги охирги нархлардан. Бўш — номаълум."""
    rate = eman_rate(con)
    src = sources(con)
    m = {"econom": {}, "craft": {}}

    def th_of(t, p):
        if re.search(r"лдсп", (t or "") + " " + (p or ""), re.I) and not re.search(r"лмдф|лдмф", t or "", re.I):
            return "ЛДСП 16"
        mm = re.search(r"(\d+[.,]?\d*)\s*мм", (p or "").split(" / ")[-1]) or re.search(r"(\d+[.,]?\d*)\s*мм", t or "")
        v = (mm.group(1).replace(".", ",").lstrip("0") if mm else "16")
        v = {"7,5": "8", "6": "5,5", "06": "5,5", "08": "8"}.get(v, v)
        return "ЛМДФ " + v if v in LMDF_TH else None
    acc = defaultdict(Counter)
    for s in src:
        if not s["price"]:
            continue
        usd = s["price"] / rate if s["cur"] == 1 else s["price"]
        if usd < 3:
            continue
        th = th_of(s["title"], s["path"])
        if th:
            acc[(s["fam"], th, s["grp"] or "")][round(usd)] += 1
    for (fam, th, grp), c in acc.items():
        if grp:
            m[fam].setdefault(th, {})[grp] = c.most_common(1)[0][0]
    return m


def matrix(con):
    m = default_matrix(con)
    over = _setting("decor_prices", {})
    for fam, rows in (over or {}).items():
        for th, gs in (rows or {}).items():
            for g, v in (gs or {}).items():
                m.setdefault(fam, {}).setdefault(th, {})[g] = v
    return m


def materials(fam):
    return ["ЛДСП 16"] + ["ЛМДФ " + t for t in LMDF_TH]


def price(con, decor_id, mat, sides=2, sort="Асино", m=None):
    """Нарх ($): материал+қалинлик ва гуруҳ бўйича асос + МДФ сорт устамаси (ЛМДФ) − бир томонли $2."""
    d = con.execute("SELECT fam, grp FROM cat_decor WHERE id=?", (decor_id,)).fetchone()
    if not d:
        return None
    m = m or matrix(con)
    base = (m.get(d["fam"], {}).get(mat) or {}).get(d["grp"])
    if base in (None, ""):
        return None
    v = float(base)
    if mat.startswith("ЛМДФ") and d["fam"] == "econom":
        v += dict(SORTS).get(sort, 0)
    if int(sides or 2) == 1:
        v -= ONE_SIDE_LESS
    return round(v, 2)


# ================================================================ саҳифа
def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init(con); con.commit()
    finally:
        con.close()
    from flask import request, render_template, jsonify, abort

    @app.route("/erp/catalog/decors")
    @A.login_required
    def erp_decors():
        con = A.db()
        init(con)
        fam = request.args.get("fam") if request.args.get("fam") in FAMS else "econom"
        if not con.execute("SELECT 1 FROM cat_decor LIMIT 1").fetchone():
            build(con)
        dec = {r["id"]: dict(r) for r in con.execute("SELECT * FROM cat_decor WHERE fam=?", (fam,))}
        for d in dec.values():
            try:
                d["srcs"] = json.loads(d["src"] or "[]")
            except ValueError:
                d["srcs"] = []
        pairs = [dict(r, an=dec[r["a"]]["name"], bn=dec[r["b"]]["name"], asrc=dec[r["a"]]["srcs"], bsrc=dec[r["b"]]["srcs"])
                 for r in con.execute("SELECT * FROM cat_decor_pair WHERE fam=? AND status='pending' ORDER BY score DESC", (fam,))
                 if r["a"] in dec and r["b"] in dec and not dec[r["a"]]["merged_into"] and not dec[r["b"]]["merged_into"]]
        live = sorted([d for d in dec.values() if not d["merged_into"] and not d["deleted"]], key=lambda d: d["name"].casefold())
        done = con.execute("SELECT COUNT(*) FROM cat_decor_pair WHERE fam=? AND status IN ('same','diff')", (fam,)).fetchone()[0]
        return render_template("erp_decors.html", fam=fam, FAMS=FAMS, pairs=pairs, live=live, done=done, GRP=GRP[fam],
                               m=matrix(con).get(fam, {}), mats=materials(fam), SORTS=SORTS, edit=A.can("super"),
                               one_less=ONE_SIDE_LESS)

    @app.route("/erp/api/decor", methods=["POST"])
    @A.login_required
    def erp_api_decor():
        if not A.can("super"):
            return jsonify(error="Бу ҳаракатга доступ йўқ. Рухсатни админ беради."), 403
        con = A.db()
        j = request.get_json(force=True) or {}
        u = A.current_user()
        try:
            if j.get("act") == "pair":
                resolve(con, int(j["id"]), bool(j.get("same")), u["username"] if u else "")
            elif j.get("act") == "grp":
                con.execute("UPDATE cat_decor SET grp=?, manual=1 WHERE id=?", (j.get("grp") or "", int(j["id"])))
                con.commit()
            elif j.get("act") == "name":
                nm = (j.get("name") or "").strip()
                if not nm:
                    raise ValueError("Номини ёзинг")
                con.execute("UPDATE cat_decor SET name=?, manual=1 WHERE id=?", (nm, int(j["id"])))
                con.commit()
            elif j.get("act") == "price":
                cur = _setting("decor_prices", {}) or {}
                v = str(j.get("v") or "").replace(",", ".").strip()
                row = cur.setdefault(j["fam"], {}).setdefault(j["mat"], {})
                if v:
                    row[j["grp"]] = float(v)
                else:
                    row.pop(j["grp"], None)
                A.set_settings(decor_prices=json.dumps(cur, ensure_ascii=False))
            elif j.get("act") == "build":
                return jsonify(ok=True, st=build(con))
            else:
                raise ValueError("?")
        except (ValueError, KeyError) as e:
            return jsonify(error=str(e)), 400
        return jsonify(ok=True)
