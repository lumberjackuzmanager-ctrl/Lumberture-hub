"""31.10 (эгаси): приход қилинмаган (минусга кетган) товарлар — кассир платформа ойнасида огоҳлантириш кўради,
рўйхатдан белгилаб приход очади (миқдор ўзи тушади); приход проведен бўлса — огоҳлантириш йўқолади."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, catalog as C, pickup as PK, erp as E, roles as R
from werkzeug.security import generate_password_hash

with A.app.app_context():
    con = A.db()
    for nid, par, nm, wh, svc in ((1, 0, "Услуги", None, 1), (2, 1, "Распил", None, 1), (10, 0, "ЛДСП", 1, 0), (20, 0, "Кромка", 2, 0),
                                  (21, 20, "ABS", 2, 0), (30, 0, "Опора", 3, 0)):
        con.execute("INSERT INTO cat_nodes(id,parent,name,wh,svc,skey,deleted,manual) VALUES(?,?,?,?,?,?,0,1)", (nid, par, nm, wh, svc, f"x:t{nid}"))
    for iid, node, nm, unit, cur, price, svc in ((101, 2, "Распил ЛДСП", "шт", 1, 30000, 1), (103, 10, "ЛДСП Эггер Белый 16мм", "лист", 2, 40, 0),
                                                 (104, 10, "ЛДСП Эггер Серый 16мм", "лист", 2, 40, 0), (201, 21, "Кромка 19/0,8 ABS Белый", "м", 2, 0.3, 0),
                                                 (301, 30, "Опора кухонная 100мм", "шт", 2, 0.5, 0)):
        con.execute("INSERT INTO cat_items(id,node,name,lc,unit,cur,price,svc,kind,skey,deleted,manual,src_manual) VALUES(?,?,?,?,?,?,?,?,?,?,0,1,1)",
                    (iid, node, nm, C.lc(nm), unit, cur, price, svc, "svc" if svc else "good", f"x:i{iid}"))
    C.mirror(con)
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src) VALUES('C1','1','Алишер','','','',0,0,'алишер','pf')")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src,ckind) VALUES('S1','2','Эман','','','',0,0,'эман','pf','supplier')")

    def doc(kind, lines, status="posted", client="C1", no="00001"):
        did = con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,due_days,status,src,user,"
                          "created_by,created_at,updated_by,updated_at,ret) VALUES(?,?,'10:00:00',?,12000,0,0,0,1,'',0,0,0,date('now'),0,?,'pf','t','t',"
                          "datetime('now'),'t',datetime('now'),1)", (kind, no, client, status)).lastrowid
        con.executemany("INSERT INTO erp_lines(doc_id,lineno,good,client,qty,unit,price,usd,price_uzs,uzs,price2,debt,note,variant) "
                        "VALUES(?,?,?,'',?,'',1,0,0,0,0,0,'','')", [(did, i + 1, g, q) for i, (g, q) in enumerate(lines)])
        con.commit()
        return did
    sale = doc("sale", [("ci103", 3), ("ci201", 12), ("ci101", 3), ("ci301", 4)], no="00077")
    doc("receipt", [("ci301", 10)], client="S1", no="00005")               # опора — приход бор, минус эмас
    doc("sale", [("ci104", 5)], status="draft", no="00078")                 # проведен эмас — қолдиққа таъсир йўқ
    v = PK.minus_info(con, fresh=True)
    assert [(x["ext"], x["qty"]) for x in v["main"]] == [("ci103", -3.0)], v["main"]
    assert v["main"][0]["doc_no"] == "77" and v["main"][0]["doc_client"] == "Алишер", v["main"][0]
    assert [(x["ext"], x["qty"]) for x in v["edge"]] == [("ci201", -12.0)], v["edge"]          # кромка — алоҳида; хизмат (распил) йўқ
    lines = E.minus_lines(con, "ci103,ci301,ci201,nope")
    assert [(l["good"], l["qty"]) for l in lines] == [("ci103", "3"), ("ci201", "12")], lines
    con.execute("INSERT INTO users(username,password_hash,role,active) VALUES('kas9',?,'kassa',1)", (generate_password_hash("Kassa12345x"),))
    con.execute("INSERT INTO users(username,password_hash,role,active) VALUES('bas9',?,'basis',1)", (generate_password_hash("Basis12345x"),))
    con.execute("UPDATE roles SET kind='full', can_post=1 WHERE key='kassa'")
    con.commit(); R.reload()


def client(u, p):
    c = A.app.test_client()
    with c.session_transaction() as s_:
        s_["csrf"] = "t"
    r = c.post("/login", data={"username": u, "password": p, "csrf": "t"})
    assert r.status_code == 302, (u, r.status_code)
    with c.session_transaction() as s_:
        s_["csrf"] = "t"
    return c


for u, p in (("admin", "admin123"), ("kas9", "Kassa12345x")):
    c = client(u, p)
    r = c.get("/erp?kind=sale"); h = r.get_data(as_text=True)
    assert r.status_code == 200 and "minusbar" in h and "/erp/minus" in h, (u, h[:3000])
    h = c.get("/erp/minus").get_data(as_text=True)
    assert "ЛДСП Эггер Белый 16мм" in h and "№77" in h and "Кромка 19/0,8 ABS Белый" not in h and "minusbar" not in h, h[-3000:]
    assert "Кромка 19/0,8 ABS Белый" in c.get("/erp/minus?edge=1").get_data(as_text=True)
    h = c.get("/erp/receipt/new2?minus=ci103").get_data(as_text=True)
    ini = json.loads(h.split("INIT=", 1)[1].split(", CUR=", 1)[0])
    assert [(l["good"], l["qty"], l["gname"]) for l in ini] == [("ci103", "3", "ЛДСП Эггер Белый 16мм")], ini
cb = client("bas9", "Basis12345x")
r = cb.get("/erp?kind=sale")
assert r.status_code == 200 and "minusbar" not in r.get_data(as_text=True)
assert cb.get("/erp/minus").status_code in (403, 404, 302)

with A.app.app_context():                                  # приход проведен → огоҳлантириш йўқолади
    con = A.db()
    doc("receipt", [("ci103", 3), ("ci201", 20)], client="S1", no="00006")
    E.minus_reset()
c = client("admin", "admin123")
r = c.get("/erp?kind=sale")
assert r.status_code == 200 and "minusbar" not in r.get_data(as_text=True)
print("MINUS OK")
