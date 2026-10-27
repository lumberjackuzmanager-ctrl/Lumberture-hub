# -*- coding: utf-8 -*-
"""«Рабочее место» — чекланган роллар (кассир, менежер, базис …) нинг бош саҳифаси (27.10).

Эгаси: «функциялар жуда кўп, бир нечтаси 2–3 жойда такрорланади» — ҳар роль кириши билан ўзининг катта тугмаларини
ва «Бугун» рўйхатини кўради: проведен кутаётган черновиклар (кассир), ўзининг черновиклари (менежер/базис),
бугунги ҳужжатлар сони. Қолган функциялар — тепадаги «Ещё» менюсида."""
from datetime import date, timedelta

from flask import render_template, url_for

A = None

KIND_LABEL = {"sale": "Накладная", "pay": "Поступление денег", "receipt": "Поступление товара", "reval": "Переоценка", "inv": "Инвентаризация"}


def _new_buttons(kinds, allowed):
    """Янги ҳужжат тугмалари (журналлардаги «+» билан бир хил)."""
    out = []
    if "sale" in kinds and allowed("erp_sale_edit"):
        out.append(("+ Новая накладная", url_for("erp_sale_edit")))
    if "pay" in kinds and allowed("erp_doc2_edit"):
        out.append(("+ Поступление денег", url_for("erp_doc2_edit", kind="pay")))
    if "receipt" in kinds and allowed("erp_doc2_edit"):
        out.append(("+ Поступление товара", url_for("erp_doc2_edit", kind="receipt")))
    return out


def data(u):
    import nav, roles
    con = A.db()
    allowed = lambda ep: roles.ep_allowed(ep, u)
    kinds = roles.doc_kinds(u) if roles.is_limited(u) else ("sale", "pay", "receipt", "reval", "inv")
    tiles = [(label, url_for(e, **a)) for label, e, a, _ in nav.quick(u, "", {}, kassa_allowed=allowed, kinds=kinds)]
    post = roles.can_post(u)
    ks = [k for k in kinds if k in KIND_LABEL]
    drafts, mine, today = [], [], []
    if ks and allowed("erp_docs"):
        ph = ",".join("?" * len(ks))
        since = (date.today() - timedelta(days=60)).isoformat()
        base = (f"SELECT d.id, d.kind, d.no, d.date, d.time, d.usd, d.created_by, d.note, d.src, c.name cname FROM erp_docs d "
                f"LEFT JOIN erp_clients c ON c.ext=d.client WHERE d.kind IN ({ph}) AND d.status='draft' AND d.src='pf' AND d.date>=? ")
        if post:                    # кассир: ҳамма проведен кутаётганлар (цехдан, менежердан)
            drafts = [dict(r) for r in con.execute(base + "ORDER BY d.date DESC, d.time DESC, d.id DESC LIMIT 40", ks + [since])]
        mine = [dict(r) for r in con.execute(base + "AND d.created_by=? ORDER BY d.date DESC, d.time DESC, d.id DESC LIMIT 20",
                                             ks + [since, u["username"]])] if not post else []
        t0 = date.today().isoformat()
        cnt = {(r[0], r[1]): r[2] for r in con.execute(f"SELECT kind, status, COUNT(*) FROM erp_docs WHERE kind IN ({ph}) AND date=? "
                                                     f"AND status<>'deleted' GROUP BY kind, status", ks + [t0])}
        for k in ks:
            p_, d_ = cnt.get((k, "posted"), 0), cnt.get((k, "draft"), 0)
            if p_ or d_:
                today.append(dict(kind=k, label=KIND_LABEL[k], posted=p_, draft=d_, url=url_for("erp_docs", kind=k, d1=t0, d2=t0)))
    for r in drafts + mine:
        r["shop"] = (r["created_by"] or "") == "цех" or (r["note"] or "").startswith("Цех (бот)")
        r["url"] = url_for("erp_doc", doc_id=r["id"])
        r["klabel"] = KIND_LABEL.get(r["kind"], r["kind"])
        r["nolabel"] = str(r["no"] or "").lstrip("0") or str(r["no"] or "")
    return dict(tiles=tiles, new=_new_buttons(kinds, allowed), drafts=drafts, mine=mine, today=today, post=post,
                draft_url=url_for("erp_docs", kind="sale", st="draft", d1=(date.today() - timedelta(days=60)).isoformat(),
                                  d2=date.today().isoformat()) if "sale" in ks else None)


def register(app_module):
    global A
    A = app_module
    app = A.app

    @app.route("/work")
    @A.login_required
    def work_home():
        return render_template("work_home.html", w=data(A.current_user()))
