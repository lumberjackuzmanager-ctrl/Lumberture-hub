"""2.11 (эгаси): «Онлайн-заказы» — «🖨 Печать карт» (static/lb_print.js, LBPrint.open). Сайт юборган layout (листлар, pieces, kerf, trim)
энди сақланади — аввал фақат лист ўлчами қоларди, карта чиқмас эди."""
import os, sys, tempfile, json, time
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app as A, online as OL

P = [{"pos": 1, "name": "Фасад", "L": 548, "W": 354, "qty": 2, "grain": True, "edges": {"L1": "E1"}, "holes": {}}]
O = {"v": 1, "material": {"id": "M1", "name": "ЛДСП Белый", "owner": "ours", "sheet": {"L": 2750, "W": 1830}}, "parts": P,
     "client": {"name": ""}, "group": "G1",
     "layout": {"kerf": 4.4, "trim": 5, "manual": False, "sheets": [{"offcut": False, "sheet": {"L": 2750, "W": 1830},
                "pieces": [{"pos": 1, "x": 5, "y": 5, "w": 548, "h": 354, "rot": False}, {"pos": 1, "x": 557.4, "y": 5, "w": 548, "h": 354, "rot": True},
                           {"pos": "x", "x": "bad"}]}]}}
with A.app.app_context():
    d = OL.validate(O)
    lay = d["layout"]
    assert lay["kerf"] == 4.4 and lay["trim"] == 5 and lay["manual"] is False, lay
    sh = lay["sheets"][0]
    assert sh["offcut"] is False and sh["L"] == 2750 and sh["sheet"] == {"L": 2750, "W": 1830}, sh
    assert sh["pieces"] == [dict(pos=1, x=5.0, y=5.0, w=548.0, h=354.0, rot=False), dict(pos=1, x=557.4, y=5.0, w=548.0, h=354.0, rot=True)], sh["pieces"]
    con = A.db(); OL.init_tables(con)
    oid = con.execute("INSERT INTO online_orders(phone, name, status, data, calc, created_at, grp) VALUES('998901234567','Азиз','new',?,?,?,?)",
                      (json.dumps(d, ensure_ascii=False), "{}", A.now(), "G1")).lastrowid
    con.commit()
c = A.app.test_client()
with c.session_transaction() as s_: s_["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
h = c.get(f"/erp/online/{oid}").get_data(as_text=True)
assert "/static/lb_print.js" in h and "button" in h and "lbprint" in h and f"/erp/online/{oid}.json" in h and 'data-cname="Азиз"' in h, h[:500]
h = c.get("/erp/online?st=all").get_data(as_text=True)
assert "/static/lb_print.js" in h and h.count("lbprint") >= 2
j = json.loads(c.get(f"/erp/online/{oid}.json").get_data(as_text=True))
assert j["number"] == oid and j["layout"]["sheets"][0]["pieces"][1]["rot"] is True
assert open(os.path.join(ROOT, "static", "lb_print.js"), encoding="utf-8").read().count("window.LBPrint") == 1
print("LBPRINT OK")
