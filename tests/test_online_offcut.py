"""28.10: /api/online/catalog — ҳамма плита (столешница, ПВХ, Faton …) kind/thick/sheet/price билан, цех хизматлари тўлиқ (пакетный,
утолщенка, доставка); буюртмада layout.sheets[i].offcut=true — мижоз кусоги: материал пули йўқ, распил — «Распил Кусок» (м²)."""
import os, sys, tempfile, json, gzip
from datetime import date
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, online as OL

CF = {"Cf-Ray": "abc-DME", "Cf-Connecting-Ip": "203.0.113.8", "Host": "app.lumberture.com"}
with A.app.app_context():
    con = A.db()
    td = date.today().isoformat()
    con.execute("INSERT INTO fx_rates(rate,source,rate_date,fetched_at,raw,is_demo) VALUES(12000,'bot',?,?,'',0)", (td, td + " 09:00:00"))

    def G(ext, name, parent="", folder=0, price=0, cur=1, unit="", cvis=1):
        con.execute("INSERT INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat, cvis) VALUES(?,?,?,?,?,?,?,?,0,?,'cat',?,?)",
                    (ext, ext, name, unit, cur, price, parent, folder, " " + name.casefold() + " ", "folder" if folder else "good", cvis))
    G("L", "ЛДСП", folder=1); G("La", "Silkway Collection 2800*2070*16мм", "L", 1)
    G("M1", "ЛДСП Silkway Антрацит", "La", price=40, cur=2, unit="шт")
    G("T", "Столешницы", folder=1); G("T1", "Столешница Кронопол 4100*600*38мм Дуб", "T", price=90, cur=2, unit="шт")
    G("AK", "Акрил", folder=1); G("AKp", "Акрил ПВС", "AK", 1); G("P1", "ПВС Белый 2440*1220*16мм", "AKp", price=55, cur=2, unit="лист")
    G("FT", "Faton", folder=1); G("FT1", "Faton GOLD 2,44*1,22*0,8 (с клеем)", "FT", price=30, cur=2, unit="лист")
    G("KR", "Кромка", folder=1); G("KR1", "Кромка Эггер 19/0,8 Белый", "KR", price=0.3, cur=2, unit="м", cvis=2)     # товар — каталогда йўқ
    G("U", "Услуги", folder=1); G("R", "Распил", "U", 1); G("Ro", "Распил ОБЫЧНЫЙ", "R", 1); G("Rp", "Распил ПАКЕТНЫЙ ОБЫЧНЫЙ", "R", 1)
    G("S6", "Распил ЛДСП/ЛМДФ 5,796 кв", "Ro", price=37000, unit="кол-в"); G("SK", "Распил Кусок", "Ro", price=11000, unit="кв.м")
    G("SP", "Распил ЛДСП/ЛМДФ 5,796 кв (Пакетный)", "Rp", price=47000, unit="кол-в")
    G("UT", "Утолшенка", "U", 1); G("UT1", "Услуга обманка (клей)", "UT", price=5000, unit="пог.м")
    G("DL", "Услуги доставки", "U", 1); G("DL1", "Доставка по городу", "DL", price=150000, unit="рейс")
    G("KE", "Кромка", "U", 1); G("E1", "Услуга ПВХ 19/1 Белый", "KE", price=6000, unit="пог.м")
    con.commit()

c = A.app.test_client()
r = c.get("/api/online/catalog", headers=dict(CF, **{"Accept-Encoding": "gzip"}))
assert r.status_code == 200 and r.headers.get("Content-Encoding") == "gzip"
cat = json.loads(gzip.decompress(r.data))
m = {x["id"]: x for x in cat["materials"]}
assert m["M1"]["kind"] == "ldsp" and m["M1"]["sheet"] == {"L": 2800, "W": 2070} and m["M1"]["thick"] == 16          # ўлчам — папка номидан
assert m["T1"]["kind"] == "countertop" and m["T1"]["sheet"] == {"L": 4100, "W": 600} and m["T1"]["thick"] == 38 and m["T1"]["price"] == 90
assert m["P1"]["kind"] == "pvc" and m["P1"]["thick"] == 16 and m["FT1"]["kind"] == "faton" and m["FT1"]["thick"] == 0.8
assert "KR1" not in m and {x["id"] for x in cat["edges"]} == {"E1"}
sv = {x["id"]: x for x in cat["services"]}
assert sv["SP"]["packet"] and not sv["S6"]["packet"] and sv["UT1"]["group"] == "utol" and sv["DL1"]["group"] == "delivery" and "E1" not in sv
assert c.get("/api/online/catalog", headers=CF).get_json()["materials"]                # gzip'сиз ҳам
# мижоз кусоги
P = [{"pos": 1, "name": "Полка", "L": 800, "W": 300, "qty": 6, "grain": False, "edges": {"L1": "E1"}, "holes": {}}]
O = {"v": 1, "material": {"id": "M1", "name": "ЛДСП Антрацит", "owner": "ours", "offcuts": [{"L": 1200, "W": 800, "qty": 1}, {"L": 900, "W": 600}]},
     "parts": P, "layout": {"sheets": [{"sheet": {"L": 2800, "W": 2070}}, {"offcut": True, "sheet": {"L": 1200, "W": 800}},
                                       {"offcut": True, "sheet": {"L": 900, "W": 600}}]}}
k = c.post("/api/online/calc", json=O, headers=CF).get_json()["calc"]
ls = {l["name"]: l for l in k["lines"]}
assert k["sheets"] == 1 and k["offcut_sheets"] == 2 and k["offcut_m2"] == 1.5, k
assert ls["ЛДСП Silkway Антрацит"]["qty"] == 1 and ls["Распил ЛДСП/ЛМДФ 5,796 кв"]["qty"] == 1 and ls["Распил Кусок"]["qty"] == 1.5, ls
assert "Услуга ПВХ 19/1 Белый" in ls                                                    # кромка — ҳисобланади
# фақат кусоклар — материал қатори йўқ
O2 = dict(O, layout={"sheets": [{"offcut": True, "sheet": {"L": 1200, "W": 800}}]})
k2 = c.post("/api/online/calc", json=O2, headers=CF).get_json()["calc"]
names = [l["name"] for l in k2["lines"]]
assert "ЛДСП Silkway Антрацит" not in names and "Распил ЛДСП/ЛМДФ 5,796 кв" not in names and "Распил Кусок" in names and k2["sheets"] == 0, names
r = c.post("/api/online/calc", json=dict(O, layout={"sheets": [{"offcut": True}]}), headers=CF)
assert r.status_code == 400 and r.get_json()["error"] == "bad_offcut"
# layout йўқ — олдингидек (платформа ўзи жойлайди)
k3 = c.post("/api/online/calc", json={"v": 1, "material": {"id": "M1", "owner": "ours"}, "parts": P}, headers=CF).get_json()["calc"]
assert k3["sheets"] == 1 and not k3["offcut_sheets"]
with A.app.app_context():
    d = OL.validate(O)
    assert d["material"]["offcuts"][1] == {"L": 900, "W": 600, "qty": 1, "name": ""} and d["layout"]["sheets"][1]["offcut"]
print("ONLINE OFFCUT OK")
