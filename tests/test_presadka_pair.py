"""Присадка (28.10): «Под раффикс» ва «Под эксцентрики» — жуфт тарафи «Глухие отверстия» (шу папкадан, 1 тага 1 та);
накладной ойнасида алоҳида «ответная часть (авто)» қатори ўзи ҳисобланади. D25 ва бошқа хизматлар — жуфтсиз."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp

with A.app.app_context():
    con = A.db()
    for ext, name, par, fold, src in (("cP", "Присадка", "cU", 1, "cat"), ("g1", "Услуга сверления Глухие отверстия (разного диаметра)", "cP", 0, "cat"),
                                      ("e1", "Услуга сверления Под эксцентрики", "cP", 0, "cat"), ("e2", "Услуга сверления Под эксцентрики (D25мм)", "cP", 0, "cat"),
                                      ("r1", "Услуга сверления Под раффикс", "cP", 0, "cat"), ("p1", "Услуга сверления Под петли (35мм)", "cP", 0, "cat"),
                                      ("F9", "9 Присадка", "0", 1, "1c"), ("G9", "Услуга сверления Глухие отверстия (разного диаметра)", "F9", 0, "1c"),
                                      ("E9", "Услуга сверления Под эксцентрики", "F9", 0, "1c")):
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,src) VALUES(?,?,?,'шт',1,750,0,?,?,0,'',?)",
                    (ext, ext, name, par, fold, src))
    con.commit()
    pr = erp.presadka_pairs(con)
    assert pr["src"] == {"e1": "g1", "r1": "g1", "E9": "G9"}, pr["src"]
    assert pr["dst"]["g1"]["price"] == 750 and pr["dst"]["g1"]["name"].startswith("Услуга сверления Глухие")
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
h = c.get("/erp/sale/new").get_data(as_text=True)
assert "ответная часть (авто)" in h and '"e1": "g1"' in h.replace('"e1":"g1"', '"e1": "g1"'), h[h.find("var PAIR"):h.find("var PAIR") + 300]
print("PRESADKA PAIR OK")
