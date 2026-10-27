"""Платформа ҳужжати тузатилганда мижозга хато/такрор хабар кетмасин (эгаси, 26.10): кассир проведенни бекор қилиб қайта
проведен қилса — узр йўқ; узрдан кейин қайта проведен бўлса — қайта хабар; ўчириб шу суммада янгисини ёзса — хабар йўқ;
бошқа суммада — узр ва янгиси бирга; хабар кетгач сумма ўзгарса — шу ҳужжат хабари янги сумма билан.
27.10 дан стандарт (c1_change_notify=0): тузатиш бўйича ҳеч қандай хабар йўқ — фақат биринчи проведен бир марта."""
import os, sys, tempfile
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("1C PF FIX TEST SKIPPED (нет копии базы)"); sys.exit(0)
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, erp
import datetime as _dt
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/1c/settings", data={"c1_path": p, "c1_doc_notify": "1", "c1_new_remind": "1", "csrf": "t"}).status_code == 302
today = _dt.date.today().isoformat()
NOW = _dt.datetime.now()
with A.app.app_context():
    con = A.db()
    erp.sync(force=True, con=con)
    c1.sync_clients(force=True)
    con.execute("UPDATE c1_seen SET notified=1"); con.commit()
    A.set_settings(c1_change_notify="1")             # пастдаги 1–5: олдинги тартиб (узр ва янгиланган хабар) — созлама билан
    con.execute("INSERT INTO erp_clients(ext, code, name, phones, folder, deleted, lc, src) VALUES('P991','P991','Тузатиш Мижоз','+998 90 555 44 11',0,0,'','pf')")
    con.commit()

    def pay(amt, no):
        d = con.execute("INSERT INTO erp_docs(kind,no,date,time,rate,status,src) VALUES('pay',?,?,'12:00:00',12500,'posted','pf')", (no, today)).lastrowid
        con.execute("INSERT INTO erp_lines(doc_id,lineno,client,usd,uzs,debt) VALUES(?,1,'P991',?,0,?)", (d, amt, amt)); con.commit()
        return d

    def st(d, s):
        con.execute("UPDATE erp_docs SET status=? WHERE id=?", (s, d)); con.commit()

    def tick(minutes):
        c1.sync_clients()
        c1.notify_new_docs(NOW + _dt.timedelta(minutes=minutes))

    def msgs():
        return [r["text"] for r in A.q("SELECT t.text FROM tg_out t JOIN clients cl ON cl.id=t.client_id WHERE cl.onec_code='1c77:P991' ORDER BY t.id")]

    d1 = pay(300, "901")
    c1.sync_clients()
    cl = A.q("SELECT * FROM clients WHERE onec_code='1c77:P991'", one=True)
    con.execute("UPDATE clients SET active=1, sms_enabled=1, c1_doc=1, sms_paused_until=NULL WHERE id=?", (cl["id"],)); con.commit()
    tick(30)
    m = msgs(); assert len(m) == 1 and "$300" in m[0], m
    # 1) бекор қилиб, тез қайта проведен — узр йўқ
    st(d1, "draft"); tick(5); tick(10); tick(20)
    assert len(msgs()) == 1, msgs()
    st(d1, "posted"); tick(120)
    assert len(msgs()) == 1, msgs()
    # 2) бир соатдан кўп бекор турди — узр; кейин қайта проведен — одатдаги хабар қайта
    st(d1, "draft"); c1.sync_clients(); c1.sync_clients(); c1.notify_new_docs(NOW + _dt.timedelta(minutes=30))
    assert len(msgs()) == 1, msgs()                                     # ҳали бир соат бўлмади
    c1.notify_new_docs(NOW + _dt.timedelta(minutes=120))
    m = msgs(); assert len(m) == 2 and "901" in m[1] and "отмен" in m[1], m
    st(d1, "posted"); tick(150)
    m = msgs(); assert len(m) == 3 and "$300" in m[2] and "отмен" not in m[2], m
    # 3) ўчириб, шу суммада бошқа ҳужжатга ёзди — хабар йўқ (на узр, на такрор)
    st(d1, "deleted"); d2 = pay(300, "902"); tick(160); tick(170); tick(300)
    assert len(msgs()) == 3, msgs()
    # 4) ўчириб, бошқа суммада ёзди — узр ва янгиси бирга (бир соат кутмасдан)
    st(d2, "deleted"); d3 = pay(250, "903"); tick(310); tick(325)
    m = msgs(); assert len(m) == 5 and "902" in m[3] and "отмен" in m[3] and "$250" in m[4], m
    tick(500); assert len(msgs()) == 5, msgs()                          # кейин узр қайта кетмайди
    # 5) хабар кетгач суммаси ўзгарди — эски сумма бўйича узр ва янгиси бирга
    con.execute("UPDATE erp_lines SET usd=200, debt=200 WHERE doc_id=?", (d3,)); con.commit()
    tick(520); tick(535)
    m = msgs(); assert len(m) == 7 and "903" in m[5] and "$250" in m[5] and "отмен" in m[5] and "$200" in m[6] and "$200" in m[5], m
    tick(700); assert len(msgs()) == 7, msgs()
    # бекор қилинган ягона ҳужжат — «Должники» тозаланади, узрда тўғри қолдиқ
    assert "Задолженности нет" in m[1] or "На вашем счёте" not in m[1], m[1]
    [print("---", x.replace("\n", " | ")) for x in msgs()] if os.environ.get("SHOWM") else None

    # ---- эгаси (27.10, стандарт): тузатиш бўйича хабар йўқ — накладной/тўлов биринчи марта проведен бўлганда бир марта
    A.set_settings(c1_change_notify="0")
    con.execute("INSERT INTO erp_clients(ext, code, name, phones, folder, deleted, lc, src) VALUES('P992','P992','Битта Хабар','+998 90 555 44 12',0,0,'','pf')")
    con.commit()
    def pay2(amt, no):
        d = con.execute("INSERT INTO erp_docs(kind,no,date,time,rate,status,src) VALUES('pay',?,?,'12:00:00',12500,'posted','pf')", (no, today)).lastrowid
        con.execute("INSERT INTO erp_lines(doc_id,lineno,client,usd,uzs,debt) VALUES(?,1,'P992',?,0,?)", (d, amt, amt)); con.commit()
        return d
    def msgs2():
        return [r["text"] for r in A.q("SELECT t.text FROM tg_out t JOIN clients cl ON cl.id=t.client_id WHERE cl.onec_code='1c77:P992' ORDER BY t.id")]
    e1 = pay2(400, "911")
    c1.sync_clients()
    cl2 = A.q("SELECT * FROM clients WHERE onec_code='1c77:P992'", one=True)
    con.execute("UPDATE clients SET active=1, sms_enabled=1, c1_doc=1, sms_paused_until=NULL WHERE id=?", (cl2["id"],)); con.commit()
    tick(1000); assert len(msgs2()) == 1 and "$400" in msgs2()[0], msgs2()
    st(e1, "draft"); c1.sync_clients(); c1.sync_clients(); c1.notify_new_docs(NOW + _dt.timedelta(minutes=1200))   # бекор — узр йўқ
    assert len(msgs2()) == 1, msgs2()
    st(e1, "posted"); tick(1300); tick(1400); assert len(msgs2()) == 1, msgs2()                                     # қайта проведен — хабар йўқ
    con.execute("UPDATE erp_lines SET usd=350, debt=350 WHERE doc_id=?", (e1,)); con.commit(); tick(1500); tick(1600)
    assert len(msgs2()) == 1, msgs2()                                                                               # суммаси ўзгарди — хабар йўқ
    st(e1, "deleted"); e2 = pay2(320, "912"); tick(1700); tick(1800); tick(2000)
    assert len(msgs2()) == 1, msgs2()                                                                               # ўрнига бошқа сумма — хабар йўқ
    e3 = pay2(100, "913"); tick(2100); tick(2200)                                                                 # янги тўлов — одатдагидек бир хабар
    m2 = msgs2(); assert len(m2) == 2 and "$100" in m2[1] and "отмен" not in " ".join(m2), m2
    tick(2500); assert len(msgs2()) == 2, msgs2()
print("C1 PF FIX OK")
