"""4-қадам: ҳисоботлар (1С'дагидек) — Остаток товаров, Отчет по накладным, Отчет по приходу, Взаиморасчеты.
Ҳаммаси erp_docs/erp_lines'дан (1С'дан кўчирилганлар + платформада ёзилганлар) ҳисобланади, проведен ҳужжатлар бўйича.
Қолдиқ ва қарз ҳозирги қийматдан орқага ҳисобланади (ҳозирги = 1С регистри ± платформа), шунинг учун бошқа ойналар билан мос."""
import csv
import io
import re
from collections import defaultdict
from datetime import date, timedelta

from flask import Response, render_template, request, url_for

import c1
import erp

A = None


# ---------------------------------------------------------------- умумий
def _mutual_xlsx(con, rows, tot, mode, cx, d1, d2):
    """Взаиморасчет → битта варақли Excel: асосий кўрсаткичлар, нима олди (товар/хизмат), кунлар бўйича олди/тўлади, ҳужжатлар."""
    kp = [("Нач. сальдо", tot["beg"], "debt"), ("Олди (товар)", tot["taken"], "money"), ("Тўлади", tot["paid"], "money"), ("Кон. сальдо", tot["end"], "debt")]
    if cx and len(rows) == 1:
        r = rows[0]
        goods = con.execute("""SELECT COALESCE(g.name, l.good) nm, COALESCE(g.unit,'') un, SUM(COALESCE(l.qty,0)) q, SUM(COALESCE(l.usd,0)) u
            FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id LEFT JOIN erp_goods g ON g.ext=l.good
            WHERE d.kind='sale' AND d.status='posted' AND COALESCE(d.nal,1)<>0 AND d.client=? AND d.date BETWEEN ? AND ?
            GROUP BY l.good ORDER BY u DESC""", (cx, d1.isoformat(), d2.isoformat())).fetchall()
        import xlbrand
        docs = [(m["date"], f"{m['kname']} № {m['no']}", m["amt"] if m["amt"] > 0 else None, -m["amt"] if m["amt"] < 0 else None, m["bal"]) for m in r["moves"]]
        return xlbrand.client_statement("vzaimoraschety_" + re.sub(r"[^0-9A-Za-z]+", "_", r["client"])[:20], r["name"], d1, d2,
                                        r["beg"], r["taken"], r["paid"], r["end"],
                                        [(g["nm"], g["un"], g["q"], round(g["u"], 2)) for g in goods], docs, firm=A.firm_info())
    if mode == "detail":
        out = []
        for r in rows:
            out.append((r["name"], None, "Нач. сальдо", None, None, r["beg"]))
            for m in r["moves"]:
                out.append((r["name"], m["date"], f"{m['kname']} № {m['no']}", m["amt"] if m["amt"] > 0 else None, -m["amt"] if m["amt"] < 0 else None, m["bal"]))
            out.append((r["name"], None, "Кон. сальдо", None, None, r["end"]))
        return _csv("vzaimoraschety_podrobno", ["Контрагент", "Сана", "Ҳужжат", "Олди $", "Тўлади $", "Қарз $"], out,
                    kinds=["text", "date", "text", "money", "money", "money"], totals=False, kpis=kp, main_title="Ҳужжатлар")
    return _csv("vzaimoraschety", ["Контрагент", "Нач. сальдо $", "Олди $", "Тўлади $", "Кон. сальдо $", "Муддати ўтган $", "Кун"],
                [(r["name"], r["beg"], r["taken"], r["paid"], r["end"], r["od_sum"] or None, r["od_days"] or None) for r in rows],
                kinds=["text", "money", "money", "money", "money", "money", "int"], kpis=kp, main_title="Контрагентлар",
                chart=dict(cat=0, vals=[4], title="Энг катта қарздорлар, $", top=10))


def _period():
    today = date.today()
    d1 = A.parse_date(request.args.get("d1")) or today.replace(day=1)
    d2 = A.parse_date(request.args.get("d2")) or today
    if d1 > d2:
        d1, d2 = d2, d1
    return d1, d2


def _arg(name, allowed, default):
    v = request.args.get(name, default)
    return v if v in allowed else default


def _goods(con):
    """{ext: dict(name, code, unit, cur, parent, group)} — фақат товарлар (папкасиз)."""
    fp = erp.folder_paths(con)
    out = {}
    for r in con.execute(f"SELECT ext, code, name, unit, cur, {erp.EFF_PARENT} p FROM erp_goods WHERE folder=0"):
        out[r["ext"]] = dict(ext=r["ext"], name=r["name"] or "", code=(r["code"] or "").strip(), unit=r["unit"] or "", cur=r["cur"],
                             parent=r["p"] or "", group=fp.get(r["p"] or "", ""))
    return out


def _folders(con):
    """Папкалар рўйхати (танлаш учун) ва {папка: {ўзи ва барча ички папкалари}}."""
    fp = erp.folder_paths(con)
    kids = defaultdict(list)
    for r in con.execute(f"SELECT ext, {erp.EFF_PARENT} p FROM erp_goods WHERE folder=1"):
        kids[r["p"] or ""].append(r["ext"])
    def sub(f):
        seen, st = set(), [f]
        while st:
            x = st.pop()
            if x in seen:
                continue
            seen.add(x); st.extend(kids.get(x, []))
        return seen
    return sorted(fp.items(), key=lambda x: x[1].casefold()), sub


def _gfilter(con, goods):
    """Товар филтри: f — папка (ички папкалари билан), q — номи/коди бўйича, g — аниқ товар. -> (set|None, dict)"""
    f, q, g = (request.args.get("f") or "").strip(), (request.args.get("q") or "").strip(), (request.args.get("g") or "").strip()
    flist, sub = _folders(con)
    keep = None
    if g:
        keep = {g}
    else:
        if f:
            fs = sub(f)
            keep = {k for k, v in goods.items() if v["parent"] in fs}
        if q:
            qq = q.casefold()
            hit = {k for k, v in goods.items() if qq in (v["name"] + " " + v["code"]).casefold()}
            keep = hit if keep is None else keep & hit
    return keep, dict(f=f, q=q, g=g, gname=goods.get(g, {}).get("name", "") if g else "", folders=flist)


def _svc_set(con, goods):
    return {k for k in goods if erp.is_service(con, k)}


TITLES = {"ostatok": "Остаток товаров", "prodazhi": "Отчет по накладным", "prihod": "Отчет по приходу", "vzaimoraschety": "Взаиморасчеты"}


def _csv(name, head, rows, kinds=None, totals=True, chart=None, title=None, kpis=None, sections=None, main_title=None, sub_extra=""):
    """Excel (.xlsx) фирма услубида (номи эскича қолди: ҳисоботлардаги «Excel» тугмаси)."""
    import xlbrand
    base = name.split("_")[0]
    d1, d2 = request.args.get("d1"), request.args.get("d2")
    sub = (f"{d1[8:10]}.{d1[5:7]}.{d1[:4]} — {d2[8:10]}.{d2[5:7]}.{d2[:4]}" if d1 and d2 and len(d1) == 10 and len(d2) == 10 else "")
    if sub_extra:
        sub = sub_extra + (" · " + sub if sub else "")
    return xlbrand.book(name, title or TITLES.get(base, name), head, rows, subtitle=sub, kinds=kinds, totals=totals, chart=chart,
                        kpis=kpis, sections=sections, main_title=main_title)


def _clients(con):
    return {r["ext"]: dict(name=r["name"] or "", kind=r["ckind"] or "client", lc=r["lc"] or "")
            for r in con.execute("SELECT ext, name, COALESCE(ckind,'client') ckind, lc FROM erp_clients WHERE folder=0")}


def _cmatch(clients, text):
    t = erp.lc(text).strip() if text else ""
    if not t:
        return None
    return {k for k, v in clients.items() if t in v["lc"] or text.casefold() in v["name"].casefold()}


# ---------------------------------------------------------------- 1. Остаток товаров
def _c1_stock_moves():
    """{iddoc: {товар: +кирим/−чиқим}} — 1С регистри (RA410). 1С баъзан накладнойни қолдиқдан ечмайди (омборда йўқ бўлса),
    шунинг учун 1С ҳужжатлари бўйича ҳаракат регистрдан олинади. 1С базаси бўлмаса — None (ҳужжат қаторлари ишлатилади)."""
    try:
        rows = c1.table("RA410")
    except Exception:
        return None
    out = defaultdict(lambda: defaultdict(float))
    for r in rows:
        out[r["IDDOC"]][r["SP412"]] += (r["SP413"] or 0) * (1 if int(r["DEBKRED"] or 0) == 0 else -1)
    return out

def stock_report(con, d1, d2, keep=None, svc=False):
    """-> {good: dict(beg, inc, out, end, moves=[...])}. Приход — поступление; расход — накладной (возврат — минус расход)."""
    goods = _goods(con)
    skip = set() if svc else _svc_set(con, goods)
    cur = erp.stock_all(con)
    res = {}
    def row(g):
        if g not in res:
            res[g] = dict(beg=0.0, inc=0.0, out=0.0, end=0.0, after=0.0, moves=[])
        return res[g]
    reg = _c1_stock_moves()                       # 1С'да ҳужжат қолдиқни қанча ўзгартирган (регистр «ОстатокТов»)
    nm = erp.norms()
    sql = ("SELECT d.id, d.ext, d.kind, d.no, d.date, d.time, d.src, d.status, c.name cname, l.good, SUM(l.qty) qty FROM erp_lines l "
           "JOIN erp_docs d ON d.id=l.doc_id LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.kind IN ('sale','receipt','inv') AND d.date>=? "
           "AND COALESCE(l.good,'')<>'' GROUP BY d.id, l.good ORDER BY d.date, d.time, d.id")
    items, seen = [], set()
    for m in con.execute(sql, (d1.isoformat(),)):
        sign = 1 if m["kind"] in ("receipt", "inv") else -1
        dq = float(m["qty"] or 0) if m["status"] == "posted" else 0.0
        if reg is not None and m["src"] == "1c":
            q = reg.get(m["ext"], {}).get(m["good"], 0.0) * sign      # 1С регистридаги сон (ҳужжат йўналишида)
            seen.add((m["ext"], m["good"]))
            items.append((m, m["good"], q, dq if abs(q - dq) > 0.0005 else None))
        elif m["status"] == "posted":
            items.append((m, m["good"], dq, None))
            if m["kind"] == "sale":                    # платформа накладнойи: хизмат нормативи бўйича товар ҳам чиқади (кромка, клей)
                for cg, k in nm.get(m["good"], ()):
                    items.append((m, cg, round(dq * k, 6), None))
    if reg is not None:                               # регистрда бор, ҳужжат қаторида йўқ (камдан-кам)
        docs = {r["ext"]: r for r in con.execute("SELECT d.id, d.ext, d.kind, d.no, d.date, d.time, d.src, d.status, c.name cname FROM erp_docs d "
                                                  "LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.src='1c' AND d.kind IN ('sale','receipt') AND d.date>=?",
                                                  (d1.isoformat(),))}
        for ext, gm in reg.items():
            d = docs.get(ext)
            if d:
                for g, v in gm.items():
                    if (ext, g) not in seen and abs(v) > 0.0005:
                        items.append((d, g, v * (1 if d["kind"] == "receipt" else -1), 0.0))
        items.sort(key=lambda x: (x[0]["date"], x[0]["time"] or "", x[0]["id"]))
    s2 = d2.isoformat()
    for m, g, q, warn in items:
        if g in skip or g not in goods or (keep is not None and g not in keep) or (abs(q) < 0.0005 and warn is None):
            continue
        sign = 1 if m["kind"] in ("receipt", "inv") else -1
        r = row(g)
        if m["date"] > s2:
            r["after"] += sign * q
            continue
        if m["kind"] == "inv":                         # инвентаризация: излишек — приход, недостача — расход
            inc, out = (q, 0.0) if q > 0 else (0.0, -q)
        else:
            inc, out = (q, 0.0) if m["kind"] == "receipt" else (0.0, q)
        r["inc"] += inc
        r["out"] += out
        r["moves"].append(dict(id=m["id"], kind=m["kind"], no=m["no"], date=m["date"], src=m["src"], cname=m["cname"] or "",
                               inc=inc, out=out, warn=warn))
    for g, q in cur.items():
        if g in goods and g not in skip and (keep is None or g in keep) and abs(q or 0) > 0.0005:
            row(g)
    for g, r in res.items():
        r["end"] = round((cur.get(g) or 0) - r["after"], 3)
        r["beg"] = round(r["end"] - r["inc"] + r["out"], 3)
        run = r["beg"]
        for m in r["moves"]:
            run = round(run + m["inc"] - m["out"], 3)
            m["bal"] = run
        r["inc"], r["out"] = round(r["inc"], 3), round(r["out"], 3)
    return goods, res


# ---------------------------------------------------------------- 2/3. Накладной ва приход қаторлари
def doc_lines(con, kind, d1, d2, keep=None, cset=None, nal="", src="", what="", goods=None):
    goods = goods or _goods(con)
    svc = _svc_set(con, goods) if what in ("goods", "svc") else set()
    sql = ("SELECT d.id, d.no, d.date, d.time, d.src, d.client, d.nal, d.usd dusd, d.uzs duzs, d.paid, d.ref_doc, c.name cname, "
           "l.good, l.qty, l.price, l.usd, l.price_uzs, l.uzs FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id "
           "LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.kind=? AND d.status='posted' AND d.date BETWEEN ? AND ?")
    args = [kind, d1.isoformat(), d2.isoformat()]
    if nal in ("1", "0"):
        sql += " AND COALESCE(d.nal,1)" + ("<>0" if nal == "1" else "=0")
    if src in ("1c", "pf"):
        sql += " AND d.src=?"; args.append(src)
    out = []
    for r in con.execute(sql + " ORDER BY d.date, d.time, d.id, l.lineno", args):
        g = r["good"] or ""
        if keep is not None and g not in keep:
            continue
        if cset is not None and r["client"] not in cset:
            continue
        if what == "goods" and g in svc or what == "svc" and g not in svc:
            continue
        gi = goods.get(g, {})
        out.append(dict(r, gname=gi.get("name", g), unit=gi.get("unit", ""), group=gi.get("group", ""), code=gi.get("code", "")))
    return out


def _group_docs(lines):
    docs = {}
    for l in lines:
        d = docs.setdefault(l["id"], dict(id=l["id"], no=l["no"], date=l["date"], src=l["src"], client=l["client"], cname=l["cname"] or "",
                                          nal=l["nal"], ref_doc=l["ref_doc"], qty=0.0, usd=0.0, uzs=0.0, n=0, dusd=l["dusd"] or 0, paid=l["paid"] or 0))
        d["qty"] += l["qty"] or 0; d["usd"] += l["usd"] or 0; d["uzs"] += l["uzs"] or 0; d["n"] += 1
    return sorted(docs.values(), key=lambda x: (x["date"], x["id"]))


def _group_goods(lines):
    gs = {}
    for l in lines:
        g = gs.setdefault(l["good"], dict(good=l["good"], name=l["gname"], unit=l["unit"], group=l["group"], qty=0.0, usd=0.0, uzs=0.0, docs=set()))
        g["qty"] += l["qty"] or 0; g["usd"] += l["usd"] or 0; g["uzs"] += l["uzs"] or 0; g["docs"].add(l["id"])
    return sorted(gs.values(), key=lambda x: (x["group"].casefold(), x["name"].casefold()))


def _group_clients(lines):
    cs = {}
    for l in lines:
        c = cs.setdefault(l["client"], dict(client=l["client"], name=l["cname"] or "", qty=0.0, usd=0.0, uzs=0.0, docs=set(), last=""))
        c["qty"] += l["qty"] or 0; c["usd"] += l["usd"] or 0; c["uzs"] += l["uzs"] or 0; c["docs"].add(l["id"]); c["last"] = max(c["last"], l["date"])
    return sorted(cs.values(), key=lambda x: -x["usd"])


# ---------------------------------------------------------------- 4. Взаиморасчеты
def _c1_moves():
    try:
        return c1.all_moves(), c1.debts()[1]
    except Exception:
        return {}, {}


def mutual(con, d1, d2, cset=None, kinds=("client",)):
    """-> [dict(client, name, kind, beg, taken, paid, end, now, moves)] қарз $ (мусбат — бизга қарз, манфий — аванс)."""
    cl = _clients(con)
    mv1, bal1 = _c1_moves()
    ext2id = {r[0]: r[1] for r in con.execute("SELECT ext, id FROM erp_docs WHERE src='1c' AND ext IS NOT NULL")}
    moves = defaultdict(list)                     # amt: + қарз ошди (олди), − камайди (тўлади)
    for cid, ms in mv1.items():
        for (dt, tm, no, kind, amt, iddoc, _ln) in ms:
            moves[cid].append(dict(date=dt.isoformat(), time=tm or "", no=no, kname=c1.DOC_NAMES.get(kind, str(kind)), amt=-amt,
                                   id=ext2id.get(iddoc), src="1c"))
    now = defaultdict(float)
    for cid, v in bal1.items():
        now[cid] = -v
    for r in con.execute("SELECT id, no, date, time, client, usd, COALESCE(paid,0) paid, ref_doc FROM erp_docs WHERE src='pf' AND kind='sale' "
                         "AND status='posted' AND COALESCE(nal,1)=1"):
        moves[r["client"]].append(dict(date=r["date"], time=r["time"] or "", no=r["no"], kname="Возврат" if r["ref_doc"] else "Накладная",
                                       amt=r["usd"] or 0, id=r["id"], src="pf"))
        if r["paid"]:
            moves[r["client"]].append(dict(date=r["date"], time=r["time"] or "", no=r["no"], kname="Оплата в накладной", amt=-r["paid"], id=r["id"], src="pf"))
        now[r["client"]] += (r["usd"] or 0) - r["paid"]
    for r in con.execute("SELECT d.id, d.no, d.date, d.time, l.client, SUM(l.debt) debt FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id "
                         "WHERE d.src='pf' AND d.kind='pay' AND d.status='posted' GROUP BY d.id, l.client"):
        moves[r["client"]].append(dict(date=r["date"], time=r["time"] or "", no=r["no"], kname="Поступление денег", amt=-(r["debt"] or 0), id=r["id"], src="pf"))
        now[r["client"]] -= r["debt"] or 0
    s1, s2 = d1.isoformat(), d2.isoformat()
    out = []
    for cid in set(moves) | set(now):
        c = cl.get(cid)
        if not c or (kinds and c["kind"] not in kinds) or (cset is not None and cid not in cset):
            continue
        ms = sorted(moves.get(cid, []), key=lambda m: (m["date"], m["time"]))
        after = sum(m["amt"] for m in ms if m["date"] > s2)
        end = round(now.get(cid, 0.0) - after, 2)
        inp = [m for m in ms if s1 <= m["date"] <= s2]
        taken = round(sum(m["amt"] for m in inp if m["amt"] > 0), 2)
        paid = round(-sum(m["amt"] for m in inp if m["amt"] < 0), 2)
        beg = round(end - taken + paid, 2)
        run = beg
        for m in inp:
            run = round(run + m["amt"], 2); m["bal"] = run
        out.append(dict(client=cid, name=c["name"], kind=c["kind"], beg=beg, taken=taken, paid=paid, end=end,
                        now=round(now.get(cid, 0.0), 2), moves=inp))
    out.sort(key=lambda r: (-r["end"], r["name"].casefold()))
    return out



def open_docs(con, ext, debt, today=None):
    """«По остаткам» битта мижоз учун: тўланмаган накладнойлар (бугунги қарз энг янги накладнойларда, тўловлар энг эскисидан ёпилади).
    -> (rows, old) rows=[dict(id,no,date,usd,part,due,days)], old — накладнойларга тушмаган эски қолдиқ (1С бошланғич қарзи)."""
    today = today or date.today()
    due = erp.client_due(con, ext)
    start = A.get_setting("erp_due_start", "") or today.isoformat()   # муддат назорати шу кундан (erp.overdue билан бир хил)
    remaining = round(debt or 0, 2)
    out = []
    if remaining <= 0.01:
        return out, 0.0
    for d in con.execute("SELECT id, no, date, usd, COALESCE(paid,0) paid FROM erp_docs WHERE kind='sale' AND status='posted' "
                         "AND COALESCE(nal,1)=1 AND client=? AND usd>0 ORDER BY date DESC, time DESC, id DESC", (ext,)):
        part = round(min(remaining, max(0.0, (d["usd"] or 0) - (d["paid"] or 0))), 2)
        if part > 0.01:
            dd = A.parse_date(d["date"])
            dl = dd + timedelta(days=due) if dd else None
            out.append(dict(id=d["id"], no=d["no"], date=d["date"], usd=round(d["usd"] or 0, 2), part=part,
                            due=dl.isoformat() if dl else "", days=max(0, (today - dl).days) if dl and d["date"] >= start else 0))
            remaining = round(remaining - part, 2)
        if remaining <= 0.01:
            break
    out.reverse()
    return out, max(0.0, remaining)


def by_days(moves):
    """«Кратко» битта мижоз учун: кунлар бўйича олди / тўлади / кун охиридаги қарз."""
    out = {}
    for m in moves:
        x = out.setdefault(m["date"], dict(date=m["date"], taken=0.0, paid=0.0, bal=0.0, docs=[]))
        if m["amt"] > 0:
            x["taken"] += m["amt"]
        else:
            x["paid"] += -m["amt"]
        x["bal"] = m["bal"]
        t = f"{m['kname']} № {m['no']}"
        if t not in x["docs"]:
            x["docs"].append(t)
    return [dict(v, taken=round(v["taken"], 2), paid=round(v["paid"], 2)) for k, v in sorted(out.items())]

# ---------------------------------------------------------------- маршрутлар
def register(app_module):
    global A
    A = app_module
    app = A.app
    lr = A.login_required

    @app.route("/erp/rep/stock")
    @lr
    def rep_stock():
        con = A.db()
        d1, d2 = _period()
        mode = _arg("mode", ("short", "detail", "only"), "short")
        svc = request.args.get("svc") == "1"
        zero = request.args.get("zero") == "1"
        goods = _goods(con)
        keep, gf = _gfilter(con, goods)
        goods, res = stock_report(con, d1, d2, keep, svc)
        rows = []
        for g, r in res.items():
            if not zero and abs(r["beg"]) < 0.0005 and abs(r["end"]) < 0.0005 and not r["moves"]:
                continue
            if mode == "only" and not zero and abs(r["end"]) < 0.0005:
                continue
            rows.append(dict(r, g=goods[g]))
        rows.sort(key=lambda x: (x["g"]["group"].casefold(), x["g"]["name"].casefold()))
        if request.args.get("csv") == "1":
            if mode == "only":
                return _csv("ostatok", ["Папка", "Код", "Товар", "Ед", "Остаток", "Факт (склад)", "Фарқ"],
                            [(r["g"]["group"], r["g"]["code"], r["g"]["name"], r["g"]["unit"], r["end"], None, None) for r in rows],
                            kinds=["text", "text", "text", "text", "qty", "qty", "qty"], totals=False)
            if mode == "detail":
                out = []
                for r in rows:
                    out.append((r["g"]["name"], None, "Нач. остаток", None, None, None, r["beg"]))
                    for m in r["moves"]:
                        out.append((r["g"]["name"], m["date"], erp.KIND_NAMES[m["kind"]] + " № " + m["no"], m["cname"], m["inc"] or None, m["out"] or None, m["bal"]))
                    out.append((r["g"]["name"], None, "Кон. остаток", None, None, None, r["end"]))
                return _csv("ostatok_podrobno", ["Товар", "Сана", "Ҳужжат", "Контрагент", "Приход", "Расход", "Остаток"], out,
                            kinds=["text", "date", "text", "text", "qty", "qty", "qty"], totals=False,
                            main_title="Ҳаракатлар")
            return _csv("ostatok_kratko", ["Папка", "Код", "Товар", "Ед", "Нач. остаток", "Приход", "Расход", "Кон. остаток"],
                        [(r["g"]["group"], r["g"]["code"], r["g"]["name"], r["g"]["unit"], r["beg"], r["inc"], r["out"], r["end"]) for r in rows],
                        kinds=["text", "text", "text", "text", "qty", "qty", "qty", "qty"],
                        chart=dict(cat=2, vals=[6], title="Энг кўп чиққан товарлар (расход)", top=10), main_title="Товарлар",
                        kpis=[("Товарлар", len(rows), "int"), ("Приход", sum(r["inc"] for r in rows), "qty"), ("Расход", sum(r["out"] for r in rows), "qty")])
        limit = 400 if mode == "detail" else 5000
        tot = dict(beg=sum(r["beg"] for r in rows), inc=sum(r["inc"] for r in rows), out=sum(r["out"] for r in rows), end=sum(r["end"] for r in rows))
        return render_template("rep_stock.html", rows=rows[:limit], n=len(rows), limit=limit, mode=mode, d1=d1, d2=d2, svc=svc, zero=zero,
                               gf=gf, tot=tot, KIND_NAMES=erp.KIND_NAMES)

    def _lines_report(kind):
        con = A.db()
        d1, d2 = _period()
        goods = _goods(con)
        keep, gf = _gfilter(con, goods)
        cl = _clients(con)
        ctext = (request.args.get("c") or "").strip()
        cx = (request.args.get("cx") or "").strip()
        cset = {cx} if cx else _cmatch(cl, ctext)
        nal = _arg("nal", ("", "1", "0"), "") if kind == "sale" else ""
        src = _arg("src", ("", "1c", "pf"), "")
        what = _arg("what", ("", "goods", "svc"), "goods" if kind == "receipt" and request.args.get("only") == "1" else "")
        lines = doc_lines(con, kind, d1, d2, keep, cset, nal, src, what, goods)
        return con, d1, d2, gf, dict(c=ctext, cx=cx, cname=cl.get(cx, {}).get("name", "") if cx else "", nal=nal, src=src, what=what), lines

    @app.route("/erp/rep/sales")
    @lr
    def rep_sales():
        con, d1, d2, gf, cf, lines = _lines_report("sale")
        mode = _arg("mode", ("docs", "goods", "clients", "lines"), "docs")
        pay = _arg("pay", ("", "paid", "unpaid"), "")
        docs = _group_docs(lines)
        unpaid = {}
        if pay or mode == "docs":
            for ext in {d["client"] for d in docs if d["client"] and d["nal"] != 0}:
                unpaid.update(erp.doc_unpaid(con, ext))
            for d in docs:
                d["unpaid"] = unpaid.get(d["id"], 0.0) if d["nal"] != 0 else 0.0
            if pay:
                ids = {d["id"] for d in docs if (d["unpaid"] > 0.01) == (pay == "unpaid")}
                docs = [d for d in docs if d["id"] in ids]
                lines = [l for l in lines if l["id"] in ids]
        if mode == "goods":
            rows = _group_goods(lines)
        elif mode == "clients":
            rows = _group_clients(lines)
            for r in rows:
                r["debt"] = erp.client_debt(con, r["client"]) if r["client"] else 0.0
        elif mode == "lines":
            rows = lines
        else:
            rows = docs
        tot = dict(qty=sum(l["qty"] or 0 for l in lines), usd=sum(l["usd"] or 0 for l in lines), uzs=sum(l["uzs"] or 0 for l in lines),
                   docs=len({l["id"] for l in lines}), unpaid=sum(d.get("unpaid", 0) for d in docs))
        if request.args.get("csv") == "1":
            return _csv_lines("nakladnye", mode, rows)
        return render_template("rep_docs.html", kind="sale", title="Отчет по накладным", rows=rows[:3000], n=len(rows), mode=mode, pay=pay,
                               d1=d1, d2=d2, gf=gf, cf=cf, tot=tot)

    @app.route("/erp/rep/receipts")
    @lr
    def rep_receipts():
        con, d1, d2, gf, cf, lines = _lines_report("receipt")
        mode = _arg("mode", ("docs", "goods", "clients", "lines"), "docs")
        rows = {"goods": _group_goods, "clients": _group_clients}.get(mode, _group_docs)(lines) if mode != "lines" else lines
        tot = dict(qty=sum(l["qty"] or 0 for l in lines), usd=sum(l["usd"] or 0 for l in lines), uzs=sum(l["uzs"] or 0 for l in lines),
                   docs=len({l["id"] for l in lines}), unpaid=0)
        if request.args.get("csv") == "1":
            return _csv_lines("prihod", mode, rows)
        return render_template("rep_docs.html", kind="receipt", title="Отчет по приходу", rows=rows[:3000], n=len(rows), mode=mode, pay="",
                               d1=d1, d2=d2, gf=gf, cf=cf, tot=tot)

    def _csv_lines(name, mode, rows):
        def _k(rs, usd="usd", uzs="uzs"):
            return [("Сумма $", sum(r.get(usd) or 0 for r in rs), "money"), ("Сумма сўм", sum(r.get(uzs) or 0 for r in rs), "som"), ("Қаторлар", len(rs), "int")]
        if mode == "goods":
            return _csv(name + "_tovary", ["Папка", "Товар", "Ед", "Сони", "$", "сўм", "Ҳужжатлар"],
                        [(r["group"], r["name"], r["unit"], r["qty"], r["usd"], r["uzs"], len(r["docs"])) for r in rows],
                        kinds=["text", "text", "text", "qty", "money", "som", "int"], chart=dict(cat=1, vals=[4], title="Товарлар бўйича, $", top=10),
                        kpis=_k(rows), main_title="Товарлар")
        if mode == "clients":
            return _csv(name + "_klienty", ["Контрагент", "Ҳужжатлар", "Сони", "$", "сўм", "Охирги"],
                        [(r["name"], len(r["docs"]), r["qty"], r["usd"], r["uzs"], r["last"]) for r in rows],
                        kinds=["text", "int", "qty", "money", "som", "date"], chart=dict(cat=0, vals=[3], title="Контрагентлар бўйича, $", top=10),
                        kpis=_k(rows), main_title="Контрагентлар")
        if mode == "lines":
            return _csv(name + "_podrobno", ["Сана", "№", "Контрагент", "Товар", "Ед", "Сони", "Нарх", "$", "сўм"],
                        [(r["date"], r["no"], r["cname"], r["gname"], r["unit"], r["qty"], r["price"], r["usd"], r["uzs"]) for r in rows],
                        kinds=["date", "text", "text", "text", "text", "qty", "money", "money", "som"],
                        chart=dict(cat=3, vals=[7], title="Товарлар бўйича, $", top=10), kpis=_k(rows), main_title="Қаторлар")
        return _csv(name, ["Сана", "№", "Контрагент", "Манба", "Сони", "$", "сўм", "Тўланмаган $"],
                    [(r["date"], r["no"], r["cname"], "Платформа" if r["src"] == "pf" else "1С", r["qty"], r["usd"], r["uzs"], r.get("unpaid") or None) for r in rows],
                    kinds=["date", "text", "text", "text", "qty", "money", "som", "money"], chart=dict(cat=2, vals=[5], title="Контрагентлар бўйича, $", top=10),
                    kpis=_k(rows) + [("Тўланмаган $", sum(r.get("unpaid") or 0 for r in rows), "debt")], main_title="Ҳужжатлар")

    @app.route("/erp/rep/mutual")
    @lr
    def rep_mutual():
        con = A.db()
        d1, d2 = _period()
        mode = _arg("mode", ("short", "detail", "bal"), "short")
        kind = _arg("ck", ("client", "supplier", "account", "partner", "all"), "client")
        od = request.args.get("od") == "1"
        zero = request.args.get("zero") == "1"
        ctext = (request.args.get("c") or "").strip()
        cx = (request.args.get("cx") or "").strip()
        if cx and request.args.get("pick") == "1":
            mode = "detail"                              # контрагент танланганда — дарров ҳужжатлар тарихи (1С'дагидек); кейин тугмалар ишлайди
        cl = _clients(con)
        cset = {cx} if cx else _cmatch(cl, ctext)
        rows = mutual(con, d1, d2, cset, None if kind == "all" else (kind,))
        if not zero and not cx:
            rows = [r for r in rows if abs(r["beg"]) >= 0.01 or abs(r["end"]) >= 0.01 or r["moves"]]
        if mode == "bal" and not cx:
            rows = [r for r in rows if zero or abs(r["end"]) >= 0.01]
        today = date.today()
        for r in rows:
            r["od"] = erp.overdue(con, r["client"]) if r["now"] > 0.5 and r["kind"] == "client" else []
            r["od_sum"] = round(sum(o["part"] for o in r["od"]), 2)
            r["od_days"] = max((o["days"] for o in r["od"]), default=0)
        if od:
            rows = [r for r in rows if r["od"]]
        tot = dict(beg=sum(r["beg"] for r in rows), taken=sum(r["taken"] for r in rows), paid=sum(r["paid"] for r in rows),
                   end=sum(r["end"] for r in rows), debt=sum(r["end"] for r in rows if r["end"] > 0), adv=-sum(r["end"] for r in rows if r["end"] < 0),
                   od=sum(r["od_sum"] for r in rows))
        if request.args.get("csv") == "1":
            return _mutual_xlsx(con, rows, tot, mode, cx, d1, d2)
        limit = 300 if mode == "detail" else 5000
        one = rows[0] if cx and len(rows) == 1 else None
        days, odocs, oold, first = [], [], 0.0, ""
        if cx:
            if one:
                days = by_days(one["moves"])
                odocs, oold = open_docs(con, cx, one["now"])
            fm = mutual(con, date(2000, 1, 1), today, {cx}, None)
            first = fm[0]["moves"][0]["date"] if fm and fm[0]["moves"] else ""
        return render_template("rep_mutual.html", one=one, days=days, odocs=odocs, oold=oold, first=first, rows=rows[:limit], n=len(rows), limit=limit, mode=mode, ck=kind, od=od, zero=zero, d1=d1, d2=d2,
                               c=ctext, cx=cx, cname=cl.get(cx, {}).get("name", "") if cx else "", tot=tot, today=today,
                               CK={"client": "Мижозлар", "supplier": "Етказиб берувчилар", "account": "Ҳисоблар", "partner": "Шериклар", "all": "Ҳаммаси"})
