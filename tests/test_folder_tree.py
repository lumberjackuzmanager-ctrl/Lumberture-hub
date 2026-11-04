"""Ҳисоботлар: «Вид товара (папка)» — папкалар дарахти (ота папка остида ички папкалари, қисқа номи, чуқурлиги);
ота папка танланса итог ҳамма ички папкалари билан; «По товарам»да гуруҳ қаторида итог, йўл танланган папкадан бошлаб."""
import os, sys, tempfile, json, re, html
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, erp, erp_reports as R
A.app.testing = True
erp.stock_now = lambda: {}
R._c1_stock_moves = lambda: None
R._c1_moves = lambda: ({}, {})
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with A.app.app_context():
    con = A.db()
    F = (("U", "Услуги", "0"), ("K", "Кромка", "U"), ("KM", "Кромка Местная", "K"), ("KM1", "Кромка Местная 19/04", "KM"),
         ("KA", "Кромка AGT", "K"), ("R", "Распил", "U"), ("L", "ЛДСП", "0"))
    for e, n, p in F:
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src) VALUES(?,?,?,'',0,0,?,1,0,?,'1c')", (e, e, n, p, erp.lc(n)))
    for e, n, p in (("G1", "Кромка 19/0.4 Белая", "KM1"), ("G2", "Кромка AGT 22/1", "KA"), ("G3", "Распил ЛДСП", "R"), ("G4", "ЛДСП Белый", "L")):
        con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,parent,folder,deleted,lc,src) VALUES(?,?,?,'пог.м',2,1,?,0,0,?,'1c')", (e, e, n, p, erp.lc(n)))
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,ckind) VALUES('C1','C1','Мижоз','','','0',0,0,'мижоз','1c','client')")
    i = con.execute("INSERT INTO erp_docs(kind,no,date,time,client,usd,uzs,nal,status,src) VALUES('sale','1','2026-10-05','10:00','C1',100,0,1,'posted','1c')").lastrowid
    for n, (g, q, u) in enumerate((("G1", 10, 5), ("G2", 20, 15), ("G3", 3, 30), ("G4", 1, 50))):
        con.execute("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,price,usd,uzs,debt) VALUES(?,?,?,'',?,?,?,0,0)", (i, n + 1, g, q, u / q, u))
    con.commit()
    tree, sub = R._folders(con)
    assert [(t[0], t[2]) for t in tree] == [("L", 0), ("U", 0), ("K", 1), ("KA", 2), ("KM", 2), ("KM1", 3), ("R", 1)], tree
    assert dict((t[0], t[1]) for t in tree)["KM1"] == "Кромка Местная 19/04"                 # қисқа номи
    assert dict((t[0], t[3]) for t in tree)["KM1"] == "Услуги / Кромка / Кромка Местная / Кромка Местная 19/04"
    assert sub("K") == {"K", "KM", "KM1", "KA"}

base = "/erp/rep/sales?go=1&d1=2026-10-01&d2=2026-10-31"
h = c.get(base + "&mode=goods").get_data(as_text=True)
d = json.loads(html.unescape(re.search(r'<script type="application/json" id="rfd">(.*?)</script>', h, re.S).group(1)))
assert [x[0] for x in d] == ["L", "U", "K", "KA", "KM", "KM1", "R"] and d[5][2] == 3, d
assert 'name="f" id="rf" value=""' in h and "— ҳаммаси —" in h
# «Кромка» танланди — итог ички папкалари билан: 5 + 15 = $20, сони 30
h = c.get(base + "&mode=goods&f=K").get_data(as_text=True)
assert 'id="rf" value="K"' in h and ">Кромка</button>" in h and "Услуги / Кромка" in h
assert "Кромка 19/0.4 Белая" in h and "Кромка AGT 22/1" in h and "Распил ЛДСП" not in h and "ЛДСП Белый" not in h
assert "$20" in re.search(r'<div class="stats four">(.*?)</div>\s*<div class="card tablewrap">', h, re.S).group(1)
grp = re.findall(r'<tr class="grp"><td colspan="2">(.*?)</td><td class="r">(.*?)</td><td class="r">(.*?)</td>', h)
assert [(g[0], g[1]) for g in grp] == [("Кромка AGT", "20"), ("Кромка Местная / Кромка Местная 19/04", "10")], grp
assert "$15" in grp[0][2] and "$5" in grp[1][2], grp
# «Услуги» — ҳамма хизматлар
h = c.get(base + "&mode=goods&f=U").get_data(as_text=True)
assert "Распил ЛДСП" in h and "Кромка AGT 22/1" in h and "ЛДСП Белый" not in h
# остаток саҳифасида ҳам шу дарахт
h = c.get("/erp/rep/stock?d1=2026-10-01&d2=2026-10-31&f=KM").get_data(as_text=True)
assert 'id="rfd"' in h and 'id="rf" value="KM"' in h and "Услуги / Кромка / Кромка Местная" in h
print("FOLDER TREE OK")
