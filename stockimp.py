# -*- coding: utf-8 -*-
"""«Ввод остатков из 1С»: 1С'даги мусбат қолдиқлар -> платформа каталоги товарлари -> Инвентаризация (қоралама).

1С товари ва каталог товари боғланмаган (алоҳида каталог), шунинг учун ҳар бир 1С товарига каталогдан мос товар таклиф қилинади
(номи, тури, декор коди, қалинлиги бўйича), мос келмаганини админ бир марта танлайди — эслаб қолинади (stock_map).
«6 Кромка склад новый» киритилмайди — кромка этикеткадан ботда киритилади (каталог режаси, 5-қадам); хизматлар ва кусок ҳам."""
import json, re
from datetime import date

from flask import request, render_template, redirect, url_for, flash, jsonify

A = None
SCHEMA = "CREATE TABLE IF NOT EXISTS stock_map(c1 TEXT PRIMARY KEY, good TEXT NOT NULL, at TEXT)"


def init_tables(con):
    con.execute(SCHEMA)
    import edgebot
    edgebot.init_tables(con)


def _skip(path, name=""):
    top = (path or "").split(" / ")[0].strip()
    if "кромк" in f"{path} {name}".casefold():           # кромка ҳеч қаерда кирмайди — цех-ботда санаб киритилади (эгаси, 25.10)
        return True
    return bool(re.match(r"\d+\s", top))                 # 1–9, 99 — хизматлар, 5 — кусок, 6 — кромка новый склад


def rows_1c():
    """[(ext, name, unit, path, qty)] — 1С'да мусбат қолдиқ."""
    import c1
    _, st = c1.stock()
    g = c1.goods()
    out = []
    for gid, q in st.items():
        if q <= 0.0005:
            continue
        it = g.get(gid)
        if not it or it["folder"]:
            continue
        path = c1.folder_path(g, it["parent"])
        if _skip(path, it["name"]):
            continue
        out.append(dict(ext=gid, name=it["name"], unit=it["unit"], path=path, qty=round(q, 3)))
    out.sort(key=lambda r: (r["path"], r["name"]))
    return out


WEAK = set("""лдсп лмдф мдф хдф лхдф дсп двп текстура гладкий глянец апельсин шагрень шагрин мм эконом ультрадекор эггер egger кроностар
крафтвуд филвуд платинум премиум кронопол timber zone старвуд уфа односторонний односторонни двухсторонний фасадный шлифованный кв св su cw pr pe sm bs st xl""".split())


def _keys(name):
    """Декорни белгилайдиган сўзлар (тури, бренди, юза тури ва рақамларсиз)."""
    ws = re.findall(r"[a-zа-яёўқғҳ]+", (name or "").casefold().replace("ё", "е"))
    return {w for w in ws if len(w) >= 3 and w not in WEAK}


BRANDS = (("ультрадекор", r"ультрадекор"), ("эггер", r"эггер|egger"), ("кроностар", r"кроностар"), ("крафтвуд", r"крафтвуд|craftwood"),
          ("кронопол", r"кронопол"), ("timber", r"timber|тимбер"), ("moonlight", r"moonlight"), ("silkway", r"silkway"),
          ("кастамону", r"кастамону|kastamonu"), ("agt", r"\bagt\b"), ("каштан", r"каштан"))


def _brands(t):
    t = (t or "").casefold()
    return {k for k, rx in BRANDS if re.search(rx, t)}


def _norm_name(t):
    return re.sub(r"\s+", " ", (t or "").casefold().replace("ё", "е")).strip()


def suggest(con, name, path=""):
    """Таклиф: аввал номи айнан бир хил каталог товари (бўшлиқлардан ташқари), кейин яқини — бренди бир хил бўлиши шарт
    (1С «ЛДСП Эконом Белый гладкий» -> «ЛДСП Ультрадекор Белый гладкий» бўлмасин)."""
    nn = _norm_name(name)
    for r in con.execute("SELECT ext, name FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 AND COALESCE(cat,'')='good' AND lc LIKE ?",
                         (f"%{nn.split(' ')[0]}%",)):
        if _norm_name(r["name"]) == nn:
            return r["ext"]
    g = _suggest(con, name)
    if g:
        r = con.execute("SELECT name FROM erp_goods WHERE ext=?", (g,)).fetchone()
        if not r or _brands(r["name"]) != _brands(name):
            return ""
    return g


def _suggest(con, name):
    """Каталогдан таклиф: basis.cat_best + текширув — 1С номидаги декор сўзларининг ҳаммаси каталог номида бўлиши шарт
    (aks holda «Жемчуг гладкий» -> «Белый гладкий» каби хато таклиф чиқади)."""
    import basis
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*мм", name or "", re.I)
    th = float(m.group(1).replace(",", ".")) if m else None
    try:
        g = basis.cat_best(con, "panel", name, th)
    except Exception:
        return ""
    if not g:
        return ""
    r = con.execute("SELECT name FROM erp_goods WHERE ext=?", (g,)).fetchone()
    cand = (r["name"] if r else "").casefold().replace("ё", "е")
    for k in _keys(name):
        if k[:5] not in cand:
            return ""
    return g


def direct(con, ext):
    """1С'дан каталогга нусха қилинган товар (skey «c1g:<1С id>») — аниқ мослик. -> «ci<id>» | «»"""
    r = con.execute("SELECT id FROM cat_items WHERE skey=? AND deleted=0 AND COALESCE(svc,0)=0", ("c1g:" + ext,)).fetchone()
    return f"ci{r[0]}" if r and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND deleted=0", (f"ci{r[0]}",)).fetchone() else ""


def search(con, q, limit=12):
    import kb as K
    out, seen = [], set()
    for v in K.variants(q or ""):
        ws = [w for w in v.casefold().split() if w][:5]
        if not ws:
            continue
        sql = ("SELECT ext, name, unit FROM erp_goods WHERE src='cat' AND folder=0 AND deleted=0 AND COALESCE(cat,'')='good' AND "
               + " AND ".join("lc LIKE ?" for _ in ws) + " ORDER BY length(name) LIMIT ?")
        for r in con.execute(sql, tuple(f"%{w}%" for w in ws) + (limit,)):
            if r["ext"] not in seen:
                seen.add(r["ext"]); out.append(dict(ext=r["ext"], name=r["name"], unit=r["unit"] or ""))
    return out[:limit]


def register(app_module):
    global A
    A = app_module
    app = A.app
    con = A.connect()
    try:
        init_tables(con); con.commit()
    finally:
        con.close()

    @app.route("/erp/stock-import", methods=["GET", "POST"])
    @A.admin_required
    def erp_stock_import():
        con = A.db()
        init_tables(con)
        try:
            src = rows_1c()
            err = ""
        except Exception as e:
            src, err = [], f"База 1С не открылась: {e}"
        if request.method == "POST":
            f = request.form
            n = 0
            for r in src:
                g = (f.get("g_" + r["ext"]) or "").strip()
                if g and con.execute("SELECT 1 FROM erp_goods WHERE ext=? AND src='cat' AND folder=0", (g,)).fetchone():
                    con.execute("INSERT INTO stock_map(c1, good, at) VALUES(?,?,?) ON CONFLICT(c1) DO UPDATE SET good=excluded.good, at=excluded.at",
                                (r["ext"], g, A.now()))
                    n += 1
                elif f.get("g_" + r["ext"]) == "":
                    con.execute("DELETE FROM stock_map WHERE c1=?", (r["ext"],))
            for e in con.execute("SELECT id FROM edge_entries WHERE state='new' AND used_doc IS NULL").fetchall():
                g = (f.get(f"eg_{e['id']}") or "").strip()
                gr = con.execute("SELECT name FROM erp_goods WHERE ext=? AND src='cat' AND folder=0", (g,)).fetchone() if g else None
                if gr:                                           # янги ранг — офис товарни танлади
                    con.execute("UPDATE edge_entries SET good=?, state='ok', note=note || ' -> ' || ? WHERE id=?", (g, gr["name"], e["id"]))
            con.commit()
            if f.get("act") == "inv":
                mp = dict(con.execute("SELECT c1, good FROM stock_map").fetchall())
                agg = {}
                for r in src:
                    if r["ext"] in mp and f.get("use_" + r["ext"]):
                        agg[mp[r["ext"]]] = agg.get(mp[r["ext"]], 0) + r["qty"]
                eids = []
                if f.get("edges"):                               # цех-ботда этикеткадан киритилган кромка
                    for e in con.execute("SELECT id, good, qty_m FROM edge_entries WHERE state='ok' AND used_doc IS NULL AND good<>''").fetchall():
                        agg[e["good"]] = agg.get(e["good"], 0) + e["qty_m"]
                        eids.append(e["id"])
                if not agg:
                    flash("Нет строк с выбранным товаром каталога", "error")
                    return redirect(url_for("erp_stock_import"))
                import erp
                form = dict(date=(f.get("date") or date.today().isoformat()), note="Ввод остатков из 1С (проверить пересчётом)",
                            lines=[dict(good=g, fact=str(q)) for g, q in agg.items()])
                try:
                    did = erp.save_doc2(con, "inv", None, form, post=False)
                except ValueError as e:
                    flash(str(e), "error")
                    return redirect(url_for("erp_stock_import"))
                for i in eids:
                    con.execute("UPDATE edge_entries SET used_doc=? WHERE id=?", (did, i))
                con.commit()
                flash(f"Создана инвентаризация (черновик): {len(agg)} поз. Проверьте и проведите после пересчёта", "ok")
                return redirect(url_for("erp_doc2_edit", doc_id=did))
            flash(f"Сопоставление сохранено: {n} поз.", "ok")
            return redirect(url_for("erp_stock_import"))
        mp = dict(con.execute("SELECT c1, good FROM stock_map").fetchall())
        names = {}
        rows = []
        for r in src:
            dg = "" if r["ext"] in mp else direct(con, r["ext"])
            g = mp.get(r["ext"]) or dg or suggest(con, r["name"], r["path"])
            r["auto"] = r["ext"] not in mp and not dg and bool(g)
            r["good"] = g
            if g and g not in names:
                x = con.execute("SELECT name, unit FROM erp_goods WHERE ext=?", (g,)).fetchone()
                names[g] = (x["name"] if x else "", x["unit"] if x else "")
            r["gname"], r["gunit"] = names.get(g, ("", ""))
            rows.append(r)
        edges = con.execute("SELECT e.good, g.name, SUM(e.qty_m) m, COUNT(*) n FROM edge_entries e JOIN erp_goods g ON g.ext=e.good "
                            "WHERE e.state='ok' AND e.used_doc IS NULL GROUP BY e.good ORDER BY g.name").fetchall()
        enew = con.execute("SELECT e.*, w.name wname FROM edge_entries e LEFT JOIN ready_workers w ON w.id=e.rw_id "
                           "WHERE e.state='new' AND e.used_doc IS NULL ORDER BY e.id").fetchall()
        return render_template("erp_stock_import.html", rows=rows, err=err, today=date.today().isoformat(), edges=edges, enew=enew,
                               n_map=sum(1 for r in rows if r["good"]), n_auto=sum(1 for r in rows if r["auto"]))

    @app.route("/erp/stock-import/search")
    @A.admin_required
    def erp_stock_import_search():
        return jsonify(items=search(A.db(), request.args.get("q", "")))
