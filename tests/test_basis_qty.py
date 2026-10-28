"""28.10: «Из Базиса» — модел «Количество изделий» (K). Хасан, Xasan1002(Stol2x): K=2 — присадка ва паз аниқ ×2
(базисчи «Мини смета» бўйича қўлда ёзгани: накладной №6642 — евростяжка 120/120, эксцентрик 60, глухие 140, насечка 96, паз 8,4).
Деталлар/юза ×K (маълумот), листлар, кромка ва проектирование — ўзгармайди. Саҳифада майдон, ўзгартирса сақланади; печать × K;
скрипт EstimateData <OrderCount> ни ўқиб «qty» юборади."""
import os, sys, tempfile, json, re, subprocess
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import app as A, basis as B
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with A.app.app_context():
    db = A.db()
    for ext, name in (("S4", "Услуга сверления Евростяжка (лицевая 5*7)"), ("S5", "Услуга сверления Евростяжка (торцевая 7*4,5)"),
                      ("S6", "Услуга сверления Глухие отверстия (разного диаметра)"), ("S7", "Услуга сверления Под эксцентрики"),
                      ("S9", "Услуга сверления Насечка (3мм)"), ("SP", "Услуга сверления Паз (4мм)"), ("S1", "Услуга конструктора СТАНДАРТ")):
        db.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,'шт',1,750,'',0,0,?,'cat','svc',1)",
                   (ext, ext, name, " " + name.casefold() + " "))
    db.commit()
D = json.load(open(os.path.join(HERE, "fx_basis_39.json"), encoding="utf-8"))
assert "qty" not in D                                     # эски скрипт юборган — K = 1
SV = ("evro_l", "evro_t", "ecc", "glukh", "nasechka")
with A.app.app_context():
    con = A.db()
    r1, f1 = B.build_rows(con, dict(D))
    r2, f2 = B.build_rows(con, dict(D, qty=2))
    r3, _ = B.build_rows(con, dict(D, qty=2, nqty=3))       # базисчи саҳифада 3 қилди — устун
q1 = {x.get("svc"): x["qty"] for x in r1 if x["kind"] == "svc"}
q2 = {x.get("svc"): x["qty"] for x in r2 if x["kind"] == "svc"}
q3 = {x.get("svc"): x["qty"] for x in r3 if x["kind"] == "svc"}
assert {k: q2[k] for k in SV} == dict(evro_l=120, evro_t=120, ecc=60, glukh=140, nasechka=96), q2     # №6642 (Мини смета ×2)
assert all(q2[k] == 2 * q1[k] for k in SV) and q2["paz_hdf"] == 8.4 == 2 * q1["paz_hdf"], (q1, q2)
assert all(q3[k] == 3 * q1[k] for k in SV) and q3["paz_hdf"] == 12.6
assert q2["konstr"] == q1["konstr"]                       # проектирование — модел бир марта чизилади
p1 = [x for x in r1 if x["kind"] == "panel"]; p2 = [x for x in r2 if x["kind"] == "panel"]
assert [x["qty"] for x in p2] == [x["qty"] for x in p1] == ["", "", ""]                 # листлар — карта раскроядан, қўлда
assert p1[0]["info"].startswith("42 дет. · 5.7235 м²") and p2[0]["info"].startswith("84 дет. (42 × 2 изд.) · 11.4470 м² × 1.15 = 13.1640 м²"), p2[0]["info"]
assert all(x["qty"] == "" for x in r2 if x["kind"] in ("edge", "edgefig"))                # кромка — қўлда
assert [x["n"] for x in f2] == [x["n"] for x in f1]         # фурнитура рўйхати 1 буюмга (саҳифада × K)
# саҳифа: майдон, × K, ўзгартирса сақланади
with A.app.app_context():
    db = A.db()
    bid = db.execute("INSERT INTO basis_imports(uid, at, project, data) VALUES(1, '2026-10-28 20:04', ?, ?)", (D["project"], json.dumps(dict(D, qty=2), ensure_ascii=False))).lastrowid
    xid = db.execute("INSERT INTO basis_imports(uid, at, project, data) VALUES(1, '2026-10-28 20:05', 'x', ?)", (json.dumps({"src": "xml", "items": [], "panels": []}),)).lastrowid
    db.commit()
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
assert 'id="nk" name="nqty" value="2"' in h and "из Базиса (свойства модели)" in h, h[:500]
assert '<span class="ih">84 дет. (42 × 2 изд.)' in h and 'data-pn1="42"' in h
row = re.search(r'<tr data-kind="svc" data-svc="evro_l"[^>]*>', h).group(0)
assert 'data-q1="60"' in row and 'data-auto="1"' in row, row
assert re.search(r'name="qty_\d+" value="120"', h)
assert '<span class="k1" data-n1="60">120</span>' in h                                     # Евровинт 60 × 2
hdr = {"X-CSRF-Token": json.loads(re.search(r"window.LBCSRF=(\"[^\"]*\")", h).group(1))}
assert c.post(f"/erp/basis/{bid}/k", json={"k": 3}, headers=hdr).get_json()["k"] == 3
assert c.post(f"/erp/basis/{bid}/k", json={"k": 0}, headers=hdr).status_code == 400
assert c.post(f"/erp/basis/{xid}/k", json={"k": 2}, headers=hdr).status_code == 400          # XML — ҳар буюм сони файлда
h = c.get(f"/erp/basis/{bid}").get_data(as_text=True)
assert 'name="nqty" value="3"' in h and re.search(r'name="qty_\d+" value="180"', h)
with A.app.app_context():
    sv = json.loads(A.db().execute("SELECT data FROM basis_imports WHERE id=?", (bid,)).fetchone()[0])
assert sv["nqty"] == 3 and sv["qty"] == 2 and not any(k.startswith("_") for k in sv)
pr = c.get(f"/erp/basis/{bid}/print?what=all").get_data(as_text=True)
assert "Изделий: <b>3</b>" in pr and re.search(r"Евровинт 7х50</td><td[^>]*>[^<]*</td><td class=\"n\">180 шт", pr), pr[pr.find("Евровинт"):pr.find("Евровинт") + 200]
assert 'name="nqty"' not in c.get(f"/erp/basis/{xid}").get_data(as_text=True)
# скрипт: OrderCount → qty
js = c.get("/setup/basis.js").get_data(as_text=True)
assert "OrderCount" in js and "qty: IC.n" in js and all(ord(ch) < 128 for ch in js)
p = os.path.join(tempfile.mkdtemp(), "s.js")
stub = ("var out=null;function alert(){};function NewCOMObject(){return {open:function(){},setRequestHeader:function(){},send:function(b){out=b},status:403,responseText:''}};"
        "var Action={Control:{EstimateData:'<Estimate><Order>X</Order><OrderCount>2</OrderCount></Estimate>'}};var Model={Count:0};")
open(p, "w").write(stub + js + "\nconsole.log(JSON.stringify(itemCount()));")
nd = subprocess.run(["node", p], capture_output=True, text=True); assert nd.returncode == 0, nd.stderr
assert json.loads(nd.stdout.strip().splitlines()[-1])["n"] == 2, nd.stdout
open(p, "w").write(stub.replace("<OrderCount>2</OrderCount>", "") + js + "\nconsole.log(JSON.stringify(itemCount()));")
nd = subprocess.run(["node", p], capture_output=True, text=True)
assert json.loads(nd.stdout.strip().splitlines()[-1])["n"] == 1, nd.stdout                    # ўқилмаса — 1
print("BASIS QTY OK")
