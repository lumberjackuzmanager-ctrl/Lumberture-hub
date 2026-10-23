# -*- coding: utf-8 -*-
"""Шериклар ҳисоби — иккита алоҳида ҳисоб.

1. Улуш ҳисоби (Excel'дагидек): ой давомида кассанинг «Шериклар» устунидан олинган жами пул улуш нисбатида (Справочник)
   тақсимланади; Қолди = тегишли − олгани. Ой охирида 1С'да ҳар бирининг ҳисобига ± қилинади (тенглаштириш ҳужжати).
2. Қарз ҳисоби (қайтадиган пул): шерик олган товар/хизмат (накладной), 1С ёки карта орқали олган пул, тўлагани.
   Тақсимланмайди. 1С'да иккаласи шерикнинг битта «мижоз»ида аралаш — тенглаштириш ҳужжати (фақат шериклар қатори, жами ≈ 0)
   ўзи танилади ва улуш ҳисобига ўтказилади.
"""
import json, re
from datetime import date, timedelta

from flask import request, render_template, redirect, url_for, flash, abort

import c1

A = None
PARTNER_RE = re.compile(r"founder|ceo|seo", re.I)


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
    """Ой охиридаги тенглаштириш ҳужжатлари: Поступление денег, қаторлари фақат шерикларники, жами ≈ 0. -> {ext ҳужжат: {client: usd}}"""
    out = {}
    if not exts:
        return out
    q = ",".join("?" * len(exts))
    for d in con.execute(f"SELECT DISTINCT d.id, d.ext FROM erp_docs d JOIN erp_lines l ON l.doc_id=d.id WHERE d.kind='pay' AND d.status='posted' "
                         f"AND l.client IN ({q})", list(exts)):
        ls = con.execute("SELECT client, usd, uzs FROM erp_lines WHERE doc_id=?", (d["id"],)).fetchall()
        if len(ls) >= 2 and all(l["client"] in exts for l in ls) and abs(sum(l["usd"] or 0 for l in ls)) < 0.5 \
                and all(abs(l["uzs"] or 0) < 1 for l in ls):
            out[d["ext"]] = {l["client"]: (l["usd"] or 0) for l in ls}
    return out


def debt_ledger(con, ext, d1, d2, eq):
    """Қарз ҳисоби (1С ҳаракатлари бўйича, $; плюс — шерик қарздор). -> dict"""
    r = dict(open=0.0, goods=0.0, inv_paid=0.0, taken=0.0, paid=0.0, eq=0.0, other=0.0, close=0.0, pf=0.0, rows=[])
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
    for k in ("open", "goods", "inv_paid", "taken", "paid", "eq", "other", "close", "pf"):
        r[k] = round(r[k], 2)
    return r


def report(ym):
    import kassa as K
    con = A.db()
    y, mo = K.ym_parse(ym)
    d1 = date(y, mo, 1)
    d2 = (date(y + (mo == 12), mo % 12 + 1, 1) - timedelta(days=1))
    mc = K.month_calc(ym) if K.has_data(ym) else None
    m = pmap(con)
    exts = set(m.values())
    eqd = equalization_docs(con, exts)
    eq_month = {}
    for iddoc, lines in eqd.items():
        d = con.execute("SELECT date, no FROM erp_docs WHERE ext=?", (iddoc,)).fetchone()
        if d and d1.isoformat() <= d["date"] <= d2.isoformat():
            for cl, v in lines.items():
                eq_month.setdefault(cl, []).append(dict(no=d["no"], date=d["date"], usd=v))
    rows = []
    for o in (mc["owners"] if mc else [dict(item=x, taken=0.0, due=0.0) for x in owners(ym)]):
        it = o["item"]
        if not it["share"]:
            continue
        ext = m.get(it["id"])
        cl = con.execute("SELECT name FROM erp_clients WHERE ext=?", (ext,)).fetchone() if ext else None
        qoldi = round(o["due"] - o["taken"], 2)
        posted = round(sum(x["usd"] for x in eq_month.get(ext, [])), 2) if ext else 0.0
        rows.append(dict(item=it, ext=ext, cname=cl[0] if cl else "", share=it["share"], taken=round(o["taken"], 2), due=round(o["due"], 2),
                         qoldi=qoldi, posted=posted, posted_docs=eq_month.get(ext, []), need=round(qoldi - posted, 2),
                         debt=debt_ledger(con, ext, d1, d2, eqd) if ext else None))
    wsum = sum(r["share"] for r in rows) or 1
    for r in rows:
        r["pct"] = round(r["share"] * 100 / wsum, 1)
    return dict(ym=ym, d1=d1, d2=d2, rows=rows, mc=mc, profit=(mc["profit"] if mc else None), taken=sum(r["taken"] for r in rows))


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
        rep = report(ym)
        cands = {}
        for r in rep["rows"]:
            first = (r["item"]["name"] or "").split()[0] if r["item"]["name"] else ""
            cands[r["item"]["id"]] = A.q("SELECT ext, name FROM erp_clients WHERE folder=0 AND deleted=0 AND lc LIKE ? ORDER BY name LIMIT 40",
                                         (f"%{first.casefold()}%",)) if first else []
        return render_template("kassa_partners.html", rep=rep, cands=cands, ym=ym, ym_title=K.ym_title(ym), prev_ym=K.ym_shift(ym, -1),
                               next_ym=K.ym_shift(ym, 1))
