"""Скидка (эгаси, 25.10): каталогда «скидка мумкин» белгиси (папка ичидагилари билан ёки товар), накладнойда % — фақат шу
товар/хизматларга; ёзиш — админ ва кассир. Қаторда скидкали нарх (асл нарх — price2), жами ва қарз скидкадан кейин."""
import os, sys, tempfile, re, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, catalog as C, c1

c1.journal = lambda: {}
c1.table = lambda name: []
c1.clients = lambda: {}
c1.goods = lambda: {}
erp.stock_now = lambda: {}
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
today = datetime.date.today().isoformat()
with A.app.app_context():
    con = A.db()
    A.set_settings(erp_test_mode="0", credit_init="1")
    for nid, par, nm, wh, svc in ((1, 0, "Услуги", None, 1), (2, 1, "Распил", None, 1), (10, 0, "ЛДСП", 1, 0), (11, 10, "Эггер ЛДСП", None, 0),
                                  (12, 10, "Кроностар ЛДСП", None, 0)):
        con.execute("INSERT INTO cat_nodes(id,parent,name,wh,svc,skey,deleted,manual) VALUES(?,?,?,?,?,?,0,1)", (nid, par, nm, wh, svc, f"x:t{nid}"))
    for iid, node, nm, unit, cur, price, svc in ((101, 2, "Распил ЛДСП/ЛМДФ 5,796 кв", "кол-в", 1, 37000, 1), (102, 2, "Распил Столешница", "кол-в", 1, 22000, 1),
                                                 (103, 11, "ЛДСП Эггер Белый 16мм", "лист", 2, 40, 0), (104, 12, "ЛДСП Кроностар Серый 16мм", "лист", 2, 30, 0)):
        con.execute("INSERT INTO cat_items(id,node,name,lc,unit,cur,price,svc,kind,skey,deleted,manual,src_manual) VALUES(?,?,?,?,?,?,?,?,?,?,0,1,1)",
                    (iid, node, nm, C.lc(nm), unit, cur, price, svc, "svc" if svc else "good", f"x:i{iid}"))
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12500,'bot',?,?,'',0)", (today, today + " 09:00:00"))
    C.mirror(con)
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c,lim,blocked) "
                "VALUES('C1','C1','Азиз мебель','','','0',0,0,' азиз мебель ','1c',0,100000,0)")
    con.commit()
    assert A.q("SELECT disc FROM erp_goods WHERE ext='ci103'", one=True)["disc"] == 0           # белгиланмаган — скидка йўқ
H = {"X-CSRF-Token": "t"}
# каталог: «Эггер ЛДСП» папкаси ва «Распил ЛДСП» хизмати — скидка мумкин
tok(); r = c.post("/erp/api/cat/save", json={"act": "disc", "id": 11, "value": 1}, headers=H); assert r.status_code == 200 and r.get_json()["n"] == 1, r.get_data(as_text=True)
tok(); r = c.post("/erp/api/cat/save", json={"act": "disc", "items": [101], "value": 1}, headers=H); assert r.get_json()["n"] == 1
with A.app.app_context():
    g = {r["ext"]: r["disc"] for r in A.q("SELECT ext, disc FROM erp_goods WHERE ext IN ('ci101','ci102','ci103','ci104')")}
    assert g == {"ci101": 1, "ci102": 0, "ci103": 1, "ci104": 0}, g
t = {x["id"]: x for x in c.get("/erp/api/cat/tree").get_json()}
assert t[11]["own_disc"] == 1 and t[11]["disc"] == 1 and t[12]["disc"] == 0
assert [x["disc"] for x in c.get("/erp/api/cat/list?f=2").get_json()["items"]] == [1, 0]
assert next(x for x in c.get("/erp/api/goods?q=эггер").get_json() if x["ext"] == "ci103")["disc"] is True
h = c.get("/erp/sale/new").get_data(as_text=True); assert 'name="disc_pct"' in h
# накладной: 10% — фақат ci101 ва ci103 га
lines = [("ci103", "2", "40"), ("ci104", "1", "30"), ("ci101", "3", "37000"), ("ci102", "1", "22000")]
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12500", "disc_pct": "10", "minus_ok": "1",
                                         "good": [x[0] for x in lines], "qty": [x[1] for x in lines], "price": [x[2] for x in lines], "lnote": [""] * 4})
assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
with A.app.app_context():
    d = A.q("SELECT * FROM erp_docs WHERE kind='sale' AND src='pf' ORDER BY id DESC LIMIT 1", one=True)
    ls = {l["good"]: l for l in A.q("SELECT * FROM erp_lines WHERE doc_id=?", (d["id"],))}
    assert ls["ci103"]["price"] == 36 and ls["ci103"]["price2"] == 40 and ls["ci103"]["usd"] == 72
    assert ls["ci104"]["price"] == 30 and not ls["ci104"]["price2"] and ls["ci104"]["usd"] == 30
    assert ls["ci101"]["price"] == 33300 and ls["ci101"]["price2"] == 37000 and ls["ci101"]["uzs"] == 99900
    assert ls["ci102"]["price"] == 22000 and not ls["ci102"]["price2"]
    assert d["disc_pct"] == 10 and abs(d["usd"] - (72 + 30 + 99900 / 12500 + 22000 / 12500)) < 0.02
    assert abs(d["discount"] - (8 + 11100 / 12500)) < 0.02, d["discount"]
    did = d["id"]
# таҳрир ойнасида — асл нарх ва %; чоп этишда — скидка
with A.app.app_context():
    A.db().execute("UPDATE erp_docs SET status='draft' WHERE id=?", (did,)); A.db().commit()
h = c.get(f"/erp/doc/{did}/edit").get_data(as_text=True).replace(" ", "")
assert 'value="10"' in h and '"price":"40"' in h and '"disc":true' in h, h[h.find("INIT"):h.find("INIT") + 300]
with A.app.app_context():
    A.db().execute("UPDATE erp_docs SET status='posted', lock_by=NULL WHERE id=?", (did,)); A.db().commit()
pr = c.get(f"/erp/doc/{did}/print?form=usd").get_data(as_text=True)
assert "скидка 10%" in pr and "8.89" in pr, pr[-1500:]
# нусха (F9) — асл нарх ва %; возврат — скидкали нархда, % йўқ
h = c.get(f"/erp/sale/new?copy={did}").get_data(as_text=True)
h = h.replace(" ", "")
assert 'value="10"' in h and '"price":"40"' in h, h[h.find("INIT"):h.find("INIT") + 400]
h = c.get(f"/erp/sale/new?ret={did}").get_data(as_text=True)
assert '"price":"36"' in h.replace(" ", ""), h[h.find("INIT"):h.find("INIT") + 400]
# скидка 0 — ҳаммаси асл нархда
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "save", "client": "C1", "nal": "1", "date": today, "rate": "12500", "disc_pct": "",
                                         "good": ["ci103"], "qty": ["1"], "price": ["40"], "lnote": [""]})
with A.app.app_context():
    d2 = A.q("SELECT * FROM erp_docs WHERE kind='sale' AND src='pf' ORDER BY id DESC LIMIT 1", one=True)
    assert d2["usd"] == 40 and not d2["discount"] and not d2["disc_pct"]
print("DISCOUNT OK")
