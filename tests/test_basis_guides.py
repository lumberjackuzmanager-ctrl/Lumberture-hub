"""Тешиги йўқ направляющий (мижоз лойиҳаси): насечка қоида бўйича + огоҳлантириш; ўзимиз чизган лойиҳа ўзгармайди."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, basis as B
here = os.path.dirname(os.path.abspath(__file__))
cli0 = json.load(open(os.path.join(here, "fx_basis_22.json"), encoding="utf-8"))     # мижоз: Hettich тандем, тешиксиз (саморезлари бор)
cli = dict(cli0, furn=[f for f in cli0["furn"] if "саморез" not in f["m"].lower()])     # крепёжсиз вариант
our = json.load(open(os.path.join(here, "fx_basis_23.json"), encoding="utf-8"))      # базисчи: шуруплар алоҳида
with A.app.app_context():
    db = A.db()
    rows, furn = B.build_rows(db, cli)
    g = [x for x in furn if x.get("guess")]
    assert len(g) == 4 and all(dict(x["slots"]).get("nasechka") == 4 and dict(x["slots"]).get("glukh") == 2 for x in g), g
    nas = [r for r in rows if r.get("svc") == "nasechka"][0]["qty"]
    _, furn0 = B.build_rows(db, dict(cli, furn=[f for f in cli["furn"] if "тандем" not in f["m"].lower()]))
    rows0, _ = B.build_rows(db, dict(cli, furn=[f for f in cli["furn"] if "тандем" not in f["m"].lower()]))
    nas0 = [r for r in rows0 if r.get("svc") == "nasechka"][0]["qty"]
    assert nas - nas0 == 50 * 4, (nas, nas0)
    # созлама: тандем 5 та
    db.execute("INSERT OR REPLACE INTO settings(key, value) VALUES('basis_guide_nas_tandem','5')"); db.commit()
    _, f2 = B.build_rows(db, cli)
    assert all(dict(x["slots"])["nasechka"] == 5 for x in f2 if x.get("guess"))
    # шуруплари алоҳида бор лойиҳалар: огоҳлантириш бор, насечка қўшилмайди (икки марта саналмасин)
    for d in (our, cli0):
        r3, f3 = B.build_rows(db, d)
        gs = [x for x in f3 if x.get("guess")]
        assert gs and all(x["guess"]["scr"] > 0 and "nasechka" not in dict(x["slots"]) for x in gs), gs
    r3, _ = B.build_rows(db, our)
    assert [r for r in r3 if r.get("svc") == "nasechka"][0]["qty"] == 216
    # базисчи тузатса — эслаб қолинади, қоида қўлланмайди
    db.execute("INSERT INTO basis_names(key, good, updated_at) VALUES(?,?,?)", ("furn:" + B.norm("Hettich тандем 400 мм. L"), json.dumps([["glukh", 2]]), A.now())); db.commit()
    _, f4 = B.build_rows(db, cli)
    x = [x for x in f4 if x["src"] == "Hettich тандем 400 мм. L"][0]
    assert not x.get("guess") and x["slots"] == [("glukh", 2.0)], x
print("GUIDE RULE OK")
# форма: базисчи ўзгартирмаса — эслаб қолинмайди
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with A.app.app_context():
    db = A.db()
    db.execute("DELETE FROM basis_names WHERE key LIKE 'furn:%'")
    db.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES('S9','S9','Услуга сверления Насечка (3мм)','шт',2,0.01,'',0,0,' услуга сверления насечка (3мм) ','cat','svc',1)")
    db.execute("INSERT INTO erp_clients(ext, name, phones, folder, deleted, lc, lim) VALUES('C1', 'Мижоз', '', 0, 0, ' мижоз ', 100000)")
    import datetime as _dt; td = _dt.date.today().isoformat()
    db.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (td, td + " 09:00:00"))
    db.commit()
    bid = db.execute("INSERT INTO basis_imports(uid, at, project, data) VALUES(1,?,?,?)", (A.now(), "T", json.dumps(cli, ensure_ascii=False))).lastrowid; db.commit()
    rows, furn = B.build_rows(db, cli)
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
assert "Направляющие без отверстий в модели" in h and "Насечка добавлена по правилу" in h, h[:200]
f = {"csrf": "t", "client": "C1"}
for i, x in enumerate(rows):
    if x.get("svc") == "nasechka":
        f[f"use_{i}"] = "on"; f[f"good_{i}"] = "S9"; f[f"qty_{i}"] = str(x["qty"])
for j, x in enumerate(furn):
    for k in (0, 1, 2):
        sl = x["slots"][k] if len(x["slots"]) > k else ("", 1)
        f[f"fc_{j}_{k}"] = sl[0]; f[f"fm_{j}_{k}"] = "%g" % sl[1]
jT = [j for j, x in enumerate(furn) if x["src"] == "Hettich тандем 500 мм. R"][0]
f[f"fm_{jT}_1"] = "6" if furn[jT]["slots"][1][0] == "nasechka" else f[f"fm_{jT}_1"]
with c.session_transaction() as s_: s_["csrf"] = "t"
rr = c.post(f"/erp/basis/{bid}", data=f); import re as _re; assert rr.status_code == 302, _re.findall(r"class=\"flash (?!warn)[^>]*>([^<]+)", rr.get_data(as_text=True))
with A.app.app_context():
    keys = {r[0] for r in A.db().execute("SELECT key FROM basis_names WHERE key LIKE 'furn:%'")}
assert "furn:" + B.norm("Hettich тандем 500 мм. R") in keys and "furn:" + B.norm("Hettich тандем 400 мм. L") not in keys, keys
h2 = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
# Базис: чап/ўнг артикулда («350 шарик» + «(350) Направляющая левая/правая») — 4+4 = 4 комплект
fs = B.furn_summary([("350 шарик", "(350) Направляющая левая", 4, None), ("350 шарик", "(350) Направляющая правая", 4, None)])
assert fs[0]["n"] == 4 and fs[0]["rows"][0]["name"] == "350 шарик (L+R)", fs

print("GUIDE LEARN OK")
