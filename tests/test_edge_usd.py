"""28.10 (эгаси): кромка товарлари долларда — приход долларда. «Кромка» папкасидаги товарлар erp_goods'да cur=2,
нарх = Эман нархи / курс; қўлда сўмда ёзилган нарх ҳам долларга; «Пленки ПВХ» ва хизматлар ўзгармайди."""
import os, sys, tempfile
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, catalog as C

with A.app.app_context():
    con = A.db()
    for nid, par, nm, wh, svc in ((1, 0, "Услуги", None, 1), (2, 1, "Кромка", None, 1), (20, 0, "Кромка", 2, 0), (21, 20, "ABS", 2, 0),
                                  (40, 0, "Пленки ПВХ Viwood", 2, 0)):
        con.execute("INSERT INTO cat_nodes(id,parent,name,wh,svc,skey,deleted,manual) VALUES(?,?,?,?,?,?,0,1)", (nid, par, nm, wh, svc, f"x:t{nid}"))
    rows = ((101, 2, "Кромка услуга", "м", 1, 3000, None, 1), (201, 21, "Кромка 22/1 ABS Белый", "м", 1, 0, 6000, 0),
            (202, 21, "Кромка 19/0,4 ABS Серый", "м", 1, 3600, None, 0), (203, 20, "Кромка 42/2 ABS Дуб", "м", 2, 0.9, None, 0),
            (204, 20, "Кромка без цены", "м", 1, 0, None, 0), (401, 40, "Пленка ПВХ Белый", "м", 1, 15000, None, 0))
    for iid, node, nm, unit, cur, price, pe, svc in rows:
        con.execute("INSERT INTO cat_items(id,node,name,lc,unit,cur,price,price_eman,svc,kind,skey,deleted,manual,src_manual) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,0,1,1)", (iid, node, nm, C.lc(nm), unit, cur, price, pe, svc, "svc" if svc else "good", f"x:i{iid}"))
    C.mirror(con, rate=12000)
    g = {r["ext"]: (r["cur"], r["price"]) for r in con.execute("SELECT ext, cur, price FROM erp_goods WHERE src='cat' AND folder=0")}
    assert g["ci201"] == (2, 0.5), g          # Эман 6000 сўм / 12000
    assert g["ci202"] == (2, 0.3), g          # қўлда 3600 сўм → $0.3
    assert g["ci203"] == (2, 0.9), g          # аллақачон долларда
    assert g["ci204"][0] == 2 and not g["ci204"][1], g
    assert g["ci401"] == (1, 15000), g        # плёнка — сўмда қолади
    assert g["ci101"] == (1, 3000), g         # хизмат — ўзгармайди
    C.mirror(con, rate=12000)                 # такрор — барқарор
    assert con.execute("SELECT cur, price FROM erp_goods WHERE ext='ci201'").fetchone()[:] == (2, 0.5)
print("EDGE USD OK")
