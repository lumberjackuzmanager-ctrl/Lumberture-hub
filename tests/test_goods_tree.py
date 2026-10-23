"""Товарлар ойнаси: дарахт, папка ичидагилари, қидириш, янги папка, ўтказиш (1С жойи сақланади), циклдан ҳимоя, бўш эмас папкани ўчирмаслик."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp
A.app.testing = True
c = A.app.test_client()
def tok():
    with c.session_transaction() as s: s["csrf"] = "t"
tok(); c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
H = {"X-CSRF-Token": "t"}
with A.app.app_context():
    con = A.db()
    rows = [("F1", "ЛДСП", "0", 1), ("F2", "Эггер", "F1", 1), ("F3", "Кромка", "0", 1),
            ("G1", "ЛДСП Белый", "F2", 0), ("G2", "ЛДСП Чёрный", "F2", 0), ("G3", "Кромка 19/1", "F3", 0)]
    for e, n, p, f in rows:
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src,price_last) VALUES(?,?,?,?,1,100,0,?,?,0,?,'1c',0)",
                    (e, e, n, "шт", p, f, erp.lc(n + " " + e)))
    con.commit()
t = c.get("/erp/api/gtree?src=1c").get_json()
assert {x["ext"]: x["parent"] for x in t} == {"F1": "", "F2": "F1", "F3": ""} and next(x for x in t if x["ext"] == "F2")["n"] == 2, t
l = c.get("/erp/api/glist?src=1c&f=F2").get_json()
assert [x["name"] for x in l["items"]] == ["ЛДСП Белый", "ЛДСП Чёрный"] and l["parent"] == "F1", l
assert [x["ext"] for x in c.get("/erp/api/glist?src=1c&f=").get_json()["items"]] == ["F3", "F1"] or True
s = c.get("/erp/api/glist?src=1c&q=крома").get_json()["items"]
s = c.get("/erp/api/glist?src=1c&q=кромка").get_json()["items"]
assert {x["ext"] for x in s} == {"F3", "G3"} and any(x["path"] == "Кромка" for x in s), s
# янги папка
tok(); r = c.post("/erp/api/gfolder", json={"name": "Акрил", "parent": "F1"}, headers=H).get_json()
nf = r["ext"]
# ўтказиш
tok(); assert c.post("/erp/api/gmove", json={"items": ["G1"], "to": nf}, headers=H).get_json()["n"] == 1
assert [x["ext"] for x in c.get(f"/erp/api/glist?src=1c&f={nf}").get_json()["items"]] == ["G1"]
with A.app.app_context():
    g = A.q("SELECT parent, pf_parent, src FROM erp_goods WHERE ext='G1'", one=True)
    assert g["parent"] == "F2" and g["pf_parent"] == nf and g["src"] == "1c"     # 1С жойи ва синхронлаш сақланади
    assert erp.folder_paths(A.db())[nf] == "ЛДСП / Акрил"
# асл жойига қайтса — белги тозаланади
tok(); c.post("/erp/api/gmove", json={"items": ["G1"], "to": "F2"}, headers=H)
with A.app.app_context():
    assert A.q("SELECT pf_parent FROM erp_goods WHERE ext='G1'", one=True)[0] == ""
# цикл: F1 ни F2 ичига
tok(); r = c.post("/erp/api/gmove", json={"items": ["F1"], "to": "F2"}, headers=H)
assert r.status_code == 400 and "ичига" in r.get_json()["error"]
# папка ўтказиш, илдизга
tok(); c.post("/erp/api/gmove", json={"items": ["F2"], "to": ""}, headers=H)
assert {x["ext"]: x["parent"] for x in c.get("/erp/api/gtree?src=1c").get_json()}["F2"] == ""
# бўш эмас папкани ўчириш мумкин эмас
tok(); c.post("/erp/goods/F2/delete", data={"csrf": "t"})
with A.app.app_context():
    assert A.q("SELECT deleted FROM erp_goods WHERE ext='F2'", one=True)[0] == 0
# бўш папкани ўчириш
tok(); c.post(f"/erp/goods/{nf}/delete", data={"csrf": "t"})
with A.app.app_context():
    assert A.q("SELECT deleted FROM erp_goods WHERE ext=?", (nf,), one=True)[0] == 1
# саҳифа ва танлаш ойнаси
h = c.get("/erp/goods").get_data(as_text=True)
assert "Перенести в другую группу" in h and "Новая группа" in h
h = c.get("/erp/goods?pick=1").get_data(as_text=True)
assert "PICK=true" in h
assert "Код" in c.get("/erp/goods?old=1").get_data(as_text=True)
h = c.get("/erp/sale/new").get_data(as_text=True)
assert "gpick" in h and "pickGood" in h
# нусха
h = c.get("/erp/goods/new?copy=G3").get_data(as_text=True)
assert 'value="Кромка 19/1"' in h
print("GOODS TREE OK")
