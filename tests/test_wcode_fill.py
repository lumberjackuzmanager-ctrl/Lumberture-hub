"""2.11 (эгаси): накладной изоҳига «Ц108» ёзилса — цех ишчилари шу кодга ёзган услуга/товарлар қатор бўлиб тушади
(/erp/api/wcode); товарсиз иш — «қўлда қўшинг»; бошқа накладнойга уланган — огоҳлантириш; черновик ўчиқ ҳолатда ҳам."""
import os, sys, tempfile, json, time
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db"); os.environ["RENT_UI_LANG"] = "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, erp as E, c1, tg

tg.call = lambda token, method, params=None, timeout=15: {"message_id": 1} if method == "sendMessage" else ({"username": "b"} if method == "getMe" else True)
c1.journal = lambda: {}
c1.table = lambda name: []
c1.clients = lambda: {}

with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    A.set_settings(raspil_wait_draft="0")
    con = A.db()
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Улмас','1',?, 'raspil', 701, 1)", (A.now(),))
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src,price_last) "
                "VALUES('G1','1','Услуга ПВХ Дуб 19/0.8','пог.м',1,4500,0,'',0,0,'услуга пвх дуб','svc','pf',0)")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src) VALUES('C1','1','Максуд ака','','+998 90 123 99 22','',0,0,'максуд ака','pf')")
    con.commit()
    rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=701").fetchone())
    code = P.new_code("Максуд 9922")
    assert not P.code_row(code)["doc_id"]                                   # черновик очилмайди (31.10)
    j1 = P.add_wait(rw, "job", 0, machine="kromka", code=code)
    P.wait_add_item(j1, "G1", "Услуга ПВХ Дуб 19/0.8", 6.5, "м")
    P.add_wait(rw, "main", 3, machine="arra", code=code)                    # распил 3 лист — товари йўқ
    cl = P.code_lines(code)
    assert [(l["good"], l["qty"], l["price"]) for l in cl["lines"]] == [("G1", 6.5, 4500)] and cl["client"] == "C1" and len(cl["nogoods"]) == 1, cl

c = A.app.test_client()
with c.session_transaction() as s_: s_["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
j = c.get(f"/erp/api/wcode?c={code},99999").get_json()
x, y = j["codes"]
assert x["code"] == code and [(l["good"], l["qty"], l["gname"]) for l in x["lines"]] == [("G1", "6.5", "Услуга ПВХ Дуб 19/0.8")], x
assert x["client"] == {"ext": "C1", "name": "Максуд ака"} and len(x["nogoods"]) == 1 and not x["other"], x
assert y == {"code": 99999, "missing": True}, y
h = c.get("/erp/sale/new").get_data(as_text=True)
assert "/erp/api/wcode" in h and "wcinfo" in h

# накладной проведен бўлди (изоҳда Ц-код) → иш уланди; бошқа накладнойда шу код — «уланган» огоҳлантириши
with A.app.app_context():
    con = A.db()
    did = con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,due_days,status,src,user,"
                      "created_by,created_at,updated_by,updated_at,ret) VALUES('sale','6753','10:00:00','C1',12000,0,0,0,1,?,0,0,0,date('now'),0,'posted','pf','t','t',"
                      "datetime('now'),'t',datetime('now'),1)", (f"Ц{code}",)).lastrowid
    con.execute("UPDATE raspil_job SET status='linked', iddoc=? WHERE id=?", (f"pf:{did}", j1)); con.commit()
j = c.get(f"/erp/api/wcode?c={code}").get_json()["codes"][0]
assert j["lines"] == [] and j["other"] == ["6753"], j
j = c.get(f"/erp/api/wcode?c={code}&doc={did}").get_json()["codes"][0]            # шу накладнойни очганда — ўз қаторлари
assert [l["good"] for l in j["lines"]] == ["G1"] and not j["other"], j

# касса роли ҳам ишлата олади
with A.app.app_context():
    from werkzeug.security import generate_password_hash
    A.db().execute("INSERT INTO users(username,password_hash,role,active) VALUES('kas8',?,'kassa',1)", (generate_password_hash("Kassa12345x"),)); A.db().commit()
k = A.app.test_client()
with k.session_transaction() as s_: s_["csrf"] = "t"
assert k.post("/login", data={"username": "kas8", "password": "Kassa12345x", "csrf": "t"}).status_code == 302
assert k.get(f"/erp/api/wcode?c={code}").status_code == 200
print("WCODE FILL OK")
