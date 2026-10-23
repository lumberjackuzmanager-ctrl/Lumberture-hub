"""Каталог склади: приход → қолдиқ, накладной (норматив билан) → айирилади, инвентаризация → фарқ, «Заполнить остатками», ҳисобот."""
import os, sys, tempfile, json, re, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp
A.app.testing = True
erp.stock_now = lambda: {}
erp.norms.__globals__["_norms"].update(at=0.0, v={})
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
today = datetime.date.today().isoformat()
with A.app.app_context():
    con = A.db()
    for e, n, cur, pr, svc in (("ci1", "ЛДСП Белый 16", 2, 40, ""), ("ci2", "Кромка 19/1 Белый", 1, 0, ""), ("ci3", "Услуга ПВХ 19/1 Белый", 1, 4600, "svc"),
                               ("ci4", "ЛМДФ Дуб", 2, 50, "")):
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src,price_last,cat,cvis) "
                    "VALUES(?,?,?,'шт',?,?,0,'cn1',0,0,?,'cat',0,?,1)", (e, e, n, cur, pr, erp.lc(n), svc or "good"))
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src,cvis) VALUES('cn1','','Плиты','',1,0,'',1,0,'плиты','cat',1)")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c,ckind) VALUES('S1','S1','Эман','','','0',0,0,'эман','1c',0,'supplier')")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,debt1c) VALUES('C1','C1','Мижоз','','','0',0,0,'мижоз','1c',0)")
    try:
        con.execute("CREATE TABLE IF NOT EXISTS cat_norms(item INTEGER, comp INTEGER, qty REAL, auto INTEGER DEFAULT 1, PRIMARY KEY(item, comp))")
    except Exception:
        pass
    con.execute("INSERT OR REPLACE INTO cat_norms(item,comp,qty,auto) VALUES(3,2,1,1)")      # Услуга ПВХ → кромка ×1
    con.execute("INSERT INTO fx_rates(rate, source, fetched_at) VALUES(12000, 'manual', ?)", (A.now(),)) if A.q("SELECT name FROM sqlite_master WHERE name='fx_rates'") else None
    con.commit()
erp._norms.update(at=0.0, v={})
def post(url, lines, act="post", **kw):
    tok(); r = c.post(url, data=dict(csrf="t", act=act, date=today, lines_json=json.dumps(lines), **kw))
    assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
    return r
h = c.get("/erp/inv/new2").get_data(as_text=True)
assert "Инвентаризация товаров" in h and "Заполнить остатками" in h and "Новый товар в каталог" in h and "Факт" in h
# приход: каталог товари омборга киради
post("/erp/receipt/new2", [{"good": "ci1", "qty": "10", "price": "30"}, {"good": "ci2", "qty": "100", "price": "500"}], client="S1")
with A.app.app_context():
    st = erp.stock_all(A.db())
    assert st["ci1"] == 10 and st["ci2"] == 100, st
# накладной: товар ва хизмат нормативи (кромка) айирилади
tok(); r = c.post("/erp/sale/new", data={"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12000",
                                         "good": ["ci1", "ci3"], "qty": ["3", "20"], "price": ["40", "4600"], "lnote": ["", ""]})
assert r.status_code == 302, re.findall(r'class="flash[^>]*>([^<]*)', r.get_data(as_text=True))
with A.app.app_context():
    st = erp.stock_all(A.db())
    assert st["ci1"] == 7 and st["ci2"] == 80, st
# goods API кўрсатади: қолдиқ (ҳаракати бор каталог товари), ҳаракатсизда — None
j = c.get("/erp/api/goods?q=лдсп").get_json(); assert j[0]["stock"] == 7, j
j = c.get("/erp/api/goods?q=лмдф").get_json(); assert j[0]["stock"] is None, j
# «Заполнить остатками»
f = c.get("/erp/api/inv_fill").get_json()
assert {x["good"]: x["stock"] for x in f} == {"ci1": 7, "ci2": 80}, f
# инвентаризация: ЛДСП 6 (недостача 1), кромка 85 (излишек 5), топилган ЛМДФ 2
post("/erp/inv/new2", [{"good": "ci1", "fact": "6"}, {"good": "ci2", "fact": "85"}, {"good": "ci4", "fact": "2", "note": "топилди"}], note="октябрь")
with A.app.app_context():
    con = A.db()
    d = A.q("SELECT * FROM erp_docs WHERE kind='inv'", one=True)
    assert d["status"] == "posted" and d["no"] == "1"
    ls = {l["good"]: (l["price2"], l["price"], l["qty"]) for l in A.q("SELECT * FROM erp_lines WHERE doc_id=?", (d["id"],))}
    assert ls == {"ci1": (7, 6, -1), "ci2": (80, 85, 5), "ci4": (0, 2, 2)}, ls
    st = erp.stock_all(con)
    assert st["ci1"] == 6 and st["ci2"] == 85 and st["ci4"] == 2, st
    iid = d["id"]
# хизматни инвентаризация қилиб бўлмайди, факт бўш — хато
tok(); r = c.post("/erp/inv/new2", data=dict(csrf="t", act="post", date=today, lines_json=json.dumps([{"good": "ci3", "fact": "1"}])))
assert r.status_code == 200 and "хизмат" in r.get_data(as_text=True)
tok(); r = c.post("/erp/inv/new2", data=dict(csrf="t", act="post", date=today, lines_json=json.dumps([{"good": "ci1", "fact": ""}])))
assert r.status_code == 200 and "Факт" in r.get_data(as_text=True)
# очиш, кўриш, чоп
h = c.get(f"/erp/doc/{iid}").get_data(as_text=True); assert "Учёт" in h and "+5" in h, h[:300]
h = c.get(f"/erp/doc/{iid}/print2").get_data(as_text=True); assert "По учёту" in h and "Материально ответственный" in h
h = c.get(f"/erp/doc/{iid}/edit2").get_data(as_text=True); assert '"fact": "6"' in h or '"fact":"6"' in h, re.findall(r'INIT=[^;]*', h)[:1]
assert "Инвентаризация" in c.get("/erp?kind=inv").get_data(as_text=True)
# проведениени бекор қилиб, қайта проведен: учёт янгиланади
tok(); c.post(f"/erp/doc/{iid}/status", data=dict(csrf="t", act="unpost"))
with A.app.app_context():
    assert erp.stock_all(A.db())["ci1"] == 7
tok(); c.post(f"/erp/doc/{iid}/status", data=dict(csrf="t", act="post"))
with A.app.app_context():
    st = erp.stock_all(A.db()); assert st["ci1"] == 6 and st["ci4"] == 2, st
# ҳисобот: инвентаризация ҳаракатлари
h = c.get(f"/erp/rep/stock?d1={today}&d2={today}&mode=detail&go=1").get_data(as_text=True)
assert "Инвентаризация № 1" in h, h[:500]
# минус: каталог товари етмаса — тасдиқ сўралади (1-вариант), тасдиқ билан — проведен, ким тасдиқлагани ёзилади
sale = {"csrf": "t", "act": "post", "client": "C1", "nal": "1", "date": today, "rate": "12000", "good": ["ci1"], "qty": ["9"], "price": ["40"], "lnote": [""]}
tok(); r = c.post("/erp/sale/new", data=sale)
assert r.status_code == 200 and "Омборда етарли эмас" in r.get_data(as_text=True)
tok(); r = c.post("/erp/sale/new", data=dict(sale, minus_ok="1")); assert r.status_code == 302
with A.app.app_context():
    assert erp.stock_all(A.db())["ci1"] == -3
    assert A.q("SELECT minus_by FROM erp_docs WHERE kind='sale' ORDER BY id DESC LIMIT 1", one=True)[0] == "admin"
# ҳаракатсиз каталог товари (ҳали инвентаризация қилинмаган) — тўхтатмайди
with A.app.app_context():
    A.db().execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src,cat,cvis) VALUES('ci9','ci9','МДФ Янги','шт',2,30,'cn1',0,0,'мдф янги','cat','good',1)"); A.db().commit()
tok(); r = c.post("/erp/sale/new", data=dict(sale, good=["ci9"], qty=["2"])); assert r.status_code == 302
print("INV OK")
