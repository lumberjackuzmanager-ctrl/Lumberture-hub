"""29.10: кассир журналида қидирувда «0» қолиб, накладнойларнинг ярми кўринмаган (фақат № ёки мижоз телефонида 0 борлари).
Менюдан қайта очилганда қидирув тушиб қолади; қидирув бўлса — сариқ огоҳлантириш ва «Сбросить поиск»."""
import os, sys, tempfile, datetime
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
t = datetime.date.today().isoformat()
with A.app.app_context():
    db = A.db()
    db.execute("INSERT INTO erp_clients(ext, name, folder, deleted, lc) VALUES('K1','Фарход',0,0,' фарход 998114222 ')")
    db.execute("INSERT INTO erp_clients(ext, name, folder, deleted, lc) VALUES('K2','Дмитрий',0,0,' дмитрий 903536054 ')")
    for no, cl in (("6652", "K1"), ("6658", "K2")):
        db.execute("INSERT INTO erp_docs(kind, no, date, time, client, status, src, usd) VALUES('sale',?,?,'12:00',?,'draft','pf',10)", (no, t, cl))
    db.commit()
h = c.get(f"/erp?kind=sale&d1={t}&d2={t}&q=0").get_data(as_text=True)
assert "6658" in h and ">6652<" not in h.replace(" ", "") and "Поиск: «0»" in h and "× Сбросить поиск" in h
r = c.get("/erp?kind=sale")                                   # менюдан: қидирувсиз, интервал сақланади
assert r.status_code == 302 and "q=" not in r.headers["Location"] and "d1=" in r.headers["Location"], r.headers["Location"]
h = c.get(r.headers["Location"]).get_data(as_text=True)
assert "6652" in h and "6658" in h and "Сбросить поиск" not in h
print("JR SEARCH OK")
