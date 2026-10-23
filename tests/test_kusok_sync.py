"""Кусок: бот омбори (РайГаз) — каталог қолдиғи; накладной проведен — кусок «сотилди», бекор — омборга; мижозники сотилмайди."""
import os, sys, tempfile, json, re, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, raspil as P
A.app.testing = True
erp.stock_now = lambda: {}
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
today = datetime.date.today().isoformat()
with A.app.app_context():
    con = A.db()
    con.execute("INSERT INTO cat_nodes(id,name,parent,deleted,skey) VALUES(900,'Остатки (кусок)',0,0,'c1g:ROOT')")
    con.execute("INSERT INTO cat_items(id,node,name,unit,cur,price,svc,kind,deleted,skey) VALUES(901,900,'ЛМДФ кусок','кв.м',2,20,0,'good',0,'c1g:G1')")
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src,cat,cvis) VALUES('ci901','ci901','ЛМДФ кусок','кв.м',2,20,'',0,0,' лмдф кусок ','cat','good',1)")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c) VALUES('C1','C1','Мижоз','','','0',0,0,'мижоз','1c',0)")
    for col, sz, m2, own in (("Кашемир", "1000×500", 0.5, "РайГаз"), ("Белый", "1000×800", 0.8, "РайГаз"), ("Сатин", "800×400", 0.32, "Азиз 1111")):
        P.kusok_add({}, "G1", "ЛМДФ кусок", col, sz, m2, own)
    con.commit()
    erp._kg["at"] = 0
    st = erp.stock_all(con); assert st["ci901"] == 1.62, st                # мижозники ҳам сотилади — қолдиққа киради
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12000",
                                         "good": ["ci901"], "qty": ["0.5"], "price": ["20"], "lnote": [""]})
assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
with A.app.app_context():
    con = A.db()
    d = A.q("SELECT id, no FROM erp_docs WHERE kind='sale' ORDER BY id DESC LIMIT 1", one=True)
    ks = {k["color"]: (k["status"], k["sold_doc"]) for k in A.q("SELECT * FROM raspil_kusok")}
    assert ks["Кашемир"] == ("sold", d["id"]) and ks["Белый"][0] == "in" and ks["Сатин"][0] == "in", ks
    assert erp.stock_all(con)["ci901"] == 1.12
    assert A.q("SELECT sold_to FROM raspil_kusok WHERE color='Кашемир'", one=True)[0] == "Мижоз"
    kc = P.kusok_catalog(con)["ci901"]; assert kc["sold"][0]["owner"] == "РайГаз" and kc["sold"][0]["to"] == "Мижоз", kc
tok(); c.post(f"/erp/doc/{d['id']}/status", data=dict(csrf="t", act="unpost"))
with A.app.app_context():
    assert A.q("SELECT status FROM raspil_kusok WHERE color='Кашемир'", one=True)[0] == "in"
    assert erp.stock_all(A.db())["ci901"] == 1.62
tok(); c.post(f"/erp/doc/{d['id']}/status", data=dict(csrf="t", act="post"))
with A.app.app_context():
    assert A.q("SELECT status FROM raspil_kusok WHERE color='Кашемир'", one=True)[0] == "sold"
# мижознинг кусоги (0.32 — аниқ мос) сотилади: «Азиз 1111 → Мижоз»
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12000",
                                         "good": ["ci901"], "qty": ["0.32"], "price": ["20"], "lnote": [""]}); assert r.status_code == 302
with A.app.app_context():
    k = A.q("SELECT status, owner, sold_to FROM raspil_kusok WHERE color='Сатин'", one=True)
    assert tuple(k) == ("sold", "Азиз 1111", "Мижоз"), tuple(k)
print("KUSOK SYNC OK")
# сотувда қатор изоҳига кусок ёзилади
with A.app.app_context():
    nt = A.q("SELECT l.note FROM erp_lines l JOIN erp_docs d ON d.id=l.doc_id WHERE d.id=?", (d["id"],), one=True)[0]
    assert "Кашемир" in nt and "1000×500" in nt and "РайГаз" in nt, nt
# возврат: асл накладнойдаги кусок омборга қайтади
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12000", "ref_doc": str(d["id"]),
                                         "good": ["ci901"], "qty": ["-0.5"], "price": ["20"], "lnote": [""]})
assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
with A.app.app_context():
    rd = A.q("SELECT id FROM erp_docs WHERE ref_doc=? ORDER BY id DESC LIMIT 1", (d["id"],), one=True)
    assert rd, "return doc"
    assert A.q("SELECT status FROM raspil_kusok WHERE color='Кашемир'", one=True)[0] == "in"
tok(); c.post(f"/erp/doc/{rd['id']}/status", data=dict(csrf="t", act="unpost"))
with A.app.app_context():
    assert A.q("SELECT status FROM raspil_kusok WHERE color='Кашемир'", one=True)[0] == "sold"
tok(); c.post(f"/erp/doc/{rd['id']}/status", data=dict(csrf="t", act="post"))
with A.app.app_context():
    assert A.q("SELECT status FROM raspil_kusok WHERE color='Кашемир'", one=True)[0] == "in"
# изоҳ парсери
assert P.kusok_note_parse("Кашемир 1000x500 Азиз 901234567") [1] == "1000×500"
c0, s0, o0 = P.kusok_note_parse("Белый глянец 600*400")
assert (c0, s0, o0) == ("Белый глянец", "600×400", "РайГаз"), (c0, s0, o0)
# поступление: изоҳдаги кусок бот омборига тушади, бекор — чиқади
with A.app.test_request_context():
    from flask import session; session["uid"] = 1
    con = A.db()
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c,ckind) VALUES('S1','S1','Пост','','','0',0,0,'пост','pf',0,'supplier')")
    con.commit()
    did = erp.save_doc2(con, "receipt", None, dict(client="S1", rate="12000", date=today, lines=[dict(good="ci901", qty="0.24", price="20", note="Дуб 600x400")]), post=True)
    con.commit()
    k = A.q("SELECT color, size, m2, owner, status FROM raspil_kusok WHERE src=?", (f"pf:{did}:1",), one=True)
    assert tuple(k) == ("Дуб", "600×400", 0.24, "РайГаз", "in"), tuple(k)
tok(); c.post(f"/erp/doc/{did}/status", data=dict(csrf="t", act="unpost"))
with A.app.app_context():
    assert A.q("SELECT status FROM raspil_kusok WHERE src=?", (f"pf:{did}:1",), one=True)[0] == "del"
print("KUSOK 2WAY OK")
