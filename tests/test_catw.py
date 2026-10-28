"""28.10: кассирнинг товар танлашида кромка йўқ эди — приходни қилолмасди. Приход / инвентаризация / переоценкада
каталогнинг ҳамма товари (кромка, фурнитура: cvis=2); сотувда ва онлайн дўконда — олдингидек фақат плита ва хизматлар (cvis=1)."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, catalog as C, roles as R
from werkzeug.security import generate_password_hash

with A.app.app_context():
    con = A.db()
    for nid, par, nm, wh, svc in ((1, 0, "Услуги", None, 1), (2, 1, "Распил", None, 1), (10, 0, "ЛДСП", 1, 0), (20, 0, "Кромка", 2, 0), (30, 0, "Опора", 3, 0)):
        con.execute("INSERT INTO cat_nodes(id,parent,name,wh,svc,skey,deleted,manual) VALUES(?,?,?,?,?,?,0,1)", (nid, par, nm, wh, svc, f"x:t{nid}"))
    for iid, node, nm, unit, cur, price, svc in ((101, 2, "Распил ЛДСП", "кол-в", 1, 37000, 1), (103, 10, "ЛДСП Эггер Белый 16мм", "лист", 2, 40, 0),
                                                 (201, 20, "Кромка Эггер 19/0,8 Белый", "м", 2, 0.3, 0), (301, 30, "Опора кухонная 100мм", "шт", 2, 0.5, 0)):
        con.execute("INSERT INTO cat_items(id,node,name,lc,unit,cur,price,svc,kind,skey,deleted,manual,src_manual) VALUES(?,?,?,?,?,?,?,?,?,?,0,1,1)",
                    (iid, node, nm, C.lc(nm), unit, cur, price, svc, "svc" if svc else "good", f"x:i{iid}"))
    C.mirror(con)
    con.execute("UPDATE roles SET sections=? WHERE key='kassa'", (json.dumps(["kassa", "sales", "pay_in", "receipts"]),))
    con.execute("INSERT INTO users(username,password_hash,role,active) VALUES('kas9',?,'kassa',1)", (generate_password_hash("Kassa12345x"),))
    con.commit(); R.reload()
    v = {r["ext"]: r["cvis"] for r in A.q("SELECT ext, cvis FROM erp_goods WHERE src='cat' AND folder=0")}
    assert v == {"ci101": 1, "ci103": 1, "ci201": 2, "ci301": 2}, v
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "kas9", "password": "Kassa12345x", "csrf": "t"})
names = lambda u: {x["name"] for x in c.get(u).get_json()}
assert "Кромка Эггер 19/0,8 Белый" not in names("/erp/api/goods?q=кромка")                 # сотув — йўқ
assert "Кромка Эггер 19/0,8 Белый" in names("/erp/api/goods?q=кромка&src=catw")            # приход — бор
assert "Опора кухонная 100мм" in names("/erp/api/goods?q=опора&src=catw")
tr = {x["name"] for x in c.get("/erp/api/gtree?src=catw").get_json()}
assert "Кромка" in tr and "Опора" in tr, tr
assert "Кромка" not in {x["name"] for x in c.get("/erp/api/gtree?src=cat").get_json()}
h = c.get("/erp/receipt/new2").get_data(as_text=True)
assert "src=catw" in h, h[:300]
assert "src=catw" not in c.get("/erp/pay/new2").get_data(as_text=True)
print("CATW OK")
