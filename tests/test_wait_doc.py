"""Накладнойсиз иш → платформада накладной черновиги: рақами — мижоз коди, мижоз «танланмаган», кассир танлаб проведен қилади → «тўланди»."""
import os, sys, tempfile, json
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, raspil as P, raspil_bot as B, erp as E, c1, tg

sent = []
def fake_call(token, method, params=None, timeout=15):
    sent.append((method, params or {}))
    return {"message_id": 900 + len(sent)} if method == "sendMessage" else ({"username": "lumb_bot"} if method == "getMe" else True)
tg.call = fake_call
c1.journal = lambda: {}
c1.table = lambda name: []
c1.clients = lambda: {}

with A.app.app_context():
    B.connect("123456:" + "A" * 30)
    con = A.db()
    con.execute("INSERT INTO ready_workers(name, phone, created_at, role, bot_uid, active) VALUES('Улмас','1',?, 'raspil', 701, 1)", (A.now(),))
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src,price_last) "
                "VALUES('G1','1','Услуга присадка','шт',1,0,0,'',0,0,'услуга присадка','svc','1c',5000)")
    con.execute("INSERT INTO erp_clients(ext,code,name,full,phones,parent,folder,deleted,lc,src) VALUES('C1','1','Алишер','','+998 90 123 11 31','',0,0,'алишер','1c')")
    con.commit()
    rw = dict(con.execute("SELECT * FROM ready_workers WHERE bot_uid=701").fetchone())

    # 1) янги мижоз: код → черновик, мижоз «танланмаган», рақам — код
    code = P.new_code("Алишер 1131")
    w = P.code_row(code)
    d = con.execute("SELECT * FROM erp_docs WHERE id=?", (w["doc_id"],)).fetchone()
    assert d and d["status"] == "draft" and d["src"] == "pf" and d["client"] == E.nocli_ext(con, create=False), dict(d) if d else None
    assert P.clabel(code) == f"№{str(d['no']).lstrip('0')}" and P.code_by_no(d["no"]) == code
    assert "Алишер 1131" in d["note"]

    # 2) иш ва товар → черновик қатори (охирги нарх), изоҳда ким нима қилди
    jid = P.add_wait(rw, "job", 0, machine="pris", code=code)
    P.wait_add_item(jid, "G1", "Услуга присадка", 3, "та")
    ls = con.execute("SELECT good, qty, price FROM erp_lines WHERE doc_id=?", (d["id"],)).fetchall()
    assert [tuple(x) for x in ls] == [("G1", 3.0, 5000.0)], [tuple(x) for x in ls]
    nt = con.execute("SELECT note, updated_at FROM erp_docs WHERE id=?", (d["id"],)).fetchone()
    assert P.mlabel("pris") in nt["note"], nt["note"]
    # ўзгармаса — версия (updated_at) тегилмайди
    P.code_doc_sync(code)
    assert con.execute("SELECT updated_at FROM erp_docs WHERE id=?", (d["id"],)).fetchone()[0] == nt["updated_at"]

    # 3) «мижоз танланмаган» билан проведен бўлмайди
    try:
        E.save_sale(con, d["id"], dict(client=E.nocli_ext(con, create=False), lines=[dict(good="G1", qty=3, price=5000)], version=nt["updated_at"]), post=True)
        raise SystemExit("проведен бўлмаслиги керак эди")
    except ValueError as e:
        assert "Мижоз танланмаган" in str(e), e

    # 4) кассир ўзгартирди — бот энди черновикка тегмайди
    con.execute("UPDATE erp_docs SET client='C1', updated_by='kassir' WHERE id=?", (d["id"],)); con.commit()
    jid2 = P.add_wait(rw, "job", 0, machine="pris", code=code)
    P.wait_add_item(jid2, "G1", "Услуга присадка", 2, "та")
    assert con.execute("SELECT COUNT(*) FROM erp_lines WHERE doc_id=?", (d["id"],)).fetchone()[0] == 1
    # платформа накладнойи — 1С кўринишида (цех боти, KPI)
    sd = c1.sale_doc(f"pf:{d['id']}")
    assert sd["lines"][0]["name"] == "Услуга присадка" and sd["client"]["name"] == "Алишер"

    # 5) кусок сотилди → проведен → уланди, ишчига «тўланди»
    code2 = P.new_code("Бобур 4410")
    j3 = P.add_wait(rw, "sale", 0, machine=P.SALE, code=code2)
    d2 = P.code_row(code2)["doc_id"]
    con.execute("UPDATE erp_docs SET client='C1', status='posted' WHERE id=?", (d2,)); con.commit()
    sent.clear()
    P.pf_link_waits()
    assert con.execute("SELECT status FROM raspil_job WHERE id=?", (j3,)).fetchone()[0] == "linked"
    assert any("тўланди" in (p.get("text") or "") for m, p in sent if m == "sendMessage"), sent

    # 6) эски накладнойга қўшимча: мижоз ўша накладнойдан
    con.execute("INSERT INTO erp_docs(kind,no,time,client,rate,usd,uzs,discount,nal,note,pay_usd,pay_uzs,paid,date,status,src,ret) "
                "VALUES('sale','000006453','10:00:00','C1',12000,10,120000,0,1,'',0,0,0,date('now'),'posted','1c',1)")
    con.commit()
    sent.clear()
    B.wait_flow(rw, 701, 950, ["w", "r", "pris"])
    B.on_message({"message_id": 2, "chat": {"id": 701, "type": "private"}, "from": {"id": 701}, "text": "6453"})
    txt = [p.get("text") or "" for m, p in sent if m in ("sendMessage", "editMessageText")]
    assert any("№6453 га қўшимча" in t and "Алишер" in t for t in txt), txt
    code3 = A.q("SELECT code FROM raspil_wcode WHERE ref_no='6453'", one=True)["code"]
    d3 = con.execute("SELECT client, note FROM erp_docs WHERE id=?", (P.code_row(code3)["doc_id"],)).fetchone()
    assert d3["client"] == "C1" and "Қўшимча: №6453" in d3["note"], dict(d3)
    # 7) код черновиги проведен бўлгандан КЕЙИН шу кодга иш қўшилди — шу мижозга қўшимча черновик ўзи очилади (26.10)
    code4 = P.new_code("Зухриддин 40")
    ja = P.add_wait(rw, "job", 0, machine="pris", code=code4)
    P.wait_add_item(ja, "G1", "Услуга присадка", 1, "та")
    d4 = P.code_row(code4)["doc_id"]
    con.execute("UPDATE erp_docs SET client='C1', status='posted', updated_by='kassir' WHERE id=?", (d4,))
    con.execute("INSERT INTO erp_log(at,user,doc_id,action,detail) VALUES('2000-01-01 00:00:00','kassir',?,'post','')", (d4,)); con.commit()
    P.pf_link_waits()
    assert con.execute("SELECT status FROM raspil_job WHERE id=?", (ja,)).fetchone()[0] == "linked"
    jb = P.add_wait(rw, "job", 0, machine="pris", code=code4)
    P.wait_add_item(jb, "G1", "Услуга присадка", 2, "та")
    assert con.execute("SELECT status FROM erp_docs WHERE id=?", (d4,)).fetchone()[0] == "posted"
    assert [tuple(x) for x in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (d4,))] == [("G1", 1.0)]   # проведенга тегилмайди
    P.pf_link_waits()
    d5 = P.code_row(code4)["doc_id"]
    assert d5 and d5 != d4, (d4, d5)
    r5 = con.execute("SELECT status, client FROM erp_docs WHERE id=?", (d5,)).fetchone()
    assert r5["status"] == "draft" and r5["client"] == "C1", dict(r5)
    assert [tuple(x) for x in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (d5,))] == [("G1", 2.0)], ([tuple(x) for x in con.execute("SELECT good, qty FROM erp_lines WHERE doc_id=?", (d5,))], con.execute("SELECT status, items FROM raspil_job WHERE id=?", (jb,)).fetchone()[:], con.execute("SELECT note FROM erp_docs WHERE id=?", (d5,)).fetchone()[0])
    con.execute("UPDATE erp_docs SET status='posted', updated_by='kassir' WHERE id=?", (d5,)); con.commit()
    P.pf_link_waits()
    assert con.execute("SELECT status FROM raspil_job WHERE id=?", (jb,)).fetchone()[0] == "linked"
    # суммада ёзилган иш (фрезеровка) — накладнойдаги ровер хизматига уланади
    con.execute("INSERT INTO erp_goods(ext,code,name,unit,cur,price,price_in,parent,folder,deleted,lc,cat,src,price_last) "
                "VALUES('RV','9','Услуга Ровер','шт',1,0,0,'',0,0,'услуга ровер','svc','1c',0)"); con.commit()
    code6 = P.new_code("Фрезер 1111")
    jr = P.add_wait(rw, "job", 0, machine="pris", code=code6)
    it = json.loads(con.execute("SELECT items FROM raspil_job WHERE id=?", (jr,)).fetchone()[0])
    it["wait"]["pick"] = [{"gid": "RV", "name": "Услуга фрезеровка (обработка ЧПУ)", "m": 10000.0, "unit": "сўм"}]
    con.execute("UPDATE raspil_job SET items=? WHERE id=?", (json.dumps(it, ensure_ascii=False), jr)); con.commit()
    d6 = P.code_row(code6)["doc_id"]
    con.execute("DELETE FROM erp_lines WHERE doc_id=?", (d6,))
    con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd,price_uzs,uzs) VALUES(?,1,'RV',1,'шт',10000,0.8,10000,10000)", (d6,))
    con.execute("UPDATE erp_docs SET client='C1', status='posted', updated_by='kassir' WHERE id=?", (d6,)); con.commit()
    P.pf_link_waits()
    assert con.execute("SELECT status, info FROM raspil_job WHERE id=?", (jr,)).fetchone()[0] == "linked", con.execute("SELECT info FROM raspil_job WHERE id=?", (jr,)).fetchone()[0]
print("WAIT DOC OK")

# тест режими: проведен йўқ, ботда «Ц27» (1С изоҳига шу ёзилади)
with A.app.app_context():
    A.set_settings(erp_test_mode="1")
    assert P.clabel(code) == f"Ц{code}" and E.test_mode()
    c9 = P.new_code("Тест 0001")                             # тест режими: янги кодга черновик очилмайди (иш 1С'да)
    P.add_wait(rw, "job", 0, machine="pris", code=c9)
    assert not P.code_row(c9)["doc_id"] and P.clabel(c9) == f"Ц{c9}"
    A.set_settings(erp_test_mode="0")
    assert P.clabel(code).startswith("№")
cl = A.app.test_client()
with cl.session_transaction() as s_: s_["csrf"] = "t"
cl.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"})
with cl.session_transaction() as s_: s_["csrf"] = "t"
assert cl.post("/erp/test_mode", data={"csrf": "t", "on": "1"}).status_code == 302
h = cl.get("/erp?kind=sale&d1=2026-01-01").get_data(as_text=True)
assert "Тестовый режим" in h and "Выключить тестовый режим" in h
with A.app.app_context():
    did = P.code_row(code)["doc_id"]
h = cl.get(f"/erp/doc/{did}/edit").get_data(as_text=True)
assert 'value="post"' not in h and "Тестовый режим: проводить нельзя" in h, h[:300]
print("TEST MODE OK")
