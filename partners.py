# -*- coding: utf-8 -*-
"""Шериклар ҳисоби — иккита алоҳида ҳисоб.

1. Улуш ҳисоби (Excel'дагидек): ой давомида кассанинг «Шериклар» устунидан олинган жами пул улуш нисбатида (Справочник)
   тақсимланади; Қолди = тегишли − олгани. Ой охирида 1С'да ҳар бирининг ҳисобига ± қилинади (тенглаштириш ҳужжати).
2. Қарз ҳисоби (қайтадиган пул): шерик олган товар/хизмат (накладной), 1С ёки карта орқали олган пул, тўлагани.
   Тақсимланмайди. 1С'да иккаласи шерикнинг битта «мижоз»ида аралаш — тенглаштириш ҳужжати (фақат шериклар қатори, жами ≈ 0)
   ўзи танилади ва улуш ҳисобига ўтказилади.
"""
import json, re, time
from datetime import date, timedelta

from flask import request, render_template, redirect, url_for, flash, abort

import c1

A = None
PARTNER_RE = re.compile(r"founder|ceo|seo", re.I)
EQ_FROM = "2026-09"


def owners(ym):
    import kassa as K
    return [o for o in K.items("owner", ym) if o["share"]]


def pmap(con=None):
    """{kassa шерик (k_items id): 1С мижоз ext}. Созлама бўлмаса — номдан топилади (Жамшид → «Жамшид ака Founder»)."""
    con = con or A.db()
    try:
        r_ = con.execute("SELECT value FROM settings WHERE key='partners_map'").fetchone()
        m = {int(k): v for k, v in json.loads((r_[0] if r_ else "") or "{}").items() if v}
    except (ValueError, TypeError):
        m = {}
    for o in con.execute("SELECT id, name FROM k_items WHERE kind='owner' AND share>0"):
        if o["id"] in m:
            continue
        first = (o["name"] or "").split()[0].casefold() if o["name"] else ""
        for c in con.execute("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0"):
            nm = (c["name"] or "")
            if first and nm.casefold().startswith(first) and PARTNER_RE.search(nm):
                m[o["id"]] = c["ext"]
                break
    return m


def mark_partner_clients(con):
    """Шерикларнинг 1С ҳисоблари — «шерик» тури: лимит, блок ва эслатма йўқ."""
    for ext in set(pmap(con).values()):
        con.execute("UPDATE erp_clients SET ckind='partner', ckind_set=1, blocked=0 WHERE ext=? AND COALESCE(ckind,'')<>'partner'", (ext,))


def equalization_docs(con, exts):
    """Ой охиридаги тенглаштириш ҳужжатлари (1С ёки платформа): Поступление денег, проведен, қаторлари фақат шерикларники, жами ≈ 0.
    -> {doc id: dict(ext=1С ҳужжат ext ёки None, date, no, lines={client: usd})}"""
    out = {}
    if not exts:
        return out
    q = ",".join("?" * len(exts))
    for d in con.execute(f"SELECT DISTINCT d.id, d.ext, d.date, d.no FROM erp_docs d JOIN erp_lines l ON l.doc_id=d.id WHERE d.kind='pay' "
                         f"AND d.status='posted' AND l.client IN ({q})", list(exts)).fetchall():
        ls = con.execute("SELECT client, usd, uzs FROM erp_lines WHERE doc_id=?", (d["id"],)).fetchall()
        if len(ls) >= 2 and all(l["client"] in exts for l in ls) and abs(sum(l["usd"] or 0 for l in ls)) < 0.5 \
                and all(abs(l["uzs"] or 0) < 1 for l in ls):
            lines = {}
            for l in ls:
                lines[l["client"]] = lines.get(l["client"], 0) + (l["usd"] or 0)
            out[d["id"]] = dict(ext=d["ext"], date=d["date"], no=d["no"], lines=lines)
    return out


def debt_ledger(con, ext, d1, d2, eqd):
    """Қарз ҳисоби (1С ҳаракатлари бўйича, $; плюс — шерик қарздор). -> dict
    pf / pf_eq — платформада ёзилган (1С'да йўқ) накладнойлар ва улуш тенглаштириши (close'га кирмайди, алоҳида кўрсатилади)."""
    eq = {v["ext"] for v in eqd.values() if v["ext"]}
    r = dict(open=0.0, goods=0.0, inv_paid=0.0, taken=0.0, paid=0.0, eq=0.0, other=0.0, close=0.0, pf=0.0, pf_eq=0.0, rows=[])
    try:
        moves = c1.client_moves(ext)
    except Exception:
        moves = []
    sales = {x["ext"]: x for x in con.execute("SELECT ext, no, usd, COALESCE(paid,0) paid FROM erp_docs WHERE src='1c' AND kind='sale' AND client=?",
                                               (ext,))}
    by_doc = {}
    for m in moves:
        dd = m["doc"]["date"] if m["doc"] else None
        if dd and dd < d1:
            r["open"] = -m["balance"]
    r["close"] = r["open"]
    for m in moves:
        dd = m["doc"]["date"] if m["doc"] else None
        if not dd or dd < d1 or dd > d2:
            continue
        r["close"] = -m["balance"]
        by_doc.setdefault(m["iddoc"], [m["doc"], 0.0])[1] += m["amount"]
    for iddoc, (doc, amt) in by_doc.items():
        kind = doc.get("kind")
        if iddoc in eq:
            r["eq"] += amt                                    # плюс — шерикка ёзилди (улуш)
        elif kind == 210:
            s = sales.get(iddoc)
            g = (s["usd"] if s else -amt) or 0
            r["goods"] += g
            r["inv_paid"] += g + amt                          # накладнойнинг ўзида тўлангани
        elif kind == 1195:
            if amt >= 0:
                r["paid"] += amt
            else:
                r["taken"] += -amt
        else:
            r["other"] += -amt
    # платформада ёзилган (1С'да ҳали йўқ) накладнойлар
    r["pf"] = con.execute("SELECT COALESCE(SUM(usd-COALESCE(paid,0)),0) FROM erp_docs WHERE src='pf' AND kind='sale' AND status='posted' "
                          "AND COALESCE(nal,1)=1 AND client=? AND date BETWEEN ? AND ?", (ext, d1.isoformat(), d2.isoformat())).fetchone()[0] or 0
    a, b = d1.isoformat(), d2.isoformat()
    r["pf_eq"] = sum(v["lines"].get(ext, 0) for v in eqd.values() if not v["ext"] and a <= (v["date"] or "") <= b)
    for k in ("open", "goods", "inv_paid", "taken", "paid", "eq", "other", "close", "pf", "pf_eq"):
        r[k] = round(r[k], 2)
    return r


def ym_last(ym):
    import kassa as K
    y, mo = K.ym_parse(ym)
    return date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1)


def eq_from():
    """Шу ойдан бошлаб тенглаштирилмаган қолдиқ кейинги ойга ўтади (август 1С'да №237 билан ёпилган)."""
    v = (A.get_setting("partners_eq_from", EQ_FROM) or EQ_FROM).strip()
    return v if re.fullmatch(r"\d{4}-\d{2}", v) else EQ_FROM


def _month(con, ym, m, eqd):
    """Бир ой: [dict(item, ext, taken, due, qoldi, posted, docs)]."""
    import kassa as K
    d1, d2 = ym_last(K.ym_shift(ym, -1)) + timedelta(days=1), ym_last(ym)
    eq_month = {}
    for v in eqd.values():
        if d1.isoformat() <= (v["date"] or "") <= d2.isoformat():
            for cl, u in v["lines"].items():
                eq_month.setdefault(cl, []).append(dict(no=v["no"], date=v["date"], usd=u))
    mc = K.month_calc(ym) if K.has_data(ym) else None
    out = []
    for o in (mc["owners"] if mc else [dict(item=x, taken=0.0, due=0.0) for x in owners(ym)]):
        it = o["item"]
        if not it["share"]:
            continue
        ext = m.get(it["id"])
        docs = eq_month.get(ext, []) if ext else []
        out.append(dict(item=it, ext=ext, taken=round(o["taken"], 2), due=round(o["due"], 2), qoldi=round(o["due"] - o["taken"], 2),
                        posted=round(sum(x["usd"] for x in docs), 2), docs=docs))
    return out, mc


def report(ym, debt=True):
    import kassa as K
    con = A.db()
    d1 = ym_last(K.ym_shift(ym, -1)) + timedelta(days=1)
    d2 = ym_last(ym)
    m = pmap(con)
    exts = set(m.values())
    eqd = equalization_docs(con, exts)
    base, mc = _month(con, ym, m, eqd)
    # олдинги ойлардан қолган (тенглаштирилмаган) қолдиқ: eq_from'дан шу ойгача
    carry, months = {}, []
    k, start = K.ym_shift(ym, -1), eq_from()
    while k >= start and len(months) < 24:
        if K.has_data(k):
            rows_k, _ = _month(con, k, m, eqd)
            months.append(k)
            for r in rows_k:
                carry[r["item"]["id"]] = carry.get(r["item"]["id"], 0) + r["qoldi"] - r["posted"]
        k = K.ym_shift(k, -1)
    # кейинги ойлардаги ҳужжат шу ойни ҳам ёпганми (жамғарма бўйича)
    later = {}
    k, today = K.ym_shift(ym, 1), K.ym_today()
    run = {r["item"]["id"]: carry.get(r["item"]["id"], 0) + r["qoldi"] - r["posted"] for r in base} if ym >= start else {}
    while run and k <= today:
        if K.has_data(k):
            rows_k, _ = _month(con, k, m, eqd)
            for r in rows_k:
                i = r["item"]["id"]
                if i in run and i not in later:
                    if r["docs"] and abs(run[i] + r["qoldi"] - r["posted"]) < 0.5:
                        later[i] = r["docs"]
                    run[i] += r["qoldi"] - r["posted"]
        k = K.ym_shift(k, 1)
    rows = []
    for r in base:
        it, ext = r["item"], r["ext"]
        cl = con.execute("SELECT name FROM erp_clients WHERE ext=?", (ext,)).fetchone() if ext else None
        cy = round(carry.get(it["id"], 0), 2)
        rows.append(dict(item=it, ext=ext, cname=cl[0] if cl else "", share=it["share"], taken=r["taken"], due=r["due"], qoldi=r["qoldi"],
                         carry=cy, posted=r["posted"], posted_docs=r["docs"], need=round(cy + r["qoldi"] - r["posted"], 2),
                         later=later.get(it["id"], []), debt=(debt_ledger(con, ext, d1, d2, eqd) if ext else None) if debt else None))
    wsum = sum(r["share"] for r in rows) or 1
    for r in rows:
        r["pct"] = round(r["share"] * 100 / wsum, 1)
    return dict(ym=ym, d1=d1, d2=d2, rows=rows, mc=mc, profit=(mc["profit"] if mc else None), taken=sum(r["taken"] for r in rows),
                carry_months=sorted(months), has_carry=any(abs(r["carry"]) >= 0.005 for r in rows), draft=draft_info(con, ym))


# --- ой охири тенглаштириш черновиги (эгаси, 02.11: «Платформа ўзи тайёрлаши керак»; сентябрь+октябрь — октябрга) ---

def _drafts(con=None):
    try:
        v = json.loads(A.get_setting("partners_eq_draft", "") or "{}")
        return {str(k): int(x) for k, x in v.items()} if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def draft_info(con, ym):
    i = _drafts().get(ym)
    if not i:
        return None
    d = con.execute("SELECT id, no, date, status, usd FROM erp_docs WHERE id=?", (i,)).fetchone()
    return dict(d) if d else None


def draft_lines(rep):
    """Черновик қаторлари {ext: usd}: «Яна ёзиш керак» (олдинги ойлардан қолгани билан), жами аниқ 0. None — ёзиш шарт эмас ёки ҳисоб йўқ."""
    if any(not r["ext"] for r in rep["rows"]):
        return None
    ls = {}
    for r in rep["rows"]:
        if abs(r["need"]) >= 0.005:
            ls[r["ext"]] = ls.get(r["ext"], 0) + r["need"]
    ls = {k: round(v, 2) for k, v in ls.items() if abs(v) >= 0.005}
    if len(ls) < 2:
        return None
    s = round(sum(ls.values()), 2)
    if abs(s) > 1:
        A.errlog.warning("partners draft: жами %s (0 эмас) — черновик тайёрланмади", s)
        return None
    if s:
        k = max(ls, key=lambda x: abs(ls[x]))
        ls[k] = round(ls[k] - s, 2)
    return ls


def ensure_draft(ym):
    """Ўтган ой учун «Поступление денег» черновиги (ойнинг охирги куни, фақат шериклар, жами 0). Черновик бўлса — суммалар янгиланади;
    проведен ёки ўчирилган бўлса — тегилмайди. -> doc id ёки None."""
    import kassa as K, erp as E
    if ym < eq_from() or not K.has_data(ym):
        return None
    con = A.db()
    drafts = _drafts()
    did = drafts.get(ym)
    d = con.execute("SELECT * FROM erp_docs WHERE id=?", (did,)).fetchone() if did else None
    if did and (not d or d["status"] != "draft"):
        return did                                       # проведен, ўчирилган ёки қўлда ўчирилган — қайта тайёрланмайди
    if d and d["lock_by"] and E.lock_info(d):
        return did                                       # ҳозир кимдир таҳрирлаяпти
    rep = report(ym, debt=False)
    ls = draft_lines(rep)
    months = [x for x in rep["carry_months"] if any(abs(r["carry"]) >= 0.005 for r in rep["rows"])] + [ym]
    note = "Шериклар улуши тенглаштириш: " + ", ".join(K.ym_title(x) for x in months) + " (платформа тайёрлади)"
    if not ls:
        if d:
            con.execute("UPDATE erp_docs SET status='deleted', updated_by='тизим', updated_at=? WHERE id=?", (A.now(), d["id"]))
            E.log(con, d["id"], "delete", "тенглаштириш шарт эмас")
            con.commit()
        return did
    if d:
        old = {l["client"]: round(l["usd"] or 0, 2) for l in con.execute("SELECT client, usd FROM erp_lines WHERE doc_id=?", (d["id"],))}
        if old == ls and (d["note"] or "") == note:
            return did
    ddate = ym_last(ym)
    rate = E.day_rate(ddate)[0] or (d["rate"] if d else 0) or A.fx_rate() or 0
    ts, first = A.now(), sorted(ls, key=lambda x: -ls[x])[0]
    if d:
        con.execute("UPDATE erp_docs SET client=?, rate=?, usd=0, uzs=0, note=?, updated_by='тизим', updated_at=? WHERE id=?",
                    (first, rate, note, ts, d["id"]))
        con.execute("DELETE FROM erp_lines WHERE doc_id=?", (d["id"],))
        E.log(con, d["id"], "save", "тенглаштириш суммалари янгиланди")
        did = d["id"]
    else:
        con.commit()
        con.execute("BEGIN IMMEDIATE")
        no = E.next_no(con, "pay", ddate.year)
        did = con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,status,src,user,"
                          "created_by,created_at,updated_by,updated_at,ret) VALUES('pay',?,?,?,?,0,0,0,1,?,0,0,0,?,'draft','pf','тизим','тизим',?,"
                          "'тизим',?,1)", (no, "23:59:00", first, rate, note, ddate.isoformat(), ts, ts)).lastrowid
        E.log(con, did, "create", f"№ {no}, шериклар тенглаштириш")
    order = sorted(ls, key=lambda x: (-ls[x]))
    for n, cl in enumerate(order, 1):
        con.execute("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,cnotes) "
                    "VALUES(?,?,'',?,0,'',0,?,0,0,0,?,'','')", (did, n, cl, ls[cl], ls[cl]))
    drafts[ym] = did
    A.set_settings(partners_eq_draft=json.dumps(drafts))
    con.commit()
    return did


def tick(dt=None):
    """Режалаштирувчидан (ҳар дақиқа): 30 дақиқада бир марта ўтган ой черновигини текширади."""
    import kassa as K
    t = time.time()
    if t - A.parse_float(A.get_setting("partners_eq_at", "0"), 0) < 1800:
        return None
    A.set_settings(partners_eq_at=str(t))
    ym = K.ym_shift(K.ym_today(), -1)
    d = draft_info(A.db(), ym)
    if d and d["status"] != "draft":
        return d["id"]
    return ensure_draft(ym)


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        mark_partner_clients(con)
        con.commit()
    except Exception:
        A.errlog.exception("partners init")
    con.close()
    import erp as E
    E.CKINDS.setdefault("partner", "Шерик (корхона эгаси)")

    @app.route("/kassa/partners", methods=["GET", "POST"])
    @A.login_required
    def kassa_partners():
        if not A.is_admin():
            abort(403)
        import kassa as K
        ym = request.values.get("ym") or K.ym_today()
        if not K.ym_parse(ym):
            abort(404)
        con = A.db()
        if request.method == "POST":
            m = {}
            for o in owners(ym):
                v = (request.form.get(f"c_{o['id']}") or "").strip()
                if v and con.execute("SELECT 1 FROM erp_clients WHERE ext=?", (v,)).fetchone():
                    m[str(o["id"])] = v
            A.set_settings(partners_map=json.dumps(m, ensure_ascii=False))
            mark_partner_clients(con); con.commit()
            flash("Сақланди", "ok")
            return redirect(url_for("kassa_partners", ym=ym))
        if ym == K.ym_shift(K.ym_today(), -1):
            try:
                ensure_draft(ym)
            except Exception:
                A.errlog.exception("partners draft")
        rep = report(ym)
        cands = {}
        for r in rep["rows"]:
            first = (r["item"]["name"] or "").split()[0] if r["item"]["name"] else ""
            cands[r["item"]["id"]] = A.q("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0 AND lc LIKE ? ORDER BY name LIMIT 40",
                                         (f"%{first.casefold()}%",)) if first else []
        return render_template("kassa_partners.html", rep=rep, cands=cands, ym=ym, ym_title=K.ym_title(ym), prev_ym=K.ym_shift(ym, -1),
                               next_ym=K.ym_shift(ym, 1))
