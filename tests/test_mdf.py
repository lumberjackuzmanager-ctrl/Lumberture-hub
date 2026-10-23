"""Маҳаллий ЛМДФ: накладнойда МДФ сорти (стандарт Асино), номда «(Алтай)»; сегмент переоценкаси каталог нархини ўзгартиради."""
import os, sys, tempfile, json, re, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp
A.app.testing = True
erp.stock_now = lambda: {}
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
today = datetime.date.today().isoformat()
with A.app.app_context():
    con = A.db()
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src,cat,cvis,msort) VALUES('ci1','','ЛМДФ Белый гладкий 16мм 5,0325','лист',2,45,'cn1',0,0,'лмдф белый гладкий','cat','good',1,1)")
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src,cat,cvis,msort) VALUES('ci2','','ЛДСП Белый 16мм','лист',2,40,'cn1',0,0,'лдсп белый','cat','good',1,0)")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c) VALUES('C1','C1','Мижоз','','','0',0,0,'мижоз','1c',0)")
    con.execute("INSERT INTO fx_rates(rate, source, fetched_at) VALUES(12000, 'manual', ?)", (A.now(),)) if A.q("SELECT name FROM sqlite_master WHERE name='fx_rates'") else None
    con.commit()
j = c.get("/erp/api/goods?q=лмдф").get_json(); assert j[0]["msort"] is True, j
j = c.get("/erp/api/goods?q=лдсп").get_json(); assert j[0]["msort"] is False, j
h = c.get("/erp/sale/new").get_data(as_text=True); assert 'var MS=' in h and "Алтай" in h
sale = {"csrf": "t", "act": "save", "client": "C1", "nal": "1", "date": today, "rate": "12000",
        "good": ["ci1", "ci2"], "qty": ["2", "1"], "price": ["48", "40"], "lnote": ["", ""], "lvar": ["Алтай", "Алтай"]}
tok(); r = c.post("/erp/sale/new", data=sale); assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
with A.app.app_context():
    ls = A.q("SELECT good, variant, price FROM erp_lines ORDER BY lineno")
    assert [(x[0], x[1]) for x in ls] == [("ci1", "Алтай"), ("ci2", "")], [tuple(x) for x in ls]     # ЛДСП'га сорт ёзилмайди
    did = A.q("SELECT id FROM erp_docs WHERE kind='sale'", one=True)[0]
h = c.get(f"/erp/doc/{did}").get_data(as_text=True); assert "ЛМДФ Белый гладкий 16мм 5,0325 (Алтай)" in h
h = c.get(f"/erp/doc/{did}/edit").get_data(as_text=True)
ini = json.loads(re.search(r"var INIT=(\[.*?\]), tb=", h).group(1)); assert ini[0]["var"] == "Алтай" and ini[0]["msort"] and not ini[1]["msort"], ini
h = c.get(f"/erp/doc/{did}/print?form=usd").get_data(as_text=True); assert "(Алтай)" in h
print("MDF OK")
