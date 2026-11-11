"""Хабари кетган платформа накладнойига кейин тўлов ёзилса — мижозга бир марта «кассага кирим» хабари (эгаси, 11.11.2026).
Бошқа ўзгаришлар (сумма) — хабарсиз; янги устун пайдо бўлганда эски ҳужжатлар бўйича хабар кетмайди."""
import os, sys, tempfile
p = os.environ.get("RENT_C1_TEST", r"C:\new base")
if not os.path.isfile(os.path.join(p, "1Cv7.DD")):
    print("PAY AFTER TEST SKIPPED (нет копии базы)"); sys.exit(0)
os.environ["RENT_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as A, c1, erp
import datetime as _dt
A.app.testing = True
c = A.app.test_client()
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/login", data={"username": "admin", "password": "admin123", "csrf": "t"}).status_code == 302
with c.session_transaction() as s: s["csrf"] = "t"
assert c.post("/1c/settings", data={"c1_path": p, "c1_doc_notify": "1", "csrf": "t"}).status_code == 302
today = _dt.date.today().isoformat()
later = lambda: _dt.datetime.now() + _dt.timedelta(hours=1)
with A.app.app_context():
    con = A.db()
    erp.sync(force=True, con=con)
    c1.sync_clients(force=True)
    con.execute("UPDATE c1_seen SET notified=1"); con.commit()
    con.execute("INSERT OR REPLACE INTO erp_goods(ext, code, name, unit, cur, price, parent, folder, deleted, lc, src, cat) "
                "VALUES('ci990001','ci990001','ЛДСП Тест Белый 16мм','лист',2,30,'',0,0,' лдсп тест белый 16мм ','cat','good')")
    con.execute("INSERT INTO erp_clients(ext, code, name, phones, folder, deleted, lc, src) VALUES('P991','P991','Ворис Тест','+998 90 555 44 34',0,0,' ворис тест ','pf')")
    def sale(no, usd, paid):
        d = con.execute("INSERT INTO erp_docs(kind,no,date,time,client,rate,usd,uzs,discount,nal,pay_usd,pay_uzs,paid,status,src) "
                        "VALUES('sale',?,?,'16:33:00','P991',12000,?,?,0,1,?,0,?,'posted','pf')", (no, today, usd, usd * 12000, paid, paid)).lastrowid
        con.execute("INSERT INTO erp_lines(doc_id,lineno,good,qty,unit,price,usd,price_uzs,uzs) VALUES(?,1,'ci990001',1,'лист',?,?,?,?)",
                    (d, usd, usd, usd * 12000, usd * 12000))
        con.commit()
        return d
    d1 = sale("6960", 75.5, 0)
    c1.sync_clients()
    cl = A.q("SELECT * FROM clients WHERE onec_code='1c77:P991'", one=True)
    con.execute("UPDATE clients SET active=1, sms_enabled=1, c1_doc=1, sms_paused_until=NULL, lang='uz' WHERE id=?", (cl["id"],)); con.commit()
    msgs = lambda: [r["text"] for r in A.q("SELECT text FROM tg_out WHERE client_id=? ORDER BY id", (cl["id"],))]
    # 1) накладной тўловсиз — одатдаги хабар, қарз $75.50
    c1.notify_new_docs(later())
    m = msgs()
    assert len(m) == 1 and "6960" in m[0] and "75.50" in m[0], m
    # 2) 1.5 соатдан кейин кассир шу накладнойга пул ёзди — бир марта «Kassaga kirim» хабари, қарз йўқ
    con.execute("UPDATE erp_docs SET pay_usd=75.5, paid=75.5 WHERE id=?", (d1,)); con.commit()
    c1.sync_clients()
    assert A.scalar("SELECT pay_due FROM c1_seen WHERE iddoc=?", (f"pf:{d1}",)) == 75.5
    c1.notify_new_docs(later())
    m = msgs()
    assert len(m) == 2, m
    assert "Kassaga kirim: $75.50 (Nakladnoy №6960)" in m[1] and "✅" in m[1] and "Voris" not in m[1] and "Ворис Тест" in m[1], m[1]
    assert abs(A.client_usd_balance(cl["id"])) < 0.01
    c1.sync_clients(); c1.notify_new_docs(later())
    assert len(msgs()) == 2                              # қайта юборилмайди
    # 3) сумма ўзгарди (тўлов эмас) — хабар йўқ (27.10 қоидаси)
    con.execute("UPDATE erp_docs SET usd=80, uzs=960000 WHERE id=?", (d1,)); con.execute("UPDATE erp_lines SET usd=80 WHERE doc_id=?", (d1,)); con.commit()
    c1.sync_clients(); c1.notify_new_docs(later())
    assert len(msgs()) == 2, msgs()
    # 4) қисман тўлов: $100 накладной, кейин $40 — «Kassaga kirim: $40», қолдиқ қарз кўрсатилади
    d2 = sale("6970", 100, 0)
    c1.sync_clients(); c1.notify_new_docs(later())
    assert len(msgs()) == 3
    con.execute("UPDATE erp_docs SET pay_usd=40, paid=40 WHERE id=?", (d2,)); con.commit()
    c1.sync_clients(); c1.notify_new_docs(later())
    m = msgs()
    assert len(m) == 4 and "Kassaga kirim: $40 (Nakladnoy №6970)" in m[3] and "🧾" in m[3] and "64.50" in m[3], m[3]   # 4.50 (80−75.50) + 60
    # 5) тўлов накладной хабаридан ОЛДИН ёзилса — битта одатдаги хабар (тўлов ичида), алоҳида хабар йўқ
    d3 = sale("6980", 50, 0)
    c1.sync_clients()
    con.execute("UPDATE erp_docs SET pay_usd=50, paid=50 WHERE id=?", (d3,)); con.commit()
    c1.sync_clients(); c1.notify_new_docs(later())
    m = msgs()
    assert len(m) == 5 and "6980" in m[4] and "Kassaga kirim" in m[4], m[4]
    c1.sync_clients(); c1.notify_new_docs(later())
    assert len(msgs()) == 5
    # 6) ўрнатишда: эски, хабари кетган ва кейин тўланган накладнойлар бўйича хабар кетмайди (paid устуни бўш → эслаб қолинади)
    d4 = sale("6990", 30, 0)
    c1.sync_clients(); c1.notify_new_docs(later())
    n = len(msgs())
    con.execute("UPDATE erp_docs SET pay_usd=30, paid=30 WHERE id=?", (d4,))
    con.execute("UPDATE c1_seen SET paid=NULL"); con.commit()
    c1.sync_clients(); c1.notify_new_docs(later())
    assert len(msgs()) == n and A.scalar("SELECT COUNT(*) FROM c1_seen WHERE pay_due>0") == 0, msgs()[n:]
    # 7) созлама ўчирилса — хабар йўқ
    A.set_settings(c1_pay_after="0")
    d5 = sale("6995", 20, 0)
    c1.sync_clients(); c1.notify_new_docs(later())
    n = len(msgs())
    con.execute("UPDATE erp_docs SET pay_usd=20, paid=20 WHERE id=?", (d5,)); con.commit()
    c1.sync_clients(); c1.notify_new_docs(later())
    assert len(msgs()) == n
print("PAY AFTER OK")
